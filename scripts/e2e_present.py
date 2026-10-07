"""ADE-41/ADE-49 browser check: estimated_present responses -> bars, colors, detail, guidance.

Steps 1-5 replace /congestion/today with fixtures captured from the original ADE-40
code (scripts/capture_ade40_fixture.py); step 6 uses the team backend as is.
All inputs are synthetic; this is not a real-data check.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
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


def expected_text(h):
    """What the bar's aria-label and tap detail say. Numbers are never shown (ADE-49)."""
    if h['estimated_present'] is None:
        reason = QUALITY.get(h['quality_status'])
        return (f'자료 부족 ({reason})' if reason else '자료 부족'), 'none'
    if not h['level']:
        return '판정 불가', 'none'
    return h.get('label') or LABEL[h['level']], h['level']


def check_hours(page, data):
    hourly = data['hourly']
    expect(page.locator('#hours')).to_be_visible()
    expect(page.locator('#hours-title')).to_have_text('시간대별 혼잡도')
    expect(page.locator('#basis')).to_contain_text('추정한 체류 인원')
    expect(page.locator('#basis')).to_contain_text('정확한 실시간 인원이나 좌석 점유율이 아닙니다')
    expect(page.locator('#chart .column')).to_have_count(len(hourly))
    expect(page.locator('#guide-message')).to_have_text(data['recommendation']['message'])
    # ADE-49: no detailed table and no number-centred text in the hours card.
    assert page.locator('table').count() == 0
    assert '명' not in page.locator('#hours').inner_text()
    top = max([1] + [h['estimated_present'] or 0 for h in hourly])
    current = int(data['reference_time'][11:13]) if data.get('reference_time', '')[:10] == data['date'] else None
    for i, h in enumerate(hourly):
        text, cls = expected_text(h)
        col = page.locator('#chart .column').nth(i)
        bar = col.locator('.bar')
        assert bar.get_attribute('class') == f'bar {cls}', (h, bar.get_attribute('class'))
        assert col.get_attribute('data-level') == cls
        height = bar.evaluate('el => el.style.height')
        if h['estimated_present'] is None:
            assert height == '8px', (h, height)
        else:
            assert height.endswith('%') and abs(float(height[:-1]) - max(4, h['estimated_present'] / top * 100)) < 1e-3, (h, height)
        time_label = f"{h['hour']}~{h['hour'] + 1}시"
        suffix = ', 지금' if h['hour'] == current else ''
        assert col.get_attribute('aria-label') == f'{time_label} {text}{suffix}', (col.get_attribute('aria-label'), text)
        col.click()
        expect(page.locator('#detail')).to_have_text(f'{time_label} {text}')


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
            first = page.locator('#chart .column').nth(0)
            expect(first).to_have_attribute('aria-label', '9~10시 여유')
            expect(first.locator('.bar')).to_have_class('bar quiet')
            third = page.locator('#chart .column').nth(2)
            expect(third).to_have_attribute('aria-label', '11~12시 자료 부족 (OUT 결측)')
            expect(third.locator('.bar')).to_have_class('bar none')
            expect(page.locator('.legend li')).to_have_text(['여유', '보통', '혼잡', '자료 부족'])
            expect(page.locator('#guide')).to_have_attribute('data-state', 'open')
            checks += ['bar height = estimated_present / max', 'bar color = API level', 'bar aria-label and detail share one text',
                       '0 is a short 여유 bar; null is a grey 자료 부족 bar with its reason', 'legend has 자료 부족',
                       'guidance message = recommendation.message', 'no table, no 명 in hours card']

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
            expect(page.locator('#chart .column')).to_have_count(0)
            expect(page.locator('#hours')).to_be_hidden(); expect(page.locator('#guide')).to_be_hidden()
            checks.append('API failure removes stale bars and guidance')

            # 4) response for another date is not shown as the selected date
            state.update(body=load('partial'), status=200)
            page.locator('#date').fill('2026-09-23'); page.get_by_role('button', name='조회', exact=True).click()
            expect(page.locator('#error')).to_contain_text('선택한 날짜의 예측을 받지 못했습니다')
            expect(page.locator('#chart .column')).to_have_count(0)
            checks.append('date mismatch is an error, not stale data')

            # 5) mobile layout with ADE-40 data
            page.locator('#date').fill(DAY); page.get_by_role('button', name='조회', exact=True).click()
            expect(page.locator('#chart .column')).to_have_count(len(partial['hourly']))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path='test-results/ade41-mobile.png', full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.screenshot(path='test-results/ade41-desktop.png', full_page=True)
            checks.append('390px no page overflow')

            # 6) the real team backend (ADE-40 ported) on all 8 KST dates, no replaced responses
            page.unroute('**/api/v1/congestion/today*')
            first = date.fromisoformat(DAY)
            for offset in range(8):
                day = (first + timedelta(days=offset)).isoformat()
                data = page.request.get(f'{url}/api/v1/congestion/today?date={day}').json()
                page.locator('#date').fill(day); page.get_by_role('button', name='조회', exact=True).click()
                expect(page.locator('#guide-when')).to_contain_text(f'{int(day[5:7])}월 {int(day[8:])}일')
                if data['data_status'] == 'closed':
                    expect(page.locator('#guide-title')).to_have_text('휴관일')
                    expect(page.locator('#hours')).to_be_hidden()
                    expect(page.locator('#chart .column')).to_have_count(0)
                else:
                    check_hours(page, data)
                    assert any(h['level'] for h in data['hourly'])
            checks.append('team backend estimated_present shown on 8 KST dates incl. closed days')

            # 7) a response without estimated_present keeps 예상 방문량 wording (no relabelling)
            def legacy(route):
                body = route.fetch().json()
                for h in body['hourly']:
                    h.pop('estimated_present'); h.pop('calculation_basis'); h.pop('quality_status')
                route.fulfill(status=200, content_type='application/json', body=json.dumps(body, ensure_ascii=False))
            page.route('**/api/v1/congestion/today*', legacy)
            page.locator('#date').fill(DAY); page.get_by_role('button', name='조회', exact=True).click()
            expect(page.locator('#basis')).to_contain_text('예상 방문량')
            assert '추정 체류 인원' not in page.locator('.hours').inner_text()
            checks.append('legacy response is not relabelled as 추정 체류 인원')

            # 8) ADE-43 v2 contract fixtures: open, insufficient, closed (ADE-49 states)
            page.unroute('**/api/v1/congestion/today*')
            contract = {'body': None}
            page.route('**/api/v1/congestion/today*', lambda route: route.fulfill(
                status=200, content_type='application/json', body=json.dumps(contract['body'], ensure_ascii=False)))
            for name, state in (('open', 'open'), ('insufficient', 'insufficient'), ('closed', 'closed')):
                body = json.loads(Path(f'contracts/fixtures/congestion-{name}.json').read_text(encoding='utf-8'))
                body['date'] = DAY  # fixtures are dated 10/2~10/5; keep inside the browser date window
                contract['body'] = body
                page.locator('#date').fill(DAY); page.get_by_role('button', name='조회', exact=True).click()
                expect(page.locator('#guide')).to_have_attribute('data-state', state)
                expect(page.locator('#guide-message')).to_have_text(body['recommendation']['message'])
                if state == 'closed':
                    expect(page.locator('#guide-title')).to_have_text('휴관일')
                    expect(page.locator('#hours')).to_be_hidden()
                else:
                    expect(page.locator('#chart .column')).to_have_count(len(body['hourly']))
                    levels = page.locator('#chart .column').evaluate_all('els => els.map(e => e.dataset.level)')
                    assert levels == [h['level'] or 'none' for h in body['hourly']], levels
                if state == 'insufficient':
                    expect(page.locator('#guide-title')).to_have_text('자료 부족')
                    assert all(page.locator('#chart .bar').evaluate_all('els => els.map(e => e.className)')[i] == 'bar none'
                               for i in range(len(body['hourly'])))
            checks.append('ADE-43 v2 fixtures: open bars, insufficient grey + 자료 부족, closed hides bars')
            browser.close()
        print(json.dumps({'e2e_present': 'PASS', 'input': 'ADE-40 fixtures + team backend (synthetic records)', 'checks': checks}, ensure_ascii=False))
    finally:
        process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    main()
