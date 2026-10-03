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

## 실제 검증

실행 환경: Windows, Python 3.12.14. 별도 가상환경에서 requirements-dev.txt 설치.

- 변경 전 develop: `python -m pytest -q` → 174 passed.
- 변경 후: `python -m pytest -q` → 206 passed, Starlette/httpx deprecation 경고 1개.
- `python -m scripts.e2e` → PASS.
- `python -m scripts.e2e_present` → PASS.
- `git diff --check` → 통과.
- 자동 테스트: 합성 Excel 정상·거부·4세대·롤백, 실제 이용자 stats/patterns/today API,
  프로세스·스레드 충돌, fsync/rebuild/replace/prune/commit 장애와 worker 종료·재시작 복구.
- 로컬 실제 Excel을 기존 `Data` 시트/partial 2026-09-10 기준으로 추가 변환·게시:
  원본 1,836행 → 29,376 records, HOURLY_TOTAL_MISMATCH 경고 3,626건 보존.
  원본 SHA-256 동일 확인, 생성 JSON은 시스템 임시 디렉터리 종료 시 삭제.
  실제 운영 API·새 관리자 웹 검증이나 ADE-44 운영 기준 승인 결과는 아니다.

## 데이터 품질 주의

공용 전처리 기준과 OUT_11 원본값을 유지했다. 실제 Excel·생성 JSON·SQLite·비밀값을
커밋하지 않는다. 신규 자동 fixture에는 실제 기록이나 개인정보가 없다.
행별 오류를 INVALID_EXCEL/details로 변환하고 경고와 게시 거부를 구분한다.

## 인수인계

기준 `origin/develop` `95ed76e3f925ed450ded869182a904d74f185547`.
브랜치 `codex/ADE-45-server-json-versions`, PR 대상 팀 `develop`.
ADE-48은 동일 기준의 독립 브랜치이며 이 변경에 임의 병합하지 않았다.

ADE-44는 Todo이고 `docs/excel-upload-contract.md`와 공통 Excel fixture가 없다.
따라서 최종 상태는 진행 중이며 새로운 검증 기준을 확정하지 않았다.
빈 버전 목록의 active id null, 저장 실패 PUBLISH_FAILED/500, 목록 충돌 409,
초 단위 id 충돌 방지와 초기 활성본 마이그레이션 메타데이터는 계약 확인이 필요한 제안이다.
ADE-43/44/46 확인 후 endpoint에 연결한다. 세션·10 MB 제한·화면은 구현하지 않았다.
Linux flock 실행, 실제 PythonAnywhere worker/파일시스템 및 운영 API는 미검증이다.
팀장 리뷰와 ADE-50 실제 통합 검증 후 운영에 반영하며 이 PR에서 배포하지 않는다.
