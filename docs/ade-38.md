# ADE-38 / T14 운영시간·휴관일 및 7일 예측 화면 정비

작업일: 2026-09-22. 실행 위치: 로컬 Windows Codex. 기준 develop: `bac705694978103ca147d89fc78b7ef1b4bdfafc`.
범위: 기존 예측 모델의 서비스 시간·달력·화면을 정비한다. 새 모델 학습이나 실데이터 수령·설치·배포 완료를 주장하지 않는다.

## 확인된 기준과 근거

- 사용자 인계와 [ADE-38](https://linear.app/ade0033/issue/ADE-38): 평일 09:00~21:00, 주말 09:00~17:00, 매주 월요일 휴관.
- 원본 records의 08~23시, 일일 원본 합계, 결측 및 품질 필드는 보존한다. ETL의 시간 라벨이나 값을 변경하지 않는다.
- [담당자 대화](https://chatgpt.com/c/6ab21930-1c3c-83e8-80df-0fbf128eca2b)에서 추석 연휴 휴관 확인. 2026년 추석 공휴일인 9/24~9/26을 명시적으로 등록했다.
- 날짜 근거: [한국천문연구원 2026년 월력요항](https://www.kasi.re.kr/kor/post/newsMaterial/32031). 주 5일제 연휴에 포함되는 일요일 9/27까지 도서관 휴관으로 확대하지 않았다.
- 다른 공휴일/임시휴관은 `closed_dates`에 확인된 날짜를 추가한다. 이번 변경은 전 연도 공휴일 자동 달력이 아니다.

## 미확인 사항 1개: IN_21 버킷 의미

`IN_21`이 21:00~21:59 시작 라벨인지, 20:00~21:00 종료 라벨인지 아직 확인되지 않았다. 21~23시의 0이 폐관 또는 수집 중단 때문이라고 확정하지 않는다.

`backend/library_hours.py`의 `LibraryHours.bucket_label='start'`, `bucket_label_confirmed=False`가 임시 기본값이다. 평일 원본 라벨 9~20, 주말 9~16을 사용한다. 운영 종료시각 21:00/17:00 자체는 변경하지 않는다.

현장에서 종료 라벨 방식으로 확인되면 `bucket_label='end'`로 변경한다. 이 설정은 원본의 시간 라벨 방식에 적용되므로 평일 10~21, 주말 10~17을 선택하고 `start_hour=hour-1`, `end_hour=hour`로 표시·추천한다. 마지막 21만 억지로 추가해 21~22시로 추천하지 않는다. 실제 확인 근거를 남긴 후에만 `bucket_label_confirmed=True`로 바꾼다. 설정 변경 후 서버를 재시작하고 테스트를 실행한다.

## 구현

- 운영시간/주간 휴관/명시적 휴관 목록/버킷 해석을 `LibraryHours`로 분리했다. `is_closed_day=True`가 하나라도 있는 날짜는 서비스 전체에서 제외한다.
- 예측의 학습 자료와 출력, 추천 후보, backtest 평가, 일별·요일별·시간별·월별 통계에 같은 정책을 적용한다. 부분 자료/결측 처리와 같은 요일 평균·fallback 알고리즘은 유지한다.
- `/api/v1/meta`는 KST 오늘~오늘+7일의 `date_window`를 반환한다. 날짜별 API 두 곳은 매 요청마다 같은 KST 범위를 검증하며 과거/+8일/잘못된 날짜에 HTTP 400 `INVALID_DATE`를 반환한다.
- 화면 날짜 입력의 min/max와 조회·새로고침 검증은 서버의 범위를 사용한다. KST 날짜·요일·시간을 브라우저 시간대와 독립적으로 계산한다.
- 미래 영업일 실제 집계는 HTTP 200 `data_status=pending`, `message=집계 전`, 빈 hourly와 null 합계다. 미래 날짜에 원본이 존재해도 실제 집계로 노출하지 않는다.
- 휴관일은 HTTP 200 `data_status=closed`, 빈 hourly, null 합계/추천이다. 화면에 ‘휴관일’을 명시하고 예측 막대·행을 비운다. 휴관은 미래 ‘집계 전’보다 우선한다.
- 영업일 실제 화면은 운영시간 내 `hourly_total_in/out`만 표시한다. `stats.total_in/out`은 기존 v1 계약의 **원본 일일 합계**로 보존하며 서비스 통계/추천/화면에는 사용하지 않는다. 운영시간 부분합과 원본 일일 합계가 같다고 가정하지 않는다.
- 내부 `LibraryService.stats`와 backtest는 과거 검증에 사용할 수 있다. 날짜 범위 제한은 공개 HTTP API 경계에 적용한다.
- 자료가 없으면 예상값은 null/자료 부족이다. 허용된 영업일이라고 예측 수치를 만들어내지 않는다.

## 변경 파일

- `backend/library_hours.py` (신규): 운영 정책 및 KST 날짜 검증.
- `backend/app.py`, `backend/service.py`, `backend/prediction.py`: API/집계/예측/추천/통계 적용.
- `frontend/app.js`, `frontend/index.html`: 날짜 제한, 휴관·집계 전, 운영시간 집계/표시.
- `scripts/rebuild.py`: 서비스에서 제외되는 원본만 있어도 원본 갱신이 실패하지 않도록 검증 대상 분리.
- `tests/test_library_hours.py` (신규): 날짜 경계, 휴관, 시간 정책, 원본 보존 회귀 검증.
- `tests/test_pipeline.py`, `tests/test_json_refresh.py`: 고정 시계 및 영업시간 계약으로 기존 테스트 갱신.
- `tests/browser_app.py` (신규), `scripts/e2e.py`: 운영 서버와 분리된 고정 시계로 브라우저 검증.
- `docs/ade-38.md`: 이번 근거·미확인·실행 결과·인수인계.

## 검증

합성 데이터로 검사한다. 실도서관 원본 Excel 검증이나 예측 정확도 인증이 아니다.

2026-09-22 실행 결과: pytest **108 passed**, 의존성 deprecation 경고 2개. 브라우저 E2E **PASS**. `git diff --check`, `node --check frontend/app.js` 통과. 원본 `data/sample/records.json` 및 ETL 원본 처리 코드 변경 없음.

```text
python -m pytest -q
python -m scripts.e2e
git diff --check
```

Windows 제한 환경에서는 시스템 임시폴더 접근이 제한되어, 테스트 전용 폴더를 만들고 `--basetemp=test-results/pytest-ade38-3`을 사용했다. 운영 코드 결함과 구분한다. 의존성의 deprecation 경고 2개는 별도 남아 있다.

브라우저 검증: API→화면 연동, 8개 날짜의 영업/휴관/집계 전, 과거/+8일 프론트·API 거부, 미국 LA 시간대에서도 KST 날짜/요일 유지, JSON 갱신 반영, 합성 Excel 갱신, OUT_11 null 보존, 잘못된 파일의 기존 결과 보존, 모바일 390px 가로 넘침 없음, 오류 시 이전 수치 제거. 고정 시계는 테스트 서버에만 존재하며 배포 진입점에는 사용하지 않는다.

## 다음 행동 및 완료 구분

구현/자동검증 결과와 현장 검증은 분리한다. 남은 현장 확인은 `IN_21` 버킷 의미 한 가지다. 확인 결과에 따라 설정을 확정하고 ADE-38 최종 완료를 판단한다. PR 병합·배포·현장 설치·새 원본 수령은 이번 구현 완료와 별개다.

기기 간 이어가기에는 원격 코드/PR과 Notion 기록을 사용한다. ChatGPT 프로젝트 소스 업로드는 업로드 성공을 확인한 경우에만 완료로 기록한다.
