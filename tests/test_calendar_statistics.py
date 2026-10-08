from datetime import date, datetime

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.library_hours import KST
from backend.service import LibraryService
from tests.test_presence import day_records


def records(day, count, *, partial=False):
    return day_records(day, [(count, 0)] * 12, partial=partial)


def result(rows, target='2026-10-08'):
    return LibraryService(rows).today(now=datetime(2026, 10, 8, 8, tzinfo=KST),
                                     target=date.fromisoformat(target))


def test_prior_years_same_calendar_date_not_recent_weekday():
    # 2021 Friday and 2025 Wednesday are older than four weeks and different weekdays.
    rows = records('2021-10-08', 2) + records('2025-10-08', 4)
    rows += records('2026-10-01', 999) + records('2026-10-08', 999) + records('2027-10-08', 999)
    body = result(rows)
    assert body['data_status'] == 'historical_statistics'
    assert body['statistics']['source_dates'] == ['2021-10-08', '2025-10-08']
    assert [h['estimated_present'] for h in body['hourly']] == list(range(3, 37, 3))
    assert body['hourly'][0]['level'] == 'quiet' and body['hourly'][-1]['level'] == 'busy'
    assert {h['level'] for h in body['hourly']} == {'quiet', 'normal', 'busy'}
    assert [h['score'] for h in body['hourly']] == sorted(h['score'] for h in body['hourly'])
    assert body['hourly'][0]['expected_visitors'] == 3
    assert all(h['sample_count'] == 2 and h['quality_status'] == 'historical_statistics'
               and h['difference_rate'] is None for h in body['hourly'])
    assert '과거 같은 날짜 통계' in body['recommendation']['message']


def test_sample_dates_follow_actual_valid_hour_not_whole_year_count():
    rows = records('2021-10-08', 2) + records('2022-10-08', 4)  # Sat closes at 17.
    rows += records('2025-10-08', 500, partial=True)
    broken = records('2020-10-08', 20)
    broken = [r for r in broken if not (r['hour'] == 11 and r['gate'] == 'back')]
    rows += broken
    hours = result(rows)['hourly']
    assert hours[0]['sample_count'] == 3
    assert hours[2]['source_dates'] == ['2021-10-08', '2022-10-08']
    assert hours[-1]['source_dates'] == ['2021-10-08']
    assert all('2025-10-08' not in h['source_dates'] for h in hours)


def test_no_matching_calendar_date_is_not_filled_with_other_dates():
    body = result(records('2026-10-01', 5))
    assert body['statistics']['sample_days'] == 0
    assert all(h['estimated_present'] is None and h['sample_count'] == 0 for h in body['hourly'])
    assert body['recommendation']['best_start_hour'] is None
    assert '예측' not in body['recommendation']['message']


def test_missing_later_hours_do_not_make_eleven_to_one_a_quiet_recommendation():
    # 11/12 are the lowest available pair, but every hour from 13 is unknown.
    rows = day_records('2021-10-08', [(10, 0), (0, 5), (0, 4), (0, 0)] + [(0, 0)] * 8)
    rows = [r for r in rows if not (r['hour'] == 13 and r['gate'] == 'back')]
    body = result(rows)
    hours = {h['hour']: h for h in body['hourly']}
    assert hours[11]['estimated_present'] == hours[12]['estimated_present'] == 1
    assert all(hours[h]['estimated_present'] is None for h in range(13, 21))
    assert body['recommendation'] == {
        'best_start_hour': None, 'best_end_hour': None,
        'message': '일부 시간대의 통계를 계산할 수 없어 여유로운 시간을 비교하기 어렵습니다.',
    }


def test_past_missing_observation_does_not_block_complete_remaining_period():
    service = LibraryService(records('2021-10-08', 1))
    # Recommendation consumes per-hour summaries: an unavailable past sample
    # must not prevent comparing a fully observed remaining period.
    service.present['2021-10-08', 9] = (None, 'insufficient_data')
    body = service.today(now=datetime(2026, 10, 8, 19, tzinfo=KST))
    assert body['hourly'][0]['estimated_present'] is None
    assert body['hourly'][-1]['level'] == 'busy'
    assert body['recommendation']['best_start_hour'] == 19
    assert body['recommendation']['best_end_hour'] == 21
    assert '남은 시간대 중' in body['recommendation']['message']
    assert '여유' not in body['recommendation']['message']


def test_future_recommendation_compares_whole_selected_day():
    service = LibraryService(records('2021-10-08', 1))
    body = service.today(now=datetime(2026, 10, 7, 19, tzinfo=KST),
                         target=date(2026, 10, 8))
    assert body['recommendation']['best_start_hour'] == 9
    assert '조회한 시간대 중' in body['recommendation']['message']


def test_corrected_zero_is_included_in_same_date_average_and_later_hours():
    rows = records('2021-10-08', 4)
    rows += day_records('2025-10-08', [(0, 5), (6, 0)] + [(0, 0)] * 10)
    body = result(rows)
    first, second = body['hourly'][:2]
    assert first['source_dates'] == ['2021-10-08', '2025-10-08']
    assert first['sample_count'] == 2 and first['estimated_present'] == 2
    assert first['corrected_source_dates'] == ['2025-10-08']
    assert first['corrected_sample_count'] == 1 and first['excluded_samples'] == []
    assert second['estimated_present'] == 7 and second['sample_count'] == 2
    assert all(h['estimated_present'] is not None for h in body['hourly'])
    assert body['statistics']['negative_balance_correction'] == 'floor_at_zero_continue'


def test_fractional_mean_is_preserved_and_legacy_week_setting_has_no_effect():
    rows = records('2021-10-08', 1) + records('2025-10-08', 2)
    now = datetime(2026, 10, 8, 8, tzinfo=KST)
    short = LibraryService(rows, weeks=1).today(now=now)
    long = LibraryService(rows, weeks=52).today(now=now)
    assert short['hourly'] == long['hourly']
    assert short['hourly'][0]['estimated_present'] == 1.5
    assert short['hourly'][0]['sample_count'] == 2


def test_zero_is_valid_and_confirmed_closed_dates_are_excluded():
    rows = records('2021-10-08', 0)
    rows += [dict(r, is_closed_day=True) for r in records('2025-10-08', 99)]
    hours = result(rows)['hourly']
    assert all(h['estimated_present'] == 0 and h['level'] == 'quiet' and h['sample_count'] == 1
               for h in hours)


def test_leap_day_uses_only_leap_day():
    rows = records('2024-02-29', 3) + records('2023-02-28', 999)
    body = result(rows, '2028-02-29')
    assert body['statistics']['source_dates'] == ['2024-02-29']
    assert body['hourly'][0]['estimated_present'] == 3


def test_real_api_uses_old_records_and_preserves_date_and_closure_guards():
    service = LibraryService(records('2021-10-08', 2) + records('2025-10-08', 4))
    now = datetime(2026, 10, 8, 8, tzinfo=KST)
    client = TestClient(create_app(type('P', (), {'get': lambda self: service})(), clock=lambda: now))
    body = client.get('/api/v1/congestion/today?date=2026-10-08').json()
    assert body['statistics']['source_years'] == [2021, 2025]
    assert body['hourly'][0]['estimated_present'] == 3
    assert client.get('/api/v1/congestion/today?date=2026-10-13').json()['data_status'] == 'historical_statistics'
    closed = client.get('/api/v1/congestion/today?date=2026-10-12').json()
    assert closed['data_status'] == 'closed' and closed['hourly'] == []
    assert client.get('/api/v1/congestion/today?date=2026-10-16').status_code == 400
