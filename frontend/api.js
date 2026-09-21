// T05 (ADE-9) API 접근 계층.
// 서버가 바뀌면 BASE_URL 한 곳만 바꿉니다. 응답 형식은 공통 규격 v1 + PR #1 확장 필드 기준.
const BASE_URL = '';   // 같은 서버에서 서빙 → 상대 경로. 분리 배포 시 'https://api.example.com'

async function request(path) {
  let response, data;
  try {
    response = await fetch(BASE_URL + path, { cache: 'no-store' });
  } catch (e) {
    throw new ApiError('NETWORK', '서버에 연결할 수 없습니다. 네트워크를 확인한 뒤 다시 시도하세요.');
  }
  try { data = await response.json(); }
  catch (e) { throw new ApiError('BAD_RESPONSE', '서버 응답을 읽을 수 없습니다.'); }
  if (!response.ok) {
    const err = data && data.error ? data.error : {};
    throw new ApiError(err.code || 'PROCESSING_ERROR', err.message || '데이터를 불러올 수 없습니다.');
  }
  return data;
}

class ApiError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

const api = {
  meta:  ()     => request('/api/v1/meta'),
  today: (date) => request('/api/v1/congestion/today' + (date ? '?date=' + encodeURIComponent(date) : '')),
  stats: (date) => request('/api/v1/stats?date=' + encodeURIComponent(date)),
};
