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
  JSON CLI는 전체 스냅샷 교체 방식입니다. Excel CLI는 T08을 통해 날짜·게이트별로
  병합하고, 임시 파일에서 rebuild 검증을 끝낸 뒤 운영 records를 게시합니다.

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
- ADE-40부터 운영일별 누적 IN − OUT으로 **추정 체류 인원**을 계산합니다(아래 절). 실시간 인원이나 좌석 점유율은 제공하지 않습니다.
- available_hours는 수집 시간대이며 운영시간이나 휴관일 달력이 아닙니다.
  운영정보 담당자가 달력을 연결하기 전에는 도서관 공지를 확인해야 합니다.

## 추정 체류 인원 (ADE-40, `backend/presence.py`)

- 운영일마다 운영 시작 시 0명에서 시작해 시간대별 `이전 값 + 정문·후문 IN 합 − OUT 합`을 누적합니다. 날짜가 바뀌면 초기화하고 원본 IN/OUT은 그대로 둡니다.
- 출입구 누락(`missing_gate`), 부분 수집(`partial`), OUT 결측(`missing_out`), 누적 음수(`negative_balance`), 시간대 누락이 나오면
  그 시점부터 그날 나머지는 값 없이 `insufficient_data`입니다. OUT 결측을 0으로 바꾸지 않습니다.
- 예측: 대상 날짜 이전 N주 유효 값 중 같은 요일·시간 평균(`same_weekday_same_hour`). 같은 요일 표본이 2개 미만이면
  같은 시간 평균(`same_hour_fallback`), 없으면 null과 `insufficient_samples`. 결과는 정수로 반올림합니다.
- `level`/`score`는 예측값을 같은 기간·같은 시간의 유효 추정 체류 인원 분포에 midrank로 비교합니다(경계는 그대로 35/70).
- 추천은 추정 체류 인원 연속 2시간 합이 가장 낮은 구간입니다. 오늘은 이미 시작된 시간을 제외합니다.
- today의 `baseline_avg`·`difference_rate`·`sample_count`도 추정 체류 인원 기준입니다. `expected_visitors`, `method`, stats의 baseline·증감률,
  patterns, backtest는 하위 호환을 위해 기존 입장량 기준을 유지합니다.
- 원본 ADE-40 구현(operator-api `50d0eaa`)과 같은 합성 입력에서 시간대별 값·상태가 같음을 확인했습니다.
  차이: 원본은 전체 기간 분포로 단계를 매기고 오늘 관측 누적을 예측에 섞지만, 여기서는 기존 설계대로 대상 날짜 이전 N주만 쓰고
  오늘 실제 값은 stats에만 붙입니다.

## 입장량 예측 기준 (`expected_visitors`)

1. 기본 N=4주(1~52 설정 가능), 대상 날짜 이전 데이터만 사용합니다.
2. 같은 요일·시간의 완전한 집계 평균을 expected_visitors로 사용합니다.
3. 동일 요일 자료가 1~3개면 있는 자료만 사용하고 sample_count에 노출합니다.
4. 동일 요일 자료가 없으면 같은 기간·같은 시간 평균으로 fallback합니다.
5. 해당 시간의 자료가 전혀 없으면 null / unavailable로 반환합니다. 오래된 자료나 다른 시간 값을 복사하지 않습니다.
6. 부분 데이터는 학습과 backtest 정답에서 제외합니다. 0은 정상 관측으로 취급합니다.
7. midrank 백분위: 35 이하 quiet, 70 이하 normal, 나머지 busy. 전부 동일한 값이면 50(normal)입니다.
   API의 `level`은 ADE-40부터 추정 체류 인원 기준입니다(위 절).
8. 추천 구간 계산(`recommendation`)은 동률이면 이른 시간 우선이고, 추천 가능한 구간이 없으면 null입니다.

예측값 자체가 동일 요일 baseline이므로 forecast difference_rate는 0%입니다.
baseline이 0이거나 fallback이면 증감률은 null입니다.
stats의 실제 관측값은 baseline 대비 증감률을 계산하지만 partial 관측은 비교하지 않습니다.
혼잡도는 과거 추정 체류 인원 분포와의 상대 비교이며 수용 인원 대비 혼잡이나 실시간 센서 값이 아닙니다.

## API

- GET /api/v1/congestion/today: v1 forecast 응답. optional `?date=YYYY-MM-DD`는 검증/날짜 조회용 확장입니다.
- GET /api/v1/stats?date=YYYY-MM-DD: 원본 일일 합계와 시간대 집계. partial/complete 구분. hourly에 그날의 `estimated_present`, `quality_status` 추가.
- GET /api/v1/meta: 이름, 수집 시간대, quiet/normal/busy 한국어 레이블.
- GET /api/v1/patterns: 완전한 날짜의 일·요일·월 통계 확장.
- 오류는 `{error: {code, message}}`. 날짜 오류 400, 자료 없음 404, 입력 처리 오류 422.
- 추가 메타데이터: method, sample_count, is_sample, basis. 공통 필드 이름을 변경하지 않습니다.
- ADE-40 today hourly 추가 필드: `estimated_present`(int|null), `calculation_basis`, `quality_status`. 값이 null이면 `level`·`score`도 null입니다.
- 조회용 API에 업로드 기능은 없습니다. 파일 갱신은 신뢰된 로컬 CLI/T08에서 수행합니다.

## backtest

최근 28개 완전한 수집일의 각 날짜를 대상으로, 해당 날짜 이전 N주로 다시 학습하는
rolling-origin 검증입니다. MAE, RMSE, 예측 가능 비율, 같은 시간 평균 비교 모델 MAE를 기록합니다.
모델/기준 설정도 대상 날짜 이후 정보를 사용하지 않습니다. 휴관일·방학 등 외생 변수는 아직 넣지 않았습니다.
검증 데이터의 missing/partial 구간은 오차를 0으로 처리하지 않고 제외합니다.
