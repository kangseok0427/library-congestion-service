import json
from datetime import date

from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app import FileProvider, create_app
from library_etl.versions import VersionError, VersionStore
from library_etl.locking import data_lock
from backend.domain import HOURS
from scripts.sample import generate
from scripts.rebuild import rebuild


def make_excel(path):
    book = Workbook()
    sheet = book.active
    sheet.append(['수집일자', '게이트', '통로ID', '전체_IN', '전체_OUT'] +
                 [f'{kind}_{hour:02}' for hour in HOURS for kind in ('IN', 'OUT')])
    for gate in ('자료실.정문', '자료실.후문'):
        sheet.append(['2026-09-10', gate, 'test-gate', 16, 16] + [1] * 32)
    book.save(path)


def setup_client(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_USERNAME', 'admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'secret')
    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'test-session-secret')
    path = tmp_path / 'records.json'
    rebuild(generate(end=date(2026, 9, 9), days=1), path)
    return path, TestClient(create_app(FileProvider(path)), base_url='https://testserver')


def login(client):
    response = client.post('/api/v1/admin/session', json={'username': 'admin', 'password': 'secret'})
    assert response.status_code == 200
    assert response.json() == {'authenticated': True, 'user': {'role': 'admin'}}
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie


def test_session_contract_and_unauthorized_routes(tmp_path, monkeypatch):
    _, client = setup_client(tmp_path, monkeypatch)
    assert client.get('/api/v1/admin/session').json() == {'authenticated': False}
    assert client.get('/api/v1/admin/versions').json()['error']['code'] == 'UNAUTHORIZED'
    bad = client.post('/api/v1/admin/session', json={'username': 'admin', 'password': 'bad'})
    assert bad.status_code == 401 and bad.json()['error']['details'] == []
    login(client)
    assert client.get('/api/v1/admin/session').json()['authenticated'] is True
    assert client.delete('/api/v1/admin/session').status_code == 204


def test_upload_version_listing_rollback_and_contract_errors(tmp_path, monkeypatch):
    path, client = setup_client(tmp_path, monkeypatch)
    login(client)
    before = client.get('/api/v1/admin/versions').json()
    old_id = before['active_version_id']
    source = tmp_path / 'records.xlsx'
    make_excel(source)
    response = client.post('/api/v1/admin/uploads', files={'file': ('records.xlsx', source.read_bytes(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded['status'] == 'published'
    assert set(uploaded['validation']) == {'warning_count', 'warnings'}
    versions = client.get('/api/v1/admin/versions').json()
    assert len(versions['versions']) == 2 and versions['max_versions'] == 4
    assert versions['active_version_id'] == uploaded['version']['id']
    assert client.post(f"/api/v1/admin/versions/{old_id}/rollback").json() == {
        'status': 'rolled_back', 'active_version_id': old_id}
    assert json.loads(path.read_text(encoding='utf-8'))
    missing = client.post('/api/v1/admin/versions/20200101T000000+0900/rollback')
    assert missing.status_code == 404 and missing.json()['error']['code'] == 'VERSION_NOT_FOUND'
    invalid = client.post('/api/v1/admin/uploads', files={'file': ('wrong.csv', b'x', 'text/csv')})
    assert invalid.status_code == 400 and invalid.json()['error']['details'] == []
    invalid_xlsx = client.post('/api/v1/admin/uploads', files={'file': ('bad.xlsx', b'not an excel workbook', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert invalid_xlsx.status_code == 422
    assert isinstance(invalid_xlsx.json()['error']['details'], list)


def test_version_store_returns_contract_conflict_when_busy(tmp_path):
    path = tmp_path / 'records.json'
    store = VersionStore(path)
    with data_lock(path):
        try:
            store.list_versions()
            assert False, 'expected the busy-store conflict'
        except VersionError as exc:
            assert exc.status_code == 409
            assert exc.code == 'PUBLISH_IN_PROGRESS'


def test_upload_rejects_over_10mb(tmp_path, monkeypatch):
    _, client = setup_client(tmp_path, monkeypatch)
    login(client)
    response = client.post('/api/v1/admin/uploads', files={'file': ('large.xlsx', b'x' * (10 * 1024 * 1024 + 1), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert response.status_code == 413
    assert response.json()['error']['code'] == 'FILE_TOO_LARGE'
    assert response.json()['error']['details'] == []


def test_sessions_shared_between_workers_and_revoked(tmp_path, monkeypatch):
    path, first = setup_client(tmp_path, monkeypatch)
    second = TestClient(create_app(FileProvider(path)), base_url='https://testserver')
    login(first)
    second.cookies.update(first.cookies)
    assert second.get('/api/v1/admin/session').json()['authenticated']
    response = second.delete('/api/v1/admin/session')
    assert response.status_code == 204 and response.content == b''
    assert first.get('/api/v1/admin/session').json() == {'authenticated': False}
    assert first.delete('/api/v1/admin/session').status_code == 401
    assert first.get('/').status_code == 200
    assert first.get('/admin').status_code == 200


def test_session_expiry_credentials_and_missing_secret(tmp_path, monkeypatch):
    from backend import admin_sessions
    path, client = setup_client(tmp_path, monkeypatch)
    bad = client.post('/api/v1/admin/session', json={'username': '관리자', 'password': '잘못됨'})
    assert bad.status_code == 401 and bad.json()['error']['code'] == 'INVALID_CREDENTIALS'
    login(client)
    current = admin_sessions.time.time()
    monkeypatch.setattr(admin_sessions.time, 'time', lambda: current + admin_sessions.SessionStore.TTL + 1)
    assert client.get('/api/v1/admin/session').json() == {'authenticated': False}
    monkeypatch.delenv('ADMIN_SESSION_SECRET')
    disabled = TestClient(create_app(FileProvider(path)), base_url='https://testserver')
    assert disabled.post('/api/v1/admin/session', json={'username': 'admin', 'password': 'secret'}).status_code == 401
