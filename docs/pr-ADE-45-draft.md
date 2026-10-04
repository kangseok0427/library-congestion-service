## 문제와 수정 결과

2026-10-04 팀장 리뷰의 두 필수 변경을 반영했습니다. SQLite 기준 저장소를 제거하고
활성 records.json + 정상 버전 JSON 최대 4개 + metadata.json으로 저장·복구합니다.
기존 정상 활성본은 첫 버전 관리 호출에서 잠금 안에 최초 버전으로 등록해
정상 목록에서 ADE-43의 유효한 VersionId를 반환합니다.

## 최종 구현

- VersionStore가 JSON 메타데이터(id·시각·출처·개수·활성 ID·내부 해시)와 불변 버전 JSON만 사용합니다.
  실행 코드의 SQLite import/연결/파일 생성과 DB 우회 판별을 제거했습니다.
  기존 requirements에 DB 전용 패키지가 없어 다른 기능의 의존성은 삭제하지 않았습니다.
- 최초 등록은 스키마·통계·예측 검증 후 원래 바이트와 품질 정보를 보존합니다.
  mtime과 기존 source_file(복수면 JSON 이름)을 기록하며 Excel 업로드 이력을 만들지 않습니다.
  반복 호출·여러 worker 초기화·실패/종료 후 재등록에서 중복 정상 버전을 만들지 않습니다.
- 공용 Excel 전처리·날짜/게이트 이력 병합·원본 OUT_11·기존 경고/거부 규칙을 유지합니다.
- 공용 OS 잠금 아래 `새 버전 JSON → 활성 파일 → metadata.json 커밋 → 정리` 순서입니다.
  파일은 같은 파일시스템 임시파일에 완성·fsync·검증한 뒤 원자 교체합니다.
  메타데이터 커밋 전 종료는 이전 정상본, 커밋 후 종료는 새 정상본으로 복구합니다.
  실패 시 기존 정상 4개를 삭제하지 않습니다. 삭제 실패는 커밋된 게시를 실패로 바꾸지 않고
  다음 잠금 작업에서 정리 재시도합니다. 정상 목록은 항상 최대 4개입니다.
- 롤백은 보관 버전을 다시 활성화하며 새 버전을 만들지 않습니다.
  과거 버전 롤백 후 게시·활성본/목록 일치·stats/patterns/today 갱신을 유지합니다.
- VersionStore/upload_excel/list_versions/rollback와 VersionError.status_code/as_dict() 연결은 유지합니다.
  기존 Excel refresh와 JSON CLI/업로드의 버전 저장소 판별은 metadata.json으로 바뀝니다.
  [연결·초기 등록·복구·threadpool 예시](server-json-versions.md)를 갱신했습니다.
- 관리자가 업로드한 임시 Excel의 생성/제한/삭제는 ADE-46 책임입니다.
  관리자 인증·endpoint·화면은 새로 구현하지 않았고 ADE-48/PR #21 변경도 합치지 않았습니다.

## 이번 실제 검증 · 2026-10-04

과거 211 passed 기록을 재사용하지 않았습니다. 실제 Excel·운영 JSON·개인정보·비밀값을 추가하지 않았습니다.

| 환경 | 관련 pytest | 전체 pytest | scripts.e2e | scripts.e2e_present |
| --- | --- | --- | --- | --- |
| Windows Python 3.12.14 | 46 passed / 11 skipped / 0 failed | 220 passed / 11 skipped / 0 failed | PASS | PASS |
| WSL Ubuntu Python 3.12.3, ext4 | 57 passed / 0 skipped / 0 failed | 231 passed / 0 skipped / 0 failed | PASS | PASS |

