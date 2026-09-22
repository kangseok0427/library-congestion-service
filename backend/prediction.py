"""Rolling-origin baseline. Every training observation strictly precedes target day."""
from collections import defaultdict
from datetime import timedelta
from math import sqrt
from statistics import mean

from .congestion import midrank_score
from .domain import parse_date
from .library_hours import DEFAULT_HOURS


def historical(rows, target, weeks, policy=DEFAULT_HOURS):
    if type(weeks) is not int or not 1 <= weeks <= 52:
        raise ValueError('weeks must be between 1 and 52')
    cutoff = target - timedelta(weeks=weeks)
    return [r for r in policy.filter_rows(rows) if not r['is_partial']
            and cutoff <= parse_date(r['date']) < target]


def percentile(value, values):
    return midrank_score(value, values)


def forecast(rows, target, weeks=4, policy=DEFAULT_HOURS):
    if policy.is_closed(target) or any(r['date'] == target.isoformat() and r.get('is_closed_day') is True for r in rows):
        return []
    history = historical(rows, target, weeks, policy)
    result = []
    for hour in policy.hours(target):
        pool = [r['visit_count'] for r in history if r['hour'] == hour]
        same = [r['visit_count'] for r in history if r['hour'] == hour
                and parse_date(r['date']).weekday() == target.weekday()]
        values = same or pool
        expected = round(mean(values), 2) if values else None
        score, level = percentile(expected, pool) if values else (None, None)
        result.append(dict(hour=hour, start_hour=policy.start_hour(hour),
                           end_hour=policy.start_hour(hour)+1, expected_visitors=expected,
                           baseline_avg=round(mean(same), 2) if same else None,
                           difference_rate=0.0 if same and mean(same) else None,
                           level=level, score=score,
                           method='same_weekday_hour' if same else 'same_hour_fallback' if pool else 'unavailable',
                           sample_count=len(values)))
    return result


def recommendation(hourly, minimum_hour=8):
    by_hour = {r.get('start_hour', r['hour']): r for r in hourly if r['expected_visitors'] is not None}
    candidates = [(by_hour[h]['expected_visitors'] + by_hour[h+1]['expected_visitors'], h)
                  for h in by_hour if h >= minimum_hour and h + 1 in by_hour]
    if not candidates:
        return dict(best_start_hour=None, best_end_hour=None,
                    message='추천할 연속 2시간의 예측 데이터가 없습니다.')
    _, start = min(candidates)
    return dict(best_start_hour=start, best_end_hour=start+2,
                message=f'{start}시~{start+2}시가 과거 이용 패턴상 한산합니다. 실제 운영시간·휴관 여부를 확인한 뒤 방문하세요.')


def backtest(rows, weeks=4, evaluation_days=28, policy=DEFAULT_HOURS):
    rows = policy.filter_rows(rows)
    dates = sorted({r['date'] for r in rows if not r['is_partial']})
    selected = dates[-evaluation_days:]
    errors, simple_errors, methods = [], [], defaultdict(int)
    actual_count = 0
    for day in selected:
        target = parse_date(day)
        predictions = {p['hour']: p for p in forecast(rows, target, weeks, policy)}
        history = historical(rows, target, weeks, policy)
        for row in rows:
            if row['date'] != day or row['is_partial']:
                continue
            actual_count += 1
            p = predictions[row['hour']]
            if p['expected_visitors'] is None:
                continue
            errors.append(p['expected_visitors'] - row['visit_count'])
            pool = [r['visit_count'] for r in history if r['hour'] == row['hour']]
            simple_errors.append(mean(pool) - row['visit_count'])
            methods[p['method']] += 1
    return dict(model='recent_same_weekday_hour_mean', weeks=weeks,
                evaluation_start=selected[0] if selected else None,
                evaluation_end=selected[-1] if selected else None,
                evaluated_slots=len(errors), eligible_slots=actual_count,
                coverage=round(len(errors)/actual_count, 4) if actual_count else 0,
                mae=round(mean(abs(e) for e in errors), 3) if errors else None,
                rmse=round(sqrt(mean(e*e for e in errors)), 3) if errors else None,
                same_hour_mean_mae=round(mean(abs(e) for e in simple_errors), 3) if errors else None,
                methods=dict(methods), strategy='rolling_origin_strictly_before_target_date')
