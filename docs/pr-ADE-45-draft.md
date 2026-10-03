# PR 제목

feat(ADE-45): 서버 Excel 게시·JSON 4세대 보관·롤백 연결

## 관련 Linear 이슈

ADE-45. 선행 기준 ADE-44, 연결 ADE-46, 운영 통합 ADE-50.

## 구현 및 검증 결과

관리자 웹에서 받은 Excel을 서버 공용 전처리로 검증하고, 정상 JSON만 원자적으로
게시하는 재사용 모듈을 추가한다. 기존 이력은 날짜·게이트 단위로 병합한다.
정상 스냅샷을 최신 4개만 보관하고 선택한 보관본으로 롤백한다.
기존 정상 활성본도 첫 성공 게시 시 보관해 최초 전환 후 롤백할 수 있다.

SQLite의 메타데이터·payload·활성 id를 확정 기준으로 두고 OS 프로세스 간 잠금 아래
파일 교체·삭제와 commit을 수행한다. 교체·삭제 후 worker가 종료돼도 다음 API 조회
전 마지막 commit으로 복원한다. CLI·기존 업로드도 같은 잠금을 사용하고 버전 우회를 차단한다.

## 처음 보는 팀원을 위한 동작 설명

`VersionStore(LIBRARY_RECORDS).upload_excel`, `.list_versions`, `.rollback`을 ADE-46에서
호출한다. 성공 결과는 기존 API v2 fixture 구조이며 `VersionError.as_dict()`와
`status_code`를 HTTP에 연결한다. FileProvider가 복구된 파일 hash를 감지하고
LibraryService를 새로 만들어 실제 통계·예측 API에 반영한다.
[상세 인터페이스와 복구 방법](server-json-versions.md)을 참고한다.
Excel 파싱·병합·재계산·파일/DB 기록은 동기 작업이므로 async FastAPI에서는
run_in_threadpool로 실행한다. API 계층은 제한된 업로드 수신·임시 `.xlsx` 생성/삭제,
확인된 시트·부분 날짜 전달을 담당한다. 문서에 최소 호출 예시와 경로 설정을 보강했다.

## 실제 검증

실행 환경: Windows Python 3.12.14 및 WSL2 Ubuntu 24.04.4 Python 3.12.3.
Linux checkout과 합성 임시 데이터는 ext4에 두고 Linux 전용 가상환경에 requirements-dev.txt를 설치했다.

- 변경 전 develop: `python -m pytest -q` → 174 passed.
- 후속 Windows: `python -m pytest -q` → 206 passed, POSIX 전용 5 skipped.
- 후속 Linux: `python -m pytest -q` → 211 passed.
- Linux 잠금·복구 3개 test 파일 → 37 passed. 각 전체 pytest에 Starlette/httpx deprecation 경고 1개.
- Linux `python -m scripts.e2e` → PASS.
- Linux `python -m scripts.e2e_present` → PASS.
- 기존 Windows 두 E2E PASS 기록은 초기 작업과 PR 게시 전 검증에서 확인했다.
- `git diff --check` → 통과.
- 자동 테스트: 합성 Excel 정상·거부·4세대·롤백, 실제 이용자 stats/patterns/today API,
  프로세스·스레드 충돌, fsync/rebuild/replace/prune/commit 장애와 worker 종료·재시작 복구.
- 후속 추가 검증: 실제 게시/롤백 worker의 rebuild·활성 교체·보관 삭제·commit 경계에서
  SIGKILL 종료 후 완전히 새 프로세스가 복구. 파일/보관본 hash·메타데이터·활성 id와
  stats/patterns/today가 마지막 커밋과 일치하고 재게시도 성공하는지 5개 경우 확인.
- 초기 Windows 실제 Excel 기록 (`Data` 시트/partial 2026-09-10, 이번에는 재실행하지 않음):
  원본 1,836행 → 29,376 records, HOURLY_TOTAL_MISMATCH 경고 3,626건 보존.
  원본 SHA-256 동일 확인, 생성 JSON은 시스템 임시 디렉터리 종료 시 삭제.
  실제 운영 API·새 관리자 웹 검증이나 ADE-44 운영 기준 승인 결과는 아니다.
- 첫 Linux 전체 실행의 2개 실패는 기존 GUI 테스트의 tkinter 미설치였다.
  python3-tk 설치 후 해결했다. 저장 모듈 결함은 추가 검증 범위에서 발견되지 않았으며
  후속 변경은 합성 테스트·연결 문서에 한정한다.

## 데이터 품질 주의

공용 전처리 기준과 OUT_11 원본값을 유지했다. 실제 Excel·생성 JSON·SQLite·비밀값을
커밋하지 않는다. 신규 자동 fixture에는 실제 기록이나 개인정보가 없다.
행별 오류를 INVALID_EXCEL/details로 변환하고 경고와 게시 거부를 구분한다.

## 인수인계

기준 `origin/develop` `95ed76e3f925ed450ded869182a904d74f185547`.
브랜치 `codex/ADE-45-server-json-versions`, PR 대상 팀 `develop`.
ADE-48은 동일 기준의 독립 브랜치이며 이 변경에 임의 병합하지 않았다.

ADE-44/46 이슈와 댓글, 팀 원격 브랜치·전체 PR·문서를 조회했다.
확인 범위에서는 `docs/excel-upload-contract.md`와 공통 Excel fixture, ADE-46 구현 PR을 찾지 못했다.
상태만으로 비공개·다른 저장소의 작업까지 없다고 판단하지 않는다.
따라서 최종 상태는 진행 중이며 새로운 검증 기준을 확정하지 않았다.
빈 버전 목록의 active id null, 저장 실패 PUBLISH_FAILED/500, 목록 충돌 409,
초 단위 id 충돌 방지와 초기 활성본 마이그레이션 메타데이터는 계약 확인이 필요한 제안이다.
ADE-43/44/46 확인 후 endpoint에 연결한다. 세션·10 MB 제한·화면은 구현하지 않았다.
Linux flock·SIGKILL 복구는 WSL2/ext4에서 검증했다. 실제 PythonAnywhere
worker/파일시스템·운영 데이터/API는 미검증이다. 로컬 Linux 검증을 운영 완료로 해석하지 않는다.
기존 PR #20의 Ubuntu tests workflow 성공과 리뷰 없음도 확인했다. 후속 commit의 CI는 별도로 확인한다.
ADE-48/PR #21과 다른 작업을 합치지 않았으며 PR #20은 Draft로 유지한다.
팀장 리뷰와 ADE-50 실제 통합 검증 후 운영에 반영하며 이 PR에서 배포하지 않는다.
