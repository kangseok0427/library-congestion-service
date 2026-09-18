# T02·T08 통합 및 검증

## 적용 범위

- T02: 원본 `.xlsx`의 필수 열·자료형·게이트·중복을 검증하고 공통 records v1.1로 변환
- T08: 날짜·게이트 묶음을 SQLite에 트랜잭션으로 교체하고 전체 스냅샷 기반 파생 데이터를 재생성
- API: `LIBRARY_DB`가 지정되면 T08 SQLite 스냅샷을 읽어 통계 응답 제공

원본 Excel, 전처리 산출물, SQLite 파일은 저장소에 커밋하지 않는다.

## v1.1 처리 규칙

- `OUT_11`은 원본에서 `IN_11`과 동일하게 복제된 결측값으로 확인되어 `out_count: null`로 보존한다.
- 시간대 합과 원본 전체 합이 다르면 `HOURLY_TOTAL_MISMATCH` 경고를 남기며 원본 값을 임의 보정하지 않는다.
- 행 검증 오류가 하나라도 있으면 파일 전체 반영을 중단한다.
- 완료 데이터가 이미 있는 날짜·게이트를 부분 데이터로 덮어쓰지 않는다.
- 갱신 또는 파생 데이터 재생성 실패 시 기존 records와 state를 함께 유지한다.

## 검증 시나리오

`tests/test_etl_api_integration.py`는 합성 Excel을 실제로 생성하여 다음 경로를 검증한다.

1. Excel을 T02 공통 records로 전처리한다.
2. T08이 records와 파생 결과를 SQLite에 한 트랜잭션으로 반영한다.
3. API가 같은 SQLite를 읽어 갱신된 통계를 반환한다.
4. 잘못된 Excel을 입력하면 갱신이 롤백되고 직전 API 값이 유지된다.

실제 원본 Excel 전용 회귀 테스트는 파일이 로컬에 있을 때만 실행한다. 저장소와 CI에서는 개인정보·운영 데이터 유출을 막기 위해 합성 파일을 사용한다.

## 실행

```sh
python -m pytest -q
python -m playwright install chromium
python -m scripts.e2e
```
