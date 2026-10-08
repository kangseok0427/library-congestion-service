"""ADE-40 estimated present: synthetic records and fixed clocks only."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.domain import DAYS
from backend.library_hours import DEFAULT_HOURS, KST
from backend.presence import forecast, observed
from backend.service import LibraryService

TUE = date(2026, 9, 22)          # weekday 09..20 labels, open
HOURS = DEFAULT_HOURS.hours(TUE)


def day_records(day, front, back=None, *, gates=('front', 'back'), partial=False, extra_hours=()):
    """front/back: one (in, out) per operating hour; back defaults to zeros."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    hours = DEFAULT_HOURS.hours(day)
    back = back or [(0, 0)] * len(hours)
    rows = []
    for gate, values in (('front', front), ('back', back)):
        if gate not in gates:
            continue
        pairs = list(zip(hours, values)) + [(h, (100, 0)) for h in extra_hours]
        for hour, (n_in, n_out) in pairs:
            rows.append(dict(date=day.isoformat(), day_of_week=DAYS[day.weekday()], gate=gate,
                             gate_name='자료실.정문' if gate == 'front' else '자료실.후문', passage_id='t-' + gate,
                             hour=hour, in_count=n_in, out_count=n_out, total_in=0, total_out=0,
                             is_partial=partial, source_file='synthetic'))
    return rows


def values(present, day):
    return [present.get((day, h), (None, None)) for h in HOURS]


def test_issue_example_and_gate_sum():
    # front+back combined per hour: (10,0) -> (5,3) -> (0,8) gives 10 -> 12 -> 4
    front = [(6, 0), (2, 1), (0, 5)] + [(0, 0)] * 9
    back = [(4, 0), (3, 2), (0, 3)] + [(0, 0)] * 9
    present = observed(day_records(TUE, front, back))
    assert [v for v, _ in values(present, '2026-09-22')[:4]] == [10, 12, 4, 4]
    assert {s for _, s in values(present, '2026-09-22')} == {'valid'}


def test_resets_each_day_and_ignores_hours_outside_operation():
    rows = day_records(TUE, [(5, 0)] * 12, extra_hours=(8, 21, 22)) + day_records(TUE + timedelta(days=1), [(1, 0)] * 12)
    present = observed(rows)
    assert present['2026-09-22', 20] == (60, 'valid')
    assert present['2026-09-23', 9] == (1, 'valid')
    assert ('2026-09-22', 8) not in present and ('2026-09-22', 21) not in present


@pytest.mark.parametrize('change,status', [
    (lambda rows: [r for r in rows if not (r['gate'] == 'back' and r['hour'] == 11)], 'missing_gate'),
    (lambda rows: [dict(r, out_count=None) if r['hour'] == 11 and r['gate'] == 'front' else r for r in rows], 'missing_out'),
])
def test_untrusted_hour_stops_the_rest_of_the_day(change, status):
    present = observed(change(day_records(TUE, [(5, 1)] * 12)))
    assert present['2026-09-22', 10] == (8, 'valid')
    assert present['2026-09-22', 11] == (None, status)
    assert all(present['2026-09-22', h] == (None, 'insufficient_data') for h in HOURS if h > 11)


def test_negative_balance_is_corrected_and_next_hour_continues():
    # 09 deficit -> 0, 10 recovery -> 5, 11 deficit -> 0, 12 recovery -> 3.
    rows = day_records(TUE, [(1, 4), (5, 0), (1, 10), (3, 0)] + [(0, 0)] * 8)
    original = [dict(r) for r in rows]
    present = observed(rows)
    assert values(present, '2026-09-22')[:5] == [
        (0, 'negative_corrected'), (5, 'valid'), (0, 'negative_corrected'),
        (3, 'valid'), (3, 'valid')]
    assert all(v is not None for v, _ in values(present, '2026-09-22'))
    assert rows == original


def test_negative_correction_happens_after_both_gates_are_combined():
    rows = day_records(TUE, [(0, 10)] + [(0, 0)] * 11,
                       [(20, 0)] + [(0, 0)] * 11)
    assert observed(rows)['2026-09-22', 9] == (10, 'valid')


def test_partial_and_missing_hour_and_closed_day():
    assert {s for _, s in values(observed(day_records(TUE, [(1, 0)] * 12, partial=True)), '2026-09-22')} == {'partial', 'insufficient_data'}
    rows = [r for r in day_records(TUE, [(1, 0)] * 12) if r['hour'] != 12]
    present = observed(rows)
    assert present['2026-09-22', 11] == (3, 'valid') and ('2026-09-22', 12) not in present
    assert present['2026-09-22', 13] == (None, 'insufficient_data')
    closed = [dict(r, is_closed_day=True) for r in day_records(TUE, [(1, 0)] * 12)]
    assert observed(closed) == {}


