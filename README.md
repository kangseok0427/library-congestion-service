# 도서관 혼잡도 예측 및 방문 안내

용산꿈나무도서관 과거 입장량을 이용한 방문 안내 프로젝트입니다.
현재 체류인원이나 좌석 점유율을 추정하지 않습니다.

## 협업 구조

- `main`: 검증된 안정 버전
- `develop`: 팀 통합
- `feature/ADE-xx-*`, `fix/ADE-xx-*`: 작업 후 develop 대상으로 PR
- `library_etl/`: T02 Excel 전처리와 T08 트랜잭션 갱신
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

T02 전처리 결과만 확인할 때:

```sh
python -m library_etl preprocess path/to/input.xlsx --all-complete --output output/preprocess
```

T08 갱신은 검증이 끝난 날짜·게이트 묶음만 SQLite에 원자적으로 교체합니다. 실패하면 기존 데이터와 파생 결과를 모두 유지합니다.

```sh
python -m library_etl refresh path/to/input.xlsx \
  --database data/library.sqlite3 --all-complete --output output/refresh
```

운영 서버가 갱신된 SQLite 스냅샷을 직접 읽게 하려면 `LIBRARY_DB`를 설정합니다.

```sh
export LIBRARY_DB=data/library.sqlite3
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Windows PowerShell에서는 `$env:LIBRARY_DB='data/library.sqlite3'`를 사용합니다.

기존 JSON 재빌드 방식도 계속 지원합니다.

```sh
python -m scripts.rebuild path/to/records.json
# 원본 Excel을 독립 어댑터로 확인할 때 (부분 수집일을 명시)
python -m scripts.rebuild path/to/input.xlsx --partial-date 2026-09-10
```

`LIBRARY_RECORDS` 환경변수를 `data/processed/records.json`으로 설정한 뒤 서버를 실행합니다. `LIBRARY_DB`가 설정되면 SQLite 입력을 우선합니다.
PowerShell: `$env:LIBRARY_RECORDS='data/processed/records.json'`
POSIX: `export LIBRARY_RECORDS=data/processed/records.json`
새 records 전체 스냅샷을 같은 경로로 rebuild한 뒤 웹에서 새로고침합니다.

## 검증

```sh
python -m pytest -q
python -m playwright install chromium
python -m scripts.e2e
python -m scripts.e2e --records data/processed/records.json --date 2026-09-10
```

[구현·인터페이스·인수인계](docs/architecture.md) / [T02·T08 통합 검증](docs/t02-t08-integration.md) / [Git 협업](docs/git-workflow.md)
