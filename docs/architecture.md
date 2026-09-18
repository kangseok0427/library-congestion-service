# ADE-15 / ADE-16 구현과 인수인계

> ADE-21 변경: Excel 입력은 T02 파서로 통합했고 T08은 날짜·게이트 병합 후
> 임시 JSON/rebuild 검증이 끝나면 운영 파일을 한 번 교체합니다.
> 아래 v1 기준 설명 중 정확히 12필드/정수 OUT 제한은 대체되었습니다.
> v1.1 선택 품질 필드와 null OUT, CLI 및 실패 보존 규칙은
> [JSON 갱신 문서](json-refresh.md)를 참고하세요. 예측 정책은 기존 LEAD 구현을 유지합니다.

## 연결 구조

`T02 records 배열 → validate_records → aggregate → LibraryService → /api/v1 → frontend`

박수용님 T02/T08 코드는 연결된 계정에서 404여서 직접 읽거나 재실행하지 못했습니다.
Linear의 공통 규격 v1과 ADE-6/ADE-12 리뷰를 확인하고 독립 어댑터를 구현했습니다.
이 구현은 박수용님 코드를 수정하거나 복제한 것이 아닙니다.

- T02: `backend.adapters.read_records(path)`가 필수 v1 12필드와 v1.1 선택 품질 필드 JSON 배열을 받습니다.
- T03: `backend.domain.aggregate(records)`의 공통 집계 결과를 대체할 수 있습니다.
- T04: `backend.prediction.percentile`이 단계 판정 교체 지점입니다.
- T06: `create_app(provider)`의 provider.get()이 서비스 스냅샷을 반환합니다.
- T05: 현재 frontend는 참고 구현입니다. 공통 API만 사용하므로 팀원 화면으로 교체할 수 있습니다.
- T08: `scripts.rebuild.rebuild(records, destination)`을 갱신 완료 후 호출합니다.
  검증/재계산 성공 후 파일을 원자적으로 교체하므로 실패한 입력이 기존 파일을 덮어쓰지 않습니다.
  실행 중 API는 다음 요청에 변경된 파일을 읽습니다. 웹 새로고침으로 반영합니다.
  CLI는 전체 스냅샷 교체 방식입니다. 기간 병합은 T08이 수행한 뒤 전체 records를 넘깁니다.

## 표준 데이터와 품질

공통 문서: https://linear.app/ade0033/document/공통-데이터api-규격-v1-2026-09-14-d7ea1e1bb9f4

12개 필드: date, day_of_week, gate, gate_name, passage_id, hour,
in_count, out_count, total_in, total_out, is_partial, source_file.

- date는 YYYY-MM-DD, day_of_week는 Mon~Sun, gate는 front/back, hour는 8~23입니다.
- 누락·음수·소수·중복 키를 0이나 합계로 보정하지 않습니다. OUT의 null은 결측으로 보존합니다.
- 정문/후문 중 한쪽이 없으면 집계 시간대를 partial로 표시하고 예측 학습에서 제외합니다.
- 원본 total_in/out은 날짜·게이트마다 한 번만 더합니다. hourly 합계와 별도로 보존합니다.
- patterns는 모든 16시간·두 게이트가 완전한 날의 시간대 IN 합계를 사용합니다.
- Excel의 부분 수집 날짜는 `--partial-date`로 명시합니다. 파일명을 추측해서 정하지 않습니다.
  T02 결과를 입력할 때는 is_partial을 그대로 존중합니다.
- 현재 체류인원, 좌석 점유율, IN-OUT 누적 추정은 제공하지 않습니다.
- available_hours는 수집 시간대이며 운영시간이나 휴관일 달력이 아닙니다.
  운영정보 담당자가 달력을 연결하기 전에는 도서관 공지를 확인해야 합니다.

## 예측 기준

1. 기본 N=4주(1~52 설정 가능), 대상 날짜 이전 데이터만 사용합니다.
2. 같은 요일·시간의 완전한 집계 평균을 expected_visitors로 사용합니다.
3. 동일 요일 자료가 1~3개면 있는 자료만 사용하고 sample_count에 노출합니다.
4. 동일 요일 자료가 없으면 같은 기간·같은 시간 평균으로 fallback합니다.
5. 해당 시간의 자료가 전혀 없으면 null / unavailable로 반환합니다. 오래된 자료나 다른 시간 값을 복사하지 않습니다.
6. 부분 데이터는 학습과 backtest 정답에서 제외합니다. 0은 정상 관측으로 취급합니다.
7. 해당 시간대 과거 분포에서 midrank 백분위 점수를 계산합니다.
   35 이하 quiet, 70 이하 normal, 나머지 busy입니다. 전부 동일한 값이면 50(normal)입니다.
8. 추천은 예측 가능한 연속 2시간 중 예상 입장량 합계가 가장 낮은 구간입니다. 동률은 이른 시간 우선입니다.
   오늘은 이미 시작된 시간대를 제외하며 추천 가능한 구간이 없으면 null입니다.

예측값 자체가 동일 요일 baseline이므로 forecast difference_rate는 0%입니다.
baseline이 0이거나 fallback이면 증감률은 null입니다.
stats의 실제 관측값은 baseline 대비 증감률을 계산하지만 partial 관측은 비교하지 않습니다.
혼잡도는 예상 입장량 분포 비교이며 수용 인원 대비 혼잡이나 실시간 센서 값이 아닙니다.

## API

- GET /api/v1/congestion/today: v1 forecast 응답. optional `?date=YYYY-MM-DD`는 검증/날짜 조회용 확장입니다.
- GET /api/v1/stats?date=YYYY-MM-DD: 원본 일일 합계와 시간대 집계. partial/complete 구분.
- GET /api/v1/meta: 이름, 수집 시간대, quiet/normal/busy 한국어 레이블.
- GET /api/v1/patterns: 완전한 날짜의 일·요일·월 통계 확장.
- 오류는 `{error: {code, message}}`. 날짜 오류 400, 자료 없음 404, 입력 처리 오류 422.
- 추가 메타데이터: method, sample_count, is_sample, basis. 공통 필드 이름을 변경하지 않습니다.
- 조회용 API에 업로드 기능은 없습니다. 파일 갱신은 신뢰된 로컬 CLI/T08에서 수행합니다.

## backtest

최근 28개 완전한 수집일의 각 날짜를 대상으로, 해당 날짜 이전 N주로 다시 학습하는
rolling-origin 검증입니다. MAE, RMSE, 예측 가능 비율, 같은 시간 평균 비교 모델 MAE를 기록합니다.
모델/기준 설정도 대상 날짜 이후 정보를 사용하지 않습니다. 휴관일·방학 등 외생 변수는 아직 넣지 않았습니다.
검증 데이터의 missing/partial 구간은 오차를 0으로 처리하지 않고 제외합니다.
