# ADE-45 서버 Excel 게시·JSON 4세대·롤백 인수인계

갱신일: 2026-10-04. 브랜치: `codex/ADE-45-server-json-versions`.
팀 PR #20, 대상 `develop`. 작업 경로는 `C:\study\library-service-ade45`입니다.
작업 전 HEAD와 fork 브랜치는 `46f2f2a66e7884890e21e34eac7a9de4f03e2794`,
fetch 후 develop은 `95ed76e3f925ed450ded869182a904d74f185547`로 추가 변경이 없었습니다.
미커밋 변경은 없었고 저장소 및 상위 경로에 AGENTS.md는 없었습니다.
README, 협업·구조·갱신·검증·호스팅 지침, CI, OpenAPI v2와 관련 fixture를 확인했습니다.

## 최신 리뷰와 범위

GitHub PR #20의 2026-10-04 팀장 Changes requested 리뷰와 Linear ADE-45 본문·댓글을
직접 확인했습니다. JSON 전용 저장과 초기 활성본 등록 요구는 사용자 기록과 일치했습니다.
PR은 Open/Draft이며 Draft 해제 지시는 없었습니다. ADE-45는 In Progress이고 상태를 변경하지 않습니다.
ADE-48/PR #21은 독립 작업이며 이 브랜치에 합치지 않습니다.

2026-10-04 조회에서 ADE-44·46·50은 Todo입니다. ADE-44·46 댓글·첨부 문서는 없으며,
최신 develop과 로컬 저장소에 `docs/excel-upload-contract.md` 및 ADE-44 공통 Excel fixture는
아직 없습니다. 팀 PR 목록에서도 해당 산출물을 찾지 못했습니다. 다른 비공개 작업의 부재를 뜻하지 않습니다.

공용 `pipeline.preprocess`와 `refresh.merge_records`를 재사용합니다. 필수 열·시트,
빈값·음수·소수·수식·날짜·중복 오류, 합계 불일치 경고, 완료→partial 거부는 기존 기준을 유지합니다.
원본 08~23시, OUT_11, 선택 품질 정보를 보존합니다. ADE-44의 새 승인 기준을 확정한 결과는 아닙니다.
관리자 인증·FastAPI endpoint·화면은 ADE-46 범위로 새로 구현하지 않습니다.

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

### 인수·반환값과 설정

| 인터페이스 | 인수 | 반환/예외 |
| --- | --- | --- |
| VersionStore(path, rebuild_fn=rebuild) | 신뢰된 records 파일 경로(str/Path), 기존 순수 재계산 함수 | 인스턴스. 생성자에서 파일을 읽거나 초기화하지 않음 |
| upload_excel(source, source_name=None, sheet=None, partial_dates=None) | 서버 임시 `.xlsx` 경로, 표시용 원본 파일명, 확인된 시트·부분 날짜 | published/버전/검증 경고 dict, VersionError |
| list_versions() | 없음 | 최신순 versions, active_version_id, max_versions=4; VersionError |
| rollback(version_id) | 보관 목록에서 받은 문자열 id | rolled_back/active_version_id dict; VersionError |
| VersionError.status_code | 예외 속성 | API에서 사용할 정수 HTTP 상태 |
| VersionError.as_dict() | 없음 | JSON 직렬화 가능한 error/code/message/details dict |

`VersionStore`는 path를 절대 경로로 resolve합니다. ADE-46이 `LIBRARY_RECORDS` 설정을
한 곳에서 읽어 FileProvider와 store에 같은 값을 제공해야 합니다. 사용자 입력으로 이 경로를
바꾸지 않습니다. `.versions` 디렉터리는 records 파일 옆에 고정되며 별도 디렉터리 인수는 없습니다.
부모 경로와 `.versions`는 같은 서비스 계정이 읽고 쓰고 잠글 수 있는 영구 로컬 저장소여야 합니다.
웹앱과 CLI/여러 worker도 같은 경로를 사용합니다. 원본 업로드 파일의 디렉터리는 시스템
임시 경로여도 되며 게시 staging은 store가 실제 목적지 파일시스템에서 별도로 만듭니다.

### 동기 작업과 FastAPI 연결 예시

