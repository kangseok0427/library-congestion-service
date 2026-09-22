# ADE-37 Windows Excel 변환·전송 프로그램

## 현재 상태

T12 업로드 API 구현과 계약은 이 저장소의 `develop`에 없습니다. **T12 API 계약 확정 후 실제 endpoint 연결 필요**. 프로그램은 운영 주소를 내장하지 않습니다. `https://ade0033.pythonanywhere.com/`은 배포 호스트로 알려져 있지만 업로드 경로가 아닙니다. 현재 클라이언트의 `POST` + JSON records 배열 + `Authorization: Bearer`는 Mock 검증용 임시 계약입니다. T12에서 endpoint, method, 인증 헤더, 요청 본문, 크기 제한, 성공·오류 응답, 반영 방식을 확인한 뒤 `windows_uploader/upload.py`와 통합 테스트를 맞춰야 합니다. 실제 토큰이나 실제 원본을 넣어 운영 업로드를 시도하지 마세요.

## 개발 PC 실행

Windows 10/11, Python 3.12 이상에서 저장소 루트의 PowerShell을 사용합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m windows_uploader
```

Excel을 선택하고 HTTPS 업로드 주소를 입력합니다. 토큰은 마스킹된 칸에서 Windows Credential Manager에 저장하며 화면으로 다시 읽어오지 않습니다. 주소만 `%LOCALAPPDATA%\YongsanLibraryUploader\config.json`에 저장합니다. 최근 성공 시각은 `state.json`, 로그는 `logs`, 업로드 직전 검증된 JSON 백업은 `backups`, 처리 중 임시 JSON은 `temp`에 보관됩니다. 로컬 최신 정상 결과는 같은 폴더의 `records.json`입니다. 변환·백업·전송 실패 시 최신 정상 결과와 최근 성공 시각은 유지됩니다. 부분 날짜 정책은 기존 `library_etl.refresh`의 보수적 기본값을 사용하며, 완료 그룹을 부분 데이터로 덮어쓰지 않습니다.

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

Mock 테스트는 HTTPS 검사, 토큰 비노출, 성공/실패 응답, 재시도, 전처리 재사용, `OUT_11` 결측, 백업과 상태 보존을 확인합니다. 운영 API 계약과 실제 운영 전송 검증은 포함하지 않습니다.
