## 문제와 수정 결과

실패 게시·롤백 뒤 recovery.json만 외부에서 제거하면 실패한 데이터를 제공하고, 과거 committed 롤백 기록만 다시 넣으면 최근 성공 상태를 되돌리는 문제를 수정했습니다. 외부 파일 제거·부분 복원으로 재현했으며 SIGKILL만으로 발생했다고 주장하지 않습니다. 기존 연속 fsync 장애 P1과 삭제 누적 P2 개선은 유지합니다. SQLite·ADE-48·관리자 인증/endpoint는 포함하지 않습니다.

## 최종 구현

- 형식 2 메타데이터와 복구 기록을 세대·트랜잭션 UUID·불변 의도 digest로 연결합니다. prepared 기록 → 복구 필수 표식 → 후보/활성본/다음 메타데이터 → committed 기록 → 정상 대기 메타데이터 → 기록 제거 → 정리 순서입니다. 표식은 후보·활성 데이터 변경 전에 내구성 저장합니다.
- prepared는 이전 상태, committed는 다음 상태로 복구합니다. 현재 메타데이터 전체와 기록의 ID·세대·의도·phase 조합을 쓰기 전에 검증합니다. committed와 변경 전 표식 조합도 거부합니다. 기록 유실·손상·과거 기록·혼합 상태는 기존 PUBLISH_FAILED/500으로 거부하고 이용자 조회도 검증되지 않은 데이터나 캐시를 제공하지 않습니다. 불일치 거부 중 활성본·버전 파일은 변경/정리하지 않습니다.
- 정상 복구와 반복 복구를 유지합니다. 확정 전 실패는 이전 결정을 유지하며 장애 해제 후 복구합니다. 최종 committed 교체 후 fsync 실패는 성공으로 판정하고 기록을 유지합니다. 프로세스 종료 검증은 전원 장애 내구성 검증이 아닙니다.
- 형식 1은 정상 확정과 기록 유실을 식별할 수 없어 자동 전환하지 않고 거부합니다. 전체 백업·진단과 별도 확인된 전환 절차가 필요합니다. 미등록 정상 records.json 최초 등록은 유지합니다. recovery.json 단독 삭제나 표식 수동 해제를 해결책으로 안내하지 않습니다. 전체 일관된 과거 백업이 함께 복원된 경우는 외부 기준 없이 감지할 수 없습니다.
- 등록 버전 최대 4개, 정리 완료 시 실제 VersionId JSON 최대 4개입니다. 깨끗한 시작 상태에서는 후보/삭제 잔여 포함 최대 5개이며 잔여 정리 실패 시 다음 후보 생성 전에 게시를 거부합니다. 단순 삭제 장애 중 조회·기존 버전 롤백은 유지합니다. 구 구현의 6개 이상 잔여는 추가 증가를 차단합니다. 활성 records.json·metadata/recovery/lock·임시파일은 이 수에서 제외하며 전체 바이트 상한은 보장하지 않습니다. 임시파일 삭제 거부 시 누적은 별도 운영 한계이며 이번 변경에서 관리 기능을 확대하지 않습니다.
- OS 프로세스 잠금·초기본 바이트/품질/출처·VersionId·순환 보관·API/캐시/통계/예측을 유지합니다. VersionStore/VersionError 인터페이스와 OpenAPI/fixtures는 변경하지 않았습니다.

[저장·복구·전환·진단 문서](https://github.com/psy0635-ctrl/library-congestion-service/blob/codex/ADE-45-server-json-versions/docs/server-json-versions.md)

## 직접 실행한 이번 검증

기준 SHA 6277925b084e61d2e283fb39ea2789b54154296f에서 기존 record_loss.py를 재실행해 게시 108→492·기존 삭제·재시도 동일 데이터 2개와 롤백 108→36을 확인했습니다. 기록 유실(게시/롤백)과 과거 committed 기록 재삽입 테스트 3개가 Windows/Linux 기존 코드에서 실패한 뒤 수정했습니다. 수정 후 새 프로세스와 캐시가 있는 이용자 API가 거부하고, 거부 직전 파일 바이트를 보존합니다. 같은 ID/세대의 committed 기록과 변경 전 메타데이터 조합도 실패 테스트로 고정한 뒤 거부했습니다.

| 환경 | 관련 pytest | 전체 pytest | scripts.e2e | scripts.e2e_present |
| --- | --- | --- | --- | --- |
| Windows Python 3.12.14 | 71 passed / 31 skipped | 245 passed / 31 skipped | PASS | PASS |
| WSL Ubuntu Python 3.12.3, ext4 | 102 passed | 276 passed | PASS | PASS |

마지막 코드 변경 후 모두 다시 실행했습니다.기존 보고의 통과 수는 재사용하지 않았습니다. Windows skips는 Linux 전용 fsync/SIGKILL이며 Linux에서 실행했습니다. Starlette/httpx 경고 1개가 남습니다. 합성 데이터만 저장소 밖 임시 checkout과 pytest basetemp/E2E test-results에 생성했습니다.

- Windows: C:\Users\user\AppData\Local\Temp\ade45-identity-validation-txhm8ihb\windows
- Linux: /home/psy/ade45-identity-g63ACV (findmnt: ext4 /dev/sdd)
- 기록 유실·손상·ID/세대/의도/phase 불일치·과거 기록 거부 및 활성/버전 바이트 보존.
- 정상 최초 등록·prepared/committed 복구, 새 표식/대기 저장/기록 제거 경계의 Linux SIGKILL 10개, 게시/롤백 표식·확정 후 대기 저장 연속 장애 4개.
- 기존 활성 ID 변경 후 metadata fsync + 복원 fsync 연속 장애, 복구 재실패·반복 SIGKILL, 실패 게시 기존 4개 보존·통계 복원·재시도 동일 데이터 1개.
- 지속 삭제 실패 추가 게시 제한·실제 파일 5개 상한·조회/롤백 유지·장애 해제 후 정리/게시.
- 4세대 순환·동시 요청·SQLite 차단/미생성·API 계약·캐시·통계·예측·기존 쓰기 경로 회귀.

## 승인 필요와 남은 의존성

- 물리 5개 허용·잔여 정리 실패 시 게시 제한은 팀장 승인 전 제안입니다. 엄격한 물리 4개와 기존 정상 4개 보존은 새 후보 저장 단계에서 양립하지 않습니다.
- 초기 데이터 없음·저장/복구 불일치 500·목록 충돌 409의 계약 명시와 형식 1의 운영 전환은 별도 승인/진단이 필요합니다. 새 OpenAPI 응답이나 전환 API를 임의로 추가하지 않았습니다.
- ADE-44 검증 계약/공통 fixture, ADE-46 관리자 연결, ADE-50 실제 운영 전환·통합 검증은 남습니다.
- PythonAnywhere·전원 장애·영구 디스크 손상·분산 파일시스템은 미검증입니다.
- PR Open/Draft 유지. force push·병합·배포·댓글·Draft 변경·Linear 변경은 하지 않습니다.

검증 통과 후 다음 단계는 팀장 재검토입니다.
