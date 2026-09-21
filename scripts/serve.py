"""Production entry point for Railway and other container platforms."""
import os
import shutil
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]


def bootstrap_records():
    """Seed an empty persistent volume only when explicitly requested."""
    configured = os.environ.get('LIBRARY_RECORDS')
    if not configured:
        return None
    destination = Path(configured)
    if destination.exists():
        return destination
    if os.environ.get('LIBRARY_BOOTSTRAP_SAMPLE', '').lower() not in {'1', 'true', 'yes'}:
        raise RuntimeError(
            f'LIBRARY_RECORDS 파일이 없습니다: {destination}. '
            '빈 Volume의 첫 배포라면 LIBRARY_BOOTSTRAP_SAMPLE=true를 설정하세요.'
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{destination.name}.bootstrap')
    shutil.copyfile(ROOT / 'data/sample/records.json', temporary)
    os.replace(temporary, destination)
    return destination


def main():
    bootstrap_records()
    port = int(os.environ.get('PORT', '8000'))
    uvicorn.run('backend.app:app', host='0.0.0.0', port=port)


if __name__ == '__main__':
    main()