Excel 파싱, 전체 이력 읽기·병합, 모든 날짜의 통계·예측 검증, JSON 기록과 fsync는
모두 동기 작업입니다. 파일 크기와 기록 기간에 따라 CPU·메모리·디스크 비용이 늘고, 잠금은
병합·재계산·저장 전체 동안 유지됩니다. 일정한 응답시간을 보장하지 않습니다.
기존 FastAPI의 `async def`에서 직접 호출하면 이벤트 루프를 막으므로 Starlette의
`run_in_threadpool`로 넘깁니다. 동기 `def` 핸들러라면 FastAPI의 기존 스레드 실행 구조를 사용합니다.

아래는 인증·확장자·10 MB 제한을 ADE-46에서 이미 확인한 뒤 사용할 호출 구조 예시입니다.
새 endpoint나 세션 구현은 아닙니다. 부분 날짜·시트 결정은 ADE-44/46의 확정 기준을 전달합니다.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from library_etl.versions import VersionStore, VersionError

async def publish_validated_bytes(records_path, upload_bytes, original_name,
                                  confirmed_sheet, confirmed_partial_dates):
    store = VersionStore(records_path)
    with TemporaryDirectory(prefix="library-admin-") as directory:
        source = Path(directory) / "upload.xlsx"  # 원본 이름을 경로에 사용하지 않음
        await run_in_threadpool(source.write_bytes, upload_bytes)
        try:
            result = await run_in_threadpool(
                store.upload_excel, source, source_name=original_name,
                sheet=confirmed_sheet, partial_dates=confirmed_partial_dates,
            )
        except VersionError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.as_dict())
        return JSONResponse(content=result)
