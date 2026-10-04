## 문제와 수정 결과

독립 재검토에서 재현한 P1(500 반환 후 새 게시 유지·기존 버전 삭제·재시도 중복)과
P2(지속 삭제 실패 중 실제 버전 파일 무제한 증가)를 수정했습니다.
SQLite를 도입하지 않았으며 ADE-48, 관리자 인증·endpoint 구현은 포함하지 않습니다.

## 최종 구현

- OS 잠금 아래 이전/다음 상태를 담은 recovery.json(prepared)을 먼저 저장합니다.
  버전 JSON → 활성 파일 → metadata.json 저장 후 recovery.json의 committed 교체로 최종 확정합니다.
  메타데이터 교체 자체는 확정 지점이 아닙니다.
- 확정 전 연속 fsync/복원 오류는 이전 결정을 유지합니다. 즉시 복원이 불가능하면 추가 게시·롤백·정리를 제한하고
  임시 새 데이터를 조회 결과로 제공하지 않습니다. 장애 해제 후 새 프로세스가 이전 활성본·메타데이터를 복구합니다.
  복구 자체가 두 번 SIGKILL돼도 기록을 재사용하고 기존 정상 4개를 보존합니다.
- 원자 교체 완료 여부를 AtomicWriteError에 보존합니다. 최종 확정 교체 이후 fsync 오류는 성공으로 판정하고
  복구 기록·이전 파일을 유지하며 다음 작업에서 내구성을 재확인합니다. 확정 뒤 정리 오류를 500으로 바꾸지 않습니다.
  해당 예외 구간의 실제 전원 장애 내구성은 미검증이며, 응답 전달 실패에 대한 별도 멱등성 API는 추가하지 않았습니다.
- 잔여 버전 JSON 정리가 끝나지 않으면 새 후보 생성 전에 PUBLISH_FAILED/500으로 추가 게시를 거부합니다.
  정상 조회와 기존 버전 롤백은 단순 삭제 장애 중에도 가능합니다. 장애 해제 후 정리·게시가 다시 가능합니다.
  디렉터리를 읽지 못하면 파일 수를 추측하지 않고 새 후보 생성을 거부합니다.
- 등록 정상 목록은 최대 4개, 정상 정리 완료 후 실제 VersionId JSON도 최대 4개입니다.
  깨끗한 시작 상태에서 후보/삭제 잔여를 포함한 실제 파일은 최대 5개입니다.
  구 구현에서 이미 쌓인 파일이나 외부 파일은 소급 보장하지 않으며, 모두 정리되기 전 추가 파일을 생성하지 않습니다.
  metadata/recovery/lock/staging 파일은 이 개수에서 제외됩니다. 전체 디스크 바이트 상한을 보장하지 않습니다.
- 최초 정상 활성본 등록·바이트/품질/출처 보존·유효 VersionId·공용 전처리·이력 병합·롤백과 캐시/통계/예측 연결을 유지합니다.
  recovery.json만 존재할 때도 기존 JSON 업로드/CLI 우회를 차단하고 Excel refresh는 VersionStore로 위임합니다.
