# ADE-37 Windows Excel 변환·전송 프로그램

## 현재 계약

ADE-35 업로드 API와 Windows 프로그램은 다음 계약을 사용합니다.

- endpoint: `https://ade0033.pythonanywhere.com/api/v1/admin/records`
- method: 기존 전체 기록은 `GET`, 검증된 갱신본은 `POST`
- 인증: `Authorization: Bearer <ADMIN_UPLOAD_TOKEN>`
- body: 공통 스키마의 전체 records JSON 배열
- 최대 크기: 50MB
- 성공: HTTP 200과 `accepted`, `record_count`, `changed`, `previous_backup`, `uploaded_at`

프로그램은 매 작업 시작 시 서버의 최신 전체 기록을 먼저 인증 다운로드한 뒤 선택한
Excel의 날짜·게이트만 교체합니다. 따라서 PC 첫 실행이나 다른 PC에서 갱신한 뒤에도
부분 Excel만으로 서버의 과거 기록을 잃지 않습니다. 서버는 인증과 전체 스키마 검증을 통과한 경우에만 운영 파일을 원자 교체합니다. 기존
파일은 교체 전에 백업하며, 동일 스냅샷 재전송은 성공으로 응답하되 파일과 백업을 다시
만들지 않습니다. Mock 모드는 가짜 전송 함수를 주입한 자동 테스트에서만 사용합니다.
서버 전체 기록의 `source_file`이 `synthetic`인 최초 샘플 상태는 병합하지 않고, 첫 실제
Excel 변환본으로 교체합니다. 서버에는 교체 전 샘플이 백업으로 남습니다.
대용량 전체 기록의 다운로드·업로드는 각 요청당 최대 20분까지 기다립니다.

## 개발 PC 실행

Windows 10/11, Python 3.12 이상에서 저장소 루트의 PowerShell을 사용합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-uploader.txt
.\.venv\Scripts\python.exe -m windows_uploader
```

승인된 업로드 주소는 기본으로 입력됩니다. 토큰은 마스킹된 칸에서 Windows Credential Manager에 저장하며 화면으로 다시 읽어오지 않습니다. 주소만 `%LOCALAPPDATA%\YongsanLibraryUploader\config.json`에 저장합니다. 최근 성공 시각은 `state.json`, 로그는 `logs`, 업로드 직전 검증된 JSON 백업은 `backups`, 처리 중 임시 JSON은 `temp`에 보관됩니다. 로컬 최신 정상 결과는 같은 폴더의 `records.json`입니다. 백업은 최근 20개만 보관합니다. 변환·백업·전송 실패 시 최신 정상 결과와 최근 성공 시각은 유지됩니다. 부분 날짜 정책은 기존 `library_etl.refresh`의 보수적 기본값을 사용하며, 완료 그룹을 부분 데이터로 덮어쓰지 않습니다.

## Python 없는 PC용 빌드

개발 PC의 PowerShell에서 다음을 실행합니다.

```powershell
.\scripts\build-windows-uploader.ps1
```

산출물은 `dist\YongsanLibraryUploader.exe`이며 콘솔 없는 단일 실행파일을 목표로 합니다. 설치 대상 PC에는 Python이나 관리자 권한이 필요하지 않습니다. 실제 배포 전에는 Python 없는 Windows 10/11 PC에서 실행, 한글 경로 Excel 선택, Credential Manager 저장, 폴더 열기, 실패 복구, 기관 백신과 SmartScreen 판정을 수동 확인해야 합니다. 백신 및 SmartScreen 통과는 자동으로 보장되지 않습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

자동 테스트는 HTTPS 검사, 토큰 비노출, 성공/실패 응답, 재시도, 전처리 재사용,
`OUT_11` 결측, 서버 인증·크기 제한·원자 교체·백업·멱등 재전송을 확인합니다. 실제
PythonAnywhere 전송은 운영 토큰을 설정한 뒤 Windows PC에서 별도로 검증합니다.
