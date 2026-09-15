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

구체적인 실행법은 구현 브랜치의 문서를 참고하세요.
