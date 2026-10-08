# Neon · Vercel 운영

이용자·관리자 화면과 Python API는 Vercel에서 제공하고, Neon의 PostgreSQL·Managed
Auth·Object Storage에 관리 정보와 파일을 저장합니다. Excel 전처리와 혼잡 계산은
기존 Python 코드를 재사용합니다. 방문 기록은 JSON 파일이며 DB는 로그인 세션,
업로드 내역, 버전 목록과 현재 표시할 버전을 관리합니다.

## 이용 방법

1. 사이트의 `/admin`에서 관리자 아이디와 비밀번호로 로그인합니다.
2. `.xlsx` 파일을 선택하고 업로드합니다. Excel은 비공개 저장소로 직접 전송됩니다.
3. Python API가 파일을 검증하고 기존 기록과 병합한 JSON을 게시합니다.
4. 이용자 화면을 새로 조회하면 새 데이터가 반영됩니다.
5. 관리자 버전 목록에서 보관 중인 이전 버전으로 복원할 수 있습니다.

화면 모양과 API v2 응답 형식은 유지합니다. 로그인·목록·복원은 같은 사이트의
`/api/v1/admin/*`에서 처리합니다. 큰 Excel 본문은 Vercel 함수 요청을 통과하지 않습니다.

## 최초 프로젝트 설정

1. Neon 프로젝트에서 Managed Auth와 Object Storage를 활성화합니다.
2. 일반 SQL 로그인 `library_backend`를 생성합니다. Console의 역할 생성 API는 기본적으로
   넓은 권한을 부여하므로 서비스에는 아래처럼 **SQL로 만든 제한 계정**을 사용합니다.
   비밀번호는 새 임의값으로 바꾸고 Git에 저장하지 않습니다.

   ```sql
   create role library_backend login password 'REPLACE_WITH_RANDOM_PASSWORD'
     nosuperuser nocreatedb nocreaterole nobypassrls;
   ```

3. `neon/migrations/202610070001_cloud.sql`을 새 데이터베이스에서 한 번 실행합니다.
   `library` 스키마의 제한 계정에는 조회, 업로드·세션 관리, 폐기 버전 삭제와 필요한
   게시 함수 실행만 허용합니다. 활성 버전을 직접 수정하거나 유지 중인 버전을
   삭제할 권한은 없습니다. 함수는 고정 search_path를 사용합니다.
4. `library-data`, `library-uploads` 버킷을 **private**로 만듭니다.
   브랜치에 한정된 storage:read/storage:write 자격증명을 생성합니다.
5. 업로드 버킷 CORS는 실제 사이트 Origin의 PUT과 Content-Type만 허용합니다.
   버킷 정책 공개나 `*` Origin 설정은 필요 없습니다.
6. Managed Auth에 관리자 이메일 계정을 만들고 Console/관리 API로 `admin` 역할을 지정합니다.
   공개 가입 계정은 기본 `user`이며 관리자 API를 사용할 수 없습니다. 프로필이나
   요청 본문에 `admin`이라고 적어도 권한이 생기지 않습니다. Auth trusted domain에
   실제 사이트 Origin을 추가합니다.
7. Vercel 프로젝트를 GitHub에 연결합니다. Framework FastAPI, 루트 디렉터리,
   운영 브랜치 `develop`, 실행 진입점 `app.py`를 사용합니다.

   | 환경 변수 | 값 |
   |---|---|
   | DATABASE_URL | library_backend 로그인으로 접속하는 pooled PostgreSQL URL, sslmode=require |
   | NEON_AUTH_URL | Managed Auth base URL, 끝의 /auth 포함 |
   | APP_ORIGIN | 실제 https 사이트 Origin |
   | NEON_STORAGE_ENDPOINT | 브랜치의 S3 endpoint |
   | NEON_STORAGE_REGION | S3 리전 |
   | NEON_STORAGE_ACCESS_KEY_ID | storage 자격증명의 token_id |
   | NEON_STORAGE_SECRET_ACCESS_KEY | storage 자격증명의 s3_secret_access_key |
   | NEON_STORAGE_BUCKET | library-data |
   | NEON_UPLOAD_BUCKET | library-uploads |
   | ADMIN_LOGIN_ID | 선택: 사이트 관리자 아이디 |
   | ADMIN_LOGIN_PASSWORD_HASH | 선택: 사이트 비밀번호 PBKDF2-SHA256 해시, .env.example 형식 |
   | NEON_ADMIN_EMAIL | 선택: 연결할 Managed Auth 관리자 이메일 |
   | NEON_ADMIN_PASSWORD | 선택: 해당 Managed Auth 관리자의 서버 전용 비밀번호 |

사이트 아이디를 사용하려면 마지막 네 변수를 모두 설정합니다. 서버가 사이트
비밀번호 해시를 확인한 뒤 지정된 Auth 계정에 연결하며, 관리자 역할 검사와
세션 폐기는 그대로 적용합니다. 일부만 설정하면 로그인을 거부합니다. 네 변수를
모두 생략한 환경은 기존 이메일 로그인을 사용합니다.

비밀값은 서버 전용 Sensitive 변수입니다. Preview에는 운영 비밀값을 공유하지 않습니다.
Preview 검증이 필요하면 별도 Neon 브랜치와 해당 자격증명을 사용합니다.
로컬/PythonAnywhere 실행 진입점은 `backend.app:app`이며 이 설정과 분리됩니다.

