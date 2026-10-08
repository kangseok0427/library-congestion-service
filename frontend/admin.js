// ADE-47 관리자 웹. 데이터는 admin-api.js의 adminApi로만 주고받습니다.
const $ = id => document.getElementById(id);

// 오류 코드 → 관리자 안내 문구. 목록에 없는 코드는 서버 message를 그대로 보여 줍니다.
// (ADE-44에서 Excel 검증 코드가 늘어나면 여기에 추가합니다.)
const ERROR_MESSAGES = {
  INVALID_CREDENTIALS: '아이디 또는 비밀번호가 맞지 않습니다. 다시 입력하세요.',
  UNAUTHORIZED: '로그인이 만료되었습니다. 다시 로그인하세요.',
  INVALID_REQUEST: '입력한 내용을 확인하세요.',
  INVALID_EXCEL: 'Excel 검증에 실패해 게시하지 않았습니다. 이용자 화면은 이전 데이터 그대로입니다. 아래 위치를 고친 뒤 다시 올리세요.',
  FILE_TOO_LARGE: 'Excel 파일은 10 MB 이하만 올릴 수 있습니다.',
  FILE_TYPE: '.xlsx 형식의 Excel 파일만 올릴 수 있습니다.',
  PUBLISH_IN_PROGRESS: '다른 게시나 되돌리기 작업이 진행 중입니다. 잠시 후 다시 시도하세요.',
  VERSION_NOT_FOUND: '선택한 버전이 이미 삭제되었습니다. 목록을 다시 불러왔습니다.',
  NETWORK: '서버에 연결할 수 없습니다. 네트워크를 확인한 뒤 다시 시도하세요.',
  BAD_RESPONSE: '서버 응답을 읽을 수 없습니다. 잠시 후 다시 시도하세요.',
};
const messageFor = error =>
  ERROR_MESSAGES[error && error.code] || (error && error.serverMessage) || '요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.';

const numberKo = n => Number(n).toLocaleString('ko-KR');
const fmtTime = iso => {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const date = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', weekday: 'short' }).format(d);
  const time = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(d);
  return `${date} ${time}`;
};
const fmtSize = bytes => bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

let selectedFile = null;
let busy = false;          // 게시·되돌리기는 한 번에 하나 (서버 잠금과 같은 규칙)
let lastList = null;

// ---------------------------------------------------------------- 공통 상태 표시
function setStatus(target, kind, text, items = []) {
  const box = $(target);
  box.replaceChildren();
  if (!text) return;
  const p = document.createElement('p');
  p.className = `notice ${kind}`;
  p.textContent = text;
  box.append(p);
  if (items.length) {
    const ul = document.createElement('ul');
    ul.className = `notice-list ${kind}`;
    for (const item of items) {
      const li = document.createElement('li');
      li.textContent = item;
      ul.append(li);
    }
    box.append(ul);
  }
}

// 오류 details(field/row/reason)를 사람이 읽는 한 줄로
const detailLine = d => {
  const where = [d.row ? `${d.row}행` : '', d.field || ''].filter(Boolean).join(' ');
  return where ? `${where}: ${d.reason || '값을 확인하세요.'}` : (d.reason || '값을 확인하세요.');
};
const warningLine = w => (w.row ? `${w.row}행: ${w.message}` : w.message);

function setBusy(on) {
  busy = on;
  $('upload-panel').setAttribute('aria-busy', String(on));
  $('versions-panel').setAttribute('aria-busy', String(on));
  $('file').disabled = on;
  $('reload-versions').disabled = on;
  $('logout').disabled = on;
  updatePublishButton();
  document.querySelectorAll('[data-rollback]').forEach(b => { b.disabled = on; });
}

// ---------------------------------------------------------------- 화면 전환
function showLogin(message = '', kind = 'error') {
  $('booting').hidden = true;
  $('console-view').hidden = true;
  $('logout').hidden = true;
  $('login-view').hidden = false;
  $('login-error').className = `notice ${kind}`;
  $('login-error').hidden = !message;
  $('login-error').textContent = message;
  $('password').value = '';
  resetSelection();
  setStatus('upload-status', '', '');
  setStatus('versions-status', '', '');
  $('version-list').replaceChildren();
  ($('username').value ? $('password') : $('username')).focus();
}

function showConsole() {
  $('booting').hidden = true;
  $('login-view').hidden = true;
  $('console-view').hidden = false;
  $('logout').hidden = false;
  $('upload-title').focus();
  loadVersions();
  loadAdminOperations();
}

