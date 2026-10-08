from collections import defaultdict
from datetime import datetime
from statistics import mean

from .domain import LEVELS, DataError, aggregate, parse_date, validate_records, nullable_sum
from .prediction import forecast, recommendation, historical, percentile
from .library_hours import DEFAULT_HOURS, KST, now_kst
from . import presence
from .calendar_statistics import summarize


class LibraryService:
    def __init__(self, records, updated_at=None, weeks=4, sample=False, policy=DEFAULT_HOURS):
        self.records = validate_records(records)
        self.rows = aggregate(self.records)
        self.policy = policy
        self.service_rows = policy.filter_rows(self.rows)
        self.closed_dates = {r['date'] for r in self.rows if r.get('is_closed_day') is True}
        self.present = presence.observed(self.records, policy)
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
            # ADE-40: raw IN/OUT stay as they are; the day's cumulative estimate is added.
            row['estimated_present'], row['quality_status'] = self.present.get((day, row['hour']), (None, 'insufficient_data'))
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
        hourly = [] if closed else summarize(self.present, self.rows, target, self.policy)
        active = next((h for h in hourly if h['start_hour'] == now.hour), None) if target == now.date() else None
        level = active['level'] if active else None
        minimum_hour = now.hour + (1 if now.minute or now.second or now.microsecond else 0) if target == now.date() else self.policy.bounds(target)[0]
        reco = recommendation(hourly, minimum_hour, key='estimated_present')
        eligible = [h for h in hourly if h['start_hour'] >= minimum_hour]
        # A low observed pair is not the day's quietest period when other
        # eligible periods are unknown. Past gaps do not affect today's choice.
        incomplete = any(h['estimated_present'] is None for h in eligible)
        if incomplete and any(h['estimated_present'] is not None for h in eligible):
            reco = dict(best_start_hour=None, best_end_hour=None,
                        message='일부 시간대의 통계를 계산할 수 없어 여유로운 시간을 비교하기 어렵습니다.')
        elif reco['best_start_hour'] is None:
            reco['message'] = '안내할 연속 2시간의 과거 같은 날짜 통계가 없습니다.'
        else:
            scope = '남은 시간대' if target == now.date() else '조회한 시간대'
            reco['message'] = (f"과거 같은 날짜 통계에서 {scope} 중 "
                               f"{reco['best_start_hour']}시~{reco['best_end_hour']}시의 평균 인원이 "
                               '가장 적었습니다. 방문 시 참고하세요.')
        if closed:
            reco['message'] = '휴관일에는 방문 시간을 추천하지 않습니다.'
        operating = self.policy.info(target)
        operating.update(is_closed=closed, available_hours=tuple(h['hour'] for h in hourly))
        source_dates = sorted({day for h in hourly for day in h['source_dates']})
        matched_dates = sorted({day for h in hourly for day in h['matched_source_dates']})
        data_dates = sorted({r['date'] for r in self.records})
        return dict(date=target.isoformat(), data_status='closed' if closed else 'historical_statistics', reference_time=now.isoformat(),
                    operating=operating,
                    congestion=dict(level=level, label='휴관일' if closed else LEVELS.get(level, '통계 자료 없음' if active else '시간대별 과거 통계 참고'),
                                    score=active['score'] if active else None),
                    recommendation=reco, hourly=hourly,
                    updated_at=self.updated_at, is_sample=self.sample,
                    basis='보유한 모든 과거 연도의 같은 월·일·시간대 추정 체류 인원(누적 IN − OUT) 평균 통계',
                    statistics=dict(method='same_month_day_hour_mean', month=target.month, day=target.day,
                                    matched_dates=matched_dates, matched_days=len(matched_dates),
                                    source_dates=source_dates, source_years=sorted({parse_date(d).year for d in source_dates}),
                                    sample_days=len(source_dates),
                                    available_data_start=data_dates[0] if data_dates else None,
                                    available_data_end=data_dates[-1] if data_dates else None))

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
