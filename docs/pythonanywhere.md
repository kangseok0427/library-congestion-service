# PythonAnywhere 무료 배포·운영 안내

## 현재 배포 구조

Week 4 목표: `관리자 웹 Excel 업로드 → 서버 공용 전처리·검증 → 홈 디렉터리 records.json → FastAPI → 이용자 웹`

관리자 로그인·업로드·4세대 버전·롤백은 ADE-45/46/47 통합과 ADE-50 검증 후 운영에
반영합니다. 아래 호스팅 준비 절차가 새 관리자 기능의 배포 완료를 뜻하지 않습니다.

- 수요일 MVP는 PythonAnywhere 무료 계정의 기본 주소를 사용합니다.
- FastAPI와 프론트 정적 파일은 하나의 ASGI 앱에서 함께 제공합니다.
- 운영 JSON은 Git 저장소 밖의 `~/library-congestion-data/records.json`에 둡니다.
- Git pull이나 웹앱 reload를 해도 운영 JSON은 덮어쓰지 않습니다.
- `railway.toml`은 PythonAnywhere에서 사용되지 않습니다.
- FastAPI용 ASGI 호스팅은 PythonAnywhere의 베타 기능입니다. 실제 운영 전
  [공식 ASGI 안내](https://help.pythonanywhere.com/pages/ASGICommandLine/)의 제한과
  서비스 정책을 다시 확인합니다. 플랫폼의 별도 정적 파일 매핑은 지원되지 않지만,
  이 프로젝트는 FastAPI의 `/static` 경로로 파일을 제공하므로 같은 앱에서 검증합니다.

## 1. 최초 준비

PythonAnywhere 계정을 만든 뒤 Account 페이지에서 API token을 생성하고 Bash console을
엽니다. 아래의 `YOURUSERNAME`은 PythonAnywhere 사용자명으로 모두 바꿉니다.

```sh
git clone https://github.com/kangseok0427/library-congestion-service.git
cd ~/library-congestion-service
git switch develop

mkvirtualenv library-congestion --python=python3.12
pip install -r requirements.txt
pip install --upgrade pythonanywhere
```

Python 3.12 가상환경 생성이 지원되지 않으면 PythonAnywhere Files 화면에 표시되는 최신
Python 버전을 사용합니다. 프로젝트의 자동 테스트는 Python 3.12 기준이므로 다른 버전을
사용할 때는 배포 후 검증 항목을 모두 확인합니다.

## 2. 영구 데이터 경로 준비

최초 한 번은 합성 샘플로 파일을 만든 뒤 실제 검증된 records로 교체합니다.

```sh
cd ~/library-congestion-service
export LIBRARY_RECORDS=/home/YOURUSERNAME/library-congestion-data/records.json
export LIBRARY_BOOTSTRAP_SAMPLE=true
python -c "from scripts.serve import bootstrap_records; bootstrap_records()"
```

`bootstrap_records`는 대상 파일이 이미 존재하면 덮어쓰지 않습니다. 실제 records는
공개 Git 저장소에 commit하지 않습니다. 최초 실데이터 전환은 ADE-50에서 합성 샘플을
운영 이력에 섞지 않도록 확인한 뒤 수행합니다.

## 3. FastAPI 웹앱 생성

다음 명령은 한 줄로 실행합니다. `${DOMAIN_SOCKET}`은 바꾸지 않습니다.

```sh
pa website create --domain YOURUSERNAME.pythonanywhere.com --command 'env LIBRARY_RECORDS=/home/YOURUSERNAME/library-congestion-data/records.json /home/YOURUSERNAME/.virtualenvs/library-congestion/bin/uvicorn --app-dir /home/YOURUSERNAME/library-congestion-service --uds ${DOMAIN_SOCKET} pythonanywhere_asgi:app'
```

생성 결과와 실제 명령을 확인합니다.

```sh
pa website get
pa website get --domain YOURUSERNAME.pythonanywhere.com
```

무료 계정에서는 우선 `https://YOURUSERNAME.pythonanywhere.com` 주소를 사용합니다.
기존 `library.gaon0033.org` 연결은 무료 MVP 완료 후 별도 호스팅 전환 때 결정합니다.

## 4. 배포 검증

브라우저에서 아래 항목을 순서대로 확인합니다.

1. `https://YOURUSERNAME.pythonanywhere.com/api/v1/health`가 `{"status":"ok"}` 반환
2. 메인 페이지가 열리고 CSS와 JavaScript가 적용됨
3. 날짜 선택 후 예상 방문량, 혼잡 단계, 추천 시간이 표시됨
4. `GET /api/v1/patterns`가 통계 JSON 반환
5. 잘못된 날짜 요청이 공통 오류 JSON으로 반환

로그는 아래 경로에서 확인합니다.

```sh
tail -n 100 /var/log/YOURUSERNAME.pythonanywhere.com.error.log
tail -n 100 /var/log/YOURUSERNAME.pythonanywhere.com.server.log
```

## 5. 코드 업데이트

`develop`에 검증된 PR을 병합한 뒤 PythonAnywhere Bash console에서 실행합니다.

```sh
cd ~/library-congestion-service
git switch develop
git pull --ff-only origin develop
workon library-congestion
pip install -r requirements.txt
python -m pytest -q
pa website reload --domain YOURUSERNAME.pythonanywhere.com
```

무료 환경에서 전체 브라우저 E2E 설치가 제한되면 pytest와 공개 주소 수동 검증을 먼저
수행하고, Chromium E2E는 GitHub Actions에서 확인합니다.

## 6. 데이터 갱신과 복구

현재 수동 갱신은 임시 경로에서 검증한 뒤 운영 파일을 원자 교체합니다.

```sh
cd ~/library-congestion-service
workon library-congestion
export LIBRARY_RECORDS=/home/YOURUSERNAME/library-congestion-data/records.json
python -m library_etl refresh path/to/input.xlsx --partial-date YYYY-MM-DD
```

갱신 실패 시 기존 정상 JSON은 유지됩니다. 문제가 생기면 error log를 확인합니다.
ADE-45 저장소가 통합된 경로에서는 활성 JSON과 버전 메타데이터를 함께 복구해야 하므로
파일만 수동으로 덮어쓰지 않습니다. 통합 전후 복구는 검증된 서버 담당 절차를 따릅니다.

관리자 웹 전환 이후의 경로·게시·롤백 절차는 [관리자 업로드 운영 안내](admin-upload.md)를
따릅니다. 기능이 통합되기 전에는 기존 서버 담당자의 검증된 수동 절차만 사용합니다.
기존 `ADMIN_UPLOAD_TOKEN` 인증 코드와 설정은 서버 호환을 위해 보존하지만 관리자에게
입력을 요구하는 운영 절차는 제공하지 않습니다. 호환 경로 폐기는 ADE-50에서 결정합니다.

## 7. 중지·삭제

```sh
pa website delete --domain YOURUSERNAME.pythonanywhere.com
```

웹앱을 삭제해도 홈 디렉터리의 records 파일은 별도이므로 필요 없어진 것이 확실할 때만
직접 정리합니다. 호스팅 비밀값, 실제 Excel과 실제 records는 Git에 올리지 않습니다.
