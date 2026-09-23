"""Authenticated records snapshot upload for the existing FastAPI service."""
import hmac
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .domain import DataError, validate_records
from scripts.rebuild import rebuild


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_BACKUPS = 20


class UploadAPIError(RuntimeError):
    def __init__(self, message, code, status_code):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def require_token(authorization, expected):
    if not expected or not authorization or not authorization.startswith('Bearer '):
        raise UploadAPIError('인증 토큰이 없거나 올바르지 않습니다.', 'UNAUTHORIZED', 401)
    supplied = authorization.removeprefix('Bearer ').strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise UploadAPIError('인증 토큰이 없거나 올바르지 않습니다.', 'UNAUTHORIZED', 401)


async def read_json_body(request, limit=MAX_UPLOAD_BYTES):
    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if media_type != 'application/json':
        raise UploadAPIError('Content-Type은 application/json이어야 합니다.',
                             'UNSUPPORTED_MEDIA_TYPE', 415)
    content_length = request.headers.get('content-length')
    try:
        if content_length is not None and int(content_length) > limit:
            raise UploadAPIError('업로드 파일이 5MB 제한을 초과했습니다.',
                                 'PAYLOAD_TOO_LARGE', 413)
    except ValueError:
        raise UploadAPIError('Content-Length가 올바르지 않습니다.', 'INVALID_REQUEST', 400)
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > limit:
            raise UploadAPIError('업로드 파일이 5MB 제한을 초과했습니다.',
                                 'PAYLOAD_TOO_LARGE', 413)
    try:
        return json.loads(bytes(payload).decode('utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        raise UploadAPIError('JSON 형식이 올바르지 않습니다.', 'INVALID_JSON', 400) from exc


class RecordsPublisher:
    """Validate, back up and atomically publish one complete records snapshot."""
    def __init__(self, path, max_backups=MAX_BACKUPS):
        self.path = Path(path)
        self.backups = self.path.parent / 'backups'
        self.logs = self.path.parent / 'logs'
        self.max_backups = max_backups
        self.lock = Lock()

    def publish(self, records):
        if not records:
            raise DataError('빈 records 배열은 업로드할 수 없습니다.')
        clean = validate_records(records)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._same_snapshot(clean):
                return {'changed': False, 'backup': None, 'record_count': len(clean)}
            fd, staged_name = tempfile.mkstemp(dir=self.path.parent,
                                               prefix='.admin-upload-', suffix='.json')
            os.close(fd)
            staged = Path(staged_name)
            backup = None
            try:
                rebuild(clean, staged)
                backup = self._backup_current()
                os.replace(staged, self.path)
                self._prune_backups()
            finally:
                staged.unlink(missing_ok=True)
            return {'changed': True, 'backup': backup, 'record_count': len(clean)}

    def _same_snapshot(self, records):
        if not self.path.exists():
            return False
        try:
            current = json.loads(self.path.read_text(encoding='utf-8-sig'))
            return validate_records(current) == records
        except (OSError, ValueError, UnicodeError):
            return False

    def _backup_current(self):
        if not self.path.exists():
            return None
        self.backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = self.backups / f'records-{stamp}.json'
        temporary = target.with_suffix('.tmp')
        try:
            with self.path.open('rb') as reader, temporary.open('xb') as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target.name

    def _prune_backups(self):
        try:
            if not self.backups.exists():
                return
            backups = sorted(self.backups.glob('records-*.json'))
            for old in backups[:-self.max_backups]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def log(self, result, code, record_count=0, changed=False):
        """Best-effort fixed-field log; never records headers, payloads or exception text."""
        try:
            self.logs.mkdir(parents=True, exist_ok=True)
            path = self.logs / 'admin-upload.log'
            if path.exists() and path.stat().st_size > 1_000_000:
                rotated = path.with_suffix('.log.1')
                rotated.unlink(missing_ok=True)
                os.replace(path, rotated)
            entry = dict(timestamp=datetime.now(timezone.utc).isoformat(), result=result,
                         code=code, record_count=record_count, changed=changed)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass
