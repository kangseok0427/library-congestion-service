"""Browser verification of records -> API -> DOM, refresh, mobile, errors."""
import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect
from openpyxl import Workbook
from backend.adapters import read_records
from backend.domain import DataError
from library_etl import refresh
from scripts.rebuild import rebuild
from scripts.sample import generate


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--records');parser.add_argument('--date',default='2026-09-14');args=parser.parse_args()
    records=read_records(args.records) if args.records else generate()
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'records.json';service=rebuild(records,path)
        expected=service.stats(args.date)['total_in']
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',str(port)],
                                 env={**os.environ,'LIBRARY_RECORDS':str(path)},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    with urlopen(url+'/api/v1/meta',timeout=1):break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch()
                page=browser.new_page(viewport={'width':1280,'height':900})
                page.goto(url)
                page.locator('#date').fill(args.date);page.get_by_role('button',name='조회',exact=True).click()
                expect(page.locator('#actual')).to_contain_text(f'IN {expected}명')
                expect(page.locator('#hourly tr')).to_have_count(16)
                assert page.locator('#chart .bar').count()==16
                before=page.locator('#hourly').inner_text()
                changed=copy.deepcopy(records)
                for row in changed:
                    row['in_count']+=100;row['total_in']+=1600
                rebuild(changed,path)
                page.get_by_role('button',name='새로고침').click()
                expect(page.locator('#actual')).to_contain_text(f'IN {expected+3200}명')
                assert before!=page.locator('#hourly').inner_text()
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
                    expect(page.locator('#actual')).to_contain_text('IN 1554명')
                    assert page.request.get(url+'/api/v1/stats?date='+args.date).json()['hourly'][3]['out_count'] is None
                    saved=path.read_bytes();sheet.cell(2,6).value='=1+1';book.save(source);book.close()
                    try:refresh(source,path,partial_dates=[])
                    except DataError:pass
                    else:raise AssertionError('Invalid workbook was accepted')
                    assert path.read_bytes()==saved
                    page.get_by_role('button',name='새로고침').click()
                    expect(page.locator('#actual')).to_contain_text('IN 1554명')
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
                expect(page.locator('#hourly tr')).to_have_count(0)
                browser.close()
            print(json.dumps({'e2e':'PASS','input':'real_records' if args.records else 'synthetic',
                              'checks':['API to DOM','16 forecast rows and bars','record replacement updates actual and forecast',
                                        '390px no page overflow','invalid replacement error and stale number removal']+
                                       (['synthetic XLSX refresh updates DOM','OUT_11 remains null',
                                         'invalid XLSX preserves JSON and DOM'] if not args.records else [])},ensure_ascii=False))
        finally:
            process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