- VersionStore 및 VersionError 호출 인터페이스와 승인된 OpenAPI/fixtures는 변경하지 않았습니다.
  [저장·복구·연결 문서](https://github.com/psy0635-ctrl/library-congestion-service/blob/codex/ADE-45-server-json-versions/docs/server-json-versions.md)를 갱신했습니다.

## 직접 실행한 새 검증

기존 임시 repro.py로 원래 결함을 확인했습니다. 새 회귀는 수정 전 Windows에서 P2 실패,
Linux에서 게시/롤백 P1과 P2 3개 실패를 확인한 뒤 구현했습니다. 과거 231 passed는 재사용하지 않았습니다.
합성 데이터만 저장소 밖 임시 checkout/pytest basetemp/E2E test-results에 생성했습니다.

| 환경 | 관련 pytest | 전체 pytest | scripts.e2e | scripts.e2e_present |
| --- | --- | --- | --- | --- |
| Windows Python 3.12.14 | 53 passed / 21 skipped | 227 passed / 21 skipped | PASS | PASS |
| WSL Ubuntu Python 3.12.3, ext4 | 74 passed | 248 passed | PASS | PASS |

0 failed. Windows skips는 Linux 전용 fsync/SIGKILL 검증입니다. 기존 Starlette/httpx 경고 1개가 남습니다.
관련: `python -B -m pytest tests/test_versions.py tests/test_versions_migration.py tests/test_versions_process_recovery.py tests/test_versions_json.py tests/test_versions_durable.py -q -p no:cacheprovider --basetemp <temp>`.
전체: `python -B -m pytest -q -p no:cacheprovider --basetemp <temp>`, E2E는 각 scripts 모듈 실행.
Windows 임시 checkout은 `C:\Users\user\AppData\Local\Temp\ade45-fix-validation-dt68z0zg\windows`,
Linux는 ext4 `/home/psy/ade45-fix-snrYL6`입니다. 플랫폼별 기존 가상환경을 사용하고 소스 해시를 대조했습니다.

- 실제 metadata 교체 후 fsync + 복원 fsync 연속 장애, 장애 중 읽기/후속 변경 제한, 새 프로세스 이전 상태 복구.
- 실패 게시 후 기존 4개 파일 보존, 통계 108 유지·패턴/예측 복구, 재시도 후 동일 payload 버전 1개.
- 게시/롤백 각각 복구 도중 active/metadata/기록 제거 직전 두 번 SIGKILL 및 반복 복구.
  총 19개 SIGKILL 시나리오·25회 실제 종료(초기 등록 3 + 게시/롤백 10 + 반복 복구 6).
- 최종 decision fsync + 후속 읽기 장애는 성공 판정·정리 보류·후속 변경 제한.
- 지속 삭제 실패 상태의 추가 업로드 4회 거부·파일 5개 유지·조회/롤백 유지, 장애 해제 후 재시작 정리·게시.
- 최초 등록 연속 장애·중복 방지, 4세대 순환·동시 요청·SQLite connect 차단/DB 미생성,
  기존 API 성공 계약과 이용자 API·통계·예측 및 JSON/CLI/refresh 우회 방지.
- `git diff --check` 통과. Windows/Linux 전체 검증을 마지막 코드 변경 후 다시 실행했습니다.

## 승인 필요와 남은 의존성

- 기존 정상 4개 보존과 새 후보 저장을 위해 일시/삭제 장애 중 물리 5개를 허용하고 추가 게시를 거부하는 정책은
  **제안이며 팀장 승인 필요**입니다. 엄격한 물리 4개 상한과 실패 시 기존 4개 보존은 후보 저장 단계에서 양립하지 않습니다.
- 초기 데이터 없음·저장/복구/정리 실패 500·목록 충돌 409의 HTTP 계약 명시는 ADE-43/46 승인이 필요합니다.
  기존 내부 오류 코드와 응답 상태를 유지했고 새 OpenAPI 상태를 임의로 추가하지 않았습니다.
- ADE-44 검증 계약/공통 Excel fixture 대조, ADE-46 관리자 세션/API 연결, ADE-50 운영 통합은 남습니다.
- 실제 PythonAnywhere, 전원 장애, 영구 디스크 손상, 분산 파일시스템은 미검증입니다.
- PR Open/Draft를 유지합니다. 리뷰 댓글·Draft 변경·Linear 변경·force push·병합·배포는 하지 않습니다.

검토 기준 SHA: de75697c3e0837f81dd947bdb9ae6a0eff16c925.
기준 develop: 95ed76e3f925ed450ded869182a904d74f185547.

최초 등록의 최종 확정 후 디렉터리 fsync가 계속 실패하면, 같은 호출의 후속 업로드 전에
복구를 재시도합니다. 기존 초기 등록 기록을 새 게시 의도로 덮어쓰지 않는 추가 회귀도 통과했습니다.
