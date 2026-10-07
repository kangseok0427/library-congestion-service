# 관리자 웹 통합 및 서버 설정

관리자 진입 주소는 `/admin`이며 `/static/admin.html`로 연결됩니다.
이용자 화면은 기존 `/`입니다. 두 화면 모두 같은 FastAPI 서버에서 제공합니다.

서버 실행 환경에 `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`를
설정해야 로그인할 수 있습니다. 비밀키는 충분히 긴 무작위 문자열을 사용하고
계정 정보와 함께 Git에 저장하지 않습니다. `.env.example`은 예시이며 서버는
`.env`를 자동으로 읽지 않습니다. PythonAnywhere 실행 명령이나 별도 실행
스크립트에서 환경변수를 전달해야 합니다. `env` 실행 파일은 `/usr/bin/env`처럼
절대 경로를 사용합니다.

로그인 쿠키는 HTTPS 전용이며 8시간 후 만료됩니다. 로그인 상태는 records와 같은
디렉터리의 `admin-sessions.json`에 해시로 저장되어 서버 프로세스 및 재시작 간
공유됩니다. 로그아웃하면 모든 프로세스에서 해당 세션이 무효화됩니다.
이 파일과 `admin-sessions.json.versions` 잠금 디렉터리는 운영 데이터이며
서버 계정만 접근 가능한 영구 저장 디렉터리에 보관합니다.

관리자는 로그인 후 `.xlsx` 파일 하나(최대 10 MB)를 업로드합니다. 서버에서
검증과 기존 기록 병합을 마친 후 JSON을 게시하며 정상 버전은 최대 4개 보관합니다.
목록에서 이전 버전으로 복원할 수 있습니다. 쿠키 인증을 사용하므로 업로드 토큰을
입력할 필요가 없습니다. 기존 토큰 방식 records API는 호환용이며 버전 관리
초기화 이후에는 기록을 직접 덮어쓸 수 없습니다.

배포 시 `git pull origin develop`, `pip install -r requirements.txt` 후 서버를
재시작합니다. 관리자 환경변수 설정과 HTTPS 실제 업로드 확인은 배포 단계입니다.
