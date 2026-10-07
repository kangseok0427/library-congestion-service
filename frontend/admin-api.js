// ADE-47 관리자 API 전송 계층. 화면(admin.js)은 이 파일의 adminApi만 사용합니다.
// 계약: contracts/openapi-v2.yaml (ADE-43). 경로·요청·응답 모양을 이 파일에서 바꾸지 않습니다.
//
// 모드
//   - 기본(운영): 같은 서버의 /api/v1/admin/* 를 호출합니다. 세션은 HttpOnly 쿠키로만 유지합니다.
//   - Mock: 주소에 ?mock 을 붙이면 contracts/fixtures/*.json 을 그대로 돌려줍니다.
//     저장소 루트에서 `python -m http.server 8000` 실행 후
//     http://127.0.0.1:8000/frontend/admin.html?mock 으로 엽니다.
//
// API가 완성되면 BASE_URL만 확인하면 됩니다. 화면 코드는 수정하지 않습니다.
const BASE_URL = '';                       // 같은 서버에서 서빙 → 상대 경로
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 계약: .xlsx 1개, 10 MB 이하
const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

class AdminApiError extends Error {
  constructor(code, status = 0, serverMessage = '', details = []) {
    super(serverMessage || code);
    this.code = code;
    this.status = status;
    this.serverMessage = serverMessage;
    this.details = Array.isArray(details) ? details : [];
  }
}

