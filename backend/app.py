import os
import hashlib
import json
import hmac
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .admin_sessions import SessionStore
from starlette.exceptions import HTTPException
from starlette.concurrency import run_in_threadpool

from .domain import LEVELS, DataError
from .admin_upload import (MAX_UPLOAD_BYTES, RecordsPublisher, UploadAPIError,
                           read_json_body, require_token)
from .service import KST, LibraryService
from .library_hours import DEFAULT_HOURS, date_window, now_kst, validate_service_date
from library_etl.versions import VersionStore, VersionError

ROOT = Path(__file__).resolve().parents[1]


class FileProvider:
    """Read-on-change snapshots; failed replacements never become active."""
    def __init__(self, path, sample=False):
        self.path, self.sample = Path(path), sample
        self.signature, self.service = None, None
        self.lock = Lock()

    def get(self):
        with self.lock:
            try:
                # Read bytes and metadata from one handle so a replacement cannot
                # pair the old signature with a new file. Content detects equal
                # size/mtime updates as well as rapid consecutive replacements.
                try:
                    payload, stat = VersionStore(self.path).read_snapshot()
                except DataError as exc:
                    raise DataError('버전 데이터 검증 또는 복구에 실패했습니다.', 'PUBLISH_FAILED') from exc
                signature = (stat.st_mtime_ns, hashlib.sha256(payload).digest())
                if signature != self.signature:
                    service = LibraryService(json.loads(payload.decode('utf-8-sig')),
                                             datetime.fromtimestamp(stat.st_mtime, KST).isoformat(), sample=self.sample)
                    self.service, self.signature = service, signature
                return self.service
            except OSError as exc:
                raise DataError('데이터 파일을 찾을 수 없습니다.', 'DATA_NOT_FOUND') from exc
            except (ValueError, UnicodeError) as exc:
                if isinstance(exc, DataError):
                    raise
                raise DataError('records 파일을 읽을 수 없습니다.') from exc


