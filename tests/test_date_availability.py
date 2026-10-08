from datetime import date, datetime

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.library_hours import KST
from backend.service import LibraryService
from tests.test_presence import day_records


NOW = datetime(2026, 10, 8, 15, tzinfo=KST)


def test_meta_skips_only_dates_without_prior_year_source_and_retains_closures():
    rows = day_records('2024-10-08', [(0, 5)] * 12)
    rows += day_records('2025-10-10', [(2, 0)] * 12)
    # Current/future year data are not prior-year statistics for October 9.
    rows += day_records('2026-10-09', [(9, 0)] * 12)
    rows += day_records('2027-10-09', [(9, 0)] * 12)
    service = LibraryService(rows)
    client = TestClient(create_app(type('P', (), {'get': lambda self: service})(), clock=lambda: NOW))
    meta = client.get('/api/v1/meta').json()
    assert meta['date_window'] == {'min_date': '2026-10-08', 'max_date': '2026-10-15'}
    assert meta['date_availability'] == {
        'available_dates': ['2026-10-08', '2026-10-10', '2026-10-12'],
        'skipped_dates': ['2026-10-09', '2026-10-11', '2026-10-13', '2026-10-14', '2026-10-15'],
    }
    body = client.get('/api/v1/congestion/today?date=2026-10-08').json()
    assert body['date'] == '2026-10-08'
    assert all(h['estimated_present'] == 0 and h['sample_count'] == 1 for h in body['hourly'])
    # A direct API request retains its explicit date; UI handles the skip.
    assert client.get('/api/v1/congestion/today?date=2026-10-09').json()['date'] == '2026-10-09'


def test_availability_observes_kst_year_rollover_and_partial_source_presence():
    rows = day_records('2025-01-01', [(1, 0)] * 12, partial=True)
    service = LibraryService(rows)
    now = datetime.fromisoformat('2025-12-31T15:00:00+00:00')
    available = service.date_availability(now)
    assert '2026-01-01' in available['available_dates']
    assert '2026-01-02' in available['skipped_dates']
    assert min(available['available_dates'] + available['skipped_dates']) == '2026-01-01'