```

ADE-46은 업로드 스트림을 제한하며 읽고, 임시 원본의 생성·권한·삭제를 책임집니다.
임시 파일을 스레드 작업이 끝나기 전에 삭제하지 않습니다. 위 정상 반환·예외 경로에서는
TemporaryDirectory가 종료되며 삭제합니다. HTTP 작업의 취소·서버 종료 시 정리 정책도
API 계층에서 확인해야 합니다. store는 자체 JSON staging만 정리하며 업로드 원본을 삭제하지 않습니다.
임시파일 생성/수신 실패는 store 호출 전 API 계층의 오류로 처리합니다.
목록·롤백도 `await run_in_threadpool(store.list_versions)` 및
`await run_in_threadpool(store.rollback, selected_id)`로 호출합니다.
새 프로세스를 HTTP 요청마다 생성하거나 API 계층에 별도 순환 저장을 구현하지 않습니다.

| 호출 | 성공 결과 | 실패 처리 |
| --- | --- | --- |
| upload_excel | `status=published`, DataVersion, ValidationSummary | INVALID_EXCEL 422, PUBLISH_IN_PROGRESS 409 |
| list_versions | `versions`, `active_version_id`, `max_versions=4` | 변경 중이면 PUBLISH_IN_PROGRESS 409 |
| rollback | `status=rolled_back`, `active_version_id` | VERSION_NOT_FOUND 404, PUBLISH_IN_PROGRESS 409 |
| 저장·복구 공통 | 성공한 JSON 메타데이터 커밋 상태 유지 | PUBLISH_FAILED 500 (아래 제안 사항) |

`VersionError.as_dict()`는 `{error: {code, message, details}}`입니다.
행별 오류의 `message`는 v2 `reason`으로 변환합니다. 경고는 `code/message/row`로 반환하고
세부 원본 합계 숫자는 표준 응답에 추가하지 않습니다. `record_count`는 이력 병합 후 전체 스냅샷 개수입니다.

### 승인된 계약과 내부 오류의 구분

- 정상 기존 파일은 첫 `list_versions`, `upload_excel`, `rollback`에서 잠금 안에 최초 등록하므로
  정상 목록은 항상 유효한 VersionId를 반환합니다. 생성자에는 파일 접근 부작용이 없습니다.
- 활성본이 없으면 내부적으로 빈 메타데이터를 만들 수 있지만 목록은 `PUBLISH_FAILED/500`으로 실패합니다.
  `active_version_id=null` 성공 응답이나 가짜 버전은 만들지 않습니다. 정상 Excel의 첫 게시로 초기화할 수 있습니다.
- 등록 전 활성본이 손상되거나 빈 배열이면 등록·게시를 거부하고 기존 파일을 보존합니다.
  초기화는 정상 데이터의 검증·등록 문제이고, 데이터가 없는 상태의 HTTP 응답 규격은 별도 계약 문제입니다.
- `PUBLISH_FAILED/500`(저장·재계산·복구·초기 데이터 없음)과 목록 잠금 충돌 `PUBLISH_IN_PROGRESS/409`는
  기존 내부 오류 인터페이스를 유지합니다. OpenAPI에 없는 응답의 승인·명시는 ADE-43/46에서 필요합니다.
  이 작업은 `contracts/openapi-v2.yaml`과 fixtures를 임의로 변경하지 않습니다.
- VersionId는 실제 OpenAPI 패턴 `^[0-9]{8}T[0-9]{6}[+-][0-9]{4}$`의 KST ID를 사용합니다.
  같은 초의 게시에서는 보관된 최신 ID보다 논리 시각을 1초 증가시켜 충돌을 막습니다.
  업로드 `created_at`은 실제 KST 생성시각이며 ID를 실제 업로드 시간으로 해석하지 않습니다.
- 최초 등록의 시각은 기존 파일 mtime입니다. 단일 `source_file`이면 그 기존 출처를,
  복수 출처면 기존 JSON 파일명을 `source_name`으로 기록합니다. 실제 Excel 업로드 이벤트를 꾸미지 않습니다.
  실제 최초 생성시각이나 업로드 이력을 알 수 없다는 의미는 API 연결 시에도 유지해야 합니다.

## 저장 구조와 커밋 순서

```text
<LIBRARY_RECORDS>                            활성 records.json
<LIBRARY_RECORDS>.versions/metadata.json     JSON 커밋 메타데이터
<LIBRARY_RECORDS>.versions/<VersionId>.json  정상 스냅샷 최대 4개 (최초 등록 포함)
<LIBRARY_RECORDS>.versions/writer.lock       OS가 소유하는 공용 프로세스 잠금
```

메타데이터는 `format_version=1`, `active_version_id`, 최신순 `versions`를 저장합니다.
각 버전은 id, created_at, source_name, record_count와 내부 검증용 sha256을 가집니다.
sha256은 API 응답에 추가하지 않습니다. 정상 데이터의 유일한 기준은 이 메타데이터와
검증된 버전 JSON입니다. 활성 파일은 선택된 정상 스냅샷과 바이트까지 일치하도록 복구합니다.
SQLite 읽기·쓰기·생성, DB 연결 및 DB 전용 구현은 제거했습니다. 기존 requirements에도
SQLite 전용 패키지는 없었으므로 다른 의존성은 삭제하지 않았습니다.

첫 버전 등록은 OS 잠금 안에서 기존 파일의 스키마·비어 있지 않음·통계·예측을 검증하고,
원래 바이트를 버전 JSON으로 저장한 뒤 메타데이터를 교체합니다. 활성 파일은 수정하지 않습니다.
반복 호출과 여러 worker 초기화는 같은 메타데이터를 재사용합니다. 등록 중 종료되면
원래 활성 파일로 다시 등록하거나 이미 커밋된 등록을 사용하며 중복 정상 버전을 만들지 않습니다.
기존 품질 필드와 원본 표현도 보존합니다.

업로드는 공용 전처리 후 잠금 안에서 이전 이력 병합과 rebuild를 수행합니다.
JSON은 각각 목적지와 같은 파일시스템의 임시파일에 완성하고 fsync·검증 후 os.replace합니다.
Linux는 교체 후 부모 디렉터리도 fsync합니다. 다중 파일 전체를 자동 원자 작업으로 간주하지 않습니다.

1. 검증 완료한 새 불변 버전 JSON을 저장합니다. 이 시점에는 기존 4개를 삭제하지 않습니다.
2. 활성 records.json을 새 스냅샷으로 원자 교체합니다. 목록·관리 조회는 같은 잠금으로 중간 상태를 보지 않습니다.
3. metadata.json을 새 목록·활성 ID로 원자 교체합니다. 이것이 커밋 지점입니다.
4. 새 메타데이터에 없는 가장 오래된 스냅샷과 임시 흔적을 정리합니다.

커밋 전 예외에서는 이전 메타데이터·활성본을 복구합니다. 메타데이터 rename 직후 예외도
이전 메타데이터로 되돌린 후 활성 파일을 복구하며, 기존 정상 4개를 삭제하지 않습니다.
지속적인 디스크·권한 오류로 즉시 복구가 안 되면 오류를 반환하고, 쓰기 가능해진 뒤 다음 관리 조회에서 재시도합니다.
SIGKILL이 메타데이터 커밋 전에 발생하면 다음 프로세스는 이전 정상본으로 복구합니다.
커밋 후 종료되면 새 정상본이 기준이며 응답 전달 전 종료됐더라도 임의로 이전 버전을 선택하지 않습니다.

정리는 커밋 후에만 수행하며 삭제·디렉터리 조회 실패를 게시 실패로 반환하지 않습니다.
다음 잠금 작업에서 재시도합니다. 목록의 정상 버전은 항상 최대 4개이며,
삭제 장애나 커밋 직후 종료 시 참조되지 않는 물리 파일이 잠시 남을 수 있습니다.
이는 정상 버전으로 표시하거나 롤백 대상으로 사용하지 않습니다.
`.*.staged-*`, `.prepared-*`, rebuild의 `*.tmp`, 메타데이터에 없는 VersionId JSON만 정리합니다.
알 수 없는 파일과 writer.lock은 삭제하지 않습니다. 별도의 영구 데이터베이스나 복구 상태 파일은 사용하지 않습니다.

롤백은 보관된 정상 JSON을 검증·재계산한 뒤 동일 커밋 순서로 활성 ID를 바꿉니다.
새 업로드 버전을 만들지 않고 목록 순서를 바꾸거나 보관 개수를 늘리지 않습니다.
가장 오래된 버전으로 롤백 후 게시해도 이력을 해당 활성본 기준으로 병합하고,
ID는 최신 보관 ID보다 크게 생성하며, 성공 커밋 후에만 가장 오래된 버전을 삭제합니다.
없는 버전 또는 경로 형태의 ID는 기존 `VERSION_NOT_FOUND/404`입니다.

Windows는 msvcrt.locking, Linux는 flock으로 CLI·서버·여러 worker를 보호합니다.
게시·등록·목록·롤백은 충돌 시 409이고 FileProvider의 조회는 최대 10초 기다립니다.
worker 종료 시 OS가 잠금을 해제합니다. writer.lock 파일을 수동 삭제하지 않습니다.
분산 잠금은 제공하지 않으므로 모든 worker는 동일 영구 로컬 경로를 사용해야 합니다.

## 기존 경로와 캐시 연결

FileProvider는 버전 관리가 시작된 경로에서 같은 잠금으로 메타데이터·스냅샷을 검증하고
활성 파일을 복구한 뒤 동일 핸들의 payload/stat을 읽습니다. 파일 hash·mtime 변경 시
LibraryService를 새로 구성해 통계·예측·추정 체류를 재계산합니다. worker별 다음 요청에도 적용됩니다.

버전 관리 전의 이용자 읽기만으로 자동 등록하지는 않습니다. 기존 업로드 경로와의 호환을 위해
전환 시 ADE-46에서 `list_versions` 등으로 명시적으로 초기 등록해야 합니다.
metadata.json이 생기면 Excel refresh는 VersionStore로 위임하고, 기존 JSON 업로드 API와
JSON rebuild CLI는 저장소 우회를 거부합니다. 저수준 rebuild 함수는 임시 경로에만 사용합니다.
새 관리자 API 연결 전에 초기화하면 기존 업로드가 거부되므로 ADE-50에서 전환 순서를 검증해야 합니다.
외부 CDN/HTTP 캐시는 이번 구현에 없으며 추가 시 ADE-46/50 연결이 필요합니다.

## 운영 복구 및 이전 구현 전환

서비스를 중지하거나 공용 잠금을 유지한 상태에서 활성 파일과 `.versions` 전체를 함께 백업합니다.
첫 관리 조회는 모든 보관 JSON의 스키마·레코드 수·해시와 메타데이터를 검증하고 활성 파일을 복구합니다.

```python
store = VersionStore(configured_records_path)
versions = store.list_versions()  # 정상 기존 파일 최초 등록 또는 복구
payload, stat = store.read_snapshot()
```

활성 파일만 손상·유실됐고 정상 보관본이 있으면 메타데이터에 지정된 활성 스냅샷으로 복구합니다.
보관 JSON·메타데이터 자체가 손상됐으면 추측한 버전을 활성화하지 않고 거부합니다.
DB에 숨겨진 payload로 복구하지 않습니다. 서비스 중지 후 일관된 정상 전체 백업을 복원해야 합니다.
메타데이터만 지워 재초기화하는 방식은 정상 복구 절차가 아닙니다.

이전 실험 구현의 state.sqlite3가 남아 있어도 새 구현은 읽거나 열거나 삭제하지 않습니다.
기존 DB 이력의 자동 이전은 제공하지 않습니다. 전환 전에 서비스를 중지하고
정상 활성 JSON을 확인·백업한 뒤 초기 등록합니다. 이전 DB에만 있는 역사 복원은 별도 운영 결정이며,
이번 변경은 실제 운영 파일이나 과거 DB를 수정하지 않았습니다.

## 이번 실행 검증 · 2026-10-04

이번 결과는 과거 211 passed 기록을 재사용한 것이 아닙니다. 합성 records·Excel만 임시 경로에 생성했습니다.
실제 Excel·운영 JSON·개인정보·비밀값은 추가하거나 변경하지 않았습니다.

Windows Python 3.12.14, 작업 경로 `C:\study\library-service-ade45`의 기존 Windows 가상환경:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_versions.py tests/test_versions_migration.py tests/test_versions_process_recovery.py tests/test_versions_json.py -q
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m scripts.e2e
.venv\Scripts\python.exe -m scripts.e2e_present
git diff --check
```

