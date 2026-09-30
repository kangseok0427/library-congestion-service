"""Capture ADE-40 /congestion/today responses as browser-test fixtures.

ADE-40 (estimated_present) currently lives in a separate repository
(thanks04122006-spec/operator-api). This runs its real today_forecast() on
synthetic records at a fixed KST time and stores the raw response, so the
ADE-41 browser test checks the real field shapes. Test-only; the numbers are
synthetic and say nothing about real-data accuracy.

    python -m scripts.capture_ade40_fixture --operator-api PATH
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from scripts.sample import generate

TARGET = date(2026, 9, 22)
OUT = Path('tests/fixtures')


def today_rows(hours):
    """Partial day: 9h IN=OUT (0 present), 10h valid, 11h OUT missing."""
    rows = []
    for gate, name in (('front', '자료실.정문'), ('back', '자료실.후문')):
        for hour, (n_in, n_out) in hours.items():
            rows.append(dict(date=TARGET.isoformat(), day_of_week='Tue', gate=gate, gate_name=name,
                             passage_id='sample-' + gate, hour=hour, in_count=n_in, out_count=n_out,
                             total_in=0, total_out=0, is_partial=False, source_file='synthetic'))
    return rows


def capture(operator_api, records, now):
    sys.path.insert(0, str(operator_api))
    from app.services import aggregation

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    with patch.object(aggregation, 'load_records', return_value=records), patch.object(aggregation, 'datetime', Fixed):
        return aggregation.today_forecast()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--operator-api', required=True, type=Path)
    args = parser.parse_args()
    commit = subprocess.run(['git', '-C', str(args.operator_api), 'rev-parse', '--short', 'HEAD'],
                            capture_output=True, text=True, check=True).stdout.strip()
    history = [r for r in generate(end=date(2026, 9, 21), days=56)]
    cases = {
        'forecast': (history, datetime(2026, 9, 22, 8, 0)),
        'partial': (history + today_rows({9: (6, 6), 10: (20, 5), 11: (15, None)}), datetime(2026, 9, 22, 13, 0)),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (records, now) in cases.items():
        data = capture(args.operator_api, records, now)
        data['_fixture'] = (f'operator-api {commit} today_forecast(), synthetic records, now={now.isoformat()} KST. '
                            'Test-only; not real library data.')
        path = OUT / f'ade40_today_{name}.json'
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(path, [(h['hour'], h['estimated_present'], h['level'], h['quality_status']) for h in data['hourly']])


if __name__ == '__main__':
    main()
