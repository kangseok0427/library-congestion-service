# ADE-45 서버 Excel 게시·4세대·롤백 인수인계

작성일: 2026-10-03. 기준: 팀 `origin/develop` `95ed76e3f925ed450ded869182a904d74f185547`.
작업 브랜치: `codex/ADE-45-server-json-versions`. PR 대상: 팀 `develop`.
기존 `library-congestion-service`와 `library-crowding-etl` 작업 경로는 수정하지 않았습니다.
저장소 및 상위 경로에 적용되는 AGENTS.md는 발견되지 않았습니다.
README, Git 협업, architecture, json-refresh, verification, PythonAnywhere/Railway,
CI와 API v2 계약·fixture를 확인했습니다.

## 확인된 기준과 미확인 기준

Linear ADE-42/44/45/46/48을 읽기 전용으로 조회했습니다. ADE-44는 Todo이며
`docs/excel-upload-contract.md`, ADE-44 공통 검증 케이스·Excel fixture는 원격 develop에 없습니다.
API v2 계약은 develop에 병합돼 있으며 성공·409·422·404 결과 형식은 이를 따릅니다.
ADE-44 미확정 사항 때문에 전체 작업 상태는 진행 중입니다. 재사용 가능한 저장 모듈과
회귀·장애 검증은 준비됐으며 새 Excel 판정 기준을 임의로 추가하지 않았습니다.

공용 `pipeline.preprocess`와 `refresh.merge_records`를 그대로 사용합니다. 필수 열·시트,
빈값·음수·소수·수식·날짜·중복 오류, 합계 불일치 경고, 완료→partial 거부는 기존 기준입니다.
원본 08~23시와 OUT_11 값을 보존하며 운영시간 필터는 기존 백엔드 정책을 따릅니다.
ADE-44 담당자의 승인이나 새 운영 기준 확정이라고 해석하지 않습니다.

## ADE-46 호출 인터페이스

```python
from library_etl.versions import VersionStore, VersionError

store = VersionStore(configured_records_path)  # FileProvider/LIBRARY_RECORDS와 같은 절대 경로
try:
    result = store.upload_excel(
        temporary_xlsx_path, source_name=uploaded_filename,
        sheet=confirmed_sheet, partial_dates=confirmed_partial_dates,
    )
    versions = store.list_versions()
    rollback = store.rollback(selected_version_id)
except VersionError as exc:
    body = exc.as_dict()
    http_status = exc.status_code
```

예제 변수는 ADE-46에서 제공해야 하는 설정·요청값이며 새 HTTP endpoint를 구현한 코드가 아닙니다.
임시 업로드 파일은 서버가 생성한 `.xlsx` 경로를 사용하고 종료 후 ADE-46이 정리합니다.
원본 파일명은 표시 메타데이터에만 사용합니다. 경로 구분자가 포함되면 기존 전처리가 거부합니다.
실제 저장 경로는 서버 설정과 내부 version id로만 구성됩니다.

`preprocess(source, ...) -> (records, report)`는 기존 공용 인터페이스입니다.
`upload_excel`은 이 전처리·기존 이력 병합·검증·재계산·게시를 한 호출로 수행하므로
ADE-46에서 미리 변환하거나 JSON 순환 저장을 중복 구현할 필요가 없습니다.
`partial_dates=None`은 최신 날짜 partial 표시, 빈 리스트는 모든 날짜 수집 완료 확인입니다.
웹에서 완료 확인을 어떻게 받는지는 ADE-44/46 연결 사항이며 무조건 빈 리스트를 넘기면 안 됩니다.
시트 선택도 기존 규칙을 따릅니다. 여러 시트인데 이름이 없으면 거부합니다.

| 호출 | 성공 결과 | 실패 처리 |
| --- | --- | --- |
| upload_excel | `status=published`, DataVersion, ValidationSummary | INVALID_EXCEL 422, PUBLISH_IN_PROGRESS 409 |
| list_versions | `versions`, `active_version_id`, `max_versions=4` | 변경 중이면 PUBLISH_IN_PROGRESS 409 |
| rollback | `status=rolled_back`, `active_version_id` | VERSION_NOT_FOUND 404, PUBLISH_IN_PROGRESS 409 |
| 저장·복구 공통 | 성공한 SQLite 커밋 상태 유지 | PUBLISH_FAILED 500 (아래 제안 사항) |

`VersionError.as_dict()`는 `{error: {code, message, details}}`입니다.
행별 오류의 `message`는 v2 `reason`으로 변환합니다. 경고는 `code/message/row`로 반환하고
세부 원본 합계 숫자는 표준 응답에 추가하지 않습니다. `record_count`는 이력 병합 후 전체 스냅샷 개수입니다.

