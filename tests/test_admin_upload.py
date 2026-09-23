"""ADE-35 authenticated upload contract and live snapshot safety."""
import copy
import json
from datetime import date, datetime
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from backend.app import FileProvider, create_app
from backend.admin_upload import MAX_UPLOAD_BYTES
from backend.domain import validate_records
from backend.library_hours import KST
from scripts.rebuild import rebuild
from scripts.sample import generate
from windows_uploader.upload import PRODUCTION_UPLOAD_ENDPOINT, UploadClient


TOKEN = 'synthetic-admin-token'


def test_production_upload_limit_allows_real_history_snapshots():
    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024


@pytest.fixture
def upload_service(tmp_path):
    path = tmp_path / 'records.json'
    original = generate(end=date(2026, 9, 10), days=2)
    rebuild(original, path)
    app = create_app(FileProvider(path),
                     clock=lambda: datetime(2026, 9, 10, 12, tzinfo=KST),
                     upload_token=TOKEN)
    return path, TestClient(app), original


def post(client, records, token=TOKEN, **kwargs):
    headers = {'Authorization': f'Bearer {token}'} if token is not None else {}
    return client.post('/api/v1/admin/records', json=records, headers=headers, **kwargs)


def test_authenticated_upload_replaces_live_snapshot_and_creates_backup(upload_service):
    path, client, original = upload_service
    replacement = generate(end=date(2026, 9, 10), days=3)
    response = post(client, replacement)
    assert response.status_code == 200
    body = response.json()
    assert body['accepted'] is True and body['changed'] is True
    assert body['record_count'] == len(replacement)
    assert body['previous_backup'].startswith('records-')
    assert json.loads(path.read_text(encoding='utf-8')) == validate_records(replacement)
    backup = path.parent / 'backups' / body['previous_backup']
    assert json.loads(backup.read_text(encoding='utf-8')) == validate_records(original)
    assert client.get('/api/v1/stats?date=2026-09-10').status_code == 200
    log = (path.parent / 'logs/admin-upload.log').read_text(encoding='utf-8')
    assert '"result": "success"' in log and TOKEN not in log


def test_authenticated_download_returns_current_full_snapshot(upload_service):
    path, client, original = upload_service
    response = client.get('/api/v1/admin/records',
                          headers={'Authorization': f'Bearer {TOKEN}'})
    assert response.status_code == 200
    assert response.json() == validate_records(original)
    assert client.get('/api/v1/admin/records').status_code == 401
    assert path.read_bytes()


def test_retry_of_same_snapshot_is_idempotent(upload_service):
    path, client, _ = upload_service
    replacement = generate(end=date(2026, 9, 10), days=3)
    first = post(client, replacement).json()
    backups = list((path.parent / 'backups').glob('records-*.json'))
    second = post(client, replacement).json()
    assert first['changed'] is True and second['changed'] is False
    assert second['previous_backup'] is None
    assert list((path.parent / 'backups').glob('records-*.json')) == backups


@pytest.mark.parametrize('token', [None, '', 'wrong-token'])
def test_missing_or_wrong_token_rejected_without_changes(upload_service, token):
    path, client, original = upload_service
    before = path.read_bytes()
    response = post(client, original, token=token)
    assert response.status_code == 401
    assert response.json()['error']['code'] == 'UNAUTHORIZED'
    assert path.read_bytes() == before
    assert not (path.parent / 'backups').exists()
    assert TOKEN not in (path.parent / 'logs/admin-upload.log').read_text(encoding='utf-8')


def test_unconfigured_server_rejects_all_uploads(tmp_path, monkeypatch):
    monkeypatch.delenv('ADMIN_UPLOAD_TOKEN', raising=False)
    path = tmp_path / 'records.json'
    records = generate(end=date(2026, 9, 10), days=1)
    rebuild(records, path)
    client = TestClient(create_app(FileProvider(path)))
    assert post(client, records).status_code == 401


def test_media_type_invalid_json_and_size_limits_preserve_live(upload_service):
    path, client, _ = upload_service
    before = path.read_bytes()
    auth = {'Authorization': f'Bearer {TOKEN}'}
    wrong_type = client.post('/api/v1/admin/records', content='[]', headers=auth)
    invalid = client.post('/api/v1/admin/records', content='{broken',
                          headers={**auth, 'Content-Type': 'application/json'})
    too_large_app = create_app(FileProvider(path), upload_token=TOKEN, max_upload_bytes=10)
    too_large = TestClient(too_large_app).post('/api/v1/admin/records', json=[{'long': 'value'}],
                                               headers=auth)
    assert wrong_type.status_code == 415
    assert invalid.status_code == 400
    assert too_large.status_code == 413
    assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['empty', 'duplicate', 'unknown-field'])
def test_invalid_records_rejected_and_previous_snapshot_preserved(upload_service, change):
    path, client, original = upload_service
    records = copy.deepcopy(original)
    if change == 'empty':
        records = []
    elif change == 'duplicate':
        records.append(copy.deepcopy(records[0]))
    else:
        records[0]['unexpected'] = 'field'
    before = path.read_bytes()
    response = post(client, records)
    assert response.status_code == 422
    assert response.json()['error']['code'] == (
        'GATE_ROW_DUPLICATED' if change == 'duplicate' else 'PROCESSING_ERROR')
    assert path.read_bytes() == before


def test_final_replace_failure_keeps_live_snapshot(upload_service, monkeypatch):
    import backend.admin_upload as module
    path, client, _ = upload_service
    replacement = generate(end=date(2026, 9, 10), days=3)
    before = path.read_bytes()
    real_replace = module.os.replace

    def fail_live_replace(source, destination):
        if module.Path(destination) == path:
            raise PermissionError('synthetic failure')
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, 'replace', fail_live_replace)
    response = post(client, replacement)
    assert response.status_code == 500
    assert response.json() == {'error': {
        'code': 'PUBLISH_FAILED',
        'message': '데이터 교체에 실패하여 기존 정상본을 유지했습니다.'}}
    assert path.read_bytes() == before


def test_windows_client_contract_matches_server_endpoint():
    assert PRODUCTION_UPLOAD_ENDPOINT == (
        'https://ade0033.pythonanywhere.com/api/v1/admin/records')
    client = UploadClient(PRODUCTION_UPLOAD_ENDPOINT,
                          transport=lambda request, timeout: None)
    assert client.endpoint == PRODUCTION_UPLOAD_ENDPOINT


def test_windows_client_to_fastapi_contract_end_to_end(upload_service):
    path, api, _ = upload_service
    replacement = generate(end=date(2026, 9, 10), days=3)

    class Response:
        def __init__(self, response):
            self.status = response.status_code
            self.body = response.content

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.body

    def transport(request, timeout):
        assert timeout == 20 * 60
        response = api.request(request.get_method(), urlsplit(request.full_url).path,
                               content=request.data, headers=dict(request.header_items()))
        return Response(response)

    uploader = UploadClient(PRODUCTION_UPLOAD_ENDPOINT, transport=transport, retries=0)
    assert uploader.fetch(TOKEN) == validate_records(generate(end=date(2026, 9, 10), days=2))
    assert uploader.send(replacement, TOKEN) == 200
    assert json.loads(path.read_text(encoding='utf-8')) == validate_records(replacement)
