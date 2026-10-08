from datetime import date

from backend.service import LibraryService
from tests.test_presence import day_records


def test_unusable_matching_date_is_distinct_from_absent_date():
    valid = day_records('2024-10-08', [(5, 1)] * 12)
    negative = day_records('2022-10-08', [(1, 10)] * 8)
    unrelated = day_records('2025-10-07', [(99, 0)] * 12)
    service = LibraryService(valid + negative + unrelated)
    hours = service.today(target=date(2026, 10, 8))['hourly']
    assert hours[0]['matched_source_dates'] == ['2022-10-08', '2024-10-08']
    assert hours[0]['source_dates'] == ['2024-10-08']
    assert hours[0]['estimated_present'] == 4
    assert hours[0]['excluded_samples'] == [
        {'date': '2022-10-08', 'reason': 'negative_balance'}]
    assert hours[1]['excluded_samples'] == [
        {'date': '2022-10-08', 'reason': 'insufficient_data'}]
    assert hours[-1]['excluded_samples'] == [
        {'date': '2022-10-08', 'reason': 'outside_operating_hours'}]
    missing = service.today(target=date(2026, 10, 9))['hourly']
    assert all(h['matched_source_dates'] == [] and h['excluded_samples'] == []
               and h['estimated_present'] is None for h in missing)


def test_partial_and_closed_source_dates_remain_traceable_without_zero_filling():
    partial = day_records('2024-10-08', [(5, 1)] * 12, partial=True)
    closed = [dict(r, is_closed_day=True)
              for r in day_records('2025-10-08', [(5, 1)] * 12)]
    body = LibraryService(partial + closed).today(target=date(2026, 10, 8))
    first = body['hourly'][0]
    assert first['source_dates'] == [] and first['sample_count'] == 0
    assert first['estimated_present'] is None
    assert first['excluded_samples'] == [
        {'date': '2024-10-08', 'reason': 'partial'},
        {'date': '2025-10-08', 'reason': 'closed'}]