def create_app(provider=None, clock=now_kst, upload_token=None,
               max_upload_bytes=MAX_UPLOAD_BYTES, admin_backend=None):
    path = os.environ.get('LIBRARY_RECORDS')
    provider = provider or FileProvider(path or ROOT / 'data/sample/records.json', sample=not bool(path))
    publisher = RecordsPublisher(provider.path) if hasattr(provider, 'path') else None
    versions = VersionStore(provider.path) if hasattr(provider, 'path') else None
    session_secret = os.environ.get('ADMIN_SESSION_SECRET', '').encode()
    sessions = SessionStore(provider.path) if hasattr(provider, 'path') else None
    session_cookie = 'library_admin_session'
    upload_limit = min(max_upload_bytes, 10 * 1024 * 1024)
    app = FastAPI(title='Library congestion v1')
    if admin_backend is not None:
        admin_backend.install(app)

    def error(code, message, status):
        return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message, 'details': []}})

    def authenticated(request):
        value = request.cookies.get(session_cookie, '')
        try:
            raw, signature = value.rsplit('.', 1)
            expected = hmac.new(session_secret, raw.encode(), hashlib.sha256).hexdigest()
            return bool(session_secret and sessions and hmac.compare_digest(signature, expected) and sessions.valid(raw))
        except (ValueError, TypeError):
            return False

    def require_session(request):
        if not authenticated(request):
            raise UploadAPIError('관리자 로그인이 필요합니다.', 'UNAUTHORIZED', 401)

    def session_id():
        raw = secrets.token_urlsafe(32)
        signature = hmac.new(session_secret, raw.encode(), hashlib.sha256).hexdigest()
        sessions.add(raw)
        return raw + '.' + signature

    @app.exception_handler(UploadAPIError)
    async def upload_error(request, exc):
        return error(exc.code, str(exc), exc.status_code)

    @app.exception_handler(VersionError)
    async def version_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.as_dict())

    @app.exception_handler(DataError)
    async def data_error(request, exc):
        status = (500 if exc.code == 'PUBLISH_FAILED' else 409 if exc.code == 'PUBLISH_IN_PROGRESS'
                  else 404 if exc.code == 'DATA_NOT_FOUND' else 400 if exc.code == 'INVALID_DATE' else 422)
        return JSONResponse(status_code=status, content={'error': {'code': exc.code, 'message': str(exc), 'details': []}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=400, content={'error': {'code': 'INVALID_REQUEST', 'message': '요청 인자를 확인하세요.', 'details': []}})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error('DATA_NOT_FOUND', str(exc.detail), exc.status_code)

    @app.post('/api/v1/admin/session')
    async def login(request: Request):
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return error('INVALID_REQUEST', '요청 내용을 확인하세요.', 400)
        try:
            body = await request.json()
        except Exception:
            return error('INVALID_REQUEST', '요청 내용을 확인하세요.', 400)
        username = os.environ.get('ADMIN_USERNAME', '')
        password = os.environ.get('ADMIN_PASSWORD', '')
        if (not isinstance(body, dict) or set(body) != {'username', 'password'}
                or not isinstance(body.get('username'), str) or not isinstance(body.get('password'), str)):
            return error('INVALID_REQUEST', '요청 내용을 확인하세요.', 400)
        if not session_secret or sessions is None or not username or not password or not hmac.compare_digest(body['username'].encode(), username.encode()) or not hmac.compare_digest(body['password'].encode(), password.encode()):
            return error('INVALID_CREDENTIALS', '아이디 또는 비밀번호가 올바르지 않습니다.', 401)
        signed = session_id()
        response = JSONResponse({'authenticated': True, 'user': {'role': 'admin'}})
        response.set_cookie(session_cookie, signed, httponly=True, secure=True, samesite='lax', path='/api/v1/admin', max_age=SessionStore.TTL)
        return response

    @app.get('/api/v1/admin/session')
    def get_session(request: Request):
        return {'authenticated': True, 'user': {'role': 'admin'}} if authenticated(request) else {'authenticated': False}

    @app.delete('/api/v1/admin/session', status_code=204)
    def logout(request: Request):
        require_session(request)
        raw = request.cookies[session_cookie].rsplit('.', 1)[0]
        sessions.discard(raw)
        response = Response(status_code=204)
        response.delete_cookie(session_cookie, path='/api/v1/admin', secure=True, httponly=True, samesite='lax')
        return response

    @app.post('/api/v1/admin/uploads')
    async def upload_excel(request: Request, file: UploadFile = File(...)):
        require_session(request)
        if publisher is None:
            return error('PROCESSING_ERROR', '이 실행 환경에서는 업로드 기능을 사용할 수 없습니다.', 500)
        if not file.filename or not file.filename.lower().endswith('.xlsx') or file.content_type not in (None, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/octet-stream'):
            return error('INVALID_REQUEST', '.xlsx 파일만 업로드할 수 있습니다.', 400)
        try:
            content = await file.read(upload_limit + 1)
            if len(content) > upload_limit:
                return error('FILE_TOO_LARGE', 'Excel 파일은 10 MB 이하여야 합니다.', 413)
            with tempfile.TemporaryDirectory() as directory:
                staged = Path(directory) / 'upload.xlsx'
                await run_in_threadpool(staged.write_bytes, content)
                result = await run_in_threadpool(
                    versions.upload_excel, staged, source_name=file.filename)
        except VersionError:
            raise
        except Exception:
            return error('PUBLISH_FAILED', '파일 처리에 실패하여 기존 정상본을 유지했습니다.', 500)
        return result

    @app.get('/api/v1/admin/versions')
    def list_versions(request: Request):
        require_session(request)
        if versions is None:
            raise UploadAPIError('버전 저장소를 사용할 수 없습니다.', 'PUBLISH_FAILED', 500)
        return versions.list_versions()

    @app.post('/api/v1/admin/versions/{version_id}/rollback')
    def rollback(request: Request, version_id: str):
        require_session(request)
        if versions is None:
            raise UploadAPIError('버전 저장소를 사용할 수 없습니다.', 'PUBLISH_FAILED', 500)
        return versions.rollback(version_id)

    @app.post('/api/v1/admin/records')
    async def upload_records(request: Request):
        try:
            if publisher is None:
                raise UploadAPIError('이 실행 환경에서는 업로드 기능을 사용할 수 없습니다.',
                                     'UPLOAD_DISABLED', 503)
            expected = upload_token if upload_token is not None else os.environ.get('ADMIN_UPLOAD_TOKEN')
            require_token(request.headers.get('authorization'), expected)
            if versions is not None and (versions.metadata.exists() or versions.recovery.exists()):
                raise UploadAPIError('버전 관리가 초기화된 데이터는 JSON 업로드 경로로 변경할 수 없습니다.',
                                     'PUBLISH_IN_PROGRESS', 409)
            records = await read_json_body(request, max_upload_bytes)
            result = publisher.publish(records)
        except UploadAPIError as exc:
            if publisher is not None:
                publisher.log('rejected', exc.code)
            raise
        except DataError as exc:
            code = 'GATE_ROW_DUPLICATED' if '중복 date+gate+hour' in str(exc) else 'PROCESSING_ERROR'
            publisher.log('rejected', code)
            raise UploadAPIError('records 스키마 검증에 실패했습니다.', code, 422) from exc
        except OSError as exc:
            publisher.log('failed', 'PUBLISH_FAILED')
            raise UploadAPIError('데이터 교체에 실패하여 기존 정상본을 유지했습니다.',
                                 'PUBLISH_FAILED', 500) from exc
        publisher.log('success', 'OK', result['record_count'], result['changed'])
        return dict(accepted=True, record_count=result['record_count'],
                    changed=result['changed'], previous_backup=result['backup'],
                    uploaded_at=datetime.now(KST).isoformat())

    @app.get('/api/v1/admin/records')
    def download_records(request: Request):
        try:
            if publisher is None:
                raise UploadAPIError('이 실행 환경에서는 다운로드 기능을 사용할 수 없습니다.',
                                     'UPLOAD_DISABLED', 503)
            expected = upload_token if upload_token is not None else os.environ.get('ADMIN_UPLOAD_TOKEN')
            require_token(request.headers.get('authorization'), expected)
            records = provider.get().records
        except UploadAPIError as exc:
            if publisher is not None:
                publisher.log('rejected', exc.code)
            raise
        publisher.log('success', 'READ_OK', len(records), False)
        return records

    @app.get('/api/v1/meta')
    def meta():
        now = clock().astimezone(KST)
        return dict(library_name='용산꿈나무도서관', available_hours=DEFAULT_HOURS.hours(now.date()), levels=LEVELS,
                    date_window=date_window(now),
                    hours_note='평일 09:00~21:00 · 주말 09:00~17:00 · 매주 월요일 및 등록된 휴관일 제외. 시간 라벨은 현장 확인 전 임시 기준입니다.')

    @app.get('/api/v1/congestion/today')
    def today(date: str | None = None):
        now = clock().astimezone(KST)
        target = validate_service_date(date, now) if date is not None else now.date()
        return provider.get().today(now=now, target=target)

    @app.get('/api/v1/stats')
    def stats(date: str):
        now = clock().astimezone(KST)
        validate_service_date(date, now)
        return provider.get().stats(date, now=now)

    @app.get('/api/v1/patterns')
    def patterns():
        return provider.get().patterns()

    @app.get('/api/v1/health')
    def health():
        # Hosting should only route traffic after the configured snapshot is
        # readable and valid, not merely after the Python process has started.
        provider.get()
        return {'status': 'ok'}

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend/index.html')

    @app.get('/admin')
    def admin_index():
        return RedirectResponse('/static/admin.html')

    app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')
    return app


app = create_app()
