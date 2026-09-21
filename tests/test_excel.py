from datetime import date
import pytest
from openpyxl import Workbook
from backend.adapters import from_excel
from backend.domain import DataError, HOURS


def workbook(path,bad=None):
    # Disposable synthetic test fixture, never a deliverable spreadsheet.
    book=Workbook();s=book.active
    header=['수집일자','게이트','통로ID','전체_IN','전체_OUT']+[f'{d}_{h:02}' for h in HOURS for d in ('IN','OUT')]
    s.append(header)
    s.append(['전체','그룹','합계'])
    for gate in ['자료실.정문','자료실.후문']:
        values=['2026-09-10',gate,'synthetic',999,160]+[10]*32
        if bad is not None:values[5]=bad
        s.append(values)
    book.save(path)


def test_excel_to_standard_records(tmp_path):
    path=tmp_path/'fixture.xlsx';workbook(path)
    records,report=from_excel(path,['2026-09-10'])
    assert len(records)==32 and all(r['is_partial'] for r in records)
    assert len(report['warnings'])==2 and records[0]['total_in']==999


@pytest.mark.parametrize('bad',[-1,1.5,'=1+1','not-a-number'])
def test_invalid_excel_fails(tmp_path,bad):
    path=tmp_path/'fixture.xlsx';workbook(path,bad)
    with pytest.raises(DataError):from_excel(path)
