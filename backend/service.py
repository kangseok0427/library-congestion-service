from collections import defaultdict
from datetime import datetime
from statistics import mean

from .domain import LEVELS, DataError, aggregate, parse_date, validate_records, nullable_sum
from .prediction import forecast, recommendation, historical, percentile
from .library_hours import DEFAULT_HOURS, KST, now_kst


class LibraryService:
    def __init__(self, records, updated_at=None, weeks=4, sample=False, policy=DEFAULT_HOURS):
        self.records = validate_records(records)
        self.rows = aggregate(self.records)
        self.policy = policy
        self.service_rows = policy.filter_rows(self.rows)
        self.closed_dates = {r['date'] for r in self.rows if r.get('is_closed_day') is True}
        self.updated_at = updated_at or datetime.now(KST).isoformat()
        self.weeks = weeks
        self.sample = sample

    def is_closed(self, day):
        return self.policy.is_closed(day) or day.isoformat() in self.closed_dates

    def stats(self, day, now=None):
        target = parse_date(day)
        closed = self.is_closed(target)
        if closed or target > (now or now_kst()).astimezone(KST).date():
            return dict(date=day, data_status='closed' if closed else 'pending',
                        message='휴관일' if closed else '집계 전', hourly=[],
                        total_in=None, total_out=None, hourly_total_in=None,
                        hourly_total_out=None, updated_at=self.updated_at)
        rows = [dict(r) for r in self.service_rows if r['date'] == day]
        if not rows:
            raise DataError('해당 날짜의 데이터를 찾을 수 없습니다.', 'DATA_NOT_FOUND')
        # Daily source totals repeat 16 times; take each gate exactly once.
        gates = {r['gate']: r for r in self.records if r['date'] == day}
        partial = len(rows) != len(self.policy.hours(target)) or any(r['is_partial'] for r in rows)
        predictions = {p['hour']: p for p in forecast(self.rows, target, self.weeks, self.policy)}
        history = historical(self.rows, target, self.weeks, self.policy)
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
                    hourly_total_out=nullable_sum(r['out_count'] for r in rows),
                    updated_at=self.updated_at)

    def today(self, now=None, target=None):
        now = now or now_kst()
        now = now.astimezone(KST)
        target = target or now.date()
        closed = self.is_closed(target)
        hourly = forecast(self.rows, target, self.weeks, self.policy)
        active = next((h for h in hourly if h['start_hour'] == now.hour), None) if target == now.date() else None
        level = active['level'] if active else None
        minimum_hour = now.hour + (1 if now.minute or now.second or now.microsecond else 0) if target == now.date() else self.policy.bounds(target)[0]
        reco = recommendation(hourly, minimum_hour)
        if closed:
            reco['message'] = '휴관일에는 방문 시간을 추천하지 않습니다.'
        operating = self.policy.info(target)
        operating.update(is_closed=closed, available_hours=tuple(h['hour'] for h in hourly))
        return dict(date=target.isoformat(), data_status='closed' if closed else 'forecast', reference_time=now.isoformat(),
                    operating=operating,
                    congestion=dict(level=level, label='휴관일' if closed else LEVELS.get(level, '예측 자료 없음' if active else '시간대별 예측 참고'),
                                    score=active['score'] if active else None),
                    recommendation=reco, hourly=hourly,
                    updated_at=self.updated_at, is_sample=self.sample,
                    basis='과거 입장량 기준 예상 방문량 · 현재 체류인원 아님')

    def patterns(self):
        daily = defaultdict(list)
        for row in self.service_rows:
            daily[row['date']].append(row)
        complete = {day: sum(r['visit_count'] for r in rows) for day, rows in daily.items()
                    if len(rows) == len(self.policy.hours(parse_date(day))) and not any(r['is_partial'] for r in rows)}
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
