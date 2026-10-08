"""Observed same-month/day statistics across all available prior years.

There is no recency window, weekday fallback or future headcount forecast.
Valid cumulative observations, including balances corrected to zero, participate;
missing samples remain null.
"""
from collections import defaultdict
from statistics import mean

from .congestion import midrank_score
from .domain import LEVELS, parse_date
from .library_hours import DEFAULT_HOURS


def summarize(present, rows, target, policy=DEFAULT_HOURS):
    # Resolve the source calendar dates before quality filtering. Otherwise a
    # present-but-unusable date disappears and looks like a failed date match.
    dates = {r['date']: parse_date(r['date']) for r in rows}
    matching_days = sorted(day for day, parsed in dates.items()
                           if parsed.year < target.year
                           and (parsed.month, parsed.day) == (target.month, target.day))
    matching = set(matching_days)
    closed_days = {r['date'] for r in rows if r.get('is_closed_day') is True}
    history = defaultdict(list)
    for (day, hour), (value, status) in present.items():
        if day in matching and value is not None and status in ('valid', 'negative_corrected'):
            history[hour].append((day, value))
    comparison_pool = [value for samples in history.values() for _, value in samples]
    entries = {(r['date'], r['hour']): r for r in rows}
    result = []
    for hour in policy.hours(target):
        samples = sorted(history[hour])
        dates = [day for day, _ in samples]
        corrected_dates = [day for day in dates if present[day, hour][1] == 'negative_corrected']
        excluded = []
        for day in matching_days:
            if day in dates:
                continue
            source_day = parse_date(day)
            if day in closed_days or policy.is_closed(source_day):
                reason = 'closed'
            elif hour not in policy.hours(source_day):
                reason = 'outside_operating_hours'
            else:
                reason = present.get((day, hour), (None, 'missing_hour'))[1]
            excluded.append(dict(date=day, reason=reason))
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
            corrected_source_dates=corrected_dates, corrected_sample_count=len(corrected_dates),
            matched_source_dates=matching_days, excluded_samples=excluded,
            source_years=[parse_date(day).year for day in dates],
            quality_status='historical_statistics' if samples else 'insufficient_samples',
        ))
    return result
