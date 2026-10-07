"""Neon Postgres, managed Auth and private S3 boundary; secrets stay server-side."""
import os
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse
from uuid import UUID
import boto3
import httpx
import psycopg
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from library_etl.versions import VersionError

TABLES = {
    'library_state': {'singleton', 'active_version_id', 'lease_id', 'lease_until'},
    'library_versions': {'id', 'object_path', 'created_at', 'source_name', 'record_count', 'status'},
    'library_uploads': {'id', 'user_id', 'object_path', 'created_at', 'source_name', 'byte_size', 'result'},
    'library_sessions': {'token_hash', 'user_id', 'expires_at'},
}
FUNCTIONS = {
    'library_begin': ('p_lease', 'p_version', 'p_path', 'p_source', 'p_upload'),
    'library_commit': ('p_lease', 'p_version', 'p_count', 'p_upload', 'p_result'),
    'library_rollback': ('p_version',), 'library_abort': ('p_lease',),
    'library_gc_candidates': (), 'library_list': (),
}
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
AUTH_COOKIE = '__Secure-neon-auth.session_token'


def is_admin(user):
    # Role is returned by managed Auth, never from submitted profile metadata.
    return 'admin' in str(user.get('role', '')).split(',') and not user.get('banned', False)


class Neon:
    mode = 'neon-direct'

    def __init__(self, s3=None):
        self.database_url = os.environ.get('DATABASE_URL', '')
        self.auth_url = os.environ.get('NEON_AUTH_URL', '').rstrip('/')
        self.origin = os.environ.get('APP_ORIGIN', '').rstrip('/')
        self.url = os.environ.get('NEON_STORAGE_ENDPOINT', '').rstrip('/')
        self.bucket = os.environ.get('NEON_STORAGE_BUCKET', 'library-data')
        self.upload_bucket = os.environ.get('NEON_UPLOAD_BUCKET', 'library-uploads')
        self.s3 = s3
        self.storage_key = os.environ.get('NEON_STORAGE_ACCESS_KEY_ID', '')
        self.storage_secret = os.environ.get('NEON_STORAGE_SECRET_ACCESS_KEY', '')

    def connection(self):
        if not self.database_url:
            raise VersionError('클라우드 서버 설정이 필요합니다.', 'PUBLISH_FAILED', 503)
        # Use Neon's pooler per operation, avoiding stale warm-function sockets.
        return psycopg.connect(self.database_url, connect_timeout=15, row_factory=dict_row,
                               prepare_threshold=None)

    def query(self, statement, params=()):
        try:
            with self.connection() as connection:
                connection.execute("SET LOCAL statement_timeout = '20s'")
                connection.execute("SET LOCAL lock_timeout = '10s'")
                cursor = connection.execute(statement, params)
                if not cursor.description:
                    return []
                return [{k: v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, UUID) else v
                         for k, v in row.items()} for row in cursor.fetchall()]
        except psycopg.Error as exc:
            if 'PUBLISH_IN_PROGRESS' in str(exc):
                raise VersionError('다른 게시 또는 정리 작업 중입니다.', 'PUBLISH_IN_PROGRESS', 409) from exc
            if 'VERSION_NOT_FOUND' in str(exc):
                raise VersionError('보관 중인 버전을 찾을 수 없습니다.', 'VERSION_NOT_FOUND', 404) from exc
            raise VersionError('저장 서버 작업에 실패했습니다.', 'PUBLISH_FAILED', 503) from exc

    def table(self, table):
        if table not in TABLES:
            raise ValueError('Unknown table')
        return sql.Identifier('library', table)

    def predicate(self, table, query):
        terms, params, order = [], [], []
        for field, value in parse_qsl(query):
            if field == 'order':
                for term in value.split(','):
                    column, direction = term.split('.')
                    if column not in TABLES[table] or direction not in ('asc', 'desc'):
                        raise ValueError('Invalid order')
                    order.append(sql.SQL('{} {}').format(sql.Identifier(column), sql.SQL(direction)))
                continue
            operator, value = value.split('.', 1)
            if field not in TABLES[table] or operator not in ('eq', 'gt', 'lt'):
                raise ValueError('Invalid filter')
            terms.append(sql.SQL('{} {} %s').format(sql.Identifier(field), sql.SQL({'eq': '=', 'gt': '>', 'lt': '<'}[operator])))
            params.append(value)
        where = sql.SQL(' WHERE ') + sql.SQL(' AND ').join(terms) if terms else sql.SQL('')
        ordering = sql.SQL(' ORDER BY ') + sql.SQL(',').join(order) if order else sql.SQL('')
        return where, ordering, params

    def rows(self, table, query=''):
        identifier = self.table(table)
        where, order, params = self.predicate(table, query)
        return self.query(sql.SQL('SELECT * FROM {}').format(identifier) + where + order, params)

    def insert(self, table, body):
        identifier = self.table(table)
        if not body or not set(body) <= TABLES[table]:
            raise ValueError('Invalid columns')
        columns = list(body)
        self.query(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(identifier,
                   sql.SQL(',').join(map(sql.Identifier, columns)),
                   sql.SQL(',').join(sql.Placeholder() for _ in columns)),
                   [Jsonb(body[k]) if isinstance(body[k], (dict, list)) else body[k] for k in columns])

    def delete(self, table, query):
        identifier = self.table(table)
        where, order, params = self.predicate(table, query)
        if not params or order.as_string():
            raise ValueError('Deletion needs filters')
        self.query(sql.SQL('DELETE FROM {}').format(identifier) + where, params)

    def rpc(self, name, body=None):
        body = body or {}
        if name not in FUNCTIONS or set(body) != set(FUNCTIONS[name]):
            raise ValueError('Invalid function arguments')
        params = [Jsonb(body[k]) if isinstance(body[k], (dict, list)) else body[k] for k in FUNCTIONS[name]]
        call = sql.SQL('{}({})').format(sql.Identifier('library', name),
                                      sql.SQL(',').join(sql.Placeholder() for _ in params))
        if name == 'library_gc_candidates':
            return self.query(sql.SQL('SELECT * FROM ') + call, params)
        return self.query(sql.SQL('SELECT ') + call + sql.SQL(' AS result'), params)[0]['result']

    def storage(self):
        if self.s3 is None:
            if not self.url or not self.storage_key or not self.storage_secret:
                raise VersionError('클라우드 서버 설정이 필요합니다.', 'PUBLISH_FAILED', 503)
            self.s3 = boto3.client('s3', region_name=os.environ.get('NEON_STORAGE_REGION', 'us-east-2'),
                endpoint_url=self.url, aws_access_key_id=self.storage_key, aws_secret_access_key=self.storage_secret,
                config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                              connect_timeout=15, read_timeout=45, retries={'max_attempts': 2}))
        return self.s3

    def object_bucket(self, path):
        return self.upload_bucket if path.startswith('uploads/') else self.bucket

    def download(self, path, limit=45_000_000):
        body = None
        try:
            response = self.storage().get_object(Bucket=self.object_bucket(path), Key=path)
            body = response['Body']
            if response.get('ContentLength', 0) > limit:
                raise VersionError('파일 크기 제한을 초과했습니다.', 'FILE_TOO_LARGE', 413)
            data = body.read(limit + 1)
            if len(data) > limit:
                raise VersionError('파일 크기 제한을 초과했습니다.', 'FILE_TOO_LARGE', 413)
            return data
        except (BotoCoreError, ClientError) as exc:
            raise VersionError('데이터 파일을 읽지 못했습니다.', 'PUBLISH_FAILED', 503) from exc
        finally:
            if body is not None:
                body.close()

    def put(self, path, content):
        try:
            self.storage().put_object(Bucket=self.object_bucket(path), Key=path, Body=content,
                                      ContentType='application/json', CacheControl='private, no-store')
        except (BotoCoreError, ClientError) as exc:
            raise VersionError('파일 저장에 실패했습니다.', 'PUBLISH_FAILED', 503) from exc

    def remove(self, path):
        try:
            self.storage().delete_object(Bucket=self.object_bucket(path), Key=path)
        except (BotoCoreError, ClientError) as exc:
            raise VersionError('파일 정리에 실패했습니다.', 'PUBLISH_FAILED', 503) from exc

    def sign_upload(self, path, size):
        try:
            url = self.storage().generate_presigned_url('put_object', Params={
                'Bucket': self.upload_bucket, 'Key': path, 'ContentType': XLSX, 'ContentLength': size,
            }, ExpiresIn=300)
            if urlparse(url).netloc != urlparse(self.url).netloc:
                raise VersionError('업로드 주소를 확인하세요.', 'PUBLISH_FAILED', 503)
            return url
        except (BotoCoreError, ClientError) as exc:
            raise VersionError('업로드 주소 발급에 실패했습니다.', 'PUBLISH_FAILED', 503) from exc

    def auth_request(self, method, path, *, token=None, body=None):
        if not self.auth_url or not self.origin:
            raise VersionError('클라우드 인증 설정이 필요합니다.', 'PUBLISH_FAILED', 503)
        headers = {'Origin': self.origin}
        if token:
            headers['Cookie'] = AUTH_COOKIE + '=' + token
        try:
            # Separate clients prevent cookies being shared between concurrent users.
            with httpx.Client(timeout=30) as client:
                response = client.request(method, self.auth_url + path, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VersionError('인증 서버에 연결하지 못했습니다.', 'PUBLISH_FAILED', 503) from exc
        if response.status_code in (400, 401, 403, 422):
            raise VersionError('관리자 인증을 확인하세요.', 'UNAUTHORIZED', 401)
        if response.is_error:
            raise VersionError('인증 서버 작업에 실패했습니다.', 'PUBLISH_FAILED', 503)
        return response

    def login(self, email, password):
        # Optional website credentials map to a server-only managed Auth account.
        # The browser never receives the provider password or chooses that account.
        names = ('ADMIN_LOGIN_ID', 'ADMIN_LOGIN_PASSWORD_HASH', 'NEON_ADMIN_EMAIL', 'NEON_ADMIN_PASSWORD')
        configured = [os.environ.get(name, '') for name in names]
        if any(configured):
            if not all(configured):
                raise VersionError('클라우드 인증 설정이 필요합니다.', 'PUBLISH_FAILED', 503)
            login_id, encoded, provider_email, provider_password = configured
            try:
                algorithm, iterations, salt, expected = encoded.split('$')
                if algorithm != 'pbkdf2_sha256' or iterations != '600000' or len(salt) != 32 or len(expected) != 64:
                    raise ValueError('Invalid password hash')
                expected_bytes = bytes.fromhex(expected)
                actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000)
            except (ValueError, TypeError) as exc:
                raise VersionError('클라우드 인증 설정이 필요합니다.', 'PUBLISH_FAILED', 503) from exc
            valid_password = hmac.compare_digest(actual, expected_bytes)
            valid_id = hmac.compare_digest(email.encode(), login_id.encode())
            if not (valid_password and valid_id):
                raise VersionError('관리자 인증을 확인하세요.', 'UNAUTHORIZED', 401)
            email, password = provider_email, provider_password
        response = self.auth_request('POST', '/sign-in/email', body={'email': email, 'password': password})
        token = response.cookies.get(AUTH_COOKIE, '')
        if not token:
            raise VersionError('관리자 인증을 확인하세요.', 'UNAUTHORIZED', 401)
        result = self.auth_request('GET', '/get-session', token=token).json() or {}
        user = result.get('user', {})
        if not is_admin(user):
            self.logout(token)
            raise VersionError('관리자 인증을 확인하세요.', 'UNAUTHORIZED', 401)
        expiry = datetime.fromisoformat(result['session']['expiresAt'].replace('Z', '+00:00'))
        return {'access_token': token, 'user': user, 'expires_in': max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))}

    def user(self, token):
        result = self.auth_request('GET', '/get-session', token=token).json() or {}
        user = result.get('user', {})
        if not is_admin(user):
            raise VersionError('관리자 권한이 필요합니다.', 'UNAUTHORIZED', 401)
        return user

    def logout(self, token):
        self.auth_request('POST', '/sign-out', token=token, body={})
