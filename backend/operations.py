"""Administrator closures and anonymous website usage, separate from Excel data."""
import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from backend.domain import parse_date
from backend.library_hours import DEFAULT_HOURS, KST
from backend.service import LibraryService
from library_etl.versions import VersionError

VISITOR_COOKIE = 'library_visitor'


class FileOperations:
    """Single-worker local fallback. Production uses atomic Postgres functions."""
    def __init__(self, path):
        self.path = Path(path).with_suffix('.operations.json')
        self.lock = RLock()

    def read(self):
        if self.path.exists():
            return json.loads(self.path.read_text(encoding='utf-8'))
        return {'closures': {d: '등록된 휴관일' for d in DEFAULT_HOURS.closed_dates},
                'visitors': [], 'days': {}}

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + str(uuid4()) + '.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def closures(self):
        with self.lock:
            return [{'date': d, 'reason': reason} for d, reason in sorted(self.read()['closures'].items())]

    def set_closure(self, day, reason):
        with self.lock:
            data = self.read()
            data['closures'][day] = reason
            self.save(data)

    def remove_closure(self, day):
        with self.lock:
            data = self.read()
            data['closures'].pop(day, None)
            self.save(data)

    def visit(self, visitor_hash, day):
        with self.lock:
            data = self.read()
            if visitor_hash not in data['visitors']:
                data['visitors'].append(visitor_hash)
            visitors = data['days'].setdefault(day, {})
            visitors[visitor_hash] = visitors.get(visitor_hash, 0) + 1
            self.save(data)

    def traffic(self, day):
        with self.lock:
            data = self.read()
            today = data['days'].get(day, {})
            return {'date': day, 'today_visitors': len(today), 'total_visitors': len(data['visitors']),
                    'today_page_views': sum(today.values()),
                    'total_page_views': sum(sum(v.values()) for v in data['days'].values())}


class NeonOperations:
    def __init__(self, storage):
        self.storage = storage

    def closures(self):
        return self.storage.rpc('library_closure_list')

    def set_closure(self, day, reason):
        self.storage.rpc('library_closure_set', {'p_date': day, 'p_reason': reason})

    def remove_closure(self, day):
        self.storage.rpc('library_closure_remove', {'p_date': day})

    def visit(self, visitor_hash, day):
        self.storage.rpc('library_visit', {'p_visitor': visitor_hash, 'p_date': day})

    def traffic(self, day):
        return self.storage.rpc('library_traffic', {'p_date': day})


class OperatingProvider:
    """Refresh the service when closures change, even on a warm server instance."""
    def __init__(self, provider, operations):
        self.provider, self.operations = provider, operations
        self.lock = RLock()
        self.source, self.dates, self.service = None, None, None

    def get(self):
        source = self.provider.get()
        dates = tuple(row['date'] for row in self.operations.closures())
        with self.lock:
            if self.source is not source or self.dates != dates:
                self.service = LibraryService(source.records, source.updated_at, source.weeks, source.sample,
                                              replace(source.policy, closed_dates=dates))
                self.source, self.dates = source, dates
            return self.service


def write_origin(request):
    from urllib.parse import urlparse
    origin = request.headers.get('origin')
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != request.headers.get('host', '').lower():
            raise VersionError('요청 출처를 확인하세요.', 'UNAUTHORIZED', 403)
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise VersionError('요청 출처를 확인하세요.', 'UNAUTHORIZED', 403)


def install_operations(app, store, authenticate, clock, secure_cookies=False):
    def valid_day(value):
        try:
            return parse_date(value).isoformat()
        except ValueError as exc:
            raise VersionError('날짜를 확인하세요.', 'INVALID_REQUEST', 400) from exc

    @app.post('/api/v1/visit', status_code=204)
    def visit(request: Request):
        write_origin(request)
        response = Response(status_code=204, headers={'Cache-Control': 'no-store'})
        if re.search(r'bot|crawler|spider', request.headers.get('user-agent', ''), re.I):
            return response
        token = request.cookies.get(VISITOR_COOKIE, '')
        try:
            token = str(UUID(token))
        except (ValueError, TypeError, AttributeError):
            token = str(uuid4())
        visitor_hash = hashlib.sha256(token.encode()).hexdigest()
        try:
            store.visit(visitor_hash, clock().astimezone(KST).date().isoformat())
        except VersionError:
            # Analytics must never prevent a visitor from using the service.
            return Response(status_code=503, headers={'Cache-Control': 'no-store'})
        response.set_cookie(VISITOR_COOKIE, token, max_age=365 * 24 * 3600, httponly=True,
                            secure=secure_cookies or request.url.scheme == 'https', samesite='lax', path='/api/v1/visit')
        return response

    @app.get('/api/v1/admin/traffic')
    def traffic(request: Request):
        authenticate(request)
        return store.traffic(clock().astimezone(KST).date().isoformat())

    @app.get('/api/v1/admin/closures')
    def closures(request: Request):
        authenticate(request)
        return {'closed_dates': store.closures(), 'closed_weekdays': list(DEFAULT_HOURS.closed_weekdays)}

    @app.put('/api/v1/admin/closures/{day}')
    async def set_closure(request: Request, day: str):
        write_origin(request)
        await run_in_threadpool(authenticate, request)
        day = valid_day(day)
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            raise VersionError('JSON 요청이 필요합니다.', 'INVALID_REQUEST', 400)
        content = await request.body()
        try:
            if len(content) > 2048:
                raise ValueError()
            body = json.loads(content)
            if not isinstance(body, dict) or set(body) != {'reason'} or not isinstance(body['reason'], str):
                raise ValueError()
            reason = body['reason'].strip()
            if len(reason) > 80:
                raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise VersionError('휴관 사유는 80자 이내로 입력하세요.', 'INVALID_REQUEST', 400) from exc
        await run_in_threadpool(store.set_closure, day, reason or '휴관일')
        return {'date': day, 'is_closed': True, 'reason': reason or '휴관일'}

    @app.delete('/api/v1/admin/closures/{day}', status_code=204)
    def remove_closure(request: Request, day: str):
        write_origin(request)
        authenticate(request)
        store.remove_closure(valid_day(day))
        return Response(status_code=204, headers={'Cache-Control': 'no-store'})
