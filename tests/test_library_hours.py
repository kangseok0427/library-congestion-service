"""ADE-38: fixed clocks and synthetic data; no library source file writes."""
import copy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import FileProvider, create_app
from backend.library_hours import DEFAULT_HOURS, KST
from backend.prediction import backtest, forecast, historical, recommendation
from backend.service import LibraryService
from scripts.rebuild import rebuild
from scripts.sample import generate

NOW = datetime(2026, 9, 22, 8, tzinfo=KST)


@pytest.fixture
def service():
    return LibraryService(generate(end=date(2026, 9, 29), days=56))


@pytest.fixture
def client(service):
    class Provider:
        def get(self):
            return service
    return TestClient(create_app(Provider(), clock=lambda: NOW))


@pytest.mark.parametrize('offset', range(8))
def test_all_eight_dates_consistent(client, offset):
    day = NOW.date() + timedelta(days=offset)
    response = client.get('/api/v1/congestion/today', params={'date': day.isoformat()})
    actual = client.get('/api/v1/stats', params={'date': day.isoformat()})
    assert response.status_code == actual.status_code == 200
    data, stats = response.json(), actual.json()
    if offset in (2, 3, 4, 6):  # Chuseok Thu..Sat; weekly Monday closure.
        assert data['data_status'] == stats['data_status'] == 'closed'
        assert data['congestion']['label'] == '휴관일'
        assert data['hourly'] == stats['hourly'] == []
        assert data['recommendation']['best_start_hour'] is None
        assert stats['total_in'] is None
    else:
        expected = list(range(9, 17 if day.weekday() >= 5 else 21))
        assert [r['hour'] for r in data['hourly']] == expected
        assert all(r['expected_visitors'] is not None for r in data['hourly'])
        reco = data['recommendation']
        assert 9 <= reco['best_start_hour'] < reco['best_end_hour'] <= expected[-1] + 1
        assert stats['data_status'] == ('complete' if offset == 0 else 'pending')
        if offset:
            assert stats['message'] == '집계 전'
            assert stats['hourly'] == [] and stats['total_in'] is None


@pytest.mark.parametrize('endpoint', ['/api/v1/congestion/today', '/api/v1/stats'])
@pytest.mark.parametrize('day', ['2026-09-21', '2026-09-30', '2026-02-30', '20260922', ''])
def test_outside_range_and_invalid_dates_rejected(client, endpoint, day):
    response = client.get(endpoint, params={'date': day})
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'INVALID_DATE'


def test_kst_midnight_rollover_and_default_date(service):
    class Provider:
        def get(self):
            return service
    utc_now = datetime(2026, 9, 21, 14, 59, 59, tzinfo=timezone.utc)
    client = TestClient(create_app(Provider(), clock=lambda: utc_now))
    assert client.get('/api/v1/meta').json()['date_window'] == {
        'min_date': '2026-09-21', 'max_date': '2026-09-28'}
    utc_now += timedelta(seconds=1)
    assert client.get('/api/v1/meta').json()['date_window'] == {
        'min_date': '2026-09-22', 'max_date': '2026-09-29'}
    assert client.get('/api/v1/congestion/today').json()['date'] == '2026-09-22'
    assert client.get('/api/v1/stats?date=2026-09-21').status_code == 400


def test_filter_applies_before_training_statistics_and_backtest(service):
    rows = service.rows
    history = historical(rows, date(2026, 9, 29), 4)
    assert history
    assert all(r['hour'] in DEFAULT_HOURS.hours(date.fromisoformat(r['date'])) for r in history)
    assert not any(r['date'] in DEFAULT_HOURS.closed_dates for r in history)
    patterns = service.patterns()
    assert all(d['day_of_week'] != 'Mon' for d in patterns['weekday'])
    assert all(9 <= r['hour'] < (17 if r['day_of_week'] in ('Sat', 'Sun') else 21)
               for r in patterns['weekday_hourly'])
    stats = service.stats('2026-09-22', now=NOW)
    assert len(stats['hourly']) == 12 and stats['data_status'] == 'complete'
    assert stats['hourly_total_in'] == sum(r['in_count'] for r in stats['hourly'])
    clean = DEFAULT_HOURS.filter_rows(rows)
    assert backtest(rows) == backtest(clean)


