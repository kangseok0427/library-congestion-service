# ADE-45 서버 Excel 게시·JSON 4세대·롤백 인수인계

갱신일: 2026-10-04. 브랜치: `codex/ADE-45-server-json-versions`.
팀 PR #20, 대상 `develop`. 작업 경로는 `C:\study\library-service-ade45`입니다.
이번 보완 전 HEAD와 fork 브랜치는 `6277925b084e61d2e283fb39ea2789b54154296f`,
GitHub에서 확인한 develop은 `95ed76e3f925ed450ded869182a904d74f185547`입니다.
미커밋 변경은 없었고 저장소 및 상위 경로에 AGENTS.md는 없었습니다.
README, 협업·구조·갱신·검증·호스팅 지침, CI, OpenAPI v2와 관련 fixture를 확인했습니다.

## 최신 리뷰와 범위

GitHub PR #20의 2026-10-04 팀장 Changes requested 리뷰를 이번 작업에서 직접 확인했습니다.
JSON 전용 저장과 초기 활성본 등록 요구는 사용자 기록과 일치했습니다.
PR은 Open/Draft이며 Draft 해제 지시는 없었습니다. Linear 상태는 변경하지 않습니다.
ADE-48/PR #21은 독립 작업이며 이 브랜치에 합치지 않습니다.

