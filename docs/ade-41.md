# ADE-40 이식 + ADE-41 추정 체류 인원 화면 — 인수인계

작성일: 2026-09-30 (KST). 작성: 도형준.
ADE-40 담당 이가영님 동의(사용자 전달)를 받아 ADE-40 계산을 팀 백엔드로 옮기고 ADE-41 화면을 연결했다.

상태: **팀 저장소에서 ADE-40 계산 → API → 화면이 합성 데이터로 연결·검증됨. 단, 병합·배포 보류.**
현재 ETL은 OUT_11을 항상 null로 내보내므로(`docs/json-refresh.md`) 이 규칙대로면 실제 운영에서 매일
11시 이후가 전부 '자료 부족'이 된다(§5-1). OUT_11 처리 규칙이 정해지기 전에는 병합하면 안 된다.

## 1. 확인 기준

| 대상 | 기준 |
| --- | --- |
| 팀 저장소 | `kangseok0427/library-congestion-service` `develop` = `e668eb7` |
| 작업 브랜치 | `feature/ADE-41-estimated-present-prep` (별도 worktree) |
| ADE-40 원본 | `thanks04122006-spec/operator-api` `b8cd27f`(계산·API), `50d0eaa`(README 정리) |
| Linear | ADE-40 Todo(댓글로 완료 알림), ADE-41 Todo, ADE-39 Todo(초안 첨부) |

`AGENTS.md`, `CLAUDE.md`, `05_START_HERE_2026-09-06.md`, `ADE-38-handoff.md`는 저장소에 없다.

## 2. 왜 이식이 필요했나

operator-api는 팀 저장소와 별개의 앱이라 팀 화면에 연결되지 않았다. 팀 화면이 쓰는
`today?date=`, `meta.date_window`, ADE-38 운영 달력, KST 시각, `stats.hourly_total_in`이 없고,
시각도 `datetime.now()`(서버 로컬)를 쓴다. 그래서 계산 규칙만 팀 `backend/`로 옮기고
기존 API·운영 규칙은 그대로 두었다.

## 3. 변경

### 백엔드 (ADE-40 이식)

| 파일 | 내용 |
| --- | --- |
| `backend/presence.py` (신규) | `observed()`: 운영일별 0명 시작, `IN − OUT` 누적, 불량 시점부터 그날 나머지 추정 불가. `forecast()`: 이전 4주 같은 요일·시간 평균, 같은 요일 표본 2개 미만이면 같은 시간 보조, 없으면 null |
| `backend/service.py` | today `hourly`에 `estimated_present`·`calculation_basis`·`quality_status` 추가. `level`·`score`·`sample_count`·`baseline_avg`·`difference_rate`·추천을 추정 체류 인원 기준으로. stats `hourly`에 그날 실제 `estimated_present`·`quality_status` 추가 |
| `backend/prediction.py` | `recommendation()`에 기준 필드 인자(`key`) 추가. 기본값은 기존 동작 |
| `backend/congestion.py` | 설명 문구를 추정 체류 인원 기준으로 |
| `docs/architecture.md`, `README.md`, `docs/t04-t05.md` | 계산 기준 문서화 |

`quality_status` 값은 operator-api와 같다:
`valid`, `forecast`, `missing_gate`, `partial`, `missing_out`, `negative_balance`, `insufficient_data`, `insufficient_samples`.

하위 호환으로 그대로 둔 것: 경로, `expected_visitors`·`method`(입장량 예측), stats의 원본 IN/OUT·baseline·증감률,
patterns, backtest, 날짜 제한, 운영시간·휴관일, Excel 갱신·업로드.

operator-api와 의도적으로 다른 점:
- 단계 비교 분포: 원본은 전체 기간, 여기는 기존 설계대로 **대상 날짜 이전 4주**(미래 자료 누출 없음)
- 오늘 예측: 원본은 오늘 관측 누적을 섞고, 여기는 예측만 두고 오늘 실제 값은 stats에 붙임
  → 원본에서 보였던 "13시에 지난 9–11시 추천" 문제가 생기지 않음
- 추천 없음: 원본 `best_start_hour=0`, 여기는 기존대로 `null`

### 프론트 (ADE-41)

| 파일 | 내용 |
| --- | --- |
| `frontend/app.js` | `estimated_present`가 오면 제목·표·막대·상세·aria-label을 추정 체류 인원 기준으로. 색은 백엔드 `level`만 사용. null이면 회색과 `추정 불가 (OUT 결측)` 등 이유. 다른 날짜 응답은 오류 처리. 필드가 없는 옛 응답이면 기존 예상 방문량 문구 유지 |
| `frontend/index.html`, `style.css` | 범례에 `자료 부족`(회색) 추가 |

