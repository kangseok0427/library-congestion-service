# PR 제목

refactor(ADE-48): Windows 업로더 제거·관리자 웹 운영 안내 정리

## 관련 Linear 이슈

ADE-48. 운영 연결 ADE-45/46/47, 최종 통합 ADE-50.

## 구현 및 검증 결과

관리자 웹 Excel 업로드 전환에 따라 설치형 업로더 패키지·전용 requirements,
실행파일 빌드 진입점·PowerShell 스크립트와 전용 테스트를 제거한다.
keyring/PyInstaller는 제거 대상에서만 사용됐고 공용 의존성 파일에는 없어
공용 requirements와 잠금 파일을 변경하지 않는다.

공용 `library_etl`, openpyxl, 서버 API·인증 설정은 보존한다.
서버 테스트에서 업로더 클라이언트 의존 테스트 2개만 제거하고 인증·업로드·실패 보존 검증은 유지한다.
README와 PythonAnywhere/Railway 운영 안내를 관리자 웹 전환 계획으로 갱신한다.
기존 설치 안내는 변경 이력 링크만 남기고 실행·다운로드·비밀값 입력 절차를 제거한다.

## 처음 보는 팀원을 위한 동작 설명

현재 API v2는 계약과 Mock fixture가 있는 상태다. 관리자 로그인·Excel 업로드·목록·롤백은
ADE-45/46/47 및 ADE-50 검증 후 운영 절차로 사용할 수 있다.
[관리자 업로드 운영 안내](admin-upload.md)는 목표 절차와 현 시점의 제한을 구분한다.
기존 서버 인증 경로 폐기와 비밀값 회수 시점은 ADE-50에서 확인한다.

## 실제 검증

Windows, Python 3.12.14. ADE-45의 별도 가상환경 Python으로 이 worktree에서 실행했다.

- 기준 develop: `python -m pytest -q` → 174 passed.
- 제거 후: `python -m pytest -q` → 136 passed, Starlette/httpx deprecation 경고 1개.
  제거된 38개는 설치형 전용 테스트 36개와 서버 파일 내 클라이언트 테스트 2개다.
- `python -m scripts.e2e` → PASS (합성 XLSX 전처리·API·DOM, 실패 보존, 모바일 포함).
- `python -m scripts.e2e_present` → PASS (추정 체류 막대·API·DOM·오류·모바일).
- `git diff --check` → 통과.
- `rg`로 실행파일명, 모듈 실행·빌드 명령, 전용 requirements, Credential Manager,
  토큰 입력·다운로드 안내와 제거된 클라이언트 import를 검색해 현재 실행 절차가 없음을 확인.
  admin-upload 문서의 제거 이유·의존성 확인 기록, 과거 Week 3 보고서·Git history는 보존한다.

## 데이터 품질 주의

실제 Excel·생성 JSON·비밀값을 포함하지 않는다. 공용 전처리와 서버 의존성은 유지한다.
새 관리자 웹이 현재 운영 중이라고 설명하지 않는다.
프론트 소스 변경은 없으며 기존 두 Chromium E2E로 회귀를 확인했다.

## 인수인계

기준 `origin/develop` `95ed76e3f925ed450ded869182a904d74f185547`.
브랜치 `codex/ADE-48-remove-windows-uploader`, PR 대상 팀 `develop`.
ADE-45와 독립 기준으로 준비했고 코드 제거 자체에는 선행 PR 의존성이 없다.
운영 안내의 게시·4세대·롤백을 실제 사용하려면 ADE-45/46/47 및 ADE-50이 필요하다.
ADE-44 새 검증 계약과 실제 운영 서버·새 관리자 세션/화면 E2E는 미검증이다.
팀장 검토 후 통합하며 이 PR에서 운영 배포·병합·Linear 상태 변경을 하지 않는다.
