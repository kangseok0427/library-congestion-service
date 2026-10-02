# ADE-47 관리자 웹 (로그인·Excel 업로드·버전 롤백)

공통 API 계약 v2(ADE-43, `contracts/openapi-v2.yaml`)의 fixture로 먼저 완성한 관리자 화면입니다.
ADE-46 API가 붙으면 화면 코드는 수정하지 않고 그대로 동작합니다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `frontend/admin.html` | 로그인, 업로드, 버전 목록, 되돌리기 확인창 |
| `frontend/admin.js` | 화면 상태 전환, 오류 코드 → 한국어 안내 매핑 |
| `frontend/admin-api.js` | 전송 계층. 운영(HTTP)과 Mock(fixture) 두 구현이 같은 함수 이름·응답 모양 |
| `frontend/admin.css` | 관리자 전용 스타일 (이용자 `style.css`와 분리해 ADE-49와 충돌 방지) |
| `scripts/e2e_admin.py` | 브라우저 검증: Mock 전체 흐름 + 운영 전송의 경로·요청 모양 |
| `tests/test_admin_web.py` | 계약 오류 코드마다 안내 문구가 있는지, 계약 밖 경로·토큰 입력이 없는지 |

## 실행

Mock (서버 없이 화면 확인):

```bash
python -m http.server 8000          # 저장소 루트에서
# http://127.0.0.1:8000/frontend/admin.html?mock
```

| 입력 | 재현되는 응답 |
| --- | --- |
| 비밀번호 `wrong` | 401 `INVALID_CREDENTIALS` (`admin-session-invalid.json`) |
| 파일 이름에 `invalid` | 422 `INVALID_EXCEL` (`admin-upload-invalid.json`) |
| 파일 이름에 `busy` | 409 `PUBLISH_IN_PROGRESS` |
| 파일 이름에 `expired` | 401 `UNAUTHORIZED` → 로그인 화면 |
| 10 MB 초과, `.xlsx` 아님 | 화면에서 차단, API 호출 안 함 |
| 그 외 `.xlsx` | 200 게시, 최신 4개 순환 |

운영: FastAPI가 `frontend/`를 `/static`으로 서빙하므로 `/static/admin.html`로 열립니다.
`/admin` 같은 짧은 주소가 필요하면 ADE-46 또는 ADE-50에서 라우트를 추가합니다.

검증:

```bash
python -m pytest -q tests/test_admin_web.py tests/test_api_contract_v2.py
python -m scripts.e2e_admin
```

## 동작 규칙

- 인증은 HttpOnly 세션 쿠키만 사용합니다. 토큰 입력칸, `Authorization` 헤더, 브라우저 저장소를 쓰지 않습니다.
- 어느 관리자 요청이든 401이면 로그인 화면으로 돌아가 "로그인이 만료되었습니다"를 표시합니다.
- 게시·되돌리기 중에는 파일 선택, 게시, 되돌리기, 새로고침, 로그아웃을 모두 잠급니다(서버 잠금과 같은 규칙).
- 422 응답의 `details`(row/field/reason)는 "12행 IN_09: 숫자가 아닌 값입니다." 형식으로 나열합니다.
- 버전 목록은 `max_versions` 칸을 항상 그리고, 목록이 가득 차면 가장 오래된 칸에 "다음 게시 때 삭제"를 표시합니다.
- 활성 버전은 `active_version_id`로 판단하고, 없을 때만 `is_active`를 봅니다.
- `version_id`의 `+`는 경로에서 `%2B`로 인코딩합니다.

## 인수인계

- ADE-44에서 Excel 검증 오류 코드가 추가되면 `admin.js`의 `ERROR_MESSAGES`에 문구를 추가합니다.
  계약(openapi·fixture)에 들어간 코드가 빠져 있으면 `tests/test_admin_web.py`가 실패합니다.
- 목록에 없는 코드는 서버 `message`를 그대로 보여 주므로 화면이 깨지지는 않습니다.
- ADE-46은 계약대로 응답하면 됩니다. `scripts/e2e_admin.py`의 `http_flow`가 경로·메서드·요청 본문을 고정합니다.