def test_forecast_same_weekday_fallback_and_insufficient():
    history = []
    for weeks, n in ((1, 4), (2, 2)):           # two Tuesdays: 4/hour and 2/hour
        history += day_records(TUE - timedelta(weeks=weeks), [(n, 0)] * 12)
    history += day_records(TUE + timedelta(days=7), [(99, 0)] * 12)     # future: no leakage
    present = observed(history)
    result = forecast(present, TUE)
    assert result[9] == dict(estimated_present=3, level=result[9]['level'], score=result[9]['score'],
                             baseline_avg=3.0, difference_rate=0.0,
                             calculation_basis='same_weekday_same_hour', sample_count=2, quality_status='forecast')
    assert result[20]['estimated_present'] == 36     # mean(48, 24)
    wednesday = forecast(present, TUE + timedelta(days=1))
    assert wednesday[9]['calculation_basis'] == 'same_hour_fallback' and wednesday[9]['sample_count'] == 2
    one = observed(day_records(TUE - timedelta(weeks=1), [(4, 0)] * 12) + day_records(TUE - timedelta(days=5), [(2, 0)] * 12))
    single = forecast(one, TUE)[9]                   # one Tuesday is not enough
    assert (single['calculation_basis'], single['sample_count'], single['estimated_present'], single['difference_rate']) == ('same_hour_fallback', 2, 3, None)
    empty = forecast({}, TUE)[9]
    assert empty == dict(estimated_present=None, level=None, score=None, baseline_avg=None, difference_rate=None,
                         calculation_basis='insufficient_samples',
                         sample_count=0, quality_status='insufficient_samples')


def test_api_contract_keeps_legacy_fields():
    # visitors are low in the morning but people stay; estimated_present peaks late.
    records = []
    for day in ('2021-09-23', '2022-09-23', '2025-09-23', '2026-09-15'):
        records += day_records(day, [(30, 0)] + [(10, 10)] * 8 + [(1, 20)] * 3,
                               [(30, 0)] + [(10, 10)] * 11)
    service = LibraryService(records)
    now = datetime(2026, 9, 22, 8, tzinfo=KST)
    client = TestClient(create_app(type('P', (), {'get': lambda self: service})(), clock=lambda: now))
    data = client.get('/api/v1/congestion/today', params={'date': '2026-09-23'}).json()
    row = data['hourly'][0]
    assert {'estimated_present', 'expected_visitors', 'method', 'calculation_basis', 'sample_count', 'quality_status', 'level'} <= row.keys()
    assert row['expected_visitors'] == 60 and row['estimated_present'] == 60   # 9h: 60 in, 0 out
    assert [h['estimated_present'] for h in data['hourly']][-3:] == [41, 22, 3]
    assert data['recommendation']['best_start_hour'] == 19    # lowest presence, not lowest IN
    assert '추정 체류 인원' in data['basis']
    stats = client.get('/api/v1/stats', params={'date': '2026-09-22'})
    assert stats.status_code == 404                           # no records for today: unchanged
    past = LibraryService(records).stats('2026-09-15', now=now)
    assert past['hourly'][0]['in_count'] == 60 and past['hourly'][0]['estimated_present'] == 60
    assert past['hourly'][-1]['quality_status'] == 'valid'
    assert client.get('/api/v1/congestion/today', params={'date': '2026-09-28'}).json()['hourly'] == []  # Monday


def test_equal_in_out_at_11_keeps_the_rest_of_the_day_available():
    # A validated source value is preserved. Equal IN/OUT means no net change,
    # and accumulation continues normally from noon onward.
    records = []
    for weeks in (1, 2, 3):
        records += day_records(TUE - timedelta(weeks=weeks), [(5, 1)] * 12, [(5, 1)] * 12)
    for r in records:
        if r['hour'] == 11:
            r['out_count'] = r['in_count']
    for day in ('2021-09-22', '2022-09-22', '2023-09-22'):
        history = day_records(day, [(5, 1)] * 12, [(5, 1)] * 12)
        for r in history:
            if r['hour'] == 11:
                r['out_count'] = r['in_count']
        records += history
    service = LibraryService(records)
    past = service.stats('2026-09-15', now=datetime(2026, 9, 22, 8, tzinfo=KST))['hourly']
    assert [(h['estimated_present'], h['quality_status']) for h in past[:4]] == [
        (8, 'valid'), (16, 'valid'), (16, 'valid'), (24, 'valid')]
    today = service.today(now=datetime(2026, 9, 22, 8, tzinfo=KST))
    assert [h['estimated_present'] for h in today['hourly']] == [8, 16, 16] + list(range(24, 89, 8))
    assert all(h['level'] is not None for h in today['hourly'])
    assert (today['recommendation']['best_start_hour'], today['recommendation']['best_end_hour']) == (9, 11)


def test_hourly_levels_use_one_reference_even_with_one_historical_day():
    present = observed(day_records(TUE - timedelta(weeks=1), [(5, 0)] * 12))
    result = forecast(present, TUE)
    assert [result[h]['estimated_present'] for h in HOURS] == list(range(5, 61, 5))
    assert [result[h]['level'] for h in HOURS] == ['quiet'] * 4 + ['normal'] * 4 + ['busy'] * 4
    assert all(result[h]['sample_count'] == 1 for h in HOURS)
    # Future observations never change the common comparison distribution.
    present.update(observed(day_records(TUE + timedelta(days=1), [(999, 0)] * 12)))
    assert forecast(present, TUE) == result


def test_zero_presence_is_quiet_and_constant_positive_presence_is_normal():
    for n, expected in ((0, 'quiet'), (10, 'normal')):
        present = observed(day_records(TUE - timedelta(weeks=1), [(n, 0)] + [(0, 0)] * 11))
        assert {r['level'] for r in forecast(present, TUE).values()} == {expected}
