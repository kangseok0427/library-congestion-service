# ADE-41 잔류 인원 기반 그래프·혼잡 색상 반영 — 확인 및 인수인계

작업일: 2026-09-30. 담당: 도형준. 기준 브랜치: `feature/ADE-41-estimated-present-prep`
(`upstream/develop` = `kangseok0427/library-congestion-service` 최신 커밋 `e668eb7`에서 분기, 별도 worktree에서 작업).

범위: ADE-41은 ADE-40(이가영 담당)이 제공할 `estimated_present` API 필드를 사용해
기존 이용자 페이지의 막대 높이·혼잡 색상·문구를 바꾸는 작업이다. 아래는 이번에 실제로
확인한 사실과, 확인 결과 ADE-41을 지금 완료로 구현할 수 없는 이유, 그리고 준비된 것만 정리한다.
**구현 완료를 주장하지 않는다.**

## 1. 시작 전 확인 결과

- 저장소 루트·`.github/`를 확인했지만 `AGENTS.md`, `CLAUDE.md`는 어느 브랜치에도 없다
  (`main`, `develop`, 모든 `feature/*`, `fix/*` 원격 브랜치 포함 확인).
- `05_START_HERE_2026-09-06.md`, `ADE-38-handoff.md`, 로컬 `ADE-38.patch.txt`도 이 저장소/워크트리에는 없다.
  (Linear ADE-38 코멘트에 언급된 ChatGPT 프로젝트 업로드본은 이 세션에서 접근할 수 없어 확인하지 못했다.)
- `docs/ade-38.md`는 develop에 존재해 읽었다. **IN_21 버킷 의미(21:00~21:59 시작 라벨 vs
  20:00~21:00 종료 라벨)는 여전히 미확인 상태다.** Linear ADE-38 코멘트(2026-09-23)에도
  "남은 완료 조건: 현장에서 IN_21 라벨 확인"이라고 명시되어 있다. 이번 세션에서 이를
  확정할 근거를 찾지 못했으므로 **미확인으로 유지**한다. (ADE-38 Linear 상태는 Done이지만,
  이슈 설명 자체가 IN_21 현장 확인을 완료 조건에서 분리하고 있다.)
- 로컬 git 상태: 원래 체크아웃 브랜치(`feature/ADE-9-web-ui-mobile`)는 clean, origin과 동기화 상태였고
  그대로 보존했다. 이번 작업은 `git worktree add`로 별도 디렉터리(`../library-congestion-service-ade41`)와
  새 브랜치를 만들어 진행했다. 기존 브랜치에 강제 checkout/reset을 하지 않았다.
- Linear 접근 가능. ADE-41, ADE-40, ADE-39, ADE-26, ADE-38 이슈 본문·댓글·관계(blocks/relatedTo)를
  모두 조회했다.

## 2. ADE-40 API 준비 상태 — 확인 결과: **미구현**

`estimated_present` 필드를 저장소의 모든 로컬/원격 브랜치(`main`, `develop`, 모든 `feature/*`,
`fix/*`, 총 20여 개 ref)에서 `git grep`으로 전수 검색했지만 **어디에도 없다.**
Linear ADE-40 이슈는 상태 `Todo`, `startedAt`/`completedAt` 없음, 코멘트 없음 — 착수 전으로 확인된다.

현재 develop의 실제 계약(`backend/congestion.py`, `backend/service.py`, `backend/prediction.py`,
`docs/architecture.md`):

