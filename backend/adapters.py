"""Independent v1 adapter; replace with T02 records without changing consumers."""
import json
from pathlib import Path


from .domain import DataError, validate_records


def read_records(path):
    try:
        return validate_records(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError('records 파일을 읽을 수 없습니다.') from exc


def from_excel(path, partial_dates=(), *, sheet=None, source_name=None):
    """Compatibility entry point; T02 owns Excel normalization and validation."""
    from library_etl.pipeline import preprocess
    return preprocess(path, partial_dates=partial_dates, sheet=sheet, source_name=source_name)