- 관련 테스트: 46 passed, POSIX 전용 11 skipped, 0 failed.
- 전체: 220 passed, 11 skipped, 0 failed.
- 두 E2E: PASS. 전체 pytest의 기존 Starlette/httpx deprecation 경고 1개.

WSL Ubuntu Python 3.12.3, ext4 `/home/psy/ade45-json-20261004-d3UPyj`:
기존 Linux 전용 가상환경 `/home/psy/ade45-linux-20261003-e9oKAZ/.venv`를 심볼릭 링크로 재사용했습니다.
Windows 가상환경은 사용하지 않았고 checkout과 합성 임시 데이터는 ext4에 있습니다.

```sh
cd /home/psy/ade45-json-20261004-d3UPyj
.venv/bin/python -m pytest tests/test_versions.py tests/test_versions_migration.py tests/test_versions_process_recovery.py tests/test_versions_json.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.e2e
.venv/bin/python -m scripts.e2e_present
```

- 관련 테스트: 57 passed, 0 skipped/failed.
- 전체: 231 passed, 0 skipped/failed.
- 두 E2E: PASS. 전체 pytest의 기존 Starlette/httpx deprecation 경고 1개.
- Linux SIGKILL 11개 경우: 최초 등록 rebuild/버전 파일/메타데이터 경계 3개,
  게시 rebuild/버전 파일/활성 파일/커밋 직전/메타데이터 교체/정리와
  롤백 커밋 직전/메타데이터 교체 경계 8개. 새 프로세스로 파일·목록·활성 ID와
  실제 stats/patterns/today 결과를 비교하며 후속 게시·잠금 해제도 검증합니다.
