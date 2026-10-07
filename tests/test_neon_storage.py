"""Real driver boundaries: SQL values, managed Auth and bounded private S3 reads."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import parse_qs, urlparse
import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.stub import Stubber
from backend.neon_storage import Neon, AUTH_COOKIE, is_admin, XLSX
from library_etl.versions import VersionError


def test_presigned_upload_binds_size_type_path_and_expiry(monkeypatch):
    monkeypatch.setenv('NEON_STORAGE_ENDPOINT', 'https://storage.neon.tech')
    client = boto3.client('s3', endpoint_url='https://storage.neon.tech', region_name='us-east-2',
        aws_access_key_id='synthetic-key', aws_secret_access_key='synthetic-secret',
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    storage = Neon(s3=client)
    url = urlparse(storage.sign_upload('uploads/test.xlsx', 123))
    assert url.path == '/library-uploads/uploads/test.xlsx'
    params = parse_qs(url.query)
    assert params['X-Amz-Expires'] == ['300']
    assert params['X-Amz-SignedHeaders'] == ['content-length;content-type;host']
    assert 'synthetic-secret' not in url.query
    body = BytesIO(b'x' * 11)
    with Stubber(client) as stub:
        stub.add_response('get_object', {'Body': body, 'ContentLength': 11},
                          {'Bucket': 'library-uploads', 'Key': 'uploads/test.xlsx'})
        with pytest.raises(VersionError) as exc:
            storage.download('uploads/test.xlsx', limit=10)
        assert exc.value.status_code == 413 and body.closed


def test_sql_values_are_parameters_and_identifiers_are_allowlisted():
    storage = Neon()
    calls = []
    storage.query = lambda statement, params=(): calls.append((statement.as_string(), params)) or []
    storage.rows('library_versions', "id=eq.%27%3Bdrop%20table%20library_state--&status=eq.retained")
    assert 'drop' not in calls[0][0] and calls[0][1][0].startswith("';drop")
    assert '"library"."library_versions"' in calls[0][0]
    with pytest.raises(ValueError): storage.rows('neon_auth.user')
    with pytest.raises(ValueError): storage.rows('library_sessions', 'secret=eq.x')
    with pytest.raises(ValueError): storage.delete('library_versions', '')
    storage.delete('library_sessions', 'expires_at=lt.2026-10-07T00%3A00%3A00Z')
    assert calls[-1][0].startswith('DELETE FROM "library"."library_sessions"')
    assert calls[-1][1] == ['2026-10-07T00:00:00Z']
    with pytest.raises(ValueError): storage.delete('library_versions', 'id=eq.x&order=id.desc')
    with pytest.raises(ValueError): storage.rpc('arbitrary_function')


def test_managed_auth_reads_fresh_role_and_never_trusts_metadata():
    storage = Neon()
    expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    calls = []
    user = {'id': 'managed-user-id', 'role': 'admin'}
    def request(method, path, **kw):
        calls.append((path, kw))
        if path == '/sign-in/email':
            return httpx.Response(200, headers={'set-cookie': AUTH_COOKIE + '=signed-token; Path=/; Secure; HttpOnly'},
                                  json={'token': 'unsigned-do-not-use'}, request=httpx.Request('POST','https://auth.neon.tech/sign-in/email'))
        return httpx.Response(200, json={'session': {'expiresAt': expiry}, 'user': user})
    storage.auth_request = request
    result = storage.login('admin@example.com', 'password')
    assert result['access_token'] == 'signed-token'
    assert calls[-1][1]['token'] == 'signed-token'
    user['role'] = 'user'
    with pytest.raises(VersionError): storage.user('signed-token')
    assert not is_admin({'role': 'user', 'app_metadata': {'library_admin': True}})
    assert not is_admin({'role': 'admin', 'banned': True})
    assert is_admin({'role': 'user,admin'})


def test_auth_clients_do_not_share_cookies_and_errors_hide_secrets(monkeypatch):
    monkeypatch.setenv('NEON_AUTH_URL', 'https://auth.neon.tech/auth')
    monkeypatch.setenv('APP_ORIGIN', 'https://app.example.com')
    client_type = httpx.Client
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(401, json={'message': 'private-provider-error'})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw))
    storage = Neon()
    for token in ('one', 'two'):
        with pytest.raises(VersionError) as exc: storage.user(token)
        assert exc.value.code == 'UNAUTHORIZED'
        assert 'private-provider-error' not in str(exc.value)
    assert [r.headers['cookie'] for r in requests] == [AUTH_COOKIE + '=one', AUTH_COOKIE + '=two']
    assert all(r.headers['origin'] == 'https://app.example.com' for r in requests)


def test_website_credentials_are_checked_before_server_only_provider_login(monkeypatch):
    import hashlib
    salt = '01' * 16
    digest = hashlib.pbkdf2_hmac('sha256', b'website-password', bytes.fromhex(salt), 600000).hex()
    values = {'ADMIN_LOGIN_ID': 'admin', 'ADMIN_LOGIN_PASSWORD_HASH': f'pbkdf2_sha256$600000${salt}${digest}',
              'NEON_ADMIN_EMAIL': 'provider@example.com', 'NEON_ADMIN_PASSWORD': 'provider-secret'}
    for key, value in values.items(): monkeypatch.setenv(key, value)
    storage = Neon()
    calls = []
    def request(method, path, **kw):
        calls.append((path, kw))
        if path == '/sign-in/email':
            return httpx.Response(200, headers={'set-cookie': AUTH_COOKIE + '=managed-token; Secure; HttpOnly'},
                                  request=httpx.Request('POST', 'https://auth.example.com'))
        return httpx.Response(200, json={'user': {'id': 'managed-user', 'role': 'admin'},
            'session': {'expiresAt': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}})
    storage.auth_request = request
    for username, password in [('admin', 'wrong'), ('provider@example.com', 'provider-secret'),
                               ('다른아이디', 'website-password')]:
        with pytest.raises(VersionError) as exc: storage.login(username, password)
        assert exc.value.code == 'UNAUTHORIZED'
    assert not calls
    result = storage.login('admin', 'website-password')
    assert calls[0][1]['body'] == {'email': 'provider@example.com', 'password': 'provider-secret'}
    assert result['access_token'] == 'managed-token' and 'provider-secret' not in str(result)
    monkeypatch.delenv('NEON_ADMIN_PASSWORD')
    with pytest.raises(VersionError) as exc: storage.login('admin', 'website-password')
    assert exc.value.status_code == 503
    monkeypatch.setenv('NEON_ADMIN_PASSWORD', 'provider-secret')
    monkeypatch.setenv('ADMIN_LOGIN_PASSWORD_HASH', 'malformed')
    with pytest.raises(VersionError) as exc: storage.login('admin', 'website-password')
    assert exc.value.status_code == 503
