"""Publish a complete validated snapshot once, after all fallible computation."""
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from backend.adapters import read_records
from backend.domain import DataError, validate_records
from backend.service import LibraryService
from .pipeline import preprocess
from .locking import data_lock, storage_directory


@contextmanager
def writer_lock(destination, report):
    with data_lock(destination):
        yield


def merge_records(existing, incoming):
    groups = {(r['date'], r['gate']) for r in incoming}
    complete = {(r['date'], r['gate']) for r in existing if not r['is_partial']}
    if any(r['is_partial'] and (r['date'], r['gate']) in complete for r in incoming):
        raise DataError('완료된 날짜·게이트를 부분 데이터로 덮어쓸 수 없습니다.')
    return validate_records([r for r in existing if (r['date'], r['gate']) not in groups] + incoming)


def refresh(source, destination, *, rebuild_fn=None, **options):
    """Replace incoming date/gate groups; preserve all other history.

    rebuild_fn is a trusted integration/test hook, called only with a staged
    destination. It must produce the same records and may not modify live files.
    """
    if rebuild_fn is None:
        from scripts.rebuild import rebuild
        rebuild_fn = rebuild
    destination = Path(destination).resolve()
    if (storage_directory(destination) / 'metadata.json').exists():
        from .versions import VersionStore
        return VersionStore(destination, rebuild_fn=rebuild_fn).upload_excel(source, **options)
    stage = 'preprocess'
    try:
        incoming, report = preprocess(source, **options)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with writer_lock(destination, report):
            if (storage_directory(destination) / 'metadata.json').exists():
                raise DataError('버전 저장소가 초기화됐습니다. 갱신을 다시 요청하세요.', 'PUBLISH_IN_PROGRESS')
            stage = 'merge'
            existing = read_records(destination) if destination.exists() else []
            merged = merge_records(existing, incoming)
            with tempfile.TemporaryDirectory(prefix=f'.{destination.name}.',
                                             dir=destination.parent,
                                             ignore_cleanup_errors=True) as directory:
                staged = Path(directory) / 'records.json'
                stage = 'stage'
                with staged.open('w', encoding='utf-8') as stream:
                    json.dump(merged, stream, ensure_ascii=False, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                stage = 'rebuild'
                rebuild_fn(read_records(staged), staged)
                prepared = read_records(staged)
                if prepared != merged:
                    raise DataError('rebuild가 원본 records를 변경했습니다.')
                # Validate the actual bytes that API consumers will open, not
                # merely the return value of an injected rebuild implementation.
                service = LibraryService(prepared)
                json.dumps(service.today(), allow_nan=False)
                json.dumps(service.patterns(), allow_nan=False)
                for day in sorted({r['date'] for r in prepared}):
                    json.dumps(service.stats(day), allow_nan=False)
                report.update(contract_version='v1.1', record_count=len(prepared),
                              integration_status='rebuilt')
                json.dumps(report, allow_nan=False)
                stage = 'replace'
                os.replace(staged, destination)
        return report
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f'갱신 실패 ({stage}): {type(exc).__name__}: {exc}') from exc