// 401은 어느 작업에서 나와도 로그인 화면으로 돌아갑니다.
function handleAuthError(error) {
  if (error && error.code === 'UNAUTHORIZED') {
    showLogin(ERROR_MESSAGES.UNAUTHORIZED);
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- 로그인
$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const username = $('username').value.trim();
  const password = $('password').value;
  if (!username || !password) {
    $('login-error').className = 'notice error';
    $('login-error').textContent = '아이디와 비밀번호를 모두 입력하세요.';
    $('login-error').hidden = false;
    (!username ? $('username') : $('password')).focus();
    return;
  }
  $('login-submit').disabled = true;
  $('login-submit').textContent = '확인하는 중';
  try {
    await adminApi.login(username, password);
    $('password').value = '';
    $('login-error').hidden = true;
    showConsole();
  } catch (error) {
    $('login-error').className = 'notice error';
    $('login-error').textContent = messageFor(error);
    $('login-error').hidden = false;
    $('password').value = '';
    $('password').focus();
  } finally {
    $('login-submit').disabled = false;
    $('login-submit').textContent = '로그인';
  }
});

$('logout').addEventListener('click', async () => {
  try {
    await adminApi.logout();
  } catch (error) {
    // 세션이 이미 만료(401)됐으면 로그아웃된 것과 같습니다.
    if (error.code !== 'UNAUTHORIZED') {
      setStatus('upload-status', 'error', `로그아웃하지 못했습니다. ${messageFor(error)}`);
      return;
    }
  }
  showLogin('로그아웃했습니다.', 'info');
});

// ---------------------------------------------------------------- 파일 선택
function resetSelection() {
  selectedFile = null;
  $('file').value = '';
  $('file-name').textContent = 'Excel 파일 선택';
  $('file-meta').textContent = '.xlsx 파일 1개, 10 MB 이하. 여기로 끌어다 놓아도 됩니다.';
  $('drop').classList.remove('picked', 'rejected');
  updatePublishButton();
}

function updatePublishButton() {
  $('publish').disabled = busy || !selectedFile;
}

function pickFile(file) {
  setStatus('upload-status', '', '');
  $('drop').classList.remove('picked', 'rejected');
  selectedFile = null;
  if (!file) { resetSelection(); return; }
  $('file-name').textContent = file.name;
  $('file-meta').textContent = fmtSize(file.size);
  let problem = '';
  if (!/\.xlsx$/i.test(file.name)) problem = ERROR_MESSAGES.FILE_TYPE;
  else if (file.size > MAX_UPLOAD_BYTES) problem = ERROR_MESSAGES.FILE_TOO_LARGE;
  if (problem) {
    $('drop').classList.add('rejected');
    setStatus('upload-status', 'error', problem);
  } else {
    selectedFile = file;
    $('drop').classList.add('picked');
  }
  updatePublishButton();
}

$('file').addEventListener('change', () => pickFile($('file').files[0]));

const drop = $('drop');
['dragenter', 'dragover'].forEach(type => drop.addEventListener(type, event => {
  event.preventDefault();
  if (!busy) drop.classList.add('over');
}));
['dragleave', 'drop'].forEach(type => drop.addEventListener(type, () => drop.classList.remove('over')));
drop.addEventListener('drop', event => {
  event.preventDefault();
  if (busy) return;
  const files = event.dataTransfer.files;
  if (files.length > 1) {
    setStatus('upload-status', 'error', '한 번에 Excel 파일 1개만 올릴 수 있습니다.');
    return;
  }
  pickFile(files[0]);
});

// ---------------------------------------------------------------- 게시
$('upload-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !selectedFile) return;
  const file = selectedFile;
  setBusy(true);
  $('publish').textContent = '검증하는 중';
  setStatus('upload-status', 'progress', '선택한 파일을 검증하고 게시하는 중입니다. 창을 닫지 마세요.');
  try {
    const result = await adminApi.upload(file);
    const v = result.version;
    const validation = result.validation || {};
    const reported = validation.warnings || [];
    // Older completed upload jobs can replay notices from before this policy.
    const internalCodes = new Set(['HOURLY_TOTAL_MISMATCH', 'PARTIAL_DATE_UNCONFIRMED']);
    const warnings = reported.filter(w => !internalCodes.has(w.code));
    const warningCount = reported.length === warnings.length
      ? Math.max(warnings.length, Number(validation.warning_count) || 0) : warnings.length;
    const text = `게시했습니다. ${v.source_name}의 ${numberKo(v.record_count)}건이 지금 이용자 화면에 반영됩니다.`
      + (warningCount ? ` 확인할 경고 ${warningCount}건이 있습니다.` : '');
    setStatus('upload-status', warningCount ? 'warn' : 'ok', text, warnings.map(warningLine));
    resetSelection();
    setStatus('versions-status', '', ''); // 직전 되돌리기 결과는 더 이상 현재 상태가 아닙니다.
    await loadVersions({ keepStatus: true });
  } catch (error) {
    if (handleAuthError(error)) return;
    const items = error.code === 'INVALID_EXCEL' || error.status === 422 ? error.details.map(detailLine) : [];
    setStatus('upload-status', 'error', messageFor(error), items);
  } finally {
    $('publish').textContent = '검증 후 게시';
    setBusy(false);
  }
});