### 공통 계약이 없는 항목: 제안

- 저장/재계산/복구 실패 `PUBLISH_FAILED`, HTTP 500의 명시는 API v2에 아직 없습니다. ADE-43/46에서 승인·반영해야 합니다.
- 첫 게시 전 빈 목록은 내부적으로 `versions=[]`, `active_version_id=None`입니다.
  현재 v2는 활성 id를 문자열로만 정의하므로 이 상태를 200으로 그대로 내보내면 계약 위반입니다.
  ADE-43/46에서 nullable 또는 별도 초기 상태 오류를 결정해야 합니다.
- 목록이 변경 잠금과 충돌하는 경우의 409도 목록 endpoint 계약에 추가할지 확인해야 합니다.
- 같은 초에 게시가 반복되면 v2의 초 단위 ID 형식을 유지하기 위해 논리 시각을 1초 증가시킵니다.
  `created_at`에는 실제 KST 생성시각을 기록합니다. ID는 생성시각 자체로 해석하지 않습니다.
- 최초 성공 게시 시 기존 정상 JSON도 한 보관 버전으로 등록합니다. 이전 파일의 생성시각을
  알 수 없어 파일 mtime을 사용하고, 단일 `source_file`이면 그 값을 사용합니다.
  여러 원본이 섞인 경우 메타데이터에는 기존 JSON 파일명을 기록합니다. 이는 마이그레이션 제안입니다.
- 재게시도 정상 버전 1개로 셉니다. 멱등 업로드 키나 미리보기→게시 분리 계약은 없습니다.

API의 파일 형식·10 MB 제한, 세션·권한·쿠키와 비동기 요청에서 작업 스레드 실행은 ADE-46 담당입니다.
새 업로드·목록·롤백 endpoint, 로그인, 관리자 화면은 구현하지 않았습니다.

## 저장·잠금·복구

```text
<LIBRARY_RECORDS>                         활성 records JSON
<LIBRARY_RECORDS>.versions/state.sqlite3  버전 메타데이터·정상 payload·활성 id·초기 복구본
<LIBRARY_RECORDS>.versions/<id>.json      최신 정상 버전 4개 이하
<LIBRARY_RECORDS>.versions/writer.lock    OS 소유 잠금 (삭제하지 않음)
```

SQLite의 commit이 데이터 변경의 확정점입니다. 메타데이터와 정상 payload를 같은
트랜잭션으로 저장하고 JSON 파일은 이 상태에서 복원 가능한 사본으로 관리합니다.
상태 파일은 백업이 아니라 버전 저장소의 필수 구성입니다. 통계/예측 DB를 새로 만든 것은 아닙니다.

1. 기존 공용 전처리가 전체 Excel을 성공 검증합니다.
2. 공용 OS 잠금 획득 후 마지막 커밋으로 미완료 작업을 복구합니다.
3. 날짜·게이트 이력을 병합하고 임시 경로에서 기존 rebuild로 통계·예측 직렬화를 검증합니다.
   rebuild에는 복사본을 넘기고, 임시 JSON을 재검증해 입력 변경도 거부합니다.
4. 버전 JSON과 활성 JSON은 각각 동일 파일시스템의 임시파일 작성·fsync·검증 후 os.replace합니다.
5. 새 파일 생성·교체 후 5번째 버전이면 가장 오래된 1개를 삭제합니다.
6. 메타데이터를 commit합니다. 실패 시 SQLite rollback 후 이전 활성본·보관본을 복원합니다.

파일 삭제 후 worker가 죽어도 미커밋 SQLite는 이전 상태로 되돌아갑니다. 다음
`FileProvider.get`, 목록·게시·롤백은 잠금 아래 마지막 커밋의 payload로 활성 파일과
보관 파일을 복원하고 orphan·임시파일을 정리합니다. 복구 전 새 데이터를 API에 반환하지 않습니다.
롤백 대상은 DB에 보관 중인 id로 조회하고 실제 payload를 검증·재계산한 뒤 활성화합니다.
보관 JSON 사본이 손상되면 커밋된 payload로 복원합니다. SQLite 권위본 자체가 손상되면 거부합니다.

Windows는 msvcrt 바이트 잠금, Linux는 flock을 사용합니다. 서로 다른 인스턴스·스레드·worker가
같은 절대 데이터 경로를 사용할 때 공유되며 프로세스 종료 시 해제됩니다.
문서의 PythonAnywhere uvicorn UDS 실행과 scripts.serve의 단일 uvicorn 실행에 맞췄습니다.
추가 worker도 동일 영구 로컬 저장소를 사용해야 합니다. 서로 다른 디스크/네트워크 저장소의
분산 잠금은 제공하지 않습니다. 실제 호스팅 파일시스템의 잠금 의미는 ADE-50에서 확인해야 합니다.
게시·롤백은 충돌 시 즉시 409, 조회는 최대 10초 기다린 뒤 잠금 오류를 반환합니다.

