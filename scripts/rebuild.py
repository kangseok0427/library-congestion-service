"""Local trusted CLI update boundary for T02/T08; no unauthenticated upload API."""
import argparse
import json
import os
import tempfile
from pathlib import Path
from backend.adapters import read_records
from backend.service import LibraryService
from backend.prediction import backtest


def rebuild(records, destination):
    service=LibraryService(records)  # validate + recompute before publishing
    json.dumps(service.today(), allow_nan=False)
    json.dumps(service.patterns(), allow_nan=False)
    # Raw-only out-of-hours dates are valid inputs, even with no service stats.
    for day in sorted({r['date'] for r in service.service_rows}):
        json.dumps(service.stats(day), allow_nan=False)
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=destination.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(service.records,stream,ensure_ascii=False,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,destination)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return service


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source');parser.add_argument('--output',default='data/processed/records.json')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--partial-date',action='append')
    group.add_argument('--all-complete',action='store_true')
    args=parser.parse_args()
    if args.source.lower().endswith('.xlsx'):
        from library_etl import refresh
        report=refresh(args.source,args.output,partial_dates=[] if args.all_complete else args.partial_date)
    else:
        records=read_records(args.source)
        report={'record_count':len(records),'backtest':backtest(LibraryService(records).rows)}
        json.dumps(report,allow_nan=False)
        rebuild(records,args.output)
    # Reports are returned on stdout; no fallible report-file write after commit.
    try:
        print(json.dumps(report,ensure_ascii=True))
    except OSError:
        pass


if __name__=='__main__':main()
