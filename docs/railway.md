# Railway PaaS 배포·운영 인수인계

> 현재 수요일 MVP 배포 대상은 PythonAnywhere 무료 계정입니다. 이 문서는 향후 유료
> PaaS 전환을 다시 검토할 때를 위해 보존하며 현재 배포 절차로 사용하지 않습니다.

## 확정 구조

`도서관 PC Excel 변환기 → 인증 업로드 API → Railway Volume의 records.json → FastAPI → 이용자 웹`

- 개인 PC와 Cloudflare Tunnel은 운영 경로에서 제외합니다.
- Railway 서비스는 GitHub 저장소의 배포 브랜치에서 자동 배포합니다.
- 운영 데이터는 컨테이너 파일시스템이 아니라 Railway Volume에 둡니다.
- 업로드 API와 PC 전송 프로그램은 T12·T13에서 구현합니다.

## Railway 최초 설정

1. Railway에서 GitHub 저장소 `kangseok0427/library-congestion-service`를 연결합니다.
2. 배포 브랜치를 검증된 `develop` 또는 별도 release 브랜치로 지정합니다.
3. 서비스에 Volume을 추가하고 `/data`에 마운트합니다.
4. Variables에 `LIBRARY_RECORDS=/data/records.json`을 설정합니다.
5. 빈 Volume의 최초 배포에서만 `LIBRARY_BOOTSTRAP_SAMPLE=true`를 설정합니다.
6. `/api/v1/health`가 200을 반환하고 페이지에 샘플 안내가 표시되는지 확인합니다.
7. 첫 배포 성공 후 `LIBRARY_BOOTSTRAP_SAMPLE=false`로 변경합니다.

`railway.toml`이 Nixpacks 빌드, `python -m scripts.serve` 실행, 헬스체크,
실패 시 최대 3회 재시작을 지정합니다. Railway가 제공하는 `PORT`를 프로그램이
읽으므로 포트 숫자를 직접 고정하지 않습니다.

## 사용자 도메인

1. Railway 서비스의 Networking에서 `library.gaon0033.org`를 추가합니다.
2. Railway가 보여주는 DNS 대상값을 Cloudflare DNS의 CNAME으로 등록합니다.
3. Cloudflare 프록시 사용 여부는 Railway 안내에 맞추고 HTTPS 발급 완료를 기다립니다.
4. `https://library.gaon0033.org/api/v1/health`와 메인 페이지를 확인합니다.

DNS 대상값은 서비스 생성 후 Railway가 발급하므로 저장소에 고정해 두지 않습니다.

## 보안정보

- Railway 계정, 결제수단, API 토큰과 운영 환경변수는 서비스 소유자가 관리합니다.
- `.env`, Railway 토큰, 업로드 API 토큰은 Git에 커밋하지 않습니다.
- 공개 저장소에는 변수 이름과 예시만 둡니다.
- T12의 업로드 토큰은 Railway Variables와 도서관 PC의 보호된 설정에 각각 저장합니다.

## 배포와 복구

- 새 커밋 배포 전 자동 테스트를 통과시킵니다.
- 배포 후 헬스체크와 메인 페이지를 확인합니다.
- 애플리케이션 배포와 Volume 데이터는 분리되므로 코드 재배포가 records.json을 지우지 않습니다.
- 장애 시 Railway Deployments에서 직전 정상 배포로 Rollback합니다.
- 데이터 이상이면 T12에서 만들 백업본을 검증한 뒤 records.json으로 원자 교체합니다.

## 소유·비용 인수인계

- Railway 서비스와 결제: 프로젝트 팀장 계정
- GitHub 저장소와 배포 브랜치: 프로젝트 팀장 계정
- 도메인과 Cloudflare DNS: 프로젝트 팀장 계정
- 도서관 PC 설치·운영: 지정된 도서관 담당자
- 업로드 토큰 교체 및 폐기: 프로젝트 팀장 또는 인수받은 운영 책임자

Hobby 요금제 등 실제 요금은 계약 시점의 Railway 화면에서 다시 확인하고,
월 사용 한도와 결제 알림을 서비스 소유 계정에 설정합니다.
