"""ADE-45: JSON-only recoverable snapshots; HTTP handling belongs to ADE-46."""
import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from backend.domain import DataError, validate_records
from backend.library_hours import KST
from scripts.rebuild import rebuild
from .locking import data_lock, storage_directory
from .pipeline import preprocess
from .refresh import merge_records

MAX_VERSIONS = 4
VERSION_ID = re.compile(r'^[0-9]{8}T[0-9]{6}[+-][0-9]{4}$')


class VersionError(DataError):
    def __init__(self, message, code, status_code, details=None):
        super().__init__(message, code)
        self.status_code = status_code
        self.details = details or []

    def as_dict(self):
        return {'error': {'code': self.code, 'message': str(self), 'details': self.details}}


class AtomicWriteError(OSError):
    """A write error with an explicit indication that replacement completed."""
    def __init__(self, cause, *, replaced):
        super().__init__(str(cause))
        self.replaced = replaced


def atomic_bytes(path, payload, *, records=True):
    """Complete and fsync a same-filesystem temporary file, then replace."""
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.staged-', dir=path.parent)
    staged = Path(name)
    replaced = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        value = json.loads(staged.read_bytes().decode('utf-8-sig'))
        if records:
            validate_records(value)
        os.replace(staged, path)
        replaced = True
        if os.name != 'nt':
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise AtomicWriteError(exc, replaced=replaced) from exc
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError as exc:
            raise AtomicWriteError(exc, replaced=replaced) from exc


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')


def normal_records(payload):
    rows = validate_records(json.loads(payload.decode('utf-8-sig')))
    if not rows:
        raise DataError('정상 활성 records가 없습니다.')
    return rows


