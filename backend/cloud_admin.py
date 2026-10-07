"""Same-origin admin API using managed Auth and signed direct uploads."""
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse
from uuid import uuid4, UUID
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from library_etl.versions import VersionError
from backend.neon_storage import is_admin

COOKIE = 'library_admin_session'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class CloudAdmin:
    def __init__(self, storage, versions):
        self.storage, self.versions = storage, versions

    def user(self, request):
        token = request.cookies.get(COOKIE,'')
        if not token:
            raise VersionError('관리자 로그인이 필요합니다.','UNAUTHORIZED',401)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        rows = self.storage.rows('library_sessions','token_hash=eq.'+hashed+
                    '&expires_at=gt.'+quote(datetime.now(timezone.utc).isoformat(),safe=''))
        if not rows:
            raise VersionError('관리자 로그인이 필요합니다.','UNAUTHORIZED',401)
        user = self.storage.user(token)
        if not is_admin(user) or user.get('id')!=rows[0]['user_id']:
            raise VersionError('관리자 권한이 필요합니다.','UNAUTHORIZED',401)
        return user

    def write_origin(self, request):
        origin = request.headers.get('origin')
        if origin:
            parsed=urlparse(origin)
            # Compare browser Origin to Host; reverse proxies may report the
            # internal ASGI scheme as http while the browser uses HTTPS.
            if parsed.scheme not in ('https','http') or parsed.netloc.lower()!=request.headers.get('host','').lower():
                raise VersionError('요청 출처를 확인하세요.','UNAUTHORIZED',403)
        if request.headers.get('sec-fetch-site')=='cross-site':
            raise VersionError('요청 출처를 확인하세요.','UNAUTHORIZED',403)

    async def body(self, request):
        if request.headers.get('content-type','').split(';')[0].strip()!='application/json':
            raise VersionError('JSON 요청이 필요합니다.','INVALID_REQUEST',400)
        content = await request.body()
        if len(content)>8192:
            raise VersionError('요청이 너무 큽니다.','INVALID_REQUEST',400)
        try:
            import json
            body = json.loads(content)
            if not isinstance(body,dict):
                raise ValueError()
            return body
        except (ValueError,UnicodeError) as exc:
            raise VersionError('요청 내용을 확인하세요.','INVALID_REQUEST',400) from exc

    def housekeeping(self):
        now=datetime.now(timezone.utc)
        self.storage.delete('library_sessions','expires_at=lt.'+quote(now.isoformat(),safe=''))
        cutoff=quote((now-timedelta(days=1)).isoformat(),safe='')
        for row in self.storage.rows('library_uploads','created_at=lt.'+cutoff):
            self.storage.remove(row['object_path'])
            self.storage.delete('library_uploads','id=eq.'+row['id'])

    def install(self, app):
        @app.middleware('http')
        async def no_cache(request, call_next):
            response = await call_next(request)
            if request.url.path.startswith('/api/'):
                response.headers['Cache-Control'] = 'no-store'
            return response

        @app.post('/api/v1/admin/session')
        async def login(request: Request):
            self.write_origin(request)
            body = await self.body(request)
            if set(body)!={'username','password'} or any(not isinstance(body[k],str) or not body[k] for k in body):
                raise VersionError('아이디와 비밀번호를 입력하세요.','INVALID_REQUEST',400)
            try:
                result = await run_in_threadpool(self.storage.login,body['username'],body['password'])
            except VersionError as exc:
                if exc.code=='UNAUTHORIZED':
                    raise VersionError('아이디 또는 비밀번호를 확인하세요.','INVALID_CREDENTIALS',401) from exc
                raise
            user=result.get('user',{})
            if not is_admin(user):
                raise VersionError('관리자 계정이 아닙니다.','INVALID_CREDENTIALS',401)
            token=result['access_token']
            ttl=min(int(result['expires_in']),8*3600)
            await run_in_threadpool(self.storage.insert,'library_sessions',{'token_hash':hashlib.sha256(token.encode()).hexdigest(),
                'user_id':user['id'],'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=ttl)).isoformat()})
            response=JSONResponse({'authenticated':True,'user':{'role':'admin'}})
            response.set_cookie(COOKIE,token,max_age=ttl,secure=True,httponly=True,samesite='lax',path='/api/v1/admin')
            return response

        @app.get('/api/v1/admin/session')
        def session(request: Request):
            try:
                self.user(request)
                return {'authenticated':True,'user':{'role':'admin'}}
            except VersionError as exc:
                if exc.status_code!=401:
                    raise
                return {'authenticated':False}

        @app.delete('/api/v1/admin/session')
        def logout(request: Request):
            self.write_origin(request)
            self.user(request)
            token=request.cookies[COOKIE]
            self.storage.delete('library_sessions','token_hash=eq.'+hashlib.sha256(token.encode()).hexdigest())
            # Invalidate our shared session before the upstream session.
            try:
                self.storage.logout(token)
            except VersionError:
                pass
            response=Response(status_code=204)
            response.delete_cookie(COOKIE,path='/api/v1/admin',secure=True,httponly=True,samesite='lax')
            return response

        @app.get('/api/v1/admin/upload-mode')
        def upload_mode(request: Request):
            self.user(request)
            return {'mode':self.storage.mode,'max_upload_bytes':10*1024*1024}

        @app.post('/api/v1/admin/uploads/sign')
        async def sign(request: Request):
            self.write_origin(request)
            user=await run_in_threadpool(self.user,request)
            body=await self.body(request)
            if set(body)!={'filename','size'} or not isinstance(body['filename'],str) or not body['filename'].lower().endswith('.xlsx'):
                raise VersionError('.xlsx 파일을 선택하세요.','INVALID_REQUEST',400)
            if type(body['size']) is not int or body['size']<=0:
                raise VersionError('파일 크기를 확인하세요.','INVALID_REQUEST',400)
            if body['size']>10*1024*1024:
                raise VersionError('Excel 파일은 10 MB 이하여야 합니다.','FILE_TOO_LARGE',413)
            if len(body['filename'])>240:
                raise VersionError('파일 이름이 너무 깁니다.','INVALID_REQUEST',400)
            await run_in_threadpool(self.housekeeping)
            identifier=str(uuid4())
            path='uploads/'+identifier+'.xlsx'
            await run_in_threadpool(self.storage.insert,'library_uploads',{'id':identifier,'user_id':user['id'],
                'object_path':path,'source_name':body['filename'],'byte_size':body['size']})
            url=await run_in_threadpool(self.storage.sign_upload,path,body['size'])
            return {'upload_id':identifier,'upload_url':url}

        @app.post('/api/v1/admin/uploads/publish')
        async def publish(request: Request):
            self.write_origin(request)
            user=await run_in_threadpool(self.user,request)
            body=await self.body(request)
            try:
                if set(body)!={'upload_id'} or str(UUID(body['upload_id']))!=body['upload_id']:
                    raise ValueError()
            except (ValueError,TypeError,AttributeError) as exc:
                raise VersionError('업로드 ID를 확인하세요.','INVALID_REQUEST',400) from exc
            rows=await run_in_threadpool(self.storage.rows,'library_uploads','id=eq.'+body['upload_id']+'&user_id=eq.'+user['id'])
            if not rows:
                raise VersionError('본인 업로드를 찾을 수 없습니다.','INVALID_REQUEST',400)
            # Keep blocking Python processing off the event loop.
            return await run_in_threadpool(self.versions.publish,rows[0])

        @app.post('/api/v1/admin/uploads')
        def legacy_upload(request: Request):
            self.user(request)
            raise VersionError('클라우드 직접 업로드 방식을 사용하세요.','INVALID_REQUEST',400)

        @app.get('/api/v1/admin/versions')
        def versions(request: Request):
            self.user(request)
            return self.versions.list_versions()

        @app.post('/api/v1/admin/versions/{version_id}/rollback')
        def rollback(request: Request,version_id: str):
            self.write_origin(request)
            self.user(request)
            return self.versions.rollback(version_id)