카드 수·순서, 추천 카드, 날짜 제한은 바꾸지 않았다.

### 테스트

| 파일 | 내용 |
| --- | --- |
| `tests/test_presence.py` (신규 8개) | 이슈 예시 (10,0)→(5,3)→(0,8) = 10→12→4, 정문·후문 합산, 날짜별 초기화, 운영시간 필터, 출입구 누락·OUT 결측·음수·부분 수집·시간 누락·휴관일, 같은 요일/보조/표본 1개/표본 없음, 미래 자료 누출 없음, API 필드·하위 호환·추천 기준 |
| `scripts/e2e_present.py` (신규) | 브라우저: 원본 ADE-40 출력 픽스처 + **팀 서버 실제 응답 8개 날짜** |
| `scripts/capture_ade40_fixture.py`, `tests/fixtures/ade40_today_*.json` | operator-api 원본 `today_forecast()` 출력(합성 데이터) |

## 4. 검증 (2026-09-30 직접 실행, Python 3.14.7 venv)

| 명령 | 결과 |
| --- | --- |
| `python -m pytest -q` | **167 passed** (기존 159 + 신규 8), 1 warning (httpx deprecation) |
| `python -m scripts.e2e` | **PASS** — 8개 KST 날짜(휴관·집계 전), 과거/+8일 거부, LA 시간대, 합성 XLSX 갱신, OUT_11 null 보존, 잘못된 파일 시 이전 값 유지, 390px |
| `python -m scripts.e2e_present` | **PASS** — 막대 높이 = 값/최댓값, 색 = `level`, 표·aria-label·상세 같은 값, 0명과 null 구분, 새로고침·API 실패·다른 날짜 응답 시 이전 수치 제거, 390px, 팀 서버 8개 날짜(휴관 포함), 옛 응답 문구 유지 |
| 원본 대조 | 같은 합성 records(OUT 결측·출입구 누락·음수·부분 수집 섞음)에서 operator-api `_hourly_rows()`와 **512개 시간대 값·상태 불일치 0** |
| operator-api 자체 테스트 | 6 passed (fastapi 0.142 환경, 원본 고정 버전 아님) |

테스트가 실제로 결함을 잡는지도 확인했다:
- 프론트가 `expected_visitors`를 읽도록 바꾸면 `e2e_present` 실패
- 불량 시간 이후에도 누적을 계속하면 `test_presence` 4개 실패
- 같은 요일 최소 표본을 1로 바꾸면 `test_presence` 1개 실패

PC·모바일 스크린샷을 눈으로 확인했다(`test-results/`, 커밋 안 함).

**한계**: 모든 입력은 합성 데이터다. 실제 도서관 데이터의 정확도나 추정 불가 비율은 확인하지 않았다(§5-1).

## 5. 미완료 · 확인 필요

1. **OUT_11 때문에 실제 운영에서 11시 이후가 전부 비게 됨 — 병합 차단 사유.**
   `docs/json-refresh.md:65`: "OUT_11은 복제 결함 때문에 전처리 출력에서 항상 null". ADE-40 규칙은 OUT 결측 시점부터
   그날 나머지를 추정 불가로 둔다. ETL처럼 OUT_11만 null로 만든 합성 데이터로 확인한 결과, today 응답이
   9시·10시만 값이 있고 11–20시는 모두 null, 추천은 항상 9–11시였다. operator-api 원본도 규칙이 같아 같은 결과가 된다.
   ADE-40 이슈가 요구한 대로 **OUT_11 처리 규칙을 강민재(자료)·이가영(계산)님과 정해야 한다.**
   이슈상 OUT 결측을 0으로 바꾸는 것은 금지라 임의로 정하지 않았다. 논의할 선택지 예:
   (가) 일일 `total_out`에서 다른 시간 OUT을 빼 OUT_11 복원 — `total_out`에 잘못된 OUT_11이 포함되는지 먼저 확인 필요,
   (나) 11시 이후는 추정하지 않음 — 현재 동작, 화면 가치가 크게 떨어짐,
   (다) 11시를 건너뛰고 오차를 문서화 — 추정 근거 약함.