// ---------------------------------------------------------------- 운영 전송
async function httpRequest(method, path, { json, form } = {}) {
  const init = { method, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
  if (json !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(json);
  }
  if (form !== undefined) init.body = form; // Content-Type(boundary)은 브라우저가 붙입니다.

  let response;
  try {
    response = await fetch(BASE_URL + path, init);
  } catch (e) {
    throw new AdminApiError('NETWORK');
  }
  if (response.status === 204) return null;

  let data;
  try {
    data = await response.json();
  } catch (e) {
    throw new AdminApiError('BAD_RESPONSE', response.status);
  }
  if (!response.ok) {
    const error = data && data.error ? data.error : {};
    throw new AdminApiError(error.code || 'UNKNOWN', response.status, error.message || '', error.details);
  }
  return data;
}

const cloudUploads = new WeakMap(); // Reuse the same upload ID after a lost publish response.
const httpApi = {
  getSession: () => httpRequest('GET', '/api/v1/admin/session'),
  login: (username, password) => httpRequest('POST', '/api/v1/admin/session', { json: { username, password } }),
  logout: () => httpRequest('DELETE', '/api/v1/admin/session'),
  upload: async file => {
    // Local/PythonAnywhere servers retain the multipart transport.
    let mode;
    try {
      mode = await httpRequest('GET', '/api/v1/admin/upload-mode');
    } catch (error) {
      if (error.status !== 404) throw error;
    }
    if (mode && mode.mode === 'supabase-direct') {
      let uploadId = cloudUploads.get(file);
      if (!uploadId) {
        const signed = await httpRequest('POST', '/api/v1/admin/uploads/sign', {
          json: {filename: file.name, size: file.size},
        });
        let response;
        try {
          response = await fetch(signed.upload_url, {
            method: 'PUT', body: file,
            headers: {'Content-Type': XLSX_TYPE}, credentials: 'omit',
          });
        } catch (error) {
          throw new AdminApiError('NETWORK');
        }
        if (!response.ok) throw new AdminApiError('PUBLISH_FAILED', response.status);
        uploadId = signed.upload_id;
        cloudUploads.set(file, uploadId);
      }
      try {
        const result = await httpRequest('POST', '/api/v1/admin/uploads/publish', {
          json: {upload_id: uploadId},
        });
        cloudUploads.delete(file);
        return result;
      } catch (error) {
        if (error.status === 400 || error.status === 422) cloudUploads.delete(file);
        throw error;
      }
    }
    const form = new FormData();
    form.append('file', file, file.name);
    return httpRequest('POST', '/api/v1/admin/uploads', { form });
  },
  listVersions: () => httpRequest('GET', '/api/v1/admin/versions'),
  rollback: versionId => httpRequest('POST', `/api/v1/admin/versions/${encodeURIComponent(versionId)}/rollback`),
};

// ---------------------------------------------------------------- Mock 전송
// 응답 본문은 계약 fixture를 그대로 씁니다. 화면 흐름 확인을 위해 아래 상태만 메모리에 둡니다.
//   - 로그인 여부, 보관 버전 목록(최대 4개 순환), 활성 버전
// 실패 화면은 입력으로 재현합니다.
//   - 비밀번호 wrong                → 401 INVALID_CREDENTIALS (admin-session-invalid.json)
//   - 파일 이름에 invalid 포함       → 422 INVALID_EXCEL      (admin-upload-invalid.json)
//   - 파일 이름에 busy 포함          → 409 PUBLISH_IN_PROGRESS
//   - 파일 이름에 expired 포함       → 401 UNAUTHORIZED (세션 만료)
//   - 10 MB 초과                     → 413 FILE_TOO_LARGE
const FIXTURE_BASE = new URL('../contracts/fixtures/', document.baseURI).href;
const MOCK_DELAY_MS = 350;

// openapi-v2.yaml의 example과 같은 문구입니다(별도 fixture 파일이 없는 응답).
const MOCK_ERRORS = {
  UNAUTHORIZED: [401, '관리자 로그인이 필요합니다.'],
  PUBLISH_IN_PROGRESS: [409, '다른 파일을 게시하는 중입니다. 잠시 후 다시 시도하세요.'],
  FILE_TOO_LARGE: [413, 'Excel 파일은 10 MB 이하여야 합니다.'],
  VERSION_NOT_FOUND: [404, '보관 중인 데이터 버전을 찾을 수 없습니다.'],
};

function createMockApi() {
  const cache = new Map();
  const state = { authenticated: false, versions: null, activeId: null, maxVersions: 4 };

  const wait = () => new Promise(resolve => setTimeout(resolve, MOCK_DELAY_MS));
  const clone = value => JSON.parse(JSON.stringify(value));
  async function fixture(name) {
    if (!cache.has(name)) {
      const response = await fetch(FIXTURE_BASE + name, { cache: 'no-store' });
      if (!response.ok) throw new AdminApiError('BAD_RESPONSE', response.status, `fixture ${name}를 읽을 수 없습니다.`);
      cache.set(name, await response.json());
    }
    return clone(cache.get(name));
  }
  const fail = code => {
    const [status, message] = MOCK_ERRORS[code];
    return new AdminApiError(code, status, message, []);
  };
  async function failFromFixture(name, status) {
    const { error } = await fixture(name);
    return new AdminApiError(error.code, status, error.message, error.details);
  }
  function requireSession() {
    if (!state.authenticated) throw fail('UNAUTHORIZED');
  }
  async function ensureVersions() {
    if (state.versions) return;
    const list = await fixture('admin-versions-four.json');
    state.versions = list.versions;
    state.activeId = list.active_version_id;
    state.maxVersions = list.max_versions;
  }
  // 계약 VersionId 형식: 20261002T090000+0900 (KST)
  function newVersionId() {
    const kst = new Date(Date.now() + 9 * 3600 * 1000);
    const pad = n => String(n).padStart(2, '0');
    const stamp = d => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`;
    let id = `${stamp(kst)}+0900`;
    while (state.versions.some(v => v.id === id)) {
      kst.setUTCSeconds(kst.getUTCSeconds() + 1);
      id = `${stamp(kst)}+0900`;
    }
    const iso = `${id.slice(0, 4)}-${id.slice(4, 6)}-${id.slice(6, 8)}T${id.slice(9, 11)}:${id.slice(11, 13)}:${id.slice(13, 15)}+09:00`;
    return { id, created_at: iso };
  }
  const markActive = () => state.versions.forEach(v => { v.is_active = v.id === state.activeId; });

  return {
    async getSession() {
      await wait();
      return state.authenticated ? fixture('admin-session-success.json') : { authenticated: false };
    },
    async login(username, password) {
      await wait();
      if (!username || !password) throw new AdminApiError('INVALID_REQUEST', 400, '요청 내용을 확인하세요.');
      if (password === 'wrong') throw await failFromFixture('admin-session-invalid.json', 401);
      state.authenticated = true;
      return fixture('admin-session-success.json');
    },
    async logout() {
      await wait();
      requireSession();
      state.authenticated = false;
      return null;
    },
    async upload(file) {
      await wait();
      requireSession();
      await ensureVersions();
      const name = file.name.toLowerCase();
      if (name.includes('expired')) {
        state.authenticated = false;
        throw fail('UNAUTHORIZED');
      }
      if (file.size > MAX_UPLOAD_BYTES) throw fail('FILE_TOO_LARGE');
      if (name.includes('busy')) throw fail('PUBLISH_IN_PROGRESS');
      if (name.includes('invalid')) throw await failFromFixture('admin-upload-invalid.json', 422);

      const result = await fixture('admin-upload-success.json');
      Object.assign(result.version, newVersionId(), { source_name: file.name });
      state.versions.unshift(clone(result.version));
      state.versions = state.versions.slice(0, state.maxVersions); // 5번째 생성 시 가장 오래된 1개 삭제
      state.activeId = result.version.id;
      markActive();
      return result;
    },
    async listVersions() {
      await wait();
      requireSession();
      await ensureVersions();
      return { versions: clone(state.versions), active_version_id: state.activeId, max_versions: state.maxVersions };
    },
    async rollback(versionId) {
      await wait();
      requireSession();
      await ensureVersions();
      if (!state.versions.some(v => v.id === versionId)) throw fail('VERSION_NOT_FOUND');
      const result = await fixture('admin-rollback-success.json');
      result.active_version_id = versionId;
      state.activeId = versionId;
      markActive();
      return result;
    },
  };
}

const ADMIN_MODE = new URLSearchParams(location.search).has('mock') ? 'mock' : 'http';
const adminApi = ADMIN_MODE === 'mock' ? createMockApi() : httpApi;
