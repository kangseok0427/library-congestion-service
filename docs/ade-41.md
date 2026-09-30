# ADE-41 추정 체류 인원 그래프·혼잡 색상 — 인수인계

작성일: 2026-09-30 (KST). 담당: 도형준. 상태: **프론트 구현·검증 완료, 실제 API 연결 대기 — ADE-41 미완료.**

## 1. 확인 기준

| 대상 | 기준 |
| --- | --- |
| 팀 저장소 | `kangseok0427/library-congestion-service` `upstream/develop` = `e668eb7` (9/23 이후 새 커밋 없음) |
| 작업 브랜치 | `feature/ADE-41-estimated-present-prep` (별도 worktree, 원래 체크아웃 브랜치는 건드리지 않음) |
| ADE-40 구현 | `thanks04122006-spec/operator-api` `main` — `b8cd27f`(계산·API), `50d0eaa`(README 정리). 9/30 14:07 KST |
| ADE-40 Linear | 상태 **Todo**, 이가영 댓글(9/30 14:20)로 완료 알림. 팀 저장소 PR 없음 |
| ADE-39 | 상태 Todo, 첨부 초안 `congestion-explained.md`(9/29 작성) 대조 |

`AGENTS.md`, `CLAUDE.md`, `05_START_HERE_2026-09-06.md`, `ADE-38-handoff.md`는 저장소에 없다. `docs/ade-38.md`는 읽었다.

## 2. ADE-40 API 계약 (operator-api 코드 기준)

`GET /api/v1/congestion/today`의 `hourly[]` 항목:

| 필드 | 형 | 의미 |
| --- | --- | --- |
| `estimated_present` | int \| null | 운영 시작 0명부터 `이전 값 + 정문·후문 IN − OUT`. 과거 4주 같은 요일·시간 평균으로 예측 |
| `expected_visitors` | int \| null | 하위 호환용. **`estimated_present`와 같은 값** |
| `level` / `score` | `quiet`·`normal`·`busy` \| null / float \| null | 같은 시간대 과거 추정 체류 인원 분포의 midrank 백분위, 35/70 경계 |
| `calculation_basis` | str | `observed_cumulative`, `same_weekday_same_hour`, `same_hour_fallback`, `insufficient_samples` |
| `sample_count` | int | 예측 표본 수 |
| `quality_status` | str | `valid`, `forecast`, `missing_out`, `partial`, `missing_gate`, `negative_balance`, `insufficient_data`, `insufficient_samples` |

자료 부족이면 `estimated_present`·`level`·`score`가 모두 null이다. 휴관일은 `data_status=closed`, `hourly=[]`.

### 팀 화면에 바로 연결할 수 없는 이유

operator-api는 팀 저장소와 **별개의 앱**이고, 팀 화면은 같은 서버의 `backend/`를 호출한다. operator-api에는 팀 화면이 쓰는 다음 계약이 없다.

- `/congestion/today?date=` — 날짜 파라미터가 없고 항상 서버 시계 기준 오늘만 반환 (ADE-38 8일 조회 불가)
- `/meta.date_window`, `operating`, 운영 달력(ADE-38) — 운영시간이 평일 9–21·주말 9–17로 고정, 휴관일은 빈 `CLOSED_DATES`
- `/stats.hourly_total_in`·`hourly_total_out`, `data_status=pending`
- 시각이 `datetime.now()`(서버 로컬 시각) — 서버가 UTC이면 KST와 9시간 어긋남

따라서 **ADE-40 계산을 팀 `backend/`에 옮기는 작업이 끝나야** ADE-41 완료 기준("ADE-40 API 연결 후 화면 동작")을 만족한다. 이 이식은 ADE-40(이가영) 범위라 대신 구현하지 않았다.

## 3. 이번 변경

| 파일 | 내용 |
| --- | --- |
| `frontend/app.js` | 응답 `hourly`에 `estimated_present`가 있으면 **추정 체류 인원**, 없으면 기존 **예상 방문량**으로 표시. 막대 높이·표·상세·aria-label이 한 `describe()` 결과를 사용. 값이 null이면 단계 색을 쓰지 않고 `추정 불가 (OUT 결측)` 등 이유 표시. `calculation_basis`·`quality_status` 문구 추가. 선택 날짜와 다른 `date` 응답은 오류로 처리. 추천 시작 시각 0(ADE-40의 "추천 없음")을 `추천 자료 부족`으로 표시 |
| `frontend/index.html` | 제목·표 머리글에 id 부여, 범례에 `자료 부족`(회색) 추가 |
| `frontend/style.css` | 범례 회색 항목, 좁은 화면 줄바꿈 |
| `scripts/capture_ade40_fixture.py` | operator-api의 실제 `today_forecast()`를 합성 records·고정 시각으로 실행해 응답을 저장 |
| `tests/fixtures/ade40_today_{forecast,partial}.json` | 위 스크립트 결과 (operator-api `50d0eaa`, **합성 데이터, 테스트 전용**) |
| `scripts/e2e_present.py` | ADE-41 브라우저 검증 |

