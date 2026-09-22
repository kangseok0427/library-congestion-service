import copy
import importlib
import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import FileProvider, create_app
from backend.domain import DataError, aggregate, validate_records
from backend.prediction import backtest, forecast, percentile, recommendation
from backend.service import KST, LibraryService
from scripts.rebuild import rebuild
from scripts.sample import generate
from scripts.serve import bootstrap_records


@pytest.fixture
def records():
    return generate(end=date(2026,9,14),days=35)


@pytest.mark.parametrize('field,value',[('in_count',None),('in_count',-1),('out_count',1.5),('hour',True),('is_partial','false'),('day_of_week','Wrong'),('gate','other'),('total_in',True)])
def test_invalid_records(records,field,value):
    records[0][field]=value
    with pytest.raises(DataError):validate_records(records)


def test_duplicate_not_summed(records):
    with pytest.raises(DataError):validate_records(records+[records[0]])


def test_daily_total_not_multiplied(records):
    service=LibraryService(records)
    stats=service.stats('2026-09-11')
    expected=sum(r['total_in'] for r in records if r['date']=='2026-09-11' and r['hour']==8)
    assert stats['total_in']==expected
    assert stats['hourly_total_in']==sum(r['in_count'] for r in records if r['date']=='2026-09-11' and 9 <= r['hour'] < 21)


def test_recent_weekday_baseline_and_future_excluded(records):
    rows=aggregate(records);target=date(2026,9,15)
    expected=sum(r['visit_count'] for r in rows if r['hour']==10 and r['date'] in ['2026-08-18','2026-08-25','2026-09-01','2026-09-08'])/4
    p=next(p for p in forecast(rows,target) if p['hour']==10)
    assert p['expected_visitors']==expected and p['sample_count']==4
    future=dict(rows[-1],date='2026-09-15',hour=10,visit_count=999999)
    assert forecast(rows+[future],target)==forecast(rows,target)


def test_fallback_no_weekday_and_no_history():
    rows=aggregate(generate(end=date(2026,9,13),days=1))
    p=forecast(rows,date(2026,9,15))[0]
    assert p['method']=='same_hour_fallback' and p['baseline_avg'] is None
    assert forecast(rows,date(2027,1,1))[0]['expected_visitors'] is None


def test_partial_and_missing_gate_excluded():
    records=generate(end=date(2026,9,13),days=1)
    for r in records:r['is_partial']=True
    assert all(p['expected_visitors'] is None for p in forecast(aggregate(records),date(2026,9,15)))
    missing=[dict(r,is_partial=False) for r in records if r['gate']=='front']
    assert all(p['expected_visitors'] is None for p in forecast(aggregate(missing),date(2026,9,15)))


def test_zero_is_real_observation():
    rows=[dict(date='2026-09-08',hour=9,visit_count=0,is_partial=False)]
    p=forecast(rows,date(2026,9,15))[0]
    assert p['expected_visitors']==0 and p['level']=='normal'


def test_percentile_levels():
    assert [percentile(v,[10,20,30,40])[1] for v in [5,25,45]]==['quiet','normal','busy']


def test_recommendation_needs_consecutive_future_slots():
    rows=[dict(hour=h,expected_visitors=v) for h,v in [(8,1),(9,1),(10,None),(11,5),(12,3),(13,2)]]
    assert recommendation(rows,10)['best_start_hour']==12
    assert recommendation(rows,14)['best_start_hour'] is None


def test_backtest_no_target_leakage():
    rows=[dict(date=f'2026-09-{d:02}',hour=9,visit_count=v,is_partial=False) for d,v in [(1,10),(8,20),(15,100)]]
    result=backtest(rows,evaluation_days=1)
    assert result['mae']==85 and result['evaluated_slots']==1


