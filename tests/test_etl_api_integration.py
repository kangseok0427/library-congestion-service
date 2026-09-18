from openpyxl import Workbook
from fastapi.testclient import TestClient
import pytest

from backend.app import SQLiteProvider, create_app
from backend.service import LibraryService
from library_etl import DataError, refresh
from library_etl.pipeline import REQUIRED


def write_workbook(path, count=1, bad=False):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Data'
    sheet.append(REQUIRED)
    for gate, sensor in [('자료실.정문', 'front-sensor'), ('자료실.후문', 'back-sensor')]:
        row = {key: count for key in REQUIRED}
        row.update({'수집일자': '2026-09-10', '게이트': gate, '통로ID': sensor,
                    '전체_IN': count * 16, '전체_OUT': count * 16})
        if bad:
            row['IN_08'] = -1
        sheet.append([row[key] for key in REQUIRED])
    book.save(path)


def rebuild_summary(records):
    service = LibraryService(records)
    return {'record_count': len(service.records), 'hour_slots': len(service.rows)}


def test_t02_t08_refresh_rebuild_changes_api_and_rolls_back(tmp_path):
    source = tmp_path / 'visitors.xlsx'
    database = tmp_path / 'library.sqlite3'

    write_workbook(source, count=1)
    first = refresh(source, database, partial_dates=[], rebuild=rebuild_summary)
    assert first['contract_version'] == 'v1.1'
    assert first['integration_status'] == 'rebuilt'
    assert first['record_count'] == 32

    client = TestClient(create_app(SQLiteProvider(database)))
    before = client.get('/api/v1/stats?date=2026-09-10').json()
    assert before['total_in'] == 32
    assert before['hourly_total_in'] == 32
    assert next(row for row in before['hourly'] if row['hour'] == 11)['out_count'] is None

    write_workbook(source, count=3)
    second = refresh(source, database, partial_dates=[], rebuild=rebuild_summary)
    after = client.get('/api/v1/stats?date=2026-09-10').json()
    assert second['integration_status'] == 'rebuilt'
    assert after['total_in'] == 96
    assert after['hourly_total_in'] == 96

    write_workbook(source, count=9, bad=True)
    with pytest.raises(DataError):
        refresh(source, database, partial_dates=[], rebuild=rebuild_summary)
    preserved = client.get('/api/v1/stats?date=2026-09-10').json()
    assert preserved['total_in'] == 96
    assert preserved['hourly_total_in'] == 96
