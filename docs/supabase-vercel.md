# Supabase · Vercel 전환

관리자와 이용자 화면, FastAPI는 Vercel에서 제공하고 로그인과 영구 저장은
Supabase를 사용합니다. Excel 전처리·혼잡 계산 코드는 그대로 재사용합니다.
Supabase Edge Function에서 Python을 실행하는 구조가 아닙니다.

## 데이터 흐름

1. 관리자 이메일·비밀번호를 서버가 Supabase Auth로 확인합니다.
2. 관리자 권한을 확인한 후 파일 하나의 서명 업로드 URL을 발급합니다.
3. 브라우저가 Excel을 private Storage로 직접 전송합니다. Excel 본문은 Vercel을 통과하지 않습니다.
4. 브라우저가 upload_id만 Vercel에 전달하면 Python이 Excel을 내려받아 기존 기록과 병합합니다.
5. 검증된 JSON을 새 파일에 저장한 후 DB 트랜잭션으로 활성 버전을 교체합니다.
6. 이용자 API는 활성 버전만 읽습니다. 정상 목록은 최신 4개, 정리 중 후보 포함 최대 5개입니다.

방문 기록은 JSON 파일에 있습니다. DB 테이블은 활성 버전·게시 잠금·업로드 ID·관리자
세션 등 관리 정보용입니다. SQL migration은 local JSON VersionStore를 cloud adapter로
교체하는 데 필요합니다. SQLite와 서버 디스크를 영구 저장소로 사용하지 않습니다.

## 최초 설정

1. Supabase 프로젝트를 생성합니다. SQL Editor에서
   `supabase/migrations/202610070001_cloud.sql` 전체를 **새 프로젝트에 한 번** 실행합니다.
   기존 동일 테이블/함수가 있는 프로젝트에 재실행하지 않습니다.
2. Auth의 Users에서 본인이 사용할 관리자 이메일 계정을 생성합니다. 공개 가입을 끕니다.
3. SQL Editor에서 아래 관리자 지정 SQL을 실행합니다. 이메일을 실제 관리자 이메일로 바꿉니다.
   `user_metadata`가 아니라 관리자만 수정 가능한 `raw_app_meta_data`에 설정합니다.

   ```sql
   update auth.users
   set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb)
                          || '{"library_admin":true}'::jsonb
   where email = 'YOUR_ADMIN_EMAIL';
   ```

4. Vercel에서 GitHub 저장소를 Import합니다. Framework Preset은 FastAPI,
   Root Directory는 저장소 루트, Production Branch는 `develop`입니다.
   실행 진입점은 루트 `app.py`입니다. 로컬 서버는 계속 `backend.app:app`을 사용할 수 있습니다.
5. Vercel Environment Variables에 다음을 넣습니다. **서버 전용 값**이며 프론트 코드나
   `NEXT_PUBLIC_*` 변수에 비밀키를 넣지 않습니다.

   | 변수 | 값 |
   |---|---|
   | SUPABASE_URL | 프로젝트 URL |
   | SUPABASE_ANON_KEY | 프로젝트의 legacy anon API key |
   | SUPABASE_SERVICE_ROLE_KEY | 프로젝트의 legacy service_role API key |
   | SUPABASE_STORAGE_BUCKET | library-data |
   | SUPABASE_UPLOAD_BUCKET | library-uploads |

6. Preview와 Production은 **서로 다른 Supabase 프로젝트**로 연결합니다.
   Preview에 운영 비밀키를 넣으면 미병합 코드가 운영 데이터를 변경할 수 있습니다.
7. 배포 후 `/admin`에서 로그인하고 OBT에 사용한 실제 Excel을 한 번 업로드합니다.
   첫 업로드 전에는 버전 목록이 비어 있고 이용자 API에 자료 없음이 표시됩니다.
8. 이용자 화면의 날짜·막대, 잘못된 파일 거부 후 기존 화면 유지, 5번째 게시 후
   4개 보관, 이전 버전 복원, 로그아웃 후 API 거부를 확인합니다.

## 제한과 장애 처리

- Excel 최대 10 MB는 브라우저·Storage 업로드 버킷·처리 서버 모두에서 검사합니다.
  압축 해제 내용은 최대 256 MB, 결과 JSON은 최대 45 MB입니다. Supabase Storage 설정의
  **전역 파일 한도를 50 MB**로 설정합니다. Free 한도 안에서 저장하도록 잡았습니다.
  검증 경고는 전체 개수를 유지하고 응답에는 최대 100개를 표시합니다.
- Vercel 설정은 함수 최대 300초입니다. Supabase 게시 임대는 10분이며 만료된 임대로
  늦게 완료한 작업은 활성화할 수 없습니다. 실제 대용량 Excel이 300초 안에 처리되는지는
  실배포에서 측정해야 합니다. 초과하면 별도 처리 worker가 필요하며 20분 실행을 보장하지 않습니다.
- 처리 중 서버가 종료되면 이전 활성본을 유지합니다. 10분 후 다음 게시가 남은 후보를 정리합니다.
  파일 삭제 실패 시 후속 게시를 막아 후보가 계속 누적되지 않게 합니다.
- 응답을 놓친 경우 같은 파일의 재시도는 같은 upload_id를 사용합니다. 이미 완료한 요청은
  저장된 결과를 반환합니다. 페이지를 새로고침하거나 파일을 다시 선택하면 새 작업입니다.
- 오래된 미게시 Excel과 만료 세션은 다음 업로드 URL 발급 시 정리합니다(Excel 24시간).
  활동이 없을 때 자동 정리는 실행되지 않습니다. 원본은 장기 보관용 백업이 아닙니다.
- 세션은 Supabase access token 만료 시 종료됩니다(프로젝트 기본은 보통 1시간).
  자동 갱신은 넣지 않았으며 다시 로그인합니다. 로그아웃은 공유 세션을 삭제합니다.
- PythonAnywhere 기존 데이터는 자동 이동하지 않습니다. 전환 전 전체 백업을 남기고 실제
  Excel 재업로드로 이관합니다. 새 사이트를 검증하기 전에는 기존 서비스를 종료하지 않습니다.

## 검증 명령

```bash
pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npm run test:cloud-sql
python -m scripts.e2e
python -m scripts.e2e_admin
```

SQL 테스트는 PGlite의 PostgreSQL 엔진으로 migration과 원자 게시·중복 재시도·동시 게시
거부·만료 임대·최신 4개·롤백·권한 거부를 실행합니다. Supabase Auth/Storage 호출은
HTTP 경계 테스트이며, 실제 계정·Storage CORS·Vercel 빌드는 실환경 검증이 별도로 필요합니다.

공식 참고: https://vercel.com/docs/frameworks/backend/fastapi,
https://vercel.com/docs/functions/limitations,
https://supabase.com/docs/guides/storage/uploads/standard-uploads