def test_api_contract_and_errors(records,tmp_path):
    path=tmp_path/'records.json';rebuild(records,path)
    client=TestClient(create_app(FileProvider(path), clock=lambda: datetime(2026,9,11,12,tzinfo=KST)))
    data=client.get('/api/v1/congestion/today?date=2026-09-15').json()
    assert {'date','data_status','reference_time','congestion','recommendation','hourly','updated_at'}<=data.keys()
    assert len(data['hourly'])==12 and data['data_status']=='forecast'
    assert client.get('/api/v1/meta').json()['levels']['quiet']=='여유'
    for query,code in [('20260914','INVALID_DATE'),('2026-02-30','INVALID_DATE'),('2000-01-01','INVALID_DATE')]:
        response=client.get('/api/v1/stats?date='+query)
        assert response.status_code in (400,404) and response.json()['error']['code']==code
    assert client.get('/').status_code==200
    patterns=client.get('/api/v1/patterns').json()
    assert patterns['monthly'] and patterns['hourly'] and patterns['weekday_hourly']
    assert patterns['weekday'][0]['day_of_week'] in {'Mon','Tue','Wed','Thu','Fri','Sat','Sun'}
    assert client.get('/api/v1/health').json()=={'status':'ok'}


def test_t03_patterns_use_complete_records_only(records):
    for row in records:
        if row['date']=='2026-09-11':
            row['is_partial']=True
    patterns=LibraryService(records).patterns()
    assert '2026-09-11' not in {row['date'] for row in patterns['daily']}
    assert len(patterns['hourly'])==12
    assert len(patterns['weekday_hourly'])==4*12+2*8
    assert {row['day_of_week'] for row in patterns['weekday']}=={'Tue','Wed','Thu','Fri','Sat','Sun'}


def test_atomic_update_changes_api_and_failure_preserves_data(records,tmp_path):
    path=tmp_path/'records.json';rebuild(records,path)
    provider=FileProvider(path);client=TestClient(create_app(provider, clock=lambda: datetime(2026,9,11,12,tzinfo=KST)))
    before=client.get('/api/v1/stats?date=2026-09-11').json()
    modified=copy.deepcopy(records)
    for r in modified:
        if r['date']=='2026-09-11':r['in_count']+=10;r['total_in']+=160
    rebuild(modified,path)
    after=client.get('/api/v1/stats?date=2026-09-11').json()
    assert after['total_in']==before['total_in']+320
    saved=path.read_bytes();modified[0]['in_count']=None
    with pytest.raises(DataError):rebuild(modified,path)
    assert path.read_bytes()==saved
    assert client.get('/api/v1/stats?date=2026-09-11').json()==after


def test_partial_label_and_source_mismatch(records):
    for r in records:
        if r['date']=='2026-09-11':r['is_partial']=True;r['total_in']+=1
    stats=LibraryService(records).stats('2026-09-11')
    assert stats['data_status']=='partial'
    assert stats['total_in']==sum(r['total_in'] for r in records if r['date']=='2026-09-11' and r['hour']==8)
    assert stats['hourly_total_in']==sum(r['in_count'] for r in records if r['date']=='2026-09-11' and 9 <= r['hour'] < 21)


def test_recommendation_after_day_end(records):
    response=LibraryService(records).today(datetime(2026,9,15,23,30,tzinfo=KST))
    assert response['recommendation']['best_start_hour'] is None


def test_health_fails_when_snapshot_is_missing(tmp_path):
    client = TestClient(create_app(FileProvider(tmp_path / 'missing.json')))
    response = client.get('/api/v1/health')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'DATA_NOT_FOUND'


def test_persistent_storage_bootstrap_requires_opt_in_and_preserves_existing(tmp_path, monkeypatch):
    destination = tmp_path / 'volume' / 'records.json'
    monkeypatch.setenv('LIBRARY_RECORDS', str(destination))
    monkeypatch.delenv('LIBRARY_BOOTSTRAP_SAMPLE', raising=False)
    with pytest.raises(RuntimeError):
        bootstrap_records()
    monkeypatch.setenv('LIBRARY_BOOTSTRAP_SAMPLE', 'true')
    assert bootstrap_records() == destination
    first = destination.read_bytes()
    destination.write_text('{"preserved": true}', encoding='utf-8')
    assert bootstrap_records() == destination
    assert destination.read_text(encoding='utf-8') == '{"preserved": true}'


def test_pythonanywhere_entrypoint_bootstraps_configured_records(tmp_path, monkeypatch):
    destination = tmp_path / 'pythonanywhere-data' / 'records.json'
    monkeypatch.setenv('LIBRARY_RECORDS', str(destination))
    monkeypatch.setenv('LIBRARY_BOOTSTRAP_SAMPLE', 'true')

    module = importlib.import_module('pythonanywhere_asgi')
    importlib.reload(module)

    assert destination.exists()
    assert module.app.title == 'Library congestion v1'