| 확인 항목 | 현재 상태 |
| --- | --- |
| `estimated_present` 위치·자료형 | **존재하지 않음.** `/api/v1/congestion/today`의 `hourly[]`는 `expected_visitors`(방문 "입장량" 기준 예측치, `int \| null`)만 제공. |
| 혼잡 단계 기준 | `backend/congestion.py`의 `midrank_score` — 같은 시간대 **과거 방문량(IN 합계) 분포**의 백분위. 35 이하 quiet / 70 이하 normal / 나머지 busy. ADE-40은 이 분포의 입력을 "입장량"에서 "추정 체류 인원"으로 바꾸는 작업이며, 아직 반영되지 않았다. |
| 자료 부족/휴관일/미래 응답 | 기존 패턴 확인됨: 휴관일 `data_status=closed`(빈 hourly, null 합계), 미래 영업일 `data_status=pending`(집계 전), 표본 없는 시간대는 `expected_visitors=null`, `level=null`(자료 부족, 0이나 "여유"로 치환하지 않음). ADE-40이 이 패턴을 그대로 따르는지는 확인 이슈로 별도 요청함(§4). |
| 기존 예측·추천 API 호환 | `recommendation()`은 `expected_visitors`(입장량) 합이 가장 낮은 연속 2시간을 추천한다. ADE-40 이슈 설명은 "혼잡 점수" 전환만 언급하고 추천 로직 전환은 명시하지 않아 **확인이 필요한 공백**이다(§4에 질문으로 포함). |

**결론: API 계약이 아직 없으므로, 이번 세션에서는 이가영님 담당 계산을 대신 구현하지 않았고,
`estimated_present`의 필드 모양이나 혼잡 경계를 임의로 정하지 않았다.**

## 3. 이번에 한 일 / 하지 않은 일

### 했다
- develop 기준 커밋에서 baseline 테스트를 직접 실행해 현재 상태가 정상임을 확인했다(§5).
- ADE-39(강민재 담당, 상태 Todo)에 첨부된 초안 설명자료(`congestion-explained.md`, Linear
  description embed에서 서명 URL로 1회 확인, 5분 만료)를 읽고 용어를 대조했다. **이 초안은
  미완료(Todo) 이슈의 첨부 자료이며 최종본이 아니다.** 확인한 용어: "추정 체류 인원", 초록/노랑/빨강
  3단계, "누적 IN − 누적 OUT = 체류 인원" 공식, "같은 요일·시간대 비교 = 상대적 혼잡도"(절대 인원 아님),
  한계로 OUT_11(오전 11시) 기록 오류·센서 특성상 일평균 약 40명 누락 언급. 현재 develop 프론트의
  안내 문구("정확한 현재 인원이 아님")와 방향은 일치하지만, "추정 체류 인원"이라는 용어 자체는 아직
  실제 화면에 없다(ADE-40 연결 전이므로).
- ADE-40 담당자에게 보낼 API 계약 확인 요청 문안을 작성했다(§4). **전송하지 않았다.**
- 현재 프론트(`frontend/app.js`, `style.css`)가 ADE-41 완료 조건 중 **API 연결과 무관하게 이미
  만족하는 부분**을 구조 분석으로 확인했다(변경 불필요, 참고용):
  - 색상은 이미 백엔드가 반환한 `level`을 그대로 클래스에 사용한다(`bar ${h.level ?? 'none'}`).
    프론트가 자체적으로 혼잡 경계를 계산하지 않는다 — ADE-41 요구사항 기존 충족.
  - `expected_visitors === null`인 시간대는 이미 회색(`--none`) 막대 + "자료 부족" 문구로 표시된다.
    0이나 "여유"로 치환하지 않는다 — 기존 충족.
  - 표(`#hourly`)·막대(`#chart`)·상세설명(`#detail`)이 모두 같은 `describe(h)` 함수 결과를 쓴다 —
    수치 불일치 위험이 이미 낮다.
  - 다만 범례(`.legend`)에는 여유/보통/혼잡 3개만 있고 "자료 부족"(회색) 항목이 없다. 이는
    ADE-40과 무관하게 존재하는 기존 결함이며, ADE-41 완료 조건("범례가 같은 단계를 사용")과
    관련이 있어 **참고로만 기록**하고 이번에는 수정하지 않았다(범위 불확실한 상태에서 화면 문구를
    바꾸는 것을 피하기 위함). 실제 연결 시점에 함께 처리할 것을 제안한다.

