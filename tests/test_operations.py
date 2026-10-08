import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app import FileProvider, create_app, ROOT
from backend.cloud_admin import CloudAdmin
from backend.library_hours import KST
from backend.operations import FileOperations, NeonOperations, VISITOR_COOKIE
from library_etl.versions import VersionError
from tests.test_cloud import MemoryStorage, login


@pytest.fixture
def operations(tmp_path):
    path = tmp_path / 'records.json'
    path.write_bytes((ROOT / 'data/sample/records.json').read_bytes())
    store = FileOperations(path)
    now = [datetime(2026, 9, 10, 23, 59, tzinfo=KST)]
    storage = MemoryStorage()
    app = create_app(FileProvider(path), clock=lambda: now[0], operations=store,
                     admin_backend=CloudAdmin(storage, None))
    return store, now, TestClient(app, base_url='https://testserver'), app


def test_visitor_and_page_views_are_separate_and_kst_rolls_over(operations):
    store, now, client, app = operations
    assert client.get('/api/v1/admin/traffic').status_code == 401
    for _ in range(3):
        response = client.post('/api/v1/visit')
        assert response.status_code == 204
        assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
    cookie = client.cookies.get(VISITOR_COOKIE)
    UUID(cookie)
    login(client)
    assert client.get('/api/v1/admin/traffic').json() == dict(date='2026-09-10', today_visitors=1,
          total_visitors=1, today_page_views=3, total_page_views=3)
    now[0] += timedelta(minutes=2)
    client.post('/api/v1/visit')
    other = TestClient(app, base_url='https://testserver')
    other.post('/api/v1/visit')
    assert client.get('/api/v1/admin/traffic').json() == dict(date='2026-09-11', today_visitors=2,
          total_visitors=2, today_page_views=2, total_page_views=5)
    assert cookie not in store.path.read_text()  # only one-way hashes are retained
    assert FileOperations(store.path.parent / 'records.json').traffic('2026-09-11')['total_visitors'] == 2


def test_bots_and_cross_origin_requests_do_not_count(operations):
    store, now, client, app = operations
    assert client.post('/api/v1/visit', headers={'user-agent': 'Googlebot'}).status_code == 204
    assert client.post('/api/v1/visit', headers={'origin': 'https://other.example'}).status_code == 403
    assert store.traffic('2026-09-10')['total_page_views'] == 0


def test_counter_failure_does_not_break_visitor_api(operations, monkeypatch):
    store, now, client, app = operations
    def fail(*args):
        raise VersionError('offline', 'PUBLISH_FAILED', 503)
    monkeypatch.setattr(store, 'visit', fail)
    assert client.post('/api/v1/visit').status_code == 503
    assert client.get('/api/v1/congestion/today?date=2026-09-10').status_code == 200


def test_closures_require_auth_and_apply_without_restart_or_excel_edit(operations):
    store, now, client, app = operations
    path = store.path.parent / 'records.json'
    original = path.read_bytes()
    day = '2026-09-11'
    assert client.put('/api/v1/admin/closures/' + day, json={'reason': '점검'}).status_code == 401
    assert client.get('/api/v1/admin/closures').status_code == 401
    login(client)
    assert client.put('/api/v1/admin/closures/' + day, json={'reason': '점검'},
                      headers={'origin': 'https://elsewhere.example'}).status_code == 403
    before = client.get('/api/v1/congestion/today?date=' + day).json()
    assert before['data_status'] != 'closed'
    assert client.put('/api/v1/admin/closures/' + day, json={'reason': '시설 점검'}).status_code == 200
    rows = client.get('/api/v1/admin/closures').json()['closed_dates']
    assert {'date': day, 'reason': '시설 점검'} in rows
    assert day in client.get('/api/v1/meta').json()['date_availability']['available_dates']
    closed = client.get('/api/v1/congestion/today?date=' + day).json()
    assert closed['data_status'] == 'closed' and closed['hourly'] == []
    assert closed['recommendation']['best_start_hour'] is None
    assert client.put('/api/v1/admin/closures/' + day, json={'reason': '공휴일'}).status_code == 200
    assert len([r for r in store.closures() if r['date'] == day]) == 1
    assert client.delete('/api/v1/admin/closures/' + day).status_code == 204
    assert client.get('/api/v1/congestion/today?date=' + day).json() == before
    assert path.read_bytes() == original
    # Unregistering a Monday cannot undo the existing weekly closure.
    client.delete('/api/v1/admin/closures/2026-09-14')
    assert client.get('/api/v1/congestion/today?date=2026-09-14').json()['data_status'] == 'closed'


@pytest.mark.parametrize('day,body', [('2026-02-30', {'reason': '점검'}), ('nonsense', {'reason': '점검'}),
    ('2026-09-11', {'reason': 'x' * 81}), ('2026-09-11', {'reason': []}), ('2026-09-11', {})])
def test_bad_closure_requests_are_rejected(operations, day, body):
    store, now, client, app = operations
    login(client)
    assert client.put('/api/v1/admin/closures/' + day, json=body).status_code == 400
    assert len(store.closures()) == 3


def test_concurrent_local_counts_are_not_lost(tmp_path):
    store = FileOperations(tmp_path / 'records.json')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.visit('a' * 64, '2026-10-08'), range(40)))
    assert store.traffic('2026-10-08')['today_page_views'] == 40
    assert store.traffic('2026-10-08')['today_visitors'] == 1


def test_neon_operations_uses_shared_durable_rpc():
    class Storage:
        def __init__(self): self.calls = []
        def rpc(self, name, body=None): self.calls.append((name, body))
    storage = Storage()
    store = NeonOperations(storage)
    store.closures(); store.set_closure('2026-10-09', '공휴일'); store.remove_closure('2026-10-09')
    store.visit('a' * 64, '2026-10-08'); store.traffic('2026-10-08')
    assert storage.calls == [('library_closure_list', None),
        ('library_closure_set', {'p_date': '2026-10-09', 'p_reason': '공휴일'}),
        ('library_closure_remove', {'p_date': '2026-10-09'}),
        ('library_visit', {'p_visitor': 'a' * 64, 'p_date': '2026-10-08'}),
        ('library_traffic', {'p_date': '2026-10-08'})]
