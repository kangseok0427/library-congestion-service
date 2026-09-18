# ADE-21 · T02/T08 JSON 갱신

## 작업 기준과 변경 이유

사용자가 전달한 팀장 승인에 따라 PR #1 병합을 기다리지 않고 작업했다.
기준 브랜치는 `feature/ADE-15-16-integration-forecast`, 기준 커밋은
`8a6b1a4727593d596a43e365133eb2f84007aa93`이다. 당시 develop은
`ee9ea709f79aeac72b1a7e93c2cd0f5a2ec08592`로 초기 구조만 있었다.
작업 브랜치는 `feature/ADE-6-12-json-refresh`이며 종료된 PR #2는 사용하지 않았다.
PR #1이 미병합이면 그 브랜치 대상 stacked PR로 제출한다. PR #1 병합 후 팀장이
develop 기준으로 변경분과 CI를 다시 확인한다. 직접 병합하지 않는다.

기존 `psy0635-ctrl/library-crowding-etl`의 `d8d26c2`에서 전처리 검증을 재사용했다.
DB 트랜잭션, 테이블, DB 조회·내보내기 명령은 가져오지 않았다.
실행 저장소는 records.json 하나이며 API는 `LIBRARY_RECORDS`로 같은 파일을 읽는다.
원래 ETL 저장소와 그곳의 미커밋 설계 문서는 보존했다.

## 수정 파일과 책임

| 파일 | 역할 |
| --- | --- |
| library_etl/pipeline.py | XLSX 형식·시트·필드·날짜·수치·중복 검증, v1.1 records와 경고 생성 |
| library_etl/refresh.py | 동시 쓰기 차단, 날짜·게이트 병합, 임시 JSON/rebuild, 원자적 게시 |
| library_etl/__main__.py | 로컬 refresh CLI, 오류 코드·진단 반환 |
| backend/adapters.py | 기존 from_excel 진입점을 T02로 위임 |
| backend/domain.py | 필수 12필드 + 선택 품질 필드 검증, null OUT 전파 |
| backend/service.py | 시간대 OUT 합계의 null 보존 |
| backend/app.py | 동일 파일 핸들의 내용·시각을 읽어 API 캐시 갱신 |
| scripts/rebuild.py | 통계·예측 직렬화 확인 후 기록, Excel CLI는 T08로 위임 |
| tests/test_json_refresh.py | 합성 XLSX/JSON의 정상·실패·반복·API 통합 검증 |
| scripts/e2e.py | 실제 Chromium의 Excel 갱신/실패 후 화면 확인 |

backend 수정은 T02 v1.1 출력을 기존 통계·예측·API가 소비하는 데 필요한 호환 범위다.
baseline 기준 변경, 공식 휴관 달력, 운영시간 추천 제한 등 별도의 LEAD 기능은 바꾸지 않았다.

## 안전한 갱신 순서

1. 원본 XLSX 전체를 검사한다. 요약 행은 정확한 전체/그룹/합계 또는 평균 조합만 제외한다.
2. `.records.json.lock` 디렉터리 생성으로 CLI 프로세스 간 쓰기를 배제한다. 다른 갱신은 즉시 오류를 반환한다.
3. 기존 JSON을 검증한다. 기존 파일이 손상됐으면 이를 무시하고 덮어쓰지 않는다.
4. 입력에 있는 `(date, gate)`의 16시간 그룹만 교체한다. 다른 날짜·게이트는 그대로 보존한다.
   입력의 최소~최대 날짜 전체를 삭제하지 않는다. 완료 그룹을 partial로 낮추는 갱신은 거부한다.
5. 운영 파일과 같은 디렉터리 아래 임시 JSON을 쓰고 flush/fsync한다.
6. `rebuild(records, staged_destination)`를 호출한다. API 서비스의 통계·예측 계산과
   JSON 직렬화도 확인한다. staged JSON을 다시 읽어 병합 결과와 동일한지 확인한다.
7. 마지막에 `os.replace` 한 번으로 운영 JSON을 교체한다. 기존 파일을 먼저 삭제하지 않는다.

전처리·임시 기록·rebuild·최종 교체 실패 시 기존 JSON 바이트/mtime은 그대로다.
API가 이전 파일을 계속 읽으므로 기존 응답도 유지한다. 임시 작업 영역은 정리한다.
교체 후 잠금 정리 실패는 성공을 실패로 뒤집지 않고 `cleanup_warnings`로 알린다.
프로세스 강제 종료로 잠금이 남으면 실행 중인 갱신이 없는지 확인한 후 운영자가 제거한다.
원자적 교체는 실행 중 오류에 대한 보호이며 정전·디스크 손실까지의 백업 보장은 아니다.
다른 도구가 운영 JSON을 임의로 훼손하면 API는 오류를 표시한다. 이 동작은 기존 정책을 유지한다.

