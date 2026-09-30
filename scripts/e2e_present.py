"""ADE-41 browser check: ADE-40 estimated_present responses -> bars, colors, table, detail.

/congestion/today is replaced with fixtures captured from ADE-40's real code
(scripts/capture_ade40_fixture.py, synthetic records). Everything else comes from
the team test server. The fixtures are test-only; this is not a real-data check.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

from scripts.rebuild import rebuild
from scripts.sample import generate

DAY = '2026-09-22'
LABEL = {'quiet': '여유', 'normal': '보통', 'busy': '혼잡'}
QUALITY = {'missing_out': 'OUT 결측', 'partial': '부분 수집', 'missing_gate': '출입구 누락', 'negative_balance': '누적 음수',
           'insufficient_samples': '표본 부족'}
FIXTURES = Path('tests/fixtures')


def load(name):
    return json.loads((FIXTURES / f'ade40_today_{name}.json').read_text(encoding='utf-8'))


def expected_cells(h):
    v = h['estimated_present']
    if v is None:
        reason = QUALITY.get(h['quality_status'])
        return (f'추정 불가 ({reason})' if reason else '자료 부족'), '자료 부족', 'none'
    return f'약 {v}명', LABEL.get(h['level'], '판정 불가'), h['level'] or 'none'


def check_hours(page, data):
    hourly = data['hourly']
    expect(page.locator('#hours-title')).to_have_text('시간대별 추정 체류 인원')
    expect(page.locator('#metric-col')).to_have_text('추정 체류 인원')
    expect(page.locator('#chart')).to_have_attribute('aria-label', '시간대별 추정 체류 인원 그래프')
    expect(page.locator('#basis')).to_contain_text('정확한 실시간 인원이나 좌석 점유율이 아닙니다')
    expect(page.locator('#hourly tr')).to_have_count(len(hourly))
    top = max([1] + [h['estimated_present'] or 0 for h in hourly])
    for i, h in enumerate(hourly):
        value, label, cls = expected_cells(h)
        cells = page.locator('#hourly tr').nth(i).locator('td')
        expect(cells.nth(1)).to_have_text(value)
        expect(cells.nth(2)).to_have_text(label)
        col = page.locator('#chart .column').nth(i)
        bar = col.locator('.bar')
        assert bar.get_attribute('class') == f'bar {cls}', (h, bar.get_attribute('class'))
        height = bar.evaluate('el => el.style.height')
        if h['estimated_present'] is None:
            assert height == '3px', (h, height)
        else:
            assert height.endswith('%') and abs(float(height[:-1]) - max(4, h['estimated_present'] / top * 100)) < 1e-3, (h, height)
        assert col.get_attribute('aria-label') == f"{h['hour']}~{h['hour'] + 1}시 추정 체류 인원 {value} {label}"
        col.click()
        expect(page.locator('#detail')).to_contain_text(f"{h['hour']}~{h['hour'] + 1}시 · 추정 체류 인원 {value} · {label}")


def main():
    Path('test-results').mkdir(exist_ok=True)
    path = Path('test-results/e2e_present_records.json')
    rebuild(generate(end=date.fromisoformat(DAY)), path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tests.browser_app:app', '--host', '127.0.0.1', '--port', str(port)],
                               env={**os.environ, 'LIBRARY_RECORDS': str(path.resolve()), 'E2E_DATE': DAY},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    checks = []
    try:
        for _ in range(100):
            try:
                with urlopen(url + '/api/v1/meta', timeout=1): break
            except OSError: time.sleep(.1)
        else: raise RuntimeError('Server did not start')
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900}, timezone_id='America/Los_Angeles')
            page.clock.install(time=datetime.fromisoformat(DAY + 'T13:00:00+09:00'))
            state = {'body': load('partial'), 'status': 200}

            def today(route):
                route.fulfill(status=state['status'], content_type='application/json',
                              body=json.dumps(state['body'], ensure_ascii=False))
            page.route('**/api/v1/congestion/today*', today)

            # 1) partial day: 0 (여유), 30 (혼잡), OUT 결측, 자료 부족
            partial = load('partial')
            page.goto(url)
            check_hours(page, partial)
            first = page.locator('#hourly tr').nth(0).locator('td')
            expect(first.nth(1)).to_have_text('약 0명'); expect(first.nth(2)).to_have_text('여유')
            third = page.locator('#hourly tr').nth(2).locator('td')
            expect(third.nth(1)).to_have_text('추정 불가 (OUT 결측)'); expect(third.nth(2)).to_have_text('자료 부족')
            expect(page.locator('.legend li')).to_have_text(['여유', '보통', '혼잡', '자료 부족'])
            expect(page.locator('#best')).to_have_text(f"{partial['recommendation']['best_start_hour']}시 – {partial['recommendation']['best_end_hour']}시")
            checks += ['bar height = estimated_present / max', 'bar color = API level', 'table, bar aria-label, detail share one value',
                       '0 shown as 약 0명 여유; null shown as 추정 불가/자료 부족 in neutral color', 'legend has 자료 부족']

            # 2) forecast day
            state['body'] = load('forecast')
            page.get_by_role('button', name='새로고침').click()
            check_hours(page, load('forecast'))
            checks.append('refresh replaces all hourly values')
            # the bars must follow estimated_present even if the legacy field disagrees (test-only change)
            skewed = load('forecast')
            for h in skewed['hourly']: h['expected_visitors'] = 999 - h['hour'] * 50
            state['body'] = skewed
            page.get_by_role('button', name='새로고침').click()
            check_hours(page, skewed)
            checks.append('estimated_present is used, not expected_visitors')

            # 3) API failure clears old numbers
            state.update(body={'error': {'code': 'PROCESSING_ERROR', 'message': '서버 오류'}}, status=500)
            page.get_by_role('button', name='새로고침').click()
            expect(page.locator('#error')).to_be_visible()
            expect(page.locator('#hourly tr')).to_have_count(0); expect(page.locator('#chart .column')).to_have_count(0)
            expect(page.locator('#basis')).to_contain_text('좌석 점유율이 아닙니다')
            checks.append('API failure removes stale bars and rows')

            # 4) response for another date is not shown as the selected date
            state.update(body=load('partial'), status=200)
            page.locator('#date').fill('2026-09-23'); page.get_by_role('button', name='조회', exact=True).click()
            expect(page.locator('#error')).to_contain_text('선택한 날짜의 예측을 받지 못했습니다')
            expect(page.locator('#hourly tr')).to_have_count(0)
            checks.append('date mismatch is an error, not stale data')

            # 5) mobile layout with ADE-40 data
            page.locator('#date').fill(DAY); page.get_by_role('button', name='조회', exact=True).click()
            expect(page.locator('#hourly tr')).to_have_count(len(partial['hourly']))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path='test-results/ade41-mobile.png', full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.screenshot(path='test-results/ade41-desktop.png', full_page=True)
            checks.append('390px no page overflow')

            # 6) current team backend (no estimated_present) keeps 예상 방문량 wording
            page.unroute('**/api/v1/congestion/today*')
            page.get_by_role('button', name='새로고침').click()
            expect(page.locator('#hours-title')).to_have_text('시간대별 예상 방문량')
            expect(page.locator('#metric-col')).to_have_text('예상 방문량')
            expect(page.locator('#hourly tr').first).not_to_contain_text('추정 불가')
            assert '추정 체류 인원' not in page.locator('.hours').inner_text()
            checks.append('legacy API is not relabelled as 추정 체류 인원')
            browser.close()
        print(json.dumps({'e2e_present': 'PASS', 'input': 'ADE-40 fixtures (synthetic records)', 'checks': checks}, ensure_ascii=False))
    finally:
        process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    main()