이용자에게 달라지는 점: **지금 운영 서버에서는 거의 달라지지 않는다**(범례에 `자료 부족` 항목, 상세 문구에 지표 이름이 붙는 것만 추가). 팀 백엔드가 `estimated_present`를 내보내는 순간 제목·표·막대·설명이 추정 체류 인원 기준으로 바뀐다. 기존 방문량을 이름만 바꿔 보여 주지 않는다.

유지한 것: 카드 수·순서, 날짜 제한·KST, 운영시간·휴관일, 추천 기능, Excel 갱신·업로드 흐름, 색 경계 계산 없음(백엔드 `level` 그대로).

## 4. 검증 (2026-09-30 직접 실행, worktree venv Python 3.14.7)

| 명령 | 결과 |
| --- | --- |
| `python -m pytest -q` (팀 저장소) | **159 passed**, 1 warning (httpx deprecation) |
| `python -m scripts.e2e` (기존 회귀) | **PASS** — 8개 KST 날짜(휴관·집계 전), 과거/+8일 거부, LA 시간대, 합성 XLSX 갱신, OUT_11 null 보존, 잘못된 파일 시 이전 값 유지·오류 표시, 390px |
| `python -m scripts.e2e_present` (신규) | **PASS** — 아래 항목 |
| `python -m pytest -q` (operator-api, 같은 venv) | **6 passed**. 단 fastapi 0.142/pydantic 2.13 환경이며 operator-api 고정 버전(0.115/2.9)으로는 실행하지 않음 |

`e2e_present` 확인 항목:
- 막대 높이 = `estimated_present / 최댓값` (최소 4%), null은 3px 회색
- 막대 색 = API `level` (여유 초록·보통 노랑·혼잡 빨강)
- 표·막대 aria-label·상세 문구가 같은 숫자·단계
- 0명은 `약 0명 · 여유`, null은 `추정 불가 (OUT 결측)`/`자료 부족` + 회색 (혼잡·여유로 표시 안 함)
- `expected_visitors`를 일부러 다르게 넣어도 `estimated_present`를 따름. 프론트를 `expected_visitors`로 되돌리는 변이를 넣으면 이 테스트가 **실패함을 확인**
- 새로고침 시 값 전부 교체, API 500이면 막대·표 제거, 다른 날짜 응답은 오류
- 390px 가로 넘침 없음, 데스크톱/모바일 스크린샷 육안 확인 (`test-results/ade41-*.png`, 커밋 안 함)
- 현재 팀 백엔드 응답에서는 `예상 방문량` 문구 유지, `추정 체류 인원` 미표시

**한계**: 모든 입력은 합성 데이터다. 실제 도서관 데이터의 정확도나, 실데이터에서 `negative_balance`로 비는 시간대 비율은 검증하지 않았다.

## 5. 미완료 · 막힘

1. **실제 API 연결 없음** — ADE-40이 팀 `backend/`에 반영되지 않음(§2). ADE-41 완료로 기록하지 않는다.
2. **ADE-39와 표시 방식 충돌** — ADE-39 초안은 "OUT이 하루 약 40명 더 많이 세어져 음수가 나오므로 **숫자는 보이지 않고 색으로만**" 보여 준다고 쓴다. ADE-41·ADE-40 README는 숫자 표시를 요구한다. 이번 구현은 ADE-41 명세를 따랐고, 팀 결정이 필요하다.
3. **실데이터 커버리지 위험(미확인)** — ADE-40은 누적이 음수가 되면 그날 나머지를 모두 추정 불가로 둔다. ADE-39의 관찰(하루 OUT 초과 약 40명)이 맞다면 오후 대부분이 회색이 될 수 있다. 또 ADE-39는 OUT_11이 결측이 아니라 **IN_11과 같은 값으로 잘못 기록**된다고 하는데, ADE-40은 OUT 결측(null)만 걸러 낸다.
4. **ADE-40 추천 로직** — 합성 데이터 13시 기준 응답에서 이미 지난 9–11시를 추천했고, 그 구간에 `혼잡`인 10시가 포함됐다(`tests/fixtures/ade40_today_partial.json`). 팀 백엔드는 지난 시간을 추천에서 제외한다.
5. operator-api 마지막 커밋 `50d0eaa`가 README의 HTML 연동 안내(98줄)를 지웠다. 계약은 `b8cd27f`의 README와 코드로 확인했다.
6. ADE-38 IN_21 라벨 현장 확인은 여전히 근거 없음(이번 범위 아님).