### 하지 않았다 (의도적으로)
- ADE-40의 잔류 인원 계산 로직(`이전 추정치 + IN − OUT`, 날짜별 초기화, OUT 결측 처리 등)을
  대신 구현하지 않았다.
- `estimated_present` 필드명·위치(hourly별 vs 최상위)·null 규칙을 추측해 프론트 코드에
  미리 반영하지 않았다. 확정되지 않은 계약을 프론트가 먼저 정의하면 실제 계약과 어긋날 위험이
  있고, "임의로 API 필드를 확정하지 않는다"는 제약과 충돌한다.
- 기존 "예상 방문량" 문구를 "추정 체류 인원"으로 바꾸지 않았다. 실제로 연결된 지표가 여전히
  입장량이므로, 이름만 바꾸면 이용자에게 잘못된 근거를 제공하게 된다.
- Linear 상태 변경, PR 생성, 병합, 배포를 하지 않았다.

**따라서 ADE-41은 이번 세션에서 미완료다.** 완료 기준("ADE-40 API 연결 후 추정 체류 인원 기준
화면이 동작함")을 만족하지 못했다.

## 4. ADE-40 담당자(이가영)에게 전달할 요청 초안 — **미발송**

> 아래 문안은 Linear ADE-40 코멘트 또는 팀 채널에 직접 붙여넣을 수 있도록 준비한 것이며,
> 이번 세션에서 실제로 전송하지 않았다.

```text
[ADE-41 프론트 작업 중 확인 요청 — 도형준]

ADE-41(잔류 인원 기반 그래프)을 준비하면서 develop 기준 코드를 확인했는데
estimated_present 필드가 아직 없어서, 화면 쪽을 미리 확정하지 않고
아래 항목만 여쭤보고 싶습니다.

1. estimated_present가 /api/v1/congestion/today 응답의 hourly[] 항목 안에
   들어가나요 (expected_visitors와 같은 레벨), 아니면 별도 최상위 필드인가요?
2. 자료 부족/추정 불가 시간대는 null로 오나요? 그때 level도 null로 오는지,
   아니면 별도의 data_quality 같은 상태 필드가 따로 오는지 확인 부탁드립니다.
   (현재 화면은 expected_visitors===null && level==null 조합을 "자료 부족"
   회색으로 표시하고 있어서, 같은 패턴을 유지하고 싶습니다.)
3. 혼잡 단계(level)는 이미 있는 quiet/normal/busy 키를 그대로 쓰나요,
   아니면 값 자체가 바뀌나요? (색상은 프론트가 계산하지 않고 그대로 쓸 계획입니다.)
4. 추천 시간(recommendation) 로직도 추정 체류 인원 기준으로 바뀌나요,
   아니면 기존 입장량(expected_visitors) 기준을 유지하나요? ADE-40 설명에는
   혼잡 단계 전환만 있고 추천 로직은 명시되어 있지 않아서 확인이 필요합니다.
5. sample_count/계산 근거/자료 품질 필드는 필드명이 확정되면 알려주세요.
   상세 설명 문구(#detail)에 그대로 노출할 계획입니다.
6. 작업 일정(대략적인 API 반영 시점)을 알 수 있을까요? ADE-41을 그 시점에 맞춰
   프론트 PR을 준비하겠습니다.

참고로 ADE-39(강민재님) 초안 설명자료의 "누적 IN - 누적 OUT" 정의, 초록/노랑/빨강
3단계, "상대적 혼잡도" 표현과 화면 문구를 맞출 예정입니다.
```

## 5. 검증 (오늘 직접 실행)

아래는 **오늘(2026-09-30) 이 세션에서 직접 실행한 결과**다. 과거 ADE-38 문서에 적힌
"108 passed"는 2026-09-22 당시 기록이며, 이번 실행 결과와 구분한다. 프론트 코드를
변경하지 않았으므로 이는 ADE-41 구현 검증이 아니라 **작업 착수 전 develop 상태 확인**이다.

```text
# 위치: worktree feature/ADE-41-estimated-present-prep @ e668eb7 (upstream/develop HEAD)
python -m pytest -q
# 결과: 159 passed, 1 warning (starlette/httpx deprecation, 실패 아님) — 9.40s

python -m scripts.e2e
# 결과: {"e2e": "PASS", "input": "synthetic", "checks": [...]}
#  - API→DOM 연동, 8개 KST 날짜(휴관·집계 전 포함), 과거/+8일 거부,
#    LA 시간대에서도 KST 유지, 합성 Excel 갱신, OUT_11 null 보존,
#    잘못된 파일 시 이전 값 유지, 모바일 390px 가로 넘침 없음
```

이 결과는 **합성 데이터 기준**이며 실제 도서관 Excel 데이터 검증이 아니다. 예측 정확도
검증도 아니다(ADE-16 backtest는 별도 문서 `docs/verification.md` 참고, 이번 세션에서
재실행하지 않았다).

ADE-41 자체의 변경 사항 검증(막대 높이·색상·표 일치 등)은 **실행하지 못했다** — 프론트 코드를
변경하지 않았기 때문이다. 통과로 기록하지 않는다.

## 6. 변경 파일

- `docs/ade-41.md` (신규, 이 문서) — 그 외 프로덕션 코드 변경 없음.

## 7. 미완료 · 막힘 · 다음 행동

- **막힘 원인**: ADE-40(이가영 담당) API 미착수. `estimated_present` 필드가 코드 어디에도 없음.
- **다음 행동(우선순위 1개)**: §4의 요청 문안을 이가영님께 실제로 전달(Linear 코멘트 또는
  구두)하고, 필드 위치·null 규칙·추천 로직 기준·일정에 대한 답을 받는다. 답을 받으면
  `feature/ADE-41-estimated-present-prep` 브랜치에서 실제 필드에 맞춰 `describe()`의
  `expected_visitors` → `estimated_present` 전환, 문구 변경("예상 방문량" → "추정 체류 인원"),
  범례에 "자료 부족" 항목 추가를 진행하고 `scripts/e2e.py`에 회귀 케이스를 추가한다.
- ADE-39 초안이 Todo 상태이므로, 화면 문구 확정 전 강민재님의 최종본과 다시 한 번 대조가 필요하다.
- ADE-38의 IN_21 현장 확인도 여전히 열려 있다(이번 세션에서 해결 대상 아님, 참고로만 기록).

## 8. PR 설명 초안 (참고용, 아직 PR 없음)

이번 세션은 코드 변경이 아닌 확인·문서화이므로 실제 PR을 만들지 않았다. 사용자 승인 시
아래를 PR 본문으로 쓸 수 있다.

> **제목**: docs(ADE-41): API 미착수 확인 및 인수인계 정리
>
> **요약**
> - ADE-41은 ADE-40 API(`estimated_present`)에 의존하는데, 전 브랜치 검색 결과 해당 필드가
>   아직 구현되지 않음을 확인했다.
> - develop(`e668eb7`) 기준 baseline 테스트(pytest 159 passed, Playwright e2e PASS)를 재확인했다.
> - ADE-39 초안 설명자료와 용어를 대조했다.
> - ADE-40 담당자에게 전달할 API 계약 확인 질문을 정리했다(미발송).
> - 프로덕션 코드는 변경하지 않았다(계약 미확정 상태에서 필드/경계를 임의로 만들지 않기 위함).
>
> **테스트 계획**
> - [x] `python -m pytest -q` (159 passed)
> - [x] `python -m scripts.e2e` (PASS, synthetic)
> - [ ] ADE-40 연결 후 프론트 변경 및 회귀 테스트 (별도 후속 PR)
