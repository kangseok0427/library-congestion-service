# 도서관 혼잡도 예측 및 방문 안내

용산꿈나무도서관 과거 입장량을 이용한 방문 안내 프로젝트입니다.
현재 체류인원이나 좌석 점유율을 추정하지 않습니다.

## 협업 구조

- `main`: 검증된 안정 버전
- `develop`: 팀 통합
- `feature/ADE-xx-*`, `fix/ADE-xx-*`: 작업 후 develop 대상으로 PR
- `backend/`: 표준 records 검증, 통계, 예측, API 및 교체 가능한 어댑터
- `frontend/`: API를 사용하는 참고 웹 화면
- `data/sample/`: 합성 데이터만 공개
- `docs/`: 규격, 동작 설명, 검증, 인수인계
- `tests/`, `scripts/`: 자동 검증과 실행 도구

실제 Excel, 전처리 결과, SQLite, 비밀값은 공개 저장소에 올리지 않습니다.
팀원은 fork에서 작업하고 develop에 PR을 제출할 수 있습니다.
직접 push/merge 권한은 Public 여부와 별개이며 소유자가 관리합니다.

## 실행 (Python 3.12 이상)

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

http://127.0.0.1:8000 에서 확인합니다. 기본 입력은 합성 샘플입니다.
샘플 기간은 2026-07-21~2026-09-14입니다. 2026-09-15로 조회하면 예측을 볼 수 있습니다.
운영 날짜가 샘플 기간에서 멀어지면 데이터 부족으로 표시하는 것이 정상입니다.

## 실제 입력 / 갱신

```sh
python -m scripts.rebuild path/to/records.json
# Excel은 기존 이력을 보존하며 날짜·게이트 단위로 갱신합니다.
python -m library_etl refresh path/to/input.xlsx --output data/processed/records.json --partial-date 2026-09-10
# 모든 날짜의 수집 완료가 확인된 파일만 --all-complete 사용
```

`LIBRARY_RECORDS` 환경변수를 `data/processed/records.json`으로 설정한 뒤 서버를 실행합니다.
PowerShell: `$env:LIBRARY_RECORDS='data/processed/records.json'`
POSIX: `export LIBRARY_RECORDS=data/processed/records.json`
새 records 전체 스냅샷을 같은 경로로 rebuild한 뒤 웹에서 새로고침합니다.

## Railway 배포

운영 환경은 개인 PC와 Cloudflare Tunnel 대신 Railway PaaS를 사용합니다.
`railway.toml`의 설정에 따라 `python -m scripts.serve`로 실행되며, 운영 데이터는
Railway Volume의 `/data/records.json`에 보존합니다. 최초 설정, 사용자 도메인,
환경변수, Rollback 절차는 [Railway 배포·운영 인수인계](docs/railway.md)를 따릅니다.
Excel 갱신 시에는 위 `library_etl refresh`를 사용합니다. 부분 날짜를 생략하면
최신 날짜를 보수적으로 partial 처리하며, 완료 데이터를 partial로 덮어쓰지 않습니다.
`--output` 생략 시 `LIBRARY_RECORDS`, 그다음 `data/processed/records.json`을 사용합니다.
보고서는 stdout JSON으로 반환하며 운영 JSON 외의 별도 보고서 파일을 게시하지 않습니다.

[ADE-21 JSON 갱신 실행법·실패 보존·검증](docs/json-refresh.md)

## 검증

### T03·T06 통계/API

- `GET /api/v1/health`: 서버 상태 확인
- `GET /api/v1/patterns`: 일별·시간대별·요일별·요일/시간별·월별 기본 통계

통계와 API는 공통 `records.json`을 직접 사용합니다. 별도 CSV·SQLite 저장소나
`pandas`/`numpy` 실행 의존성은 사용하지 않습니다.

```sh
python -m pytest -q
python -m playwright install chromium
python -m scripts.e2e
python -m scripts.e2e --records data/processed/records.json --date 2026-09-10
```

[구현·인터페이스·인수인계](docs/architecture.md) / [Git 협업](docs/git-workflow.md)
