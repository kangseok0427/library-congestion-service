"""Observed same-month/day statistics across all available prior years.

There is no recency window, weekday fallback or future headcount forecast.
Only valid cumulative observations participate; missing samples remain null.
"""
from collections import defaultdict
from statistics import mean

from .congestion import midrank_score
from .domain import LEVELS, parse_date
from .library_hours import DEFAULT_HOURS


def summarize(present, rows, target, policy=DEFAULT_HOURS):
    history = defaultdict(list)
    for (day, hour), (value, status) in present.items():
        observed_day = parse_date(day)
        if (value is not None and status == 'valid'
                and observed_day.year < target.year
                and (observed_day.month, observed_day.day) == (target.month, target.day)):
            history[hour].append((day, value))
    comparison_pool = [value for samples in history.values() for _, value in samples]
    entries = {(r['date'], r['hour']): r for r in rows}
    result = []
    for hour in policy.hours(target):
        samples = sorted(history[hour])
        dates = [day for day, _ in samples]
        value = round(mean(v for _, v in samples), 2) if samples else None
        incoming = [entries[day, hour]['in_count'] for day in dates]
        score, level = midrank_score(value, comparison_pool) if samples else (None, None)
        if value == 0:
            score, level = 0.0, 'quiet'
        result.append(dict(
            hour=hour, start_hour=policy.start_hour(hour), end_hour=policy.start_hour(hour) + 1,
            estimated_present=value,
            # Compatibility name; this is now an observed IN mean, not a forecast.
            expected_visitors=round(mean(incoming), 2) if incoming else None,
            method='same_month_day_hour_mean' if samples else 'unavailable',
            baseline_avg=value, difference_rate=None, level=level, score=score,
            label=LEVELS.get(level, '자료 부족'),
            calculation_basis='same_month_day_hour_mean' if samples else 'insufficient_samples',
            sample_count=len(samples), source_dates=dates,
            source_years=[parse_date(day).year for day in dates],
            quality_status='historical_statistics' if samples else 'insufficient_samples',
        ))
    return result
