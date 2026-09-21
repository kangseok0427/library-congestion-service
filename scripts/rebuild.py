"""Local trusted CLI update boundary for T02/T08; no unauthenticated upload API."""
import argparse
import json
import os
import tempfile
from pathlib import Path
from backend.adapters import from_excel, read_records
from backend.service import LibraryService
from backend.prediction import backtest


def rebuild(records, destination):
    service=LibraryService(records)  # validate + recompute before publishing
    service.today()
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=destination.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(service.records,stream,ensure_ascii=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,destination)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return service


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source');parser.add_argument('--output',default='data/processed/records.json')
    parser.add_argument('--partial-date',action='append',default=[])
    parser.add_argument('--report',default='data/processed/report.json')
    args=parser.parse_args()
    records,report=from_excel(args.source,args.partial_date) if args.source.lower().endswith('.xlsx') else (read_records(args.source),{})
    service=rebuild(records,args.output)
    report.update(record_count=len(records),backtest=backtest(service.rows))
    path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'record_count':len(records),'warning_count':len(report.get('warnings',[])),'backtest':report['backtest']},ensure_ascii=True))


if __name__=='__main__':main()
