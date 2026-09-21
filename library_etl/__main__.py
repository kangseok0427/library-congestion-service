"""Trusted local Excel refresh CLI. LIBRARY_RECORDS selects the same API file."""
import argparse
import json
import os
import sys

from backend.domain import DataError
from .refresh import refresh


def main():
    parser = argparse.ArgumentParser(description='T02/T08 Excel → JSON 갱신')
    parser.add_argument('action', choices=['refresh'])
    parser.add_argument('source')
    parser.add_argument('--output', default=os.environ.get('LIBRARY_RECORDS', 'data/processed/records.json'))
    parser.add_argument('--sheet')
    parser.add_argument('--source-name')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--partial-date', action='append')
    group.add_argument('--all-complete', action='store_true')
    args = parser.parse_args()
    try:
        report = refresh(args.source, args.output, sheet=args.sheet, source_name=args.source_name,
                         partial_dates=[] if args.all_complete else args.partial_date)
    except DataError as exc:
        print(json.dumps({'error': {'code': exc.code, 'message': str(exc)},
                          'issues': getattr(exc, 'issues', [])}, ensure_ascii=True), file=sys.stderr)
        return 2
    # No extra report file to fail after publishing the live snapshot. A closed
    # output pipe must not turn a committed update into an apparent rollback.
    try:
        print(json.dumps(report, ensure_ascii=True))
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