이전 보고의 Linear 상태 조회는 이번 작업의 새 확인 결과로 사용하지 않습니다.
로컬 저장소에 `docs/excel-upload-contract.md` 및 ADE-44 공통 Excel fixture는 아직 없습니다.
다른 비공개 작업의 부재를 뜻하지 않으며 ADE-44·46·50 승인·연결은 별도 의존성으로 유지합니다.

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
<LIBRARY_RECORDS>.versions/metadata.json     등록 목록·활성 ID·내부 해시
<LIBRARY_RECORDS>.versions/recovery.json     작업 중 이전/다음 상태와 확정 여부
<LIBRARY_RECORDS>.versions/<VersionId>.json  등록 정상 최대 4개 + 후보/잔여 최대 1개
<LIBRARY_RECORDS>.versions/writer.lock       공용 OS 프로세스 잠금
```

SQLite는 읽거나 생성하지 않습니다. API 성공 응답과 VersionStore/VersionError 인터페이스는 유지합니다.
이번 두 문제는 외부 `recovery.json` 제거·과거 파일 단독 복원으로 재현했습니다.
SIGKILL만으로 이 파일 유실·재등장이 발생했다고 주장하지 않습니다.

### 형식 2의 연결 정보

metadata.json은 `format_version=2`, `generation`, `transaction_id`, `recovery_required`,
`recovery_digest`, `active_version_id`, `versions`를 저장합니다. 세대는 성공 확정마다 1 증가하며,
실패 작업 복구는 이전 세대를 유지합니다. 시도마다 UUID의 32자리 hex ID를 새로 만들어
실패 후 같은 목표 세대를 재사용하더라도 서로 다른 작업임을 구분합니다. 공개 VersionId와는 별개입니다.

recovery.json은 `format_version=2`, `generation`, `transaction_id`, `phase=prepared|committed`,
`initialize`, `previous`, `next`를 저장합니다. previous/next는 복구 필수 표식이 없는 메타데이터입니다.
목표 세대는 이전 세대+1이며, next와 기록의 ID·세대가 같아야 합니다.
`recovery_digest`는 phase를 제외한 불변 게시 의도의 canonical JSON SHA-256입니다.
prepared→committed 전환 중 동일하게 유지하며 기록 본문의 부분 변경을 감지합니다.
암호화 서명·외부 DB·별도 서비스 또는 외부 인증 기준은 추가하지 않았습니다.

대기 상태는 `recovery_required=false`, `recovery_digest=null`입니다. 표식이 true이면
기록이 반드시 존재하고 ID·세대·게시 의도 digest·전체 메타데이터 상태가 일치해야 합니다.
형식·연결·상태가 잘못되면 활성본·버전 파일을 쓰거나 정리하기 **전에** 거부합니다.
FileProvider도 이 검증을 거쳐 조회하므로 이전 캐시가 있어도 미검증 데이터를 정상으로 반환하지 않습니다.

### 저장과 최종 확정

공용 OS 잠금 안에서 다음 순서로 실행합니다.

1. 이전 잔여 VersionId JSON을 정리합니다. 실패하면 새 후보 생성 전에 게시를 거부합니다.
2. 이전/다음 상태와 새 ID·세대를 담은 prepared 복구 기록을 저장합니다.
3. 이전 활성 ID·목록을 유지한 채 metadata.json에 새 ID·세대·복구 필수 표식·digest를 저장합니다.
   **이 표식이 내구성 있게 저장되기 전에는 버전 JSON과 활성 파일을 변경하지 않습니다.**
4. 후보 버전 JSON과 활성 파일을 저장하고, 다음 활성 ID·목록을 담은 복구 필수 metadata.json을 저장합니다.
5. 복구 기록을 committed로 원자 교체합니다. 이것이 논리적 최종 확정 지점입니다.
6. metadata.json을 같은 ID·세대의 대기 상태로 저장합니다. 그 후 복구 기록을 제거하고 디렉터리를 동기화합니다.
7. 오래된 버전 파일을 정리합니다.

각 파일은 같은 파일시스템의 임시파일에 완성하고 파일 fsync·검증 후 os.replace합니다.
Linux는 부모 디렉터리도 fsync합니다. Windows는 동일한 디렉터리 fsync를 제공하지 않습니다.
다중 파일 전체를 원자 작업으로 보거나 실제 전원 장애 내구성을 검증했다고 표현하지 않습니다.

최종 committed 교체 후 fsync가 실패하면 실제 교체 완료에 근거해 성공으로 판정하고,
복구 기록과 복구 필수 메타데이터를 유지하며 정리를 보류합니다. 이후 대기 표식 저장·기록 삭제·정리
오류도 이미 확정된 게시를 500으로 바꾸지 않습니다. 기록이 남으면 다음 잠금 작업에서 복구를 재시도합니다.
이 예외 구간의 전원 장애 결과는 미검증입니다. 응답 전달 실패에 대한 별도 멱등성 API는 추가하지 않았습니다.

### 허용하는 복구 조합

| 기록 | 현재 메타데이터 | 선택 결과 |
| --- | --- | --- |
| 없음 | 형식 2 대기 상태 | 검증한 현재 상태 사용 |
| prepared | 정확한 이전 대기 상태 | 기록 작성 후 표식 저장 전 종료: 이전 상태 |
| prepared | 동일 ID·세대·digest의 변경 전/후 복구 필수 상태 | 이전 상태 |
| committed | 동일 ID·세대·digest의 변경 후 복구 필수 상태 | 다음 상태 |
| committed | 정확한 다음 대기 상태 | 대기 표식 저장 후 기록 제거 전 종료: 다음 상태 |
| 없음 | 복구 필수 상태 | 거부: 확정 여부를 추측하지 않음 |
| 손상·다른 ID/세대·오래된 상태·허용되지 않는 조합 | 어떤 상태든 | 거부: 파일 변경·정리 없음 |

초기 등록의 prepared 기록에서만 previous=null과 메타데이터 없음이 허용됩니다.
원래 활성본은 초기 등록 중 쓰지 않고 바이트·품질·mtime 출처를 유지합니다.
초기 등록 복구는 원본을 유지하며 미완료 메타데이터를 제거한 뒤 재등록할 수 있습니다.
committed 기록과 변경 전 표식의 혼합은 같은 ID·세대라도 거부합니다.

prepared 복구는 이전 보관본 전체를 검증하고 활성 파일·이전 메타데이터를 다시 저장합니다.
committed 복구는 다음 보관본 전체를 검증하고 활성 파일·다음 대기 메타데이터를 다시 저장합니다.
내용이 같아도 이전 fsync 실패 가능성이 있으므로 쓰기를 생략하지 않습니다.
이 두 저장이 완료되기 전에는 기록을 제거하지 않습니다. 복구가 실패하거나 종료돼도 같은 기록으로 재시도합니다.
지속 장애 중 즉시 복원을 보장할 수 없으며 새 게시·롤백·정리를 제한합니다.

### 이전 형식의 전환과 전체 복원 제한

형식 1 메타데이터는 정상 확정과 기록을 잃은 실패 상태를 구분할 정보가 없습니다.
활성본과 보관 JSON의 해시가 맞고 recovery.json이 없어도 확정 완료라고 단정할 수 없습니다.
**형식 1 메타데이터·복구 기록은 자동 변환하거나 자동 복구하지 않고 거부합니다.**
관리되지 않은 정상 records.json만 있는 경로의 최초 등록은 형식 2로 계속 지원합니다.

기존 형식 1 저장소의 읽기·게시·롤백은 새 코드에서 차단됩니다. 전환 전에 서비스를 중지하고
원본 전체 백업·정상 확정 근거를 진단해야 합니다. 승인된 완전한 정상 상태를 새 형식으로 이관하는
운영 전환은 별도 확인 사항이며, 이번 변경에 확정 근거를 우회하는 자동 변환기나 관리자 endpoint를 추가하지 않습니다.

전체 상태가 과거의 일관된 백업으로 함께 복원되면 외부 기준 없이 그 과거 복원을 감지할 수 없습니다.
이번 연결 검증은 확인된 기록 유실·단독 복원·혼합 ID/세대/상태를 감지하는 범위입니다.
일관된 모든 파일을 함께 조작하는 경우나 일반 파일시스템 손상 전체를 해결한다고 주장하지 않습니다.

### 등록 4개와 실제 파일 상한 — 제안 정책, 팀장 승인 필요

- 등록 정상 목록은 최대 4개입니다. 정상 정리 완료 후 디스크 VersionId JSON도 최대 4개입니다.
- 기존 정상 4개를 실패 시 보존하려면 새 후보 저장 중 5번째 파일이 필요합니다.
  새 구현의 깨끗한 시작 상태에서는 후보 또는 삭제 실패 잔여를 포함해 **실제 VersionId JSON 최대 5개**입니다.
- 확정 후 삭제 실패는 해당 게시의 성공을 유지합니다. 다음 게시 전에 잔여를 재삭제하고,
  계속 실패하면 PUBLISH_FAILED/500으로 추가 게시를 거부하므로 5→6→7개 누적을 막습니다.
- 단순 잔여 삭제 장애에서는 정상 조회·목록·기존 버전 롤백을 허용합니다. 롤백은 파일을 생성하지 않습니다.
  재시작·조회도 정리를 재시도하며, 장애 해제 후 4개로 정리되고 후속 게시가 가능합니다.
- 구 구현에서 이미 6개 이상 쌓였거나 외부에서 파일을 추가한 경우 소급하여 5개를 보장하지 않습니다.
  잔여가 모두 정리되기 전에는 추가 후보를 생성하지 않습니다.
- metadata.json, recovery.json, writer.lock, staging/재계산 임시파일은 VersionId JSON 개수에서 제외합니다.
  임시파일 삭제까지 지속 실패하는 상황의 전체 디스크 바이트 상한을 보장하는 정책은 아닙니다.

정리는 공용 잠금 아래 참조되지 않는 VersionId JSON 및 알려진 staging/재계산 임시파일만 대상으로 합니다.
활성·등록 버전, recovery.json, 알 수 없는 파일과 writer.lock은 삭제하지 않습니다.
엄격한 물리 4개 제한과 기존 4개 보존은 후보 저장 단계에서 양립하지 않습니다.
일시/삭제 장애 중 5개 허용과 추가 게시 거부 정책은 제안이며 팀장 승인 완료라고 해석하지 않습니다.

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
metadata.json 또는 recovery.json이 생기면 Excel refresh는 VersionStore로 위임하고, 기존 JSON 업로드 API와
JSON rebuild CLI는 저장소 우회를 거부합니다. 저수준 rebuild 함수는 임시 경로에만 사용합니다.
새 관리자 API 연결 전에 초기화하면 기존 업로드가 거부되므로 ADE-50에서 전환 순서를 검증해야 합니다.
외부 CDN/HTTP 캐시는 이번 구현에 없으며 추가 시 ADE-46/50 연결이 필요합니다.

## 운영 진단·백업·복원

불일치·기록 유실이 발견되면 쓰기 작업을 중지하고 활성 파일과 `.versions` 전체를 먼저 함께 백업합니다.
서비스 중지 또는 공용 잠금 아래 메타데이터 형식·ID·세대·복구 필수 표식·digest와 기록·보관본을 진단합니다.
그 후 확정 근거가 있는 일관된 전체 정상 백업을 함께 복원하는 절차를 우선합니다.

**recovery.json만 삭제하거나 다른 시점의 기록만 복원하는 것을 해결책으로 사용하지 않습니다.**
복구 필수 표식을 false로 바꾸거나 세대/ID를 수동으로 맞춰 오류를 우회하지 않습니다.
형식 1은 별도 전환 진단이 필요하며 이 코드가 자동으로 정상 확정을 추측하지 않습니다.

형식 2의 정상 연결 상태에서 활성본만 손상·유실됐고 보관본이 정상이면 검증한 스냅샷으로 복구합니다.
보관본·메타데이터·기록이 손상되거나 연결되지 않으면 거부합니다. 파일 하나만 지워 재초기화하지 않습니다.
DB에 숨겨진 payload로 복원하지 않습니다. 실제 운영 환경의 권한·파일시스템·worker 전환은 ADE-50 검증 사항입니다.

이전 실험 구현의 state.sqlite3가 남아 있어도 새 구현은 읽거나 열거나 삭제하지 않습니다.
기존 DB 이력의 자동 이전은 제공하지 않습니다. 전환 전에 서비스를 중지하고
정상 활성 JSON을 확인·백업한 뒤 초기 등록합니다. 이전 DB에만 있는 역사 복원은 별도 운영 결정이며,
이번 변경은 실제 운영 파일이나 과거 DB를 수정하지 않았습니다.

## 이번 복구 기록 연결 보완 검증 · 2026-10-04

기준 SHA `6277925b084e61d2e283fb39ea2789b54154296f`에서 기존 record_loss.py를 재실행해
외부 기록 제거 후 게시 108→492·기존 삭제·재시도 동일 데이터 2개, 롤백 108→36을 확인했습니다.
기록 유실(게시/롤백)과 과거 committed 기록 재삽입의 새 테스트 3개가 Windows/Linux 기존 코드에서 실패했습니다.
수정 후에는 새 프로세스·기존 캐시가 있는 이용자 조회·게시·롤백 모두 거부하며,
거부 직전 활성본·메타데이터·기록·버전 파일의 바이트가 변경되지 않는지 검증합니다.

새 회귀는 ID·세대·불변 의도 digest·메타데이터/phase 조합 불일치, 손상 JSON,
형식 1의 자동 전환 거부, 표식/대기 상태 저장 전후·기록 삭제 후의 Linux SIGKILL 10개,
표식과 최종 대기 저장의 게시/롤백 연속 장애 4개를 포함합니다.
기존 fsync 테스트는 새 표식 쓰기가 아니라 **활성 ID가 변경된 다음 메타데이터 교체**를 대상으로
주입하도록 유지했습니다. 기존 복구 재실패·반복 SIGKILL·정리 실패 상한·초기 등록·통계·예측도 실행합니다.

합성 데이터만 저장소 밖 임시 checkout과 pytest basetemp/E2E test-results에 생성했습니다.
Windows: `C:\Users\user\AppData\Local\Temp\ade45-identity-validation-txhm8ihb\windows`.
Linux: WSL Ubuntu ext4 `/home/psy/ade45-identity-g63ACV`.
각 플랫폼의 기존 가상환경을 사용합니다. Linux는 `/home/psy/ade45-linux-20261003-e9oKAZ/.venv`입니다.

```text
python -B -m pytest tests/test_versions.py tests/test_versions_migration.py tests/test_versions_process_recovery.py tests/test_versions_json.py tests/test_versions_durable.py tests/test_versions_identity.py -q -p no:cacheprovider --basetemp <temporary-path>
python -B -m pytest -q -p no:cacheprovider --basetemp <temporary-path>
python -B -m scripts.e2e
python -B -m scripts.e2e_present
git diff --check
```

| 직접 실행 환경 | 관련 pytest | 전체 pytest | scripts.e2e | scripts.e2e_present |
| --- | --- | --- | --- | --- |
| Windows Python 3.12.14 | 71 passed / 31 skipped | 245 passed / 31 skipped | PASS | PASS |
| WSL Ubuntu Python 3.12.3, ext4 | 102 passed | 276 passed | PASS | PASS |

마지막 코드 변경 후 두 환경에서 모두 다시 실행했습니다. Windows skips는 Linux 전용
fsync/SIGKILL 검증이며 Linux에서 실행했습니다. Starlette/httpx 사용 중단 예정 경고 1개가 남습니다.
기존 248 passed 결과를 새 검증으로 사용하지 않았습니다. GitHub CI 결과는 PR 본문에 별도로 기록합니다.

## 남은 의존성과 미검증 환경

ADE-44 검증 계약·공통 fixture 확정 후 공용 전처리 대조, ADE-43/46의 초기 데이터 없음·500·목록 충돌
응답 명시, ADE-46 세션·관리자 API 연결, ADE-50 실제 PythonAnywhere worker·파일시스템·운영 데이터/API·
전체 웹 통합 검증이 남아 있습니다. 실제 전원 장애·영구 디스크 손상·분산 파일시스템은 미검증입니다.
WSL ext4의 SIGKILL 복구 결과를 PythonAnywhere 운영 검증으로 해석하지 않습니다.
PR Draft를 유지하며 Linear 변경·병합·배포하지 않습니다.
