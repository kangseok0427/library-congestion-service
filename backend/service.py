from collections import defaultdict
from datetime import datetime, timezone, timedelta
from statistics import mean

from .domain import HOURS, LEVELS, DataError, aggregate, parse_date, validate_records
from .prediction import forecast, recommendation, historical, percentile

KST = timezone(timedelta(hours=9))


class LibraryService:
    def __init__(self, records, updated_at=None, weeks=4, sample=False):
        self.records = validate_records(records)
        self.rows = aggregate(self.records)
        self.updated_at = updated_at or datetime.now(KST).isoformat()
        self.weeks = weeks
        self.sample = sample

    def stats(self, day):
        parse_date(day)
        rows = [dict(r) for r in self.rows if r['date'] == day]
        if not rows:
            raise DataError('해당 날짜의 데이터를 찾을 수 없습니다.', 'DATA_NOT_FOUND')
        # Daily source totals repeat 16 times; take each gate exactly once.
        gates = {r['gate']: r for r in self.records if r['date'] == day}
        partial = len(rows) != len(HOURS) or any(r['is_partial'] for r in rows)
        predictions = {p['hour']: p for p in forecast(self.rows, parse_date(day), self.weeks)}
        history = historical(self.rows, parse_date(day), self.weeks)
        for row in rows:
            baseline = predictions[row['hour']]['baseline_avg']
            row['baseline_avg'] = baseline
            if not row['is_partial']:
                row['difference_rate'] = round((row['visit_count']-baseline)/baseline*100, 2) if baseline else None
                pool = [r['visit_count'] for r in history if r['hour'] == row['hour']]
                if pool:
                    row['congestion_score'], row['congestion_level'] = percentile(row['visit_count'], pool)
        return dict(date=day, data_status='partial' if partial else 'complete',
                    total_in=sum(r['total_in'] for r in gates.values()),
                    total_out=sum(r['total_out'] for r in gates.values()),
                    hourly=rows, hourly_total_in=sum(r['in_count'] for r in rows),
                    hourly_total_out=sum(r['out_count'] for r in rows),
                    updated_at=self.updated_at)

    def today(self, now=None, target=None):
        now = now or datetime.now(KST)
        now = now.astimezone(KST)
        target = target or now.date()
        hourly = forecast(self.rows, target, self.weeks)
        active = next((h for h in hourly if h['hour'] == now.hour), None) if target == now.date() else None
        level = active['level'] if active else None
        minimum_hour = now.hour + (1 if now.minute or now.second else 0) if target == now.date() else 8
        return dict(date=target.isoformat(), data_status='forecast', reference_time=now.isoformat(),
                    congestion=dict(level=level, label=LEVELS.get(level, '예측 자료 없음' if active else '시간대별 예측 참고'),
                                    score=active['score'] if active else None),
                    recommendation=recommendation(hourly, minimum_hour), hourly=hourly,
                    updated_at=self.updated_at, is_sample=self.sample,
                    basis='과거 입장량 기준 예상 방문량 · 현재 체류인원 아님')

    def patterns(self):
        daily = defaultdict(list)
        for row in self.rows:
            daily[row['date']].append(row)
        complete = {day: sum(r['visit_count'] for r in rows) for day, rows in daily.items()
                    if len(rows) == len(HOURS) and not any(r['is_partial'] for r in rows)}
        hours, weekdays, weekday_hours, months = (defaultdict(list) for _ in range(4))
        for day, count in complete.items():
            weekday = parse_date(day).strftime('%a')
            weekdays[weekday].append(count)
            months[day[:7]].append(count)
            for row in daily[day]:
                hours[row['hour']].append(row['visit_count'])
                weekday_hours[(weekday, row['hour'])].append(row['visit_count'])
        return dict(daily=[dict(date=d, visit_count=n) for d, n in sorted(complete.items())],
                    weekday=[dict(day_of_week=k, average=round(mean(v), 2), sample_days=len(v)) for k, v in sorted(weekdays.items())],
                    hourly=[dict(hour=k, average=round(mean(v), 2), sample_days=len(v)) for k, v in sorted(hours.items())],
                    weekday_hourly=[dict(day_of_week=k[0], hour=k[1], average=round(mean(v), 2), sample_days=len(v))
                                    for k, v in sorted(weekday_hours.items())],
                    monthly=[dict(month=k, average=round(mean(v), 2), sample_days=len(v)) for k, v in sorted(months.items())],
                    basis='complete_hourly_in_sum')