// ---------------------------------------------------------------- 버전 목록
async function loadVersions({ keepStatus = false } = {}) {
  $('version-list').setAttribute('aria-busy', 'true');
  if (!keepStatus) setStatus('versions-status', '', '');
  try {
    lastList = await adminApi.listVersions();
    renderVersions(lastList);
  } catch (error) {
    if (handleAuthError(error)) return;
    $('version-list').replaceChildren();
    setStatus('versions-status', 'error', `버전 목록을 불러오지 못했습니다. ${messageFor(error)}`);
  } finally {
    $('version-list').removeAttribute('aria-busy');
  }
}

function renderVersions(list) {
  const max = list.max_versions || 4;
  const versions = list.versions.slice(0, max);
  const activeId = list.active_version_id || (versions.find(v => v.is_active) || {}).id;
  const full = versions.length >= max;
  const ol = $('version-list');
  ol.replaceChildren();

  versions.forEach((v, index) => {
    const active = v.id === activeId;
    const li = document.createElement('li');
    li.className = 'slot' + (active ? ' active' : '');
    li.dataset.versionId = v.id;

    const when = document.createElement('p');
    when.className = 'slot-when';
    when.textContent = fmtTime(v.created_at);

    const what = document.createElement('p');
    what.className = 'slot-what';
    what.textContent = `${v.source_name}, ${numberKo(v.record_count)}건`;

    const tags = document.createElement('p');
    tags.className = 'slot-tags';
    if (index === 0) tags.append(tag('최신'));
    if (full && index === versions.length - 1) tags.append(tag('다음 게시 때 삭제', 'leaving'));

    const side = document.createElement('div');
    side.className = 'slot-side';
    if (active) {
      const now = document.createElement('span');
      now.className = 'in-use';
      now.textContent = '사용 중';
      side.append(now);
    } else {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'quiet-button';
      button.dataset.rollback = v.id;
      button.textContent = '이 버전으로 되돌리기';
      button.setAttribute('aria-label', `${fmtTime(v.created_at)} ${v.source_name} 버전으로 되돌리기`);
      button.disabled = busy;
      button.addEventListener('click', () => askRollback(v));
      side.append(button);
    }

    const body = document.createElement('div');
    body.className = 'slot-body';
    body.append(when, what);
    if (tags.childNodes.length) body.append(tags);
    li.append(body, side);
    ol.append(li);
  });

  for (let i = versions.length; i < max; i += 1) {
    const li = document.createElement('li');
    li.className = 'slot empty';
    li.textContent = '빈 자리';
    ol.append(li);
  }
  if (!versions.length) setStatus('versions-status', 'info', '아직 게시한 버전이 없습니다. 왼쪽에서 Excel 파일을 게시하세요.');
}

function tag(text, kind = '') {
  const span = document.createElement('span');
  span.className = `tag ${kind}`.trim();
  span.textContent = text;
  return span;
}

$('reload-versions').addEventListener('click', () => loadVersions());

// ---------------------------------------------------------------- 되돌리기
const dialog = $('rollback-dialog');
let pendingRollback = null;

function askRollback(version) {
  if (busy) return;
  pendingRollback = version;
  $('rollback-summary').textContent = `${fmtTime(version.created_at)}에 게시한 ${version.source_name}(${numberKo(version.record_count)}건)을 사용합니다.`;
  dialog.showModal();
}

dialog.addEventListener('close', async () => {
  const version = pendingRollback;
  pendingRollback = null;
  if (dialog.returnValue !== 'confirm' || !version) return;
  setBusy(true);
  setStatus('versions-status', 'progress', '버전을 바꾸는 중입니다.');
  try {
    const result = await adminApi.rollback(version.id);
    const target = (lastList && lastList.versions.find(v => v.id === result.active_version_id)) || version;
    setStatus('upload-status', '', ''); // 직전 게시 결과는 더 이상 현재 상태가 아닙니다.
    setStatus('versions-status', 'ok', `되돌렸습니다. 지금 사용 중인 버전은 ${fmtTime(target.created_at)}에 게시한 ${target.source_name}입니다.`);
    await loadVersions({ keepStatus: true });
  } catch (error) {
    if (handleAuthError(error)) return;
    setStatus('versions-status', 'error', messageFor(error));
    if (error.code === 'VERSION_NOT_FOUND') await loadVersions({ keepStatus: true });
  } finally {
    setBusy(false);
    focusAfterRollback();
  }
});

// 되돌리기 후 버튼이 사라지므로 해당 행이나 목록 제목으로 포커스를 옮깁니다.
function focusAfterRollback() {
  $('versions-title').setAttribute('tabindex', '-1');
  $('versions-title').focus();
}

// ---------------------------------------------------------------- 시작
async function boot() {
  if (ADMIN_MODE === 'mock') $('mock-banner').hidden = false;
  try {
    const session = await adminApi.getSession();
    if (session && session.authenticated) showConsole();
    else showLogin();
  } catch (error) {
    showLogin(messageFor(error));
  }
}

boot();