## 제한과 실패 처리

- Excel 최대 10 MiB, 압축 해제 크기 256 MiB, 결과 JSON 45 MB입니다. 업로드 URL은
  5분 동안 한 파일 경로·Content-Length·Content-Type에 한정됩니다. 같은 URL은
  만료 전 재사용할 수 있습니다. 처리 서버는 실제 크기를 다시 검사합니다.
- 정상 JSON은 최신 4개를 보관합니다. 다섯 번째 게시 후 가장 오래된 것을 지웁니다.
  게시 중 후보를 포함하면 일시적으로 최대 5개입니다. 파일 정리가 실패하면 다음
  게시를 막아 버전이 계속 쌓이지 않게 합니다.
- 잘못된 Excel, 연결 오류, 게시 실패는 기존 활성본을 유지합니다. DB 트랜잭션과
  10분 임대로 동시 게시와 만료된 작업의 늦은 게시를 막습니다.
- 원본 전체 합계와 시간대 합계 차이, 최신 날짜의 수집 완료 여부 미확인은
  내부 진단으로 기록합니다. 원본값과 부분 데이터 표시는 보존하고 불완전한
  관측은 통계에서 제외합니다. 관리자가 조치할 경고만 화면에 표시하며,
  결측·음수·잘못된 숫자·중복 등 실제 검증 오류는 계속 게시를 거부합니다.
- 이용자 막대는 보유한 모든 과거 연도에서 선택한 날짜와 같은 월·일·시간의
  유효 체류 인원을 평균 낸 통계입니다. 최근 4주 제한과 다른 날짜 대체는 없습니다.
  예를 들어 10월 8일 조회는 과거 연도의 10월 8일을 사용합니다. 같은 날짜가
  휴관이었거나 기록이 불완전하면 해당 관측을 제외합니다. 윤일은 과거 윤일만 씁니다.
  시간별 source_dates/source_years/sample_count와 응답 statistics에 사용한 자료를
  제공합니다. 시간대마다 이용 가능한 연도 수가 다를 수 있습니다.
  원본에서 날짜가 일치한 내역은 matched_source_dates, 계산에서 제외한 날짜와
  사유는 excluded_samples에 따로 남깁니다. 날짜 없음과 계산 불가를 구분합니다.
  누적 IN−OUT이 음수가 되면 0명으로 보정하고 다음 시간에도 계속 누적합니다.
  보정된 0명도 평균에 포함하며 corrected_source_dates/corrected_sample_count로
  보정 내역을 남깁니다. 원본 IN/OUT은 그대로입니다.
  같은 월·일의 과거 원본이 아예 없는 날짜는 조회 시 다음 가능한 날짜로 건너뛰며
  선택 날짜와 안내 문구를 함께 바꿉니다. 오늘~7일 후 범위를 늘리지는 않습니다.
  비교 대상 시간 중 계산할 수 없는 구간이 있으면 여유 시간 추천을 하지 않습니다.
  예를 들어 오후 기록을 계산할 수 없다고 오전 11~13시를 여유롭다고 추천하지 않습니다.
  색상은 같은 월·일의 전체 운영 시간대 관측을 공통 기준으로 비교합니다.
  0명은 여유이며 자료가 없으면 판정하지 않습니다. 실시간 인원, 미래 예측,
  좌석 점유율은 아닙니다. 현재 조회 범위와 운영시간·휴관 정책은 유지합니다.
- 같은 upload_id의 재시도는 이미 완료한 결과를 반환합니다. 파일 재선택이나
  페이지 새로고침은 새 작업입니다. 미게시 Excel과 만료 세션은 다음 업로드 시작 시
  정리합니다(원본 24시간). 활동이 없을 때 자동 정리는 실행하지 않습니다.
- 서버 세션은 최대 8시간이며 매 요청마다 Managed Auth의 현재 권한을 확인합니다.
  로그아웃 시 DB 세션과 Auth 세션을 폐기합니다. 비밀번호를 DB나 브라우저 저장소에
  보관하지 않습니다.
- Vercel 함수 최대 실행 시간은 300초입니다. 실제 Excel이 이 시간 안에 처리되는지
  배포 환경에서 측정합니다. 초과하면 별도 worker가 필요합니다.
- 무료 요금제의 저장·처리 한도는 사용량에 따라 적용됩니다. 오래 사용하지 않은
  DB의 compute는 재개할 수 있지만 무제한 처리나 무중단 운영을 보장하는 구조는 아닙니다.
- PythonAnywhere 기존 기록은 자동 이관되지 않습니다. 기존 서비스를 종료하기 전에
  OBT 원본 Excel을 새 관리자 화면에 업로드하고 이용자 화면을 확인합니다.

## 검증

```bash
pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npm run test:cloud-sql
python -m scripts.e2e
python -m scripts.e2e_admin
```

SQL 테스트는 실제 PostgreSQL 엔진(PGlite)에서 동시 게시, 임대 만료, 중복 재시도,
4개 보관·복원, 익명 접근 거부와 제한 서비스 계정의 권한을 검사합니다. 실제 사이트에서
로그인, CORS 업로드, 잘못된 파일 거부, 다섯 번째 게시와 복원도 별도로 확인합니다.

공식 참고: https://neon.com/docs/storage/s3-compatibility,
https://neon.com/docs/auth/authentication-flow,
https://vercel.com/docs/frameworks/backend/fastapi,
https://vercel.com/docs/functions/limitations
