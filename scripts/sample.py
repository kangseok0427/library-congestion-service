"""Generate synthetic inputs, never derived from library source data."""
import json
from datetime import date, timedelta
from pathlib import Path
from backend.domain import DAYS, HOURS


def generate(end=date(2026, 9, 14), days=56):
    records=[]
    for offset in range(days):
        day=end-timedelta(days=days-1-offset)
        for gate in ('front','back'):
            counts=[max(0, 24-abs(h-14)*3)+(day.weekday()*2)+(offset%3) for h in HOURS]
            for h,n in zip(HOURS,counts):
                records.append(dict(date=day.isoformat(),day_of_week=DAYS[day.weekday()],gate=gate,
                                    gate_name='자료실.정문' if gate=='front' else '자료실.후문',passage_id='sample-'+gate,
                                    hour=h,in_count=n,out_count=max(0,n-1),total_in=sum(counts),
                                    total_out=sum(max(0,v-1) for v in counts),is_partial=False,source_file='synthetic'))
    return records


if __name__=='__main__':
    path=Path('data/sample/records.json');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(generate(),ensure_ascii=False,indent=2),encoding='utf-8')