## 6. 다음 행동

- **가장 먼저**: §7 문안으로 이가영님께 팀 `backend/` 이식 일정과 계약 질문을 확인한다.
- 이식 PR이 올라오면 이 브랜치를 그 위로 올리고 `scripts.e2e`·`scripts.e2e_present`를 실제 팀 서버 응답으로 다시 돌린다. 그때 route 대체 없이 확인하는 케이스를 추가한다.
- 숫자 표시 여부(§5-2)는 10/1 발표·토론에서 팀 결정으로 정한다. 색만 쓰기로 하면 표·상세의 숫자 칸만 숨기면 된다.

## 7. 요청 문안 (미발송)

이가영님 (ADE-40):

```text
[ADE-41 연결 확인 요청 — 도형준]
ADE-40 완료 공유 감사합니다. operator-api b8cd27f 기준으로 필드를 확인했고,
프론트는 hourly[].estimated_present / level / quality_status / calculation_basis
기준으로 표시하도록 준비해 두었습니다(합성 데이터로 테스트 통과).

다만 이용자 화면은 팀 저장소 backend/ 를 호출해서, 지금은 operator-api 변경이
화면에 연결되지 않습니다. 아래 확인 부탁드립니다.

1. 계산을 팀 저장소 backend/(develop)로 옮기는 PR 계획과 대략 일정이 있을까요?
   화면이 쓰는 today?date=, meta.date_window, ADE-38 운영 달력, KST 시각,
   stats.hourly_total_in 은 유지돼야 합니다.
2. 기존 필드 method 대신 calculation_basis 를 쓰나요? 프론트는 둘 다 읽게 해 두었습니다.
3. 추천이 없을 때 best_start_hour 가 0인데, 팀 백엔드처럼 null로 맞출 수 있을까요?
4. 합성 데이터 13시 기준으로 지난 9–11시가 추천되고 그 구간에 '혼잡' 시간이
   포함됐습니다. 지난 시간 제외, 혼잡 시간 포함 여부 확인 부탁드립니다.
5. 강민재님 자료에 따르면 OUT이 하루 약 40명 더 많이 세어지고 OUT_11은 IN_11과
   같은 값으로 기록된다고 합니다. 실제 Excel로 돌렸을 때 negative_balance 로
   비는 시간대가 얼마나 되는지 확인 가능할까요?
6. 50d0eaa 에서 README의 HTML 연동 안내가 지워졌는데 의도하신 건지 궁금합니다.
```

강민재님 (ADE-39):

```text
[ADE-39·41 문구 확인 — 도형준]
초안에는 "사람 수는 숫자로 보이지 않고 색으로만"이라고 되어 있는데,
ADE-41/ADE-40 기준은 '추정 체류 인원 약 N명'을 표·그래프에 표시하는 방식입니다.
어느 쪽으로 할지 10/1 토론에서 정하면 좋겠습니다. 숫자를 숨기기로 하면
프론트는 숫자 칸만 가리면 됩니다. 자료 부족 문구는 초안의
"비교할 자료가 부족해 표시하지 않습니다"와 맞춰 둘 수 있습니다.
```

## 8. PR 설명 초안 (PR 미생성)

> **제목**: feat(ADE-41): 추정 체류 인원 응답 표시 준비 (ADE-40 연결 대기)
>
> **요약**
> - `/congestion/today` 응답에 `estimated_present`가 있으면 막대·표·상세·접근성 문구를 추정 체류 인원 기준으로 표시하고, 없으면 기존 예상 방문량 표시를 유지합니다.
> - 색은 백엔드 `level`을 그대로 쓰고, 값이 null이면 회색과 `추정 불가 (사유)`로 표시합니다. 범례에 `자료 부족`을 추가했습니다.
> - 선택 날짜와 다른 응답은 오류로 처리해 잘못된 날짜의 수치를 보여 주지 않습니다.
> - ADE-40(operator-api `50d0eaa`)의 실제 `today_forecast()` 출력으로 만든 테스트 픽스처와 브라우저 검증을 추가했습니다(합성 데이터).
>
> **연결 상태**: 팀 `backend/`에는 아직 `estimated_present`가 없어 운영 화면 표시는 바뀌지 않습니다. ADE-41은 이 PR로 완료되지 않습니다.
>
> **테스트**
> - [x] `python -m pytest -q` — 159 passed
> - [x] `python -m scripts.e2e` — PASS (synthetic)
> - [x] `python -m scripts.e2e_present` — PASS (ADE-40 fixtures, synthetic)
> - [ ] ADE-40 팀 backend 이식 후 실제 서버 응답으로 재검증
>
> **확인 필요**: ADE-39 숫자 표시 여부, ADE-40 추천 로직·`best_start_hour` 0/null
