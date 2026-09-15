"""Common v1 records boundary and pure aggregation. No occupancy inference."""
from collections import defaultdict
from datetime import date

FIELDS = {'date', 'day_of_week', 'gate', 'gate_name', 'passage_id', 'hour',
          'in_count', 'out_count', 'total_in', 'total_out', 'is_partial', 'source_file'}
HOURS = tuple(range(8, 24))
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
LEVELS = {'quiet': '여유', 'normal': '보통', 'busy': '혼잡'}


class DataError(ValueError):
    def __init__(self, message, code='PROCESSING_ERROR'):
        super().__init__(message)
        self.code = code


def parse_date(value):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise DataError('날짜는 YYYY-MM-DD 형식이어야 합니다.', 'INVALID_DATE')


def validate_records(records):
    if not isinstance(records, list):
        raise DataError('records는 JSON 배열이어야 합니다.')
    seen, totals = set(), {}
    clean = []
    for index, row in enumerate(records):
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise DataError(f'record {index}: 공통 규격 v1의 12개 필드가 필요합니다.')
        day = parse_date(row['date'])
        if row['day_of_week'] != DAYS[day.weekday()] or row['gate'] not in ('front', 'back'):
            raise DataError(f'record {index}: 요일 또는 게이트 오류')
        if type(row['hour']) is not int or row['hour'] not in HOURS:
            raise DataError(f'record {index}: hour는 8~23 정수입니다.')
        for key in ('in_count', 'out_count', 'total_in', 'total_out'):
            if type(row[key]) is not int or row[key] < 0:
                raise DataError(f'record {index}: {key} 결측/음수/비정수')
        if type(row['is_partial']) is not bool:
            raise DataError(f'record {index}: is_partial은 boolean입니다.')
        for key in ('gate_name', 'passage_id', 'source_file'):
            if not isinstance(row[key], str) or not row[key]:
                raise DataError(f'record {index}: {key} 문자열이 필요합니다.')
        key = (row['date'], row['gate'], row['hour'])
        if key in seen:
            raise DataError(f'중복 date+gate+hour: {key}')
        seen.add(key)
        group = key[:2]
        daily = (row['total_in'], row['total_out'], row['is_partial'])
        if group in totals and totals[group] != daily:
            raise DataError(f'날짜/게이트 내 일일 합계 또는 partial 불일치: {group}')
        totals[group] = daily
        clean.append(dict(row))
    return sorted(clean, key=lambda r: (r['date'], r['hour'], r['gate']))


def aggregate(records):
    grouped = defaultdict(list)
    for row in records:
        grouped[row['date'], row['hour']].append(row)
    return [dict(date=day, hour=hour,
                 in_count=sum(r['in_count'] for r in rows),
                 out_count=sum(r['out_count'] for r in rows),
                 visit_count=sum(r['in_count'] for r in rows),
                 baseline_avg=None, difference_rate=None,
                 congestion_score=None, congestion_level=None,
                 is_partial=any(r['is_partial'] for r in rows)
                 or {r['gate'] for r in rows} != {'front', 'back'})
            for (day, hour), rows in sorted(grouped.items())]