def test_closed_quality_flag_and_configured_temporary_closure(service):
    records = copy.deepcopy(service.records)
    next(r for r in records if r['date'] == '2026-09-22')['is_closed_day'] = True
    policy = replace(DEFAULT_HOURS, closed_dates=DEFAULT_HOURS.closed_dates + ('2026-09-23',))
    updated = LibraryService(records, policy=policy)
    for day in (date(2026, 9, 22), date(2026, 9, 23)):
        assert updated.today(now=NOW, target=day)['data_status'] == 'closed'
        assert updated.stats(day.isoformat(), now=NOW)['data_status'] == 'closed'
        assert day.isoformat() not in {r['date'] for r in updated.patterns()['daily']}
        assert not any(r['date'] == day.isoformat() for r in historical(updated.rows, date(2026, 9, 29), 4, policy))


@pytest.mark.parametrize('label,last_weekday,last_weekend', [('start', 20, 16), ('end', 21, 17)])
def test_pending_bucket_setting_moves_slots_and_recommendation_together(service, label, last_weekday, last_weekend):
    policy = replace(DEFAULT_HOURS, bucket_label=label)
    assert not policy.bucket_label_confirmed
    for target, last, closing in [(date(2026, 9, 23), last_weekday, 21),
                                   (date(2026, 9, 27), last_weekend, 17)]:
        hours = forecast(service.rows, target, policy=policy)
        assert hours[-1]['hour'] == last
        assert hours[0]['start_hour'] == 9 and hours[-1]['end_hour'] == closing
        reco = recommendation(hours, closing - 2)
        assert reco['best_start_hour'] == closing - 2 and reco['best_end_hour'] == closing
        assert recommendation(hours, closing - 1)['best_start_hour'] is None


def test_outside_hours_never_recommended_and_elapsed_slot_excluded(service):
    assert service.today(now=NOW.replace(hour=21))['recommendation']['best_start_hour'] is None
    assert service.today(now=NOW.replace(hour=20, minute=1))['recommendation']['best_start_hour'] is None
    assert service.today(now=NOW.replace(hour=19))['recommendation']['best_start_hour'] == 19
    assert service.today(now=NOW.replace(hour=19, microsecond=1))['recommendation']['best_start_hour'] is None


def test_raw_records_and_snapshot_unchanged(tmp_path, service):
    path = tmp_path / 'records.json'
    rebuild(service.records, path)
    before = path.read_bytes()
    raw = copy.deepcopy(service.records)
    client = TestClient(create_app(FileProvider(path), clock=lambda: NOW))
    client.get('/api/v1/congestion/today?date=2026-09-22')
    client.get('/api/v1/stats?date=2026-09-22')
    client.get('/api/v1/patterns')
    assert path.read_bytes() == before
    assert service.records == raw
    assert {r['hour'] for r in service.records} == set(range(8, 24))


def test_missing_slots_partial_only_within_service_hours():
    records = generate(end=NOW.date(), days=1)
    filtered = [r for r in records if 9 <= r['hour'] < 21]
    assert LibraryService(filtered).stats('2026-09-22', now=NOW)['data_status'] == 'complete'
    filtered = [r for r in filtered if not (r['gate'] == 'back' and r['hour'] == 10)]
    assert LibraryService(filtered).stats('2026-09-22', now=NOW)['data_status'] == 'partial'


def test_rebuild_accepts_raw_only_outside_hours(tmp_path):
    records = [r for r in generate(end=NOW.date(), days=1) if r['hour'] in (8, 21, 22, 23)]
    service = rebuild(records, tmp_path / 'records.json')
    assert len(service.records) == len(records)
    assert not service.service_rows
