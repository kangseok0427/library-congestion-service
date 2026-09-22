"""Service policy only. Never change the 08..23 raw records contract."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .domain import DataError, parse_date

KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


@dataclass(frozen=True)
class LibraryHours:
    weekday_open: int = 9
    weekday_close: int = 21
    weekend_open: int = 9
    weekend_close: int = 17
    closed_weekdays: tuple = (0,)
    # Confirmed Chuseok holidays, not a rule that every Sunday/holiday is closed.
    closed_dates: tuple = ('2026-09-24', '2026-09-25', '2026-09-26')
    # Provisional start-label interpretation: IN_21 is NOT yet verified.
    # If the device uses end labels, change to 'end'; all slots and
    # recommendation times then shift together (weekday last label: 20 -> 21).
    bucket_label: str = 'start'
    bucket_label_confirmed: bool = False

    def __post_init__(self):
        if self.bucket_label not in ('start', 'end'):
            raise ValueError('bucket_label must be start or end')
        for opening, closing in ((self.weekday_open, self.weekday_close),
                                 (self.weekend_open, self.weekend_close)):
            if not 0 <= opening < closing <= 24:
                raise ValueError('invalid operating hours')
        for day in self.closed_dates:
            parse_date(day)

    def is_closed(self, day):
        return day.weekday() in self.closed_weekdays or day.isoformat() in self.closed_dates

    def bounds(self, day):
        return ((self.weekend_open, self.weekend_close) if day.weekday() >= 5
                else (self.weekday_open, self.weekday_close))

    def hours(self, day):
        if self.is_closed(day):
            return ()
        opening, closing = self.bounds(day)
        shift = int(self.bucket_label == 'end')
        return tuple(range(opening + shift, closing + shift))

    def start_hour(self, label):
        return label - int(self.bucket_label == 'end')

    def filter_rows(self, rows):
        # A confirmed closure flag on any source row excludes that entire day.
        closed = {r['date'] for r in rows if r.get('is_closed_day') is True}
        return [r for r in rows if r['date'] not in closed
                and r['hour'] in self.hours(parse_date(r['date']))]

    def info(self, day):
        opening, closing = self.bounds(day)
        return dict(is_closed=self.is_closed(day), opening_hour=opening,
                    closing_hour=closing, available_hours=self.hours(day),
                    bucket_label=self.bucket_label,
                    bucket_label_confirmed=self.bucket_label_confirmed)


DEFAULT_HOURS = LibraryHours()


def date_window(now):
    today = now.astimezone(KST).date()
    return dict(min_date=today.isoformat(), max_date=(today + timedelta(days=7)).isoformat())


def validate_service_date(value, now):
    day = parse_date(value)
    today = now.astimezone(KST).date()
    if not today <= day <= today + timedelta(days=7):
        raise DataError('한국시간 기준 오늘부터 7일 후까지만 조회할 수 있습니다.', 'INVALID_DATE')
    return day
