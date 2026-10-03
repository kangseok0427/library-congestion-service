"""ADE-45: recoverable four-snapshot store; HTTP/session handling belongs to ADE-46."""
import json
import os
import sqlite3
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


class VersionError(DataError):
    def __init__(self, message, code, status_code, details=None):
        super().__init__(message, code)
        self.status_code = status_code
        self.details = details or []

    def as_dict(self):
        return {'error': {'code': self.code, 'message': str(self), 'details': self.details}}


def atomic_bytes(path, payload):
    """Write, fsync and validate on the destination filesystem before replace."""
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.staged-', dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        validate_records(json.loads(staged.read_bytes()))
        os.replace(staged, path)
        if os.name != 'nt':
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        staged.unlink(missing_ok=True)


class VersionStore:
    """SQLite is the commit/recovery authority, JSON files are materialized copies.

    The OS lock spans staging, JSON changes and the SQLite commit, including reads.
    A killed worker releases its lock. The next managed read restores the last
    committed payloads before returning data, undoing any incomplete file changes.
    """
    def __init__(self, path, *, rebuild_fn=rebuild):
        self.path = Path(path).resolve()
        self.directory = storage_directory(self.path)
        self.database = self.directory / 'state.sqlite3'
        self.rebuild_fn = rebuild_fn

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            self._initialize(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    def _initialize(self, connection):
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, '
                           'created_at TEXT NOT NULL, source_name TEXT NOT NULL, '
                           'record_count INTEGER NOT NULL, payload BLOB NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS state (singleton INTEGER PRIMARY KEY '
                           'CHECK(singleton=1), active_id TEXT, baseline BLOB)')
        if not connection.execute('SELECT 1 FROM state').fetchone():
            baseline = self.path.read_bytes() if self.path.exists() else None
            if baseline is not None:
                validate_records(json.loads(baseline))
            connection.execute('INSERT INTO state VALUES (1, NULL, ?)', (baseline,))
        connection.commit()

    def _rows(self, connection):
        return connection.execute('SELECT * FROM versions ORDER BY id DESC').fetchall()

    def _restore(self, path, payload):
        validate_records(json.loads(payload))
        if not path.exists() or path.read_bytes() != payload:
            atomic_bytes(path, payload)

    def _recover(self, connection):
        state = connection.execute('SELECT * FROM state').fetchone()
        rows = self._rows(connection)
        by_id = {row['id']: row for row in rows}
        if state['active_id'] is not None and state['active_id'] not in by_id:
            raise DataError('활성 버전 메타데이터가 손상되었습니다.')
        for row in rows:
            self._restore(self.directory / (row['id'] + '.json'), row['payload'])
        payload = by_id[state['active_id']]['payload'] if state['active_id'] else state['baseline']
        if payload is None:
            self.path.unlink(missing_ok=True)
        else:
            self._restore(self.path, payload)
        for path in self.directory.glob('*.json'):
            if path.stem not in by_id:
                path.unlink()
        for path in self.directory.glob('.*.staged-*'):
            path.unlink()
        for path in self.path.parent.glob(f'.{self.path.name}.staged-*'):
            path.unlink()
        # A worker may have died while staging the live copy or running rebuild.
        for path in self.directory.glob('.prepared-*'):
            path.unlink()
        for path in self.directory.glob('*.tmp'):
            path.unlink()

    def _run(self, operation, *, wait=False):
        try:
            with data_lock(self.path, wait=wait):
                connection = self._connect()
                try:
                    self._recover(connection)
                    connection.execute('BEGIN IMMEDIATE')
                    try:
                        result = operation(connection)
                        self._commit(connection)
                    except BaseException:
                        connection.rollback()
                        self._recover(connection)
                        raise
                    return result
                finally:
                    connection.close()
        except VersionError:
            raise
        except DataError as exc:
            if exc.code == 'PUBLISH_IN_PROGRESS':
                raise VersionError(str(exc), exc.code, 409) from exc
            raise VersionError('저장 데이터 검증 또는 복구에 실패했습니다.', 'PUBLISH_FAILED', 500) from exc
        except Exception as exc:
            raise VersionError('저장 작업이 실패했습니다. 다음 조회에서 마지막 커밋을 복구합니다.',
                               'PUBLISH_FAILED', 500) from exc

    def _commit(self, connection):
        connection.commit()

    def read_snapshot(self):
        # Do not adopt an existing unmanaged file merely by reading it.
        with data_lock(self.path, wait=True):
            if self.database.exists():
                connection = self._connect()
                try:
                    self._recover(connection)
                finally:
                    connection.close()
            with self.path.open('rb') as stream:
                return stream.read(), os.fstat(stream.fileno())

    def _prepared(self, records):
        fd, name = tempfile.mkstemp(prefix='.prepared-', dir=self.directory)
        os.close(fd)
        path = Path(name)
        try:
            clean = validate_records(records)
            self.rebuild_fn(deepcopy(clean), path)
            payload = path.read_bytes()
            if validate_records(json.loads(payload)) != clean:
                raise DataError('rebuild가 records를 변경했습니다.')
            return payload
        finally:
            path.unlink(missing_ok=True)

    def _publish(self, connection, records, source_name):
        payload = self._prepared(records)
        baseline = connection.execute('SELECT baseline FROM state').fetchone()[0]
        if baseline is not None and not self._rows(connection):
            # Preserve the pre-migration normal snapshot as a rollback target.
            previous = validate_records(json.loads(baseline))
            self._prepared(previous)  # Recompute before classifying it as normal.
            stamp = datetime.fromtimestamp(self.path.stat().st_mtime, KST)
            baseline_id = stamp.strftime('%Y%m%dT%H%M%S%z')
            names = {r['source_file'] for r in previous}
            old_name = next(iter(names)) if len(names) == 1 else self.path.name
            connection.execute('INSERT INTO versions VALUES (?, ?, ?, ?, ?)',
                               (baseline_id, stamp.isoformat(), old_name, len(previous), baseline))
            atomic_bytes(self.directory / (baseline_id + '.json'), baseline)
        now = datetime.now(KST)
        latest = connection.execute('SELECT MAX(id) FROM versions').fetchone()[0]
        # v2 IDs have second precision; monotonic IDs avoid same-second collisions.
        if latest:
            now = max(now, datetime.strptime(latest, '%Y%m%dT%H%M%S%z') + timedelta(seconds=1))
        version_id = now.strftime('%Y%m%dT%H%M%S%z')
        created_at = datetime.now(KST).isoformat()
        connection.execute('INSERT INTO versions VALUES (?, ?, ?, ?, ?)',
                           (version_id, created_at, source_name, len(records), payload))
        atomic_bytes(self.directory / (version_id + '.json'), payload)
        atomic_bytes(self.path, payload)
        connection.execute('UPDATE state SET active_id=?, baseline=NULL', (version_id,))
        for row in self._rows(connection)[MAX_VERSIONS:]:
            (self.directory / (row['id'] + '.json')).unlink()
            connection.execute('DELETE FROM versions WHERE id=?', (row['id'],))
        return dict(id=version_id, created_at=created_at, source_name=source_name,
                    record_count=len(records), is_active=True)

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
        def operation(connection):
            existing = validate_records(json.loads(self.path.read_bytes())) if self.path.exists() else []
            try:
                merged = merge_records(existing, incoming)
            except DataError as exc:
                raise VersionError(str(exc), 'INVALID_EXCEL', 422) from exc
            version = self._publish(connection, merged, report['source_file'])
            warnings = [dict(code=w['code'], message=w.get('message',
                            '원본 전체 합계와 시간대 합계가 다릅니다. 원본값을 보존했습니다.'),
                            **({'row': w['row']} if 'row' in w else {})) for w in report['warnings']]
            return dict(status='published', version=version,
                        validation=dict(warning_count=len(warnings), warnings=warnings))
        return self._run(operation)

    def list_versions(self):
        def operation(connection):
            active = connection.execute('SELECT active_id FROM state').fetchone()[0]
            versions = [dict(id=r['id'], created_at=r['created_at'], source_name=r['source_name'],
                             record_count=r['record_count'], is_active=r['id'] == active)
                        for r in self._rows(connection)]
            return dict(versions=versions, active_version_id=active, max_versions=MAX_VERSIONS)
        return self._run(operation)

    def rollback(self, version_id):
        def operation(connection):
            row = connection.execute('SELECT * FROM versions WHERE id=?', (version_id,)).fetchone()
            if row is None:
                raise VersionError('보관 중인 데이터 버전을 찾을 수 없습니다.', 'VERSION_NOT_FOUND', 404)
            payload = self._prepared(validate_records(json.loads(row['payload'])))
            atomic_bytes(self.path, payload)
            connection.execute('UPDATE state SET active_id=?', (version_id,))
            return dict(status='rolled_back', active_version_id=version_id)
        return self._run(operation)