2. **실제 데이터 추정 불가 비율 미확인.** 로컬에 원본 Excel이 있지만, 실제 도서관 데이터 처리는 이번 세션에서
   권한이 막혀 실행하지 않았다. ADE-39 초안의 "OUT이 하루 약 40명 더 많이 세어짐"도 음수 발생으로 회색을 늘릴 수 있다.
3. **ADE-39와 표시 방식 충돌.** 초안은 "숫자는 보이지 않고 색으로만", ADE-41·ADE-40은 숫자 표시.
   구현은 ADE-41 명세를 따랐다. 10/1 토론에서 결정 필요. 숨기기로 하면 표·상세의 숫자만 가리면 된다.
4. ADE-38 IN_21 라벨은 여전히 현장 확인 근거 없음.
5. 리뷰·병합·배포 안 함. Linear 상태 변경 안 함. PR은 Draft로 올린다.

## 6. 다음 행동

1. **10/1 토론에서 OUT_11 처리 규칙 결정** (강민재·이가영) → `backend/presence.py`와 테스트에 반영
2. 실제 Excel로 `negative_balance`·`insufficient_data` 비율 확인
3. PR 리뷰 — 이가영님께 operator-api와 다른 세 가지(§3) 확인. 위 두 가지가 끝난 뒤 Draft 해제
4. ADE-39 숫자 표시 여부 결정 후 필요하면 프론트 조정

## 7. PR 설명 (팀 PR 템플릿)

```markdown
## 관련 Linear 이슈

ADE-40, ADE-41 (ADE-39 문구 대조)

## 구현 및 검증 결과

- ADE-40 추정 체류 인원 계산(원본: operator-api 50d0eaa, 이가영)을 팀 backend/presence.py로 이식
  - 운영일별 0명 시작, 정문·후문 IN − OUT 누적, 출입구 누락·부분 수집·OUT 결측·음수·시간 누락부터 그날 추정 불가
  - 이전 4주 같은 요일·시간 평균, 같은 요일 표본 2개 미만이면 같은 시간 보조, 없으면 null
- today hourly: estimated_present, calculation_basis, quality_status 추가.
  level·score·sample_count·baseline_avg·difference_rate·추천은 추정 체류 인원 기준
- stats hourly: 원본 IN/OUT 유지, 그날 estimated_present·quality_status 추가
- 화면(ADE-41): 막대·표·상세·aria-label을 추정 체류 인원으로, 색은 백엔드 level 그대로,
  자료 부족은 회색 + 사유, 범례에 자료 부족 추가

## 처음 보는 팀원을 위한 동작 설명

각 운영일 개장 시 0명에서 시작해 매 시간 (들어온 사람 − 나간 사람)을 더한 값이 추정 체류 인원입니다.
믿을 수 없는 시간(한쪽 문 누락, OUT 결측, 음수 등)이 나오면 그 뒤로는 숫자와 색을 만들지 않습니다.
실시간 인원이나 좌석 점유율이 아닙니다.

## 실제 검증

- python -m pytest -q → 167 passed
- python -m scripts.e2e → PASS (synthetic)
- python -m scripts.e2e_present → PASS (ADE-40 원본 출력 픽스처 + 팀 서버 8개 날짜, synthetic)
- operator-api 원본과 같은 합성 입력 512개 시간대 값·상태 불일치 0
- 변이 확인: 핵심 규칙을 깨면 새 테스트가 실패함

## 데이터 품질 주의

원본 Excel·실데이터·비밀값 포함 없음. 픽스처는 합성 데이터.

⚠️ **병합 보류 (Draft)**: 현재 ETL은 OUT_11을 항상 null로 냅니다(docs/json-refresh.md). ADE-40 규칙상 OUT 결측부터
그날 나머지를 추정하지 않으므로, 이대로 배포하면 매일 11시 이후가 전부 '자료 부족'이 됩니다
(합성 데이터로 확인: 9·10시만 값, 추천 항상 9–11시). OUT_11 처리 규칙을 강민재·이가영님과 정한 뒤 반영하겠습니다.
실제 데이터의 추정 불가 비율도 아직 확인하지 않았습니다.

## 인수인계

- 공통 규격 v1 필드·경로 유지. expected_visitors/method는 입장량 기준 그대로(하위 호환)
- operator-api와 다른 점: 단계 분포를 대상일 이전 4주로 제한, 오늘 관측은 stats에만, 추천 없음은 null
- ADE-39 초안(숫자 대신 색만)과 표시 방식 결정 필요
- docs/ade-41.md 참고
```