기존 CLI도 같은 잠금을 사용합니다. 버전 저장소가 생기면 Excel refresh는 새 store로
위임하고 JSON rebuild CLI 및 이전 JSON 업로드 API는 버전 저장소 우회를 거부합니다.
직접 파일 편집이나 저수준 rebuild 함수로 운영 파일을 덮어쓰면 다음 조회 시 커밋 상태로 복구됩니다.
저수준 rebuild는 임시 경로용으로만 사용해야 합니다. 새 관리 API를 연결하기 전 버전 저장소를
운영 경로에 초기화하면 이전 업로드 경로가 거부되므로 ADE-50에서 전환 순서를 검증해야 합니다.

## API 캐시·통계·예측 연결

FileProvider는 같은 잠금 아래 복구 후 실제 파일 payload와 stat을 읽습니다.
내용 hash와 mtime이 바뀌면 기존 LibraryService를 새로 만들어 통계·예측·추정 체류를 재계산합니다.
worker마다 다음 요청에서 같은 변경을 감지하므로 프로세스 내부 캐시 삭제 callback은 필요 없습니다.
게시·롤백 후 실제 stats/patterns/today API 응답 변경과 복구를 합성 데이터로 검증합니다.
외부 CDN/HTTP 캐시가 새로 추가되면 해당 캐시 정책은 ADE-46/50에서 별도 연결해야 합니다.

## 복구 운영

서비스를 중지한 뒤 활성 파일과 `.versions` 디렉터리를 함께 복사해 일관된 백업을 만듭니다.
writer.lock 파일이 남아 있다는 이유로 삭제하지 않습니다. 잠금은 OS가 관리합니다.
재시작하면 첫 관리 조회가 커밋 상태를 복원합니다. 확인용 호출은 아래와 같습니다.

```python
store = VersionStore(configured_records_path)
payload, stat = store.read_snapshot()
versions = store.list_versions()
```

디스크 장애로 즉시 복원이 실패하면 API는 오류를 반환합니다. 저장 공간·쓰기 권한을
복구한 뒤 위 조회를 재실행합니다. SQLite 파일이 손상되면 자동 선택/덮어쓰기를 하지 말고
서비스를 중지해 정상 전체 백업을 복원한 뒤 검증합니다. 수동 파일 교체를 운영 절차로 사용하지 않습니다.

## 검증 기록

Python 3.12.14, openpyxl 3.1.5, FastAPI 0.142.2, pytest 9.1.1,
Playwright 1.63.0. requirements-dev.txt의 허용 범위로 별도 worktree 가상환경에 설치했습니다.
requirements-lock.txt와 동일 환경을 검증한 것은 아닙니다.

- 기준 develop: `python -m pytest -q` → 174 passed.
- ADE-45: `python -m pytest -q` → 206 passed, httpx 관련 Starlette deprecation 경고 1개.
- `python -m scripts.e2e` / `python -m scripts.e2e_present` → PASS.
- `git diff --check` → 통과.
- 정상 Excel, 필수 열·중복·결측·음수·소수·수식·날짜 오류, 실패 시 전체 보존,
  5번째 버전 4개 유지, 기존 정상본 마이그레이션·롤백, API 통계·예측·캐시,
  프로세스·스레드 충돌, stage/rebuild/replace/prune/commit 실패,
  첫 게시/5번째 게시 중 프로세스 종료·재시작 복구, 사본 손상 복원·권위본 손상 거부를 검증했습니다.
- 신규 자동 테스트의 Excel은 임시 합성 fixture로만 생성하며 실제 자료나 개인정보를 포함하지 않습니다.
- 로컬 실제 Excel 추가 검증 결과는 PR 초안에 별도로 기록합니다. 원본과 생성 JSON은 Git에 포함하지 않습니다.

미실행: Linux의 flock 실제 실행, PythonAnywhere multi-worker·파일시스템 검증, 실제 운영 API,
새 관리자 세션·업로드 화면 E2E, ADE-44 새 fixture 검증, ADE-50 배포·운영 검증.
처음 기본 Python에는 pytest가 없고 기존 팀 venv에는 PyYAML이 없었습니다.
개발 의존성을 별도 환경에 설치한 후 실행했습니다. 장애 테스트가 입력 변이 비교 결함을
발견해 deepcopy로 수정했고, 이 회귀 테스트를 유지합니다.
