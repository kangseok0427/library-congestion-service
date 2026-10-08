"""Browser verification of records -> API -> DOM, refresh, mobile, errors.

ADE-49: the visitor page shows only bars and a guidance message, so DOM checks
compare each bar (hour, level, estimated_present data attribute) with the API
response instead of reading numbers from a table or an actual-count card.
"""
import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect
from openpyxl import Workbook
from backend.adapters import read_records
from backend.domain import DataError
from backend.library_hours import DEFAULT_HOURS
from library_etl import refresh
from scripts.rebuild import rebuild
from scripts.sample import generate


def bar_state(page):
    return page.locator('#chart .column').evaluate_all(
        'els => els.map(e => [Number(e.dataset.hour), e.dataset.level, e.dataset.present])')


def api_bars(page, url, day):
    data = page.request.get(f'{url}/api/v1/congestion/today?date={day}').json()
    return [[h.get('start_hour', h.get('hour')),
             (h['level'] or 'none') if h['estimated_present'] is not None else 'none',
             '' if h['estimated_present'] is None else str(h['estimated_present'])] for h in data['hourly']]


def expect_bars_match_api(page, url, day):
    expected = api_bars(page, url, day)
    expect(page.locator('#chart .column')).to_have_count(len(expected))
    for _ in range(50):
        if bar_state(page) == expected:
            return expected
        time.sleep(.1)
    raise AssertionError((bar_state(page), expected))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--records');parser.add_argument('--date',default='2026-09-22');args=parser.parse_args()
    day = date.fromisoformat(args.date)
    records=read_records(args.records) if args.records else (
        generate(end=day) + generate(end=day.replace(year=2023)) + generate(end=day.replace(year=2022)))
    Path('test-results').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir='test-results') as directory:
        path=Path(directory)/'records.json';service=rebuild(records,path)
        expected=service.stats(args.date)['hourly_total_in']
        count=len(DEFAULT_HOURS.hours(date.fromisoformat(args.date)))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        process=subprocess.Popen([sys.executable,'-m','uvicorn','tests.browser_app:app','--host','127.0.0.1','--port',str(port)],
                                 env={**os.environ,'LIBRARY_RECORDS':str(path.resolve()),'E2E_DATE':args.date},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    with urlopen(url+'/api/v1/meta',timeout=1):break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch()
                page=browser.new_page(viewport={'width':1280,'height':900}, timezone_id='America/Los_Angeles')
                page.clock.install(time=datetime.fromisoformat(args.date+'T08:00:00+09:00'))
                page.goto(url)
                page.locator('#date').fill(args.date);page.get_by_role('button',name='조회',exact=True).click()
                expect(page.locator('#guide')).to_have_attribute('data-state', 'open')
                first_bars = expect_bars_match_api(page, url, args.date)
                assert len(first_bars) == count
                assert page.request.get(url+'/api/v1/stats?date='+args.date).json()['hourly_total_in'] == expected
                # ADE-49: no detailed table, no actual-count card, no number-centred text.
                assert page.locator('table').count() == 0 and page.locator('#actual').count() == 0
                assert '명' not in page.locator('#hours').inner_text()
                changed=copy.deepcopy(records)
                for row in changed:
                    row['in_count']+=100;row['total_in']+=1600
                rebuild(changed,path)
                page.get_by_role('button',name='새로고침').click()
                assert page.request.get(url+'/api/v1/stats?date='+args.date).json()['hourly_total_in'] == expected+count*200
                assert expect_bars_match_api(page, url, args.date) != first_bars
                # Exercise T02/T08 with a real synthetic XLSX, not only JSON.
                if not args.records:
                    book=Workbook();sheet=book.active
                    sheet.append(['수집일자','게이트','통로ID','전체_IN','전체_OUT']+
                                 [f'{kind}_{hour:02}' for hour in range(8,24) for kind in ('IN','OUT')])
                    for gate in ('자료실.정문','자료실.후문'):
                        sheet.append([args.date,gate,'synthetic-browser',777,555]+[7]*32)
                    source=Path(directory)/'refresh.xlsx';book.save(source)
                    refresh(source,path,partial_dates=[])
                    page.get_by_role('button',name='새로고침').click()
                    assert page.request.get(url+'/api/v1/stats?date='+args.date).json()['hourly_total_in'] == count*14
                    assert page.request.get(url+'/api/v1/stats?date='+args.date).json()['hourly'][2]['out_count'] == 14
                    refreshed=expect_bars_match_api(page, url, args.date)
                    saved=path.read_bytes();sheet.cell(2,6).value='=1+1';book.save(source);book.close()
                    try:refresh(source,path,partial_dates=[])
                    except DataError:pass
                    else:raise AssertionError('Invalid workbook was accepted')
                    assert path.read_bytes()==saved
                    page.get_by_role('button',name='새로고침').click()
                    assert expect_bars_match_api(page, url, args.date) == refreshed
                # All 8 dates, real server rules, independent of browser timezone.
                first=date.fromisoformat(args.date)
                expect(page.locator('#date')).to_have_attribute('min',args.date)
                expect(page.locator('#date')).to_have_attribute('max',(first+timedelta(days=7)).isoformat())
                for offset in range(8):
                    target=first+timedelta(days=offset)
                    day=target.isoformat()
                    page.locator('#date').fill(day)
                    page.get_by_role('button',name='조회',exact=True).click()
                    if DEFAULT_HOURS.is_closed(target):
                        expect(page.locator('#guide')).to_have_attribute('data-state','closed')
                        expect(page.locator('#guide-title')).to_have_text('휴관일')
                        expect(page.locator('#hours')).to_be_hidden()
                        expect(page.locator('#chart .column')).to_have_count(0)
                    else:
                        expect(page.locator('#hours')).to_be_visible()
                        expect(page.locator('#chart .column')).to_have_count(len(DEFAULT_HOURS.hours(target)))
                        expect(page.locator('#error')).to_be_hidden()
                for offset in (-1,8):
                    bad=(first+timedelta(days=offset)).isoformat()
                    page.locator('#date').fill(bad)
                    assert not page.locator('#date').evaluate('(el) => el.checkValidity()')
                    page.get_by_role('button',name='새로고침').click()
                    expect(page.locator('#error')).to_contain_text('오늘부터 7일 후')
                    expect(page.locator('#chart .column')).to_have_count(0)
                    assert page.request.get(url+'/api/v1/congestion/today?date='+bad).status==400
                    assert page.request.get(url+'/api/v1/stats?date='+bad).status==400
                page.locator('#date').fill(args.date)
                page.get_by_role('button',name='새로고침').click()
                expect(page.locator('#chart .column')).to_have_count(count)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                Path('test-results').mkdir(exist_ok=True)
                page.screenshot(path='test-results/mobile.png',full_page=True)
                page.set_viewport_size({'width':1280,'height':900})
                page.screenshot(path='test-results/desktop.png',full_page=True)
                # Broken source surfaces an error; previous numbers are removed.
                path.write_text('{broken',encoding='utf-8')
                page.get_by_role('button',name='새로고침').click()
                expect(page.locator('#error')).to_be_visible()
                expect(page.locator('#chart .column')).to_have_count(0)
                browser.close()
            print(json.dumps({'e2e':'PASS','input':'real_records' if args.records else 'synthetic',
                              'checks':['API to DOM (each bar hour, level, estimated_present)','same calendar date statistics across prior years','no table, actual card or 명 text (ADE-49)','8 KST dates including closed and pending states','past and +8 rejected in UI and API','America/Los_Angeles browser timezone','record replacement updates stats API and bars',
                                        '390px no page overflow','invalid replacement error and stale bar removal']+
                                       (['synthetic XLSX refresh updates API and bars','OUT_11 source value preserved',
                                         'invalid XLSX preserves JSON and bars'] if not args.records else [])},ensure_ascii=False))
        finally:
            process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
