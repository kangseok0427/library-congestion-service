"""ADE-40 estimated present (추정 체류 인원). Not a live headcount or seat occupancy.

Each open day starts at 0 at opening; every operating hour adds front+back IN and
subtracts front+back OUT. From the first untrustworthy hour (missing gate, partial
collection, missing OUT, negative balance, missing hour) the rest of that day has
no value. Forecasts average recent valid values of the same weekday and hour and
fall back to the same hour only when fewer than two same-weekday samples exist.
The ETL preserves every validated hourly OUT value, including OUT_11. If IN and
OUT are equal in a slot, the cumulative estimate simply stays unchanged.
"""
from collections import defaultdict
from datetime import timedelta
from statistics import mean

from .congestion import midrank_score
from .domain import parse_date
from .library_hours import DEFAULT_HOURS

MIN_WEEKDAY_SAMPLES = 2
GATES = {'front', 'back'}


def observed(records, policy=DEFAULT_HOURS):
    """{(date, hour): (estimated_present | None, quality_status)} for operating hours of open days."""
    slots, closed = defaultdict(list), set()
    for row in records:
        slots[row['date'], row['hour']].append(row)
        if row.get('is_closed_day') is True:
            closed.add(row['date'])
    result = {}
    for day in sorted({d for d, _ in slots} - closed):
        balance, broken = 0, False
        for hour in policy.hours(parse_date(day)):
            rows = slots.get((day, hour))
            if rows is None:
                broken = True
                continue
            if broken:
                status = 'insufficient_data'
            elif {r['gate'] for r in rows} != GATES:
                status = 'missing_gate'
            elif any(r['is_partial'] for r in rows):
                status = 'partial'
            elif any(r['out_count'] is None for r in rows):
                status = 'missing_out'
            elif balance + sum(r['in_count'] - r['out_count'] for r in rows) < 0:
                status = 'negative_balance'
            else:
                balance += sum(r['in_count'] - r['out_count'] for r in rows)
                result[day, hour] = (balance, 'valid')
                continue
            broken = True
            result[day, hour] = (None, status)
    return result


def forecast(present, target, weeks=4, policy=DEFAULT_HOURS):
    """{hour: fields} for the target day; history strictly precedes the target."""
    cutoff = target - timedelta(weeks=weeks)
    history = [(parse_date(day), hour, value) for (day, hour), (value, _) in present.items()
               if value is not None and cutoff <= parse_date(day) < target]
    result = {}
    for hour in policy.hours(target):
        pool = [v for d, h, v in history if h == hour]
        same = [v for d, h, v in history if h == hour and d.weekday() == target.weekday()]
        if len(same) >= MIN_WEEKDAY_SAMPLES:
            values, basis = same, 'same_weekday_same_hour'
        elif pool:
            values, basis = pool, 'same_hour_fallback'
        else:
            values, basis = [], 'insufficient_samples'
        estimate = round(mean(values)) if values else None
        score, level = midrank_score(estimate, pool) if values else (None, None)
        # The forecast is the same-weekday baseline itself, so the rate is 0 when it exists.
        baseline = round(mean(same), 2) if basis == 'same_weekday_same_hour' else None
        result[hour] = dict(estimated_present=estimate, level=level, score=score,
                            baseline_avg=baseline, difference_rate=0.0 if baseline else None,
                            calculation_basis=basis, sample_count=len(values),
                            quality_status='forecast' if values else 'insufficient_samples')
    return result
