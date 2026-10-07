"""Supabase REST boundary. Service credentials never reach the browser."""
import os
from urllib.parse import quote
import httpx
from library_etl.versions import VersionError


class Supabase:
    def __init__(self, client=None):
        self.url = os.environ.get('SUPABASE_URL', '').rstrip('/')
        self.key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
        self.anon = os.environ.get('SUPABASE_ANON_KEY', '')
        self.bucket = os.environ.get('SUPABASE_STORAGE_BUCKET', 'library-data')
        self.upload_bucket = os.environ.get('SUPABASE_UPLOAD_BUCKET', 'library-uploads')
        self.client = client or httpx.Client(timeout=45)

    def request(self, method, path, *, auth=None, **kwargs):
        if not self.url or not self.key or not self.anon:
            raise VersionError('클라우드 서버 설정이 필요합니다.', 'PUBLISH_FAILED', 503)
        headers = {'apikey': self.key if auth is None else self.anon,
                   'Authorization': 'Bearer ' + (self.key if auth is None else auth),
                   **kwargs.pop('headers', {})}
        if auth == '':
            headers.pop('Authorization', None)
        try:
            response = self.client.request(method, self.url + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise VersionError('저장 서버에 연결하지 못했습니다. 다시 시도하세요.', 'PUBLISH_FAILED', 503) from exc
        if response.is_error:
            if 'PUBLISH_IN_PROGRESS' in response.text:
                raise VersionError('다른 게시 또는 정리 작업 중입니다.', 'PUBLISH_IN_PROGRESS', 409)
            if 'VERSION_NOT_FOUND' in response.text:
                raise VersionError('보관 중인 버전을 찾을 수 없습니다.', 'VERSION_NOT_FOUND', 404)
            if path.startswith('/auth/') and response.status_code in (400,401,403):
                raise VersionError('관리자 인증을 확인하세요.', 'UNAUTHORIZED', 401)
            raise VersionError('저장 서버 작업에 실패했습니다.', 'PUBLISH_FAILED', 503)
        return response

    def rpc(self, name, body=None):
        r = self.request('POST', '/rest/v1/rpc/' + name, json=body or {})
        return r.json() if r.content else None

    def rows(self, table, query=''):
        return self.request('GET', '/rest/v1/' + table + '?' + query).json()

    def insert(self, table, body):
        self.request('POST', '/rest/v1/' + table, json=body)

    def delete(self, table, query):
        self.request('DELETE', '/rest/v1/' + table + '?' + query)

    def object_url(self, path):
        bucket = self.upload_bucket if path.startswith('uploads/') else self.bucket
        return '/storage/v1/object/' + quote(bucket, safe='') + '/' + quote(path, safe='/')

    def download(self, path, limit=45_000_000):
        # Bound decompressed response memory as well as declared upload size.
        if not self.url or not self.key:
            raise VersionError('클라우드 서버 설정이 필요합니다.', 'PUBLISH_FAILED', 503)
        try:
            with self.client.stream('GET', self.url + self.object_url(path),
                                    headers={'apikey': self.key, 'Authorization': 'Bearer '+self.key}) as response:
                if response.is_error:
                    raise VersionError('데이터 파일을 읽지 못했습니다.', 'PUBLISH_FAILED', 503)
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise VersionError('파일 크기 제한을 초과했습니다.', 'FILE_TOO_LARGE', 413)
                return bytes(data)
        except httpx.HTTPError as exc:
            raise VersionError('데이터 파일을 읽지 못했습니다.', 'PUBLISH_FAILED', 503) from exc

    def put(self, path, content):
        self.request('POST', self.object_url(path), content=content,
                     headers={'Content-Type':'application/json', 'x-upsert':'false'})

    def remove(self, path):
        # Bulk deletion is idempotent for objects already removed after a crash.
        bucket = self.upload_bucket if path.startswith('uploads/') else self.bucket
        self.request('DELETE', '/storage/v1/object/' + quote(bucket, safe=''), json={'prefixes':[path]})