관련 명령: `python -m pytest tests/test_versions.py tests/test_versions_migration.py tests/test_versions_process_recovery.py tests/test_versions_json.py -q`.
전체·E2E 명령: `python -m pytest -q`, `python -m scripts.e2e`, `python -m scripts.e2e_present`.
Windows는 `C:\study\library-service-ade45\.venv\Scripts\python.exe`를 사용했습니다.
Linux는 `/home/psy/ade45-json-20261004-d3UPyj`의 ext4 checkout·임시 데이터와
기존 Linux 가상환경 `/home/psy/ade45-linux-20261003-e9oKAZ/.venv`를 사용했습니다.
Windows 가상환경을 Linux에서 사용하지 않았습니다.
각 전체 pytest에는 기존 Starlette/httpx deprecation 경고 1개가 있습니다.

- 정상 초기본의 품질/바이트 보존·유효 활성 ID·반복/4개 프로세스 초기화,
  활성본 없음/손상/빈 배열, 정상/오류 Excel·4개/5번째 순환·롤백/없는 버전·과거 롤백 후 게시 검증.
- 실제 게시/롤백 잠금 충돌, fsync/rebuild/파일 교체/메타데이터 rename 전후 예외와 실패 보존,
  커밋 후 정리 실패/재시도, JSON 손상/유실/해시 불일치 거부 검증.
- Linux SIGKILL 11개 경우: 최초 등록 3개 + 게시/롤백 8개 저장 경계에서
  실제 worker를 종료하고 새 프로세스가 파일·활성 ID·목록·stats/patterns/today를 복구하는지 검증.
  커밋 후 상태는 정상적인 중단 없는 rebuild 결과와 비교합니다.
- SQLite connect를 실패시키는 상태에서도 초기 등록/게시/롤백/재시작 목록이 성공하며
  임시 경로에 SQLite/DB 파일이 생성되지 않는지 검사합니다.
  실행 코드·requirements·문서의 관련 import/참조 검색을 완료했습니다.
- 실제 OpenAPI의 성공 응답 schema 검증과 기존 이용자 API/브라우저 회귀를 유지합니다.
- `git diff --check` 통과. Linux 검증 소스와 Windows 소스 해시를 대조합니다.

DB payload 조작 테스트를 JSON 메타데이터/보관 JSON 손상 테스트로 대체했습니다.
삭제 실패 항목은 커밋 후 정리 재시도 테스트로 이동했습니다. 전체 테스트는 211→231로
20개 증가했고 저장 실패·손상·정리 검증을 제거해 개수를 줄이지 않았습니다.

## 계약·운영의 남은 항목

- 승인된 OpenAPI/fixtures는 수정하지 않았습니다. 정상 초기 데이터가 없으면 목록은
  `PUBLISH_FAILED/500`으로 거부하고 null 활성 ID 성공 응답을 내보내지 않습니다.
  초기 데이터 없음·저장 실패 500·목록 잠금 충돌 409의 HTTP 규격 명시는 ADE-43/46 승인이 필요합니다.
- 2026-10-04 직접 조회: ADE-44/46/50 Todo, ADE-45 In Progress.
  ADE-44 문서·공통 Excel fixture는 최신 develop/로컬과 조회한 팀 PR 목록에서 찾지 못했습니다.
  ADE-44 확정 계약 대조와 ADE-46 관리자 세션/업로드/목록/롤백 API 연결이 남아 있습니다.
- 실제 PythonAnywhere worker·파일시스템·운영 데이터/API·관리자 웹 통합과
  전원 장애/영구 디스크 손상 검증은 미실행입니다. ADE-50에서 운영 통합 검증이 필요합니다.
  기존 실험 DB 이력 자동 이전은 제공하지 않으며 전환 전 정상 활성 JSON 확인·전체 백업이 필요합니다.
- Draft 해제 지시가 없어 PR Draft를 유지합니다. 리뷰 댓글·Linear 상태 변경·force push·병합·배포는 하지 않습니다.

기준 develop: `95ed76e3f925ed450ded869182a904d74f185547`.
브랜치: `codex/ADE-45-server-json-versions`, fork: `psy0635-ctrl/library-congestion-service`.