class VersionStore:
    """JSON snapshots with a replayable prepared/committed decision record.

    Metadata replacement alone is not commitment. Every access uses the OS lock;
    pending recovery is replayed before serving data, mutating or pruning.
    """
    def __init__(self, path, *, rebuild_fn=rebuild):
        self.path = Path(path).resolve()
        self.directory = storage_directory(self.path)
        self.metadata = self.directory / 'metadata.json'
        self.recovery = self.directory / 'recovery.json'
        self.rebuild_fn = rebuild_fn

    def _new_row(self, payload, source_name, state, stamp=None):
        now = datetime.now(KST)
        stamp = stamp or now
        latest = max((r['id'] for r in state['versions']), default=None)
        if latest:
            stamp = max(stamp, datetime.strptime(latest, '%Y%m%dT%H%M%S%z') + timedelta(seconds=1))
        return dict(id=stamp.strftime('%Y%m%dT%H%M%S%z'), created_at=now.isoformat(),
                    source_name=source_name, record_count=len(normal_records(payload)),
                    sha256=hashlib.sha256(payload).hexdigest())

    def _initialize(self):
        if self.metadata.exists():
            return self._load_state()
        state = dict(format_version=1, active_version_id=None, versions=[])
        self._require_clean(state)
        payload = None
        row = None
        if self.path.exists():
            payload = self.path.read_bytes()
            previous = normal_records(payload)
            self._prepared(previous)  # Validate statistics/prediction without changing live bytes.
            stamp = datetime.fromtimestamp(self.path.stat().st_mtime, KST)
            names = {r['source_file'] for r in previous}
            source = next(iter(names)) if len(names) == 1 else self.path.name
            row = self._new_row(payload, source, state, stamp)
            # Existing source name is provenance, not an invented upload event.
            row['created_at'] = stamp.isoformat()
            state.update(active_version_id=row['id'], versions=[row])
        # An empty internal state permits first upload but is never a successful list.
        self._activate(None, state, payload, row, initialize=True)
        return state

    def _load_state(self):
        return self._validate_state(json.loads(self.metadata.read_bytes()))

    def _validate_state(self, state):
        if (not isinstance(state, dict) or set(state) != {'format_version', 'active_version_id', 'versions'}
                or state['format_version'] != 1 or not isinstance(state['versions'], list)
                or len(state['versions']) > MAX_VERSIONS):
            raise DataError('버전 메타데이터가 손상되었습니다.')
        ids = []
        for row in state['versions']:
            if (not isinstance(row, dict) or set(row) != {'id', 'created_at', 'source_name', 'record_count', 'sha256'}
                    or not isinstance(row['id'], str) or not VERSION_ID.fullmatch(row['id'])
                    or not isinstance(row['source_name'], str) or not row['source_name']
                    or type(row['record_count']) is not int or row['record_count'] < 1
                    or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
                raise DataError('버전 메타데이터가 손상되었습니다.')
            stamp = datetime.strptime(row['id'], '%Y%m%dT%H%M%S%z')
            created = datetime.fromisoformat(row['created_at'])
            if stamp.utcoffset() != timedelta(hours=9) or created.utcoffset() != timedelta(hours=9):
                raise DataError('버전 시각이 KST가 아닙니다.')
            ids.append(row['id'])
        if len(set(ids)) != len(ids) or ids != sorted(ids, reverse=True):
            raise DataError('버전 순서가 손상되었습니다.')
        active = state['active_version_id']
        if (ids and active not in ids) or (not ids and active is not None):
            raise DataError('활성 버전 메타데이터가 손상되었습니다.')
        return state

    def _payload(self, row):
        payload = (self.directory / (row['id'] + '.json')).read_bytes()
        rows = normal_records(payload)
        if len(rows) != row['record_count'] or hashlib.sha256(payload).hexdigest() != row['sha256']:
            raise DataError('정상 버전 JSON 검증에 실패했습니다.')
        return payload

    def _restore(self, path, payload):
        normal_records(payload)
        if not path.exists() or path.read_bytes() != payload:
            atomic_bytes(path, payload)

    def _recover(self, state):
        # Verify every committed snapshot before changing the live file or pruning.
        payloads = {row['id']: self._payload(row) for row in state['versions']}
        active = state['active_version_id']
        if active is None:
            self.path.unlink(missing_ok=True)
        else:
            self._restore(self.path, payloads[active])
        self._cleanup(state)

    def _cleanup(self, state):
        """Post-commit garbage collection. Failure cannot turn success into failure."""
        try:
            if self.recovery.exists():
                return
            self._collect_garbage(state)
        except OSError:
            # Directory enumeration can fail just like unlink; retry next time.
            pass

    def _collect_garbage(self, state):
        retained = {row['id'] + '.json' for row in state['versions']} | {'metadata.json'}
        candidates = [p for p in self.directory.glob('*.json')
                      if VERSION_ID.fullmatch(p.stem) and p.name not in retained]
        candidates += list(self.directory.glob('.*.staged-*'))
        candidates += list(self.path.parent.glob(f'.{self.path.name}.staged-*'))
        candidates += list(self.directory.glob('.prepared-*'))
        candidates += list(self.directory.glob('*.tmp'))
        for candidate in candidates:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                # Retry under the lock on the next read/write; never remove retained data.
                pass

    def _run(self, operation, *, wait=False):
        try:
            with data_lock(self.path, wait=wait):
                self._recover_transaction()
                state = self._initialize()
                self._recover(state)
                return operation(state)
        except VersionError:
            raise
        except DataError as exc:
            if exc.code == 'PUBLISH_IN_PROGRESS':
                raise VersionError(str(exc), exc.code, 409) from exc
            raise VersionError('저장 데이터 검증 또는 복구에 실패했습니다.', 'PUBLISH_FAILED', 500) from exc
        except Exception as exc:
            raise VersionError('저장 작업이 실패했습니다. 다음 조회에서 마지막 커밋을 복구합니다.',
                               'PUBLISH_FAILED', 500) from exc

    def _commit(self, state):
        atomic_bytes(self.metadata, encoded(state), records=False)

    def _require_clean(self, state):
        """Remove existing orphans before allocating another candidate snapshot."""
        self._collect_garbage(state)
        retained = {r['id'] + '.json' for r in state['versions']}
        # scandir propagates enumeration errors; glob may silently hide them.
        with os.scandir(self.directory) as entries:
            orphan = any(p.name.endswith('.json') and VERSION_ID.fullmatch(p.name[:-5])
                         and p.name not in retained for p in entries)
        if orphan:
            raise VersionError('이전 버전 정리가 완료되지 않았습니다.', 'PUBLISH_FAILED', 500)

    def _sync_directory(self):
        if os.name != 'nt':
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _recover_transaction(self):
        if not self.recovery.exists():
            return
        record = json.loads(self.recovery.read_bytes())
        if (not isinstance(record, dict)
                or set(record) != {'format_version', 'phase', 'initialize', 'previous', 'next'}
                or record['format_version'] != 1
                or record['phase'] not in ('prepared', 'committed')
                or type(record['initialize']) is not bool
                or (record['previous'] is None) != record['initialize']):
            raise DataError('복구 기록이 손상되었습니다.')
        self._validate_state(record['next'])
        if record['previous'] is not None:
            self._validate_state(record['previous'])
        state = record['next'] if record['phase'] == 'committed' else record['previous']
        if state is None:
            # Initial registration never writes the original live file.
            self.metadata.unlink(missing_ok=True)
            self._sync_directory()
        else:
            payloads = {r['id']: self._payload(r) for r in state['versions']}
            active = state['active_version_id']
            if active is None:
                self.path.unlink(missing_ok=True)
            else:
                # Equal bytes do not prove durability after an earlier fsync failure.
                atomic_bytes(self.path, payloads[active])
            self._commit(state)
        # Keep the decision replayable until both files have been restored.
        self._finish_transaction()

    def _finish_transaction(self):
        self.recovery.unlink()
        self._sync_directory()

    def _decision(self, record):
        committed = {**record, 'phase': 'committed'}
        try:
            atomic_bytes(self.recovery, encoded(committed), records=False)
        except OSError as exc:
            # Replace may have succeeded before directory fsync failed. A visible
            # final decision cannot subsequently be reported as a failed publish.
            replaced = isinstance(exc, AtomicWriteError) and exc.replaced
            if not replaced and self.recovery.read_bytes() != encoded(committed):
                raise
            return False  # Retain record; retry durability before further work.
        return True

    def _activate(self, previous, state, payload, row=None, *, initialize=False):
        record = dict(format_version=1, phase='prepared', initialize=initialize,
                      previous=previous, next=state)
        try:
            atomic_bytes(self.recovery, encoded(record), records=False)
            if row is not None:
                atomic_bytes(self.directory / (row['id'] + '.json'), payload)
            if not initialize:
                atomic_bytes(self.path, payload)
            self._commit(state)
            finalized = self._decision(record)
        except BaseException:
            self._recover_transaction()
            if previous is not None:
                self._cleanup(previous)
            raise
        if finalized:
            try:
                self._finish_transaction()
            except OSError:
                # Already committed: cleanup/recovery failure cannot return 500.
                return
            self._cleanup(state)

    def read_snapshot(self):
        # Preserve legacy unversioned operation until a version-management call.
        def read(state=None):
            if state is not None and state['active_version_id'] is None:
                raise DataError('정상 활성 records가 없습니다.')
            with self.path.open('rb') as stream:
                return stream.read(), os.fstat(stream.fileno())
        with data_lock(self.path, wait=True):
            self._recover_transaction()
            if self.metadata.exists():
                state = self._load_state()
                self._recover(state)
                return read(state)
            return read()

    def _prepared(self, records):
        fd, name = tempfile.mkstemp(prefix='.prepared-', dir=self.directory)
        os.close(fd)
        path = Path(name)
        try:
            clean = validate_records(records)
            self.rebuild_fn(deepcopy(clean), path)
            payload = path.read_bytes()
            if normal_records(payload) != clean:
                raise DataError('rebuild가 records를 변경했습니다.')
            return payload
        finally:
            path.unlink(missing_ok=True)

    def _version(self, row, active):
        return {**{k: row[k] for k in ('id', 'created_at', 'source_name', 'record_count')},
                'is_active': row['id'] == active}

    def _publish(self, previous, records, source_name):
        self._require_clean(previous)
        payload = self._prepared(records)
        row = self._new_row(payload, source_name, previous)
        state = dict(format_version=1, active_version_id=row['id'],
                     versions=([row] + previous['versions'])[:MAX_VERSIONS])
        self._activate(previous, state, payload, row)
        return self._version(row, row['id'])

    def upload_excel(self, source, *, source_name=None, sheet=None, partial_dates=None):
        try:
            incoming, report = preprocess(source, source_name=source_name, sheet=sheet,
                                          partial_dates=partial_dates)
        except DataError as exc:
            details = []
            for issue in getattr(exc, 'issues', []):
                details.append({**{k: v for k, v in issue.items() if k != 'message'},
                                'reason': issue.get('message', str(exc))})
            raise VersionError(str(exc), 'INVALID_EXCEL', 422, details) from exc
        def operation(state):
            existing = normal_records(self.path.read_bytes()) if self.path.exists() else []
            try:
                merged = merge_records(existing, incoming)
            except DataError as exc:
                raise VersionError(str(exc), 'INVALID_EXCEL', 422) from exc
            warnings = [dict(code=w['code'], message=w.get('message',
                            '원본 전체 합계와 시간대 합계가 다릅니다. 원본값을 보존했습니다.'),
                            **({'row': w['row']} if 'row' in w else {})) for w in report['warnings']]
            # Build all fallible response fields before commit.
            result = dict(status='published', validation=dict(warning_count=len(warnings), warnings=warnings))
            result['version'] = self._publish(state, merged, report['source_file'])
            return result
        return self._run(operation)

    def list_versions(self):
        def operation(state):
            active = state['active_version_id']
            if active is None:
                raise VersionError('정상 활성 records가 없습니다. 먼저 정상 Excel을 게시하세요.',
                                   'PUBLISH_FAILED', 500)
            return dict(versions=[self._version(r, active) for r in state['versions']],
                        active_version_id=active, max_versions=MAX_VERSIONS)
        return self._run(operation)

    def rollback(self, version_id):
        def operation(previous):
            row = next((r for r in previous['versions'] if r['id'] == version_id), None)
            if row is None:
                raise VersionError('보관 중인 데이터 버전을 찾을 수 없습니다.', 'VERSION_NOT_FOUND', 404)
            payload = self._payload(row)
            self._prepared(normal_records(payload))
            state = {**previous, 'active_version_id': version_id}
            self._activate(previous, state, payload)
            return dict(status='rolled_back', active_version_id=version_id)
        return self._run(operation)
