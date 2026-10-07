"""ADE-47 browser verification of the admin page.

1. Mock mode: every UI state is driven by contracts/fixtures (ADE-43).
2. HTTP mode: the same page calls the frozen /api/v1/admin/* paths with the
   contract request shapes. Responses are fulfilled from the same fixtures, so
   the check does not depend on the ADE-46 server being finished.

Run from the repository root:  python -m scripts.e2e_admin
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'contracts' / 'fixtures'
RESULTS = ROOT / 'test-results'


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def xlsx(name, size=64):
    return {'name': name, 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'buffer': b'0' * size}


def no_page_overflow(page):
    return page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def mock_flow(page, url):
    page.goto(url + '/frontend/admin.html?mock')
    expect(page.locator('#mock-banner')).to_be_visible()
    expect(page.get_by_role('heading', name='관리자 로그인')).to_be_visible()
    # Before login only the login view is on screen.
    expect(page.locator('#console-view')).to_be_hidden()
    expect(page.get_by_role('button', name='로그아웃')).to_be_hidden()
    # The replacement admin page never asks for an upload token.
    assert page.locator('input[name*="token" i], input[id*="token" i]').count() == 0

    # Login failure -> INVALID_CREDENTIALS fixture, Korean guidance, password cleared.
    page.get_by_label('아이디').fill('admin')
    page.get_by_label('비밀번호').fill('wrong')
    page.get_by_role('button', name='로그인').click()
    expect(page.locator('#login-error')).to_have_text('아이디 또는 비밀번호가 맞지 않습니다. 다시 입력하세요.')
    expect(page.get_by_label('비밀번호')).to_have_value('')

    # Login success -> four retained versions, newest active.
    page.get_by_label('비밀번호').fill('pw')
    page.get_by_role('button', name='로그인').click()
    expect(page.get_by_role('heading', name='출입 기록 Excel 게시')).to_be_visible()
    expect(page.locator('#login-view')).to_be_hidden()
    slots = page.locator('#version-list .slot:not(.empty)')
    expect(slots).to_have_count(4)
    initial_ids = [v['id'] for v in fixture('admin-versions-four.json')['versions']]
    assert slots.evaluate_all('els => els.map(e => e.dataset.versionId)') == initial_ids
    expect(slots.nth(0)).to_have_class('slot active')
    expect(slots.nth(0)).to_contain_text('사용 중')
    expect(slots.nth(3)).to_contain_text('다음 게시 때 삭제')
    expect(page.get_by_role('button', name='검증 후 게시')).to_be_disabled()

    # Client-side rejection: wrong extension and >10 MB never reach the API.
    page.locator('#file').set_input_files(xlsx('records.csv'))
    expect(page.locator('#upload-status')).to_contain_text('.xlsx 형식의 Excel 파일만')
    expect(page.get_by_role('button', name='검증 후 게시')).to_be_disabled()
    page.locator('#file').set_input_files(xlsx('records-big.xlsx', 10 * 1024 * 1024 + 1))
    expect(page.locator('#upload-status')).to_contain_text('10 MB 이하만')
    expect(page.get_by_role('button', name='검증 후 게시')).to_be_disabled()

    # 422 INVALID_EXCEL -> details rendered, version list untouched.
    page.locator('#file').set_input_files(xlsx('records-invalid.xlsx'))
    page.get_by_role('button', name='검증 후 게시').click()
    expect(page.locator('#upload-status')).to_contain_text('검증하고 게시하는 중')
    expect(page.locator('#file')).to_be_disabled()
    expect(page.locator('#upload-status')).to_contain_text('Excel 검증에 실패해 게시하지 않았습니다')
    expect(page.locator('#upload-status li')).to_have_text(['12행 IN_09: 숫자가 아닌 값입니다.'])
    assert slots.evaluate_all('els => els.map(e => e.dataset.versionId)') == initial_ids
    RESULTS.mkdir(exist_ok=True)
    page.screenshot(path=str(RESULTS / 'admin-invalid-desktop.png'), full_page=True)

    # 409 PUBLISH_IN_PROGRESS.
    page.locator('#file').set_input_files(xlsx('records-busy.xlsx'))
    page.get_by_role('button', name='검증 후 게시').click()
    expect(page.locator('#upload-status')).to_contain_text('다른 게시나 되돌리기 작업이 진행 중입니다')

    # Success -> new newest active version, oldest rotated out, still four.
    page.locator('#file').set_input_files(xlsx('records-1003.xlsx'))
    page.get_by_role('button', name='검증 후 게시').click()
    expect(page.locator('#upload-status')).to_contain_text('게시했습니다. records-1003.xlsx의 29,376건')
    expect(slots).to_have_count(4)
    expect(slots.nth(0)).to_contain_text('records-1003.xlsx')
    expect(slots.nth(0)).to_have_class('slot active')
    expect(page.locator('#version-list')).not_to_contain_text('records-0929.xlsx')
    expect(page.get_by_role('button', name='검증 후 게시')).to_be_disabled()

    # Rollback: cancel changes nothing, confirm moves the active marker.
    target = slots.nth(1)
    target_id = target.get_attribute('data-version-id')
    target.get_by_role('button').click()
    expect(page.locator('#rollback-dialog')).to_be_visible()
    expect(page.locator('#rollback-summary')).to_contain_text('records-1002.xlsx')
    page.get_by_role('button', name='취소').click()
    expect(slots.nth(0)).to_have_class('slot active')
    target.get_by_role('button').click()
    page.get_by_role('button', name='되돌리기', exact=True).click()
    expect(page.locator('#versions-status')).to_contain_text('되돌렸습니다')
    expect(page.locator(f'[data-version-id="{target_id}"]')).to_have_class('slot active')
    expect(page.locator('#version-list .slot.active')).to_have_count(1)
    page.screenshot(path=str(RESULTS / 'admin-desktop.png'), full_page=True)

    page.set_viewport_size({'width': 390, 'height': 844})
    assert no_page_overflow(page), 'admin console overflows at 390px'
    page.screenshot(path=str(RESULTS / 'admin-mobile.png'), full_page=True)
    page.set_viewport_size({'width': 1280, 'height': 900})

    # Expired session on any admin call -> back to login with guidance.
    page.locator('#file').set_input_files(xlsx('records-expired.xlsx'))
    page.get_by_role('button', name='검증 후 게시').click()
    expect(page.get_by_role('heading', name='관리자 로그인')).to_be_visible()
    expect(page.locator('#login-error')).to_have_text('로그인이 만료되었습니다. 다시 로그인하세요.')
    expect(page.locator('#console-view')).to_be_hidden()

    # Logout.
    page.get_by_label('비밀번호').fill('pw')
    page.get_by_role('button', name='로그인').click()
    page.get_by_role('button', name='로그아웃').click()
    expect(page.locator('#login-error')).to_have_text('로그아웃했습니다.')


def http_flow(page, url):
    """Production transport: frozen paths, methods, bodies, cookie credentials."""
    calls = []
    state = {'authenticated': False}

    def reply(route, status, body=None):
        if body is None:
            route.fulfill(status=status)
        else:
            route.fulfill(status=status, content_type='application/json',
                          body=json.dumps(body, ensure_ascii=False))

    def handle(route, request):
        path = request.url.split('://', 1)[1].split('/', 1)[1]
        path = '/' + path
        calls.append((request.method, path, request.headers.get('content-type', ''), request.post_data_buffer))
        if path == '/api/v1/admin/session' and request.method == 'GET':
            return reply(route, 200, fixture('admin-session-success.json') if state['authenticated']
                         else {'authenticated': False})
        if path == '/api/v1/admin/session' and request.method == 'POST':
            body = json.loads(request.post_data)
            if body['password'] == 'wrong':
                return reply(route, 401, fixture('admin-session-invalid.json'))
            state['authenticated'] = True
            return reply(route, 200, fixture('admin-session-success.json'))
        if path == '/api/v1/admin/session' and request.method == 'DELETE':
            state['authenticated'] = False
            return reply(route, 204)
        if path == '/api/v1/admin/versions' and request.method == 'GET':
            return reply(route, 200, fixture('admin-versions-four.json'))
        if path == '/api/v1/admin/uploads' and request.method == 'POST':
            return reply(route, 422, fixture('admin-upload-invalid.json'))
        if path.startswith('/api/v1/admin/versions/') and path.endswith('/rollback') and request.method == 'POST':
            return reply(route, 200, fixture('admin-rollback-success.json'))
        return reply(route, 404, {'error': {'code': 'NOT_IN_CONTRACT', 'message': path, 'details': []}})

    page.route('**/api/v1/admin/**', handle)
    page.goto(url + '/frontend/admin.html')
    expect(page.locator('#mock-banner')).to_be_hidden()
    expect(page.get_by_role('heading', name='관리자 로그인')).to_be_visible()
    expect(page.locator('#console-view')).to_be_hidden()
    page.get_by_label('아이디').fill('admin')
    page.get_by_label('비밀번호').fill('secret')
    page.get_by_role('button', name='로그인').click()
    expect(page.locator('#version-list .slot:not(.empty)')).to_have_count(4)

    page.locator('#file').set_input_files(xlsx('records.xlsx'))
    page.get_by_role('button', name='검증 후 게시').click()
    expect(page.locator('#upload-status li')).to_have_text(['12행 IN_09: 숫자가 아닌 값입니다.'])

    page.locator('#version-list .slot').nth(1).get_by_role('button').click()
    page.get_by_role('button', name='되돌리기', exact=True).click()
    expect(page.locator('#versions-status')).to_contain_text('되돌렸습니다')
    page.get_by_role('button', name='로그아웃').click()
    expect(page.locator('#login-error')).to_have_text('로그아웃했습니다.')

    seen = [(method, path) for method, path, _, _ in calls]
    assert ('GET', '/api/v1/admin/session') in seen
    assert ('POST', '/api/v1/admin/session') in seen
    assert ('POST', '/api/v1/admin/uploads') in seen
    assert ('POST', '/api/v1/admin/versions/20261001T170000%2B0900/rollback') in seen, seen
    assert ('DELETE', '/api/v1/admin/session') in seen
    assert all(path.startswith('/api/v1/admin/') for _, path in seen)
    login = next(c for c in calls if c[:2] == ('POST', '/api/v1/admin/session'))
    assert login[2].startswith('application/json')
    assert json.loads(login[3]) == {'username': 'admin', 'password': 'secret'}
    upload = next(c for c in calls if c[:2] == ('POST', '/api/v1/admin/uploads'))
    assert upload[2].startswith('multipart/form-data; boundary=')
    assert b'name="file"; filename="records.xlsx"' in upload[3]
    # No Authorization header or token anywhere: the session cookie is the only credential.
    return seen


def cloud_flow(page, url):
    """A >4.5 MB file bypasses the function; a lost response reuses upload_id."""
    counts = {'sign':0,'storage':0,'publish':0}
    upload_id='9b222234-aaa1-4ac0-8001-0123456789ab'
    def reply(route,body,status=200):
        route.fulfill(status=status,content_type='application/json',body=json.dumps(body))
    def handle(route,request):
        path=request.url.split('://',1)[1].split('/',1)[1]
        if path=='api/v1/admin/session':
            return reply(route,fixture('admin-session-success.json'))
        if path=='api/v1/admin/versions':
            return reply(route,fixture('admin-versions-four.json'))
        if path=='api/v1/admin/upload-mode':
            return reply(route,{'mode':'neon-direct','max_upload_bytes':10485760})
        if path=='api/v1/admin/uploads/sign':
            counts['sign']+=1
            assert request.post_data_json=={'filename':'big.xlsx','size':6*1024*1024}
            assert 'cookie' not in request.post_data
            return reply(route,{'upload_id':upload_id,'upload_url':'https://storage.example/upload?token=signed'})
        if path=='api/v1/admin/uploads/publish':
            counts['publish']+=1
            assert request.post_data_json=={'upload_id':upload_id}
            assert len(request.post_data)<100
            if counts['publish']==1:
                return reply(route,{'error':{'code':'PUBLISH_FAILED','message':'response lost','details':[]}},503)
            return reply(route,fixture('admin-upload-success.json'))
        raise AssertionError(path)
    def storage(route,request):
        if request.method=='OPTIONS':
            return route.fulfill(status=204,headers={'Access-Control-Allow-Origin':url,
                'Access-Control-Allow-Methods':'PUT','Access-Control-Allow-Headers':'content-type'})
        counts['storage']+=1
        assert request.method=='PUT' and len(request.post_data_buffer)==6*1024*1024
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        route.fulfill(status=200,headers={'Access-Control-Allow-Origin':url},body='{}')
    page.route('**/api/v1/admin/**',handle)
    page.route('https://storage.example/**',storage)
    page.goto(url+'/frontend/admin.html')
    expect(page.locator('#console-view')).to_be_visible()
    page.locator('#file').set_input_files(xlsx('big.xlsx',6*1024*1024))
    page.get_by_role('button',name='검증 후 게시').click()
    expect(page.locator('#upload-status p')).to_have_class('notice error')
    page.get_by_role('button',name='검증 후 게시').click()
    expect(page.locator('#upload-status')).to_contain_text('게시했습니다')
    assert counts=={'sign':1,'storage':1,'publish':2},counts


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    server = subprocess.Popen([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'],
                              cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with urlopen(url + '/frontend/admin.html', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Static server did not start')
        with sync_playwright() as p:
            browser = p.chromium.launch()
            errors = []
            for flow in (mock_flow, http_flow, cloud_flow):
                page = browser.new_page(viewport={'width': 1280, 'height': 900}, timezone_id='America/Los_Angeles')
                page.on('pageerror', lambda e: errors.append(str(e)))
                flow(page, url)
                page.close()
            browser.close()
            assert not errors, errors
        print(json.dumps({'e2e_admin': 'PASS', 'checks': [
            'mock login failure and success from fixtures', 'no token input',
            'four-slot version list with active and next-to-delete markers',
            'client rejects non-xlsx and >10 MB', '422 details rendered without changing versions',
            '409 publish in progress', 'publish rotates oldest out and activates newest',
            'rollback cancel and confirm', '401 returns to login', 'logout',
            '390px no page overflow', 'http transport uses frozen paths, JSON login, multipart file, encoded version id',
            'cloud 6 MB direct Storage upload, no credentials sent cross-origin, idempotent publish retry',
        ]}, ensure_ascii=False))
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
    main()
