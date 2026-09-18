import os
import hashlib
import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .domain import HOURS, LEVELS, DataError, parse_date
from .service import KST, LibraryService

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
                with self.path.open('rb') as stream:
                    stat = os.fstat(stream.fileno())
                    payload = stream.read()
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


def create_app(provider=None):
    path = os.environ.get('LIBRARY_RECORDS')
    provider = provider or FileProvider(path or ROOT / 'data/sample/records.json', sample=not bool(path))
    app = FastAPI(title='Library congestion v1')

    @app.exception_handler(DataError)
    async def data_error(request, exc):
        status = 404 if exc.code == 'DATA_NOT_FOUND' else 400 if exc.code == 'INVALID_DATE' else 422
        return JSONResponse(status_code=status, content={'error': {'code': exc.code, 'message': str(exc)}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=400, content={'error': {'code': 'PROCESSING_ERROR', 'message': '요청 인자를 확인하세요.'}})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={'error': {'code': 'DATA_NOT_FOUND', 'message': str(exc.detail)}})

    @app.get('/api/v1/meta')
    def meta():
        return dict(library_name='용산꿈나무도서관', available_hours=HOURS, levels=LEVELS,
                    hours_note='데이터 수집 시간대입니다. 실제 운영시간은 도서관 공지를 확인하세요.')

    @app.get('/api/v1/congestion/today')
    def today(date: str | None = None):
        return provider.get().today(target=parse_date(date) if date else None)

    @app.get('/api/v1/stats')
    def stats(date: str):
        return provider.get().stats(date)

    @app.get('/api/v1/patterns')
    def patterns():
        return provider.get().patterns()

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend/index.html')

    app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')
    return app


app = create_app()