- 메타데이터 커밋 이후 경우는 새 정상 상태, 이전 경우는 기존 정상 상태로 복구하는지 확인합니다.
- 초기 등록·품질 바이트 보존·반복/4개 프로세스 초기화·활성본 없음/손상/빈 배열,
  정상/오류 Excel·최대 4개/5번째 정리·롤백/없는 ID·과거 롤백 후 게시·잠금 충돌,
  fsync/rebuild/파일 교체/메타데이터 예외·정리 재시도·JSON 손상·해시 불일치를 검증했습니다.
- SQLite connect를 실패시키는 상태에서 초기 등록·게시·롤백·재시작 목록이 성공하고
  임시 경로에 sqlite/db 파일이 없는지 검사합니다. 실행 코드·requirements의 DB 참조도 검색합니다.
- 정상 초기 목록과 업로드·목록·롤백 결과를 실제 OpenAPI schema로 검증하고 기존 이용자 API 회귀를 유지합니다.

기존 DB payload 손상 테스트를 JSON 메타데이터 손상·정상 보관본 손상/유실/해시 불일치 테스트로
대체했습니다. 삭제 실패 항목은 커밋 후 정리 재시도 테스트로 옮겼습니다. 테스트 개수는 211→231로
20개 증가했고 저장 실패·정리·손상 검증을 없애 개수를 줄이지 않았습니다.

## 남은 의존성과 미검증 환경

ADE-44 검증 계약·공통 fixture 확정 후 공용 전처리 대조, ADE-43/46의 초기 데이터 없음·500·목록 충돌
응답 명시, ADE-46 세션·관리자 API 연결, ADE-50 실제 PythonAnywhere worker·파일시스템·운영 데이터/API·
전체 웹 통합 검증이 남아 있습니다. 실제 전원 장애·영구 디스크 손상·분산 파일시스템은 미검증입니다.
WSL ext4의 SIGKILL 복구 결과를 PythonAnywhere 운영 검증으로 해석하지 않습니다.
PR Draft와 Linear In Progress를 유지하며 이 작업에서 병합·배포하지 않습니다.