현재 API는 파생 결과 파일을 별도로 저장하지 않고 records에서 서비스를 구성한다.
따라서 통계/예측/records 파일을 여러 번 교체하는 부분 성공 문제가 없다.
FileProvider는 파일 내용을 해시하므로 동일 크기·동일 mtime 교체도 감지한다.
대신 요청마다 JSON 파일 크기만큼 읽는 비용이 있다. MVP 규모의 정확성을 우선한다.

## v1.1 호환과 결측 의미

- 기존 필수 12개 필드는 그대로다. 선택 필드가 없는 v1 JSON도 읽는다.
- 선택 필드: is_closed_day(boolean/null), is_low_volume(boolean), quality_note(string/null).
  원본 records를 검증/병합/rebuild할 때 삭제하지 않는다. 새 Excel이 교체하는 그룹은 새 입력이 기준이다.
- OUT_11은 복제 결함 때문에 전처리 출력에서 항상 null이다. 다른 시간의 잘못된 OUT은 오류다.
- 게이트 OUT 중 하나라도 null이면 그 시간대 OUT 집계는 null이다. 시간대 중 하나라도 null이면
  hourly_total_out도 null이다. 별도의 원본 total_out은 정수 그대로 날짜·게이트당 한 번만 합산한다.
- 품질 필드는 시간대 API에도 전달한다. 휴관 여부는 하나라도 true면 true, 모두 false면 false,
  그 외에는 null이다. 저수집 여부는 하나라도 true면 true, note는 중복 없는 문자열들을 연결한다.
- 시간대/원본 합계 차이는 HOURLY_TOTAL_MISMATCH로 진단과 records.quality_note에 보존한다.
  원본 총량이나 시간대 수치를 보정하지 않는다. OUT 진단의 raw_source_including_untrusted_OUT_11은
  복제 결함값을 포함한 원시 비교이며 실제 OUT 합계로 사용하지 않는다.
- 공식 휴관일 의미를 예측·추천 정책에 반영하는 LEAD 작업은 별도 검토 대상이다.

## 실행

Python 3.12 이상, 저장소 루트에서 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:LIBRARY_RECORDS = 'data/processed/records.json'
.\.venv\Scripts\python.exe -m library_etl refresh 'C:\data\new.xlsx' --sheet Data --partial-date 2026-09-18
.\.venv\Scripts\python.exe -m uvicorn backend.app:app
```

완료가 확인된 파일만 `--all-complete`를 사용한다. `--source-name`은 임시 업로드 시
기록할 원본 파일명이며 경로는 허용하지 않는다. 성공은 종료코드 0과 stdout 진단 JSON,
실패는 종료코드 2와 stderr의 `{error: {code, message}, issues}`이다.
`python -m scripts.rebuild input.xlsx`도 같은 안전한 갱신 경로를 사용한다.
`scripts.rebuild input.json`은 기존처럼 전체 스냅샷 교체이며 기간 병합 명령이 아니다.
기존 `--report` 파일 출력 옵션은 제거했다. 파일 게시 이후 보고서 기록 실패로
갱신 결과가 모호해지지 않도록 보고서는 stdout으로 반환한다.

## 검증

합성 XLSX는 테스트 실행 시 임시 디렉터리에 생성한다. 실제 Excel, 운영 JSON,
실데이터 보고서와 화면 캡처는 커밋하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m scripts.e2e
```

샌드박스에서 기본 TEMP 권한이 없으면 `New-Item -ItemType Directory -Force work` 실행 후
pytest에 새 임시 경로 `--basetemp=work/pytest-check-1`을 지정한다.
실행 결과 및 미실행 항목은 PR과 ADE-21에 기록한다.

2026-09-18 로컬 실행: Python 3.12, 전체 pytest **52 passed**.
기존 테스트 26개와 새 테스트 26개를 포함하며 skip은 없다.
Starlette/httpx 및 AnyIO 사용 중단 예고 경고 2건이 있으며 테스트 실패는 없다.
Chromium E2E **PASS**: 기존 JSON 교체, 합성 XLSX 정상 갱신 후 DOM 변경,
잘못된 XLSX 반영 거부 후 기존 DOM 유지, OUT_11 API null, 390px 화면을 확인했다.
실제 원본 Excel은 두 작업 저장소에서 발견하지 못해 검증하지 않았다.
CI는 PR 제출 후 별도로 확인한다. 로컬 통과를 CI 통과로 간주하지 않는다.
