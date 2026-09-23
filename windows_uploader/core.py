"""Tk independent conversion, validation, backup and upload orchestration."""
import os
import shutil
from pathlib import Path
from uuid import uuid4

from backend.adapters import read_records
from backend.domain import validate_records
from library_etl import refresh
from .upload import UploadError


def process(source, store, credentials, client, progress=lambda message: None):
    source = Path(source)
    staged = store.temp / f"records-{uuid4().hex}.json"
    try:
        progress("Excel 변환 및 기존 기록 병합 중")
        if store.records.exists():
            shutil.copy2(store.records, staged)
        report = refresh(source, staged)
        progress("변환 결과 검증 중")
        records = validate_records(read_records(staged))
        if not records:
            raise ValueError("빈 records는 업로드할 수 없습니다.")
        progress("로컬 백업 생성 중")
        backup = store.backup(staged)
        progress("HTTPS 업로드 중")
        status = client.send(records, credentials.get())
        os.replace(staged, store.records)
        when = store.save_success()
        store.log(f"업로드 성공: HTTP {status}, records {len(records)}")
        progress("전송 완료")
        return {"report": report, "backup": backup, "last_success": when, "status": status}
    except Exception as exc:
        # Exceptions may contain a token supplied by a backend/transport. Do not log or display them.
        store.log(f"작업 실패: {type(exc).__name__}")
        if isinstance(exc, UploadError):
            raise
        if isinstance(exc, (ValueError, OSError)):
            raise ValueError("변환, 검증 또는 백업에 실패했습니다. Excel과 로그를 확인하세요.") from None
        raise RuntimeError("작업에 실패했습니다. 로그를 확인하세요.") from None
    finally:
        staged.unlink(missing_ok=True)
