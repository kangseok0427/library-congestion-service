"""Real worker termination and fresh-process recovery, using synthetic XLSX only."""
import hashlib
import json
import os
import select
import subprocess
import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import FileProvider, create_app
from backend.library_hours import KST
from library_etl.versions import VersionError, VersionStore
from tests.test_json_refresh import excel
from tests.test_versions import upload


def summary(path):
    store = VersionStore(path)
    client = TestClient(create_app(FileProvider(path),
                       clock=lambda: datetime(2026, 9, 10, 12, tzinfo=KST)))
    stats = client.get('/api/v1/stats?date=2026-09-10')
    forecast = client.get('/api/v1/congestion/today?date=2026-09-17')
    patterns = client.get('/api/v1/patterns')
    assert stats.status_code == forecast.status_code == patterns.status_code == 200
    stats = stats.json()
    stats.pop('updated_at')  # Recovery may replace a file; its mtime can change.
    return dict(versions=store.list_versions(),
                active_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                retained={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in store.directory.glob('*.json')},
                stats=stats, forecast=forecast.json()['hourly'], patterns=patterns.json())


WORKER = '''
import os, sys
from pathlib import Path
import library_etl.versions as module
from library_etl.versions import VersionStore
from scripts.rebuild import rebuild
path, source, stage, target = sys.argv[1:]
path = Path(path)
store = VersionStore(path)
def pause():
    print('paused', flush=True)
    sys.stdin.readline()
if stage == 'rebuild':
    def prepare(records, destination):
        pause()
        return rebuild(records, destination)
    store.rebuild_fn = prepare
elif stage == 'active':
    real_replace = module.os.replace
    def replace(source, destination):
        real_replace(source, destination)
        if Path(destination) == path:
            pause()
    module.os.replace = replace
elif stage == 'prune':
    real_unlink = Path.unlink
    def unlink(candidate, *args, **kwargs):
        real_unlink(candidate, *args, **kwargs)
        if candidate.parent == store.directory and candidate.suffix == '.json':
            pause()
    Path.unlink = unlink
else:
    store._commit = lambda connection: pause() or connection.commit()
if stage == 'rollback_commit':
    store.rollback(target)
else:
    store.upload_excel(source, partial_dates=[])
'''


@pytest.mark.skipif(os.name != 'posix', reason='POSIX SIGKILL/flock verification on Linux')
@pytest.mark.parametrize('stage', ['rebuild', 'active', 'prune', 'commit', 'rollback_commit'])
def test_sigkill_worker_and_fresh_process_api_recovery(tmp_path, stage):
    path = tmp_path / 'records.json'
    store = VersionStore(path)
    ids = [upload(store, tmp_path, value)['version']['id'] for value in range(1, 5)]
    before = summary(path)
    source = excel(tmp_path / 'worker.xlsx', value=20)
    worker = subprocess.Popen([sys.executable, '-c', WORKER, str(path), str(source), stage, ids[0]],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
    try:
        ready, _, _ = select.select([worker.stdout], [], [], 15)
        assert ready, 'Worker never reached the injected failure boundary'
        assert worker.stdout.readline().strip() == 'paused'
        # Both operations contend with a real publisher/rollback worker, not a dummy lock.
        for operation in (lambda: store.upload_excel(source, partial_dates=[]),
                          lambda: store.rollback(ids[0])):
            with pytest.raises(VersionError) as caught:
                operation()
            assert caught.value.code == 'PUBLISH_IN_PROGRESS'
            assert caught.value.status_code == 409
        worker.kill()  # SIGKILL: no finally block or graceful lock cleanup runs.
        worker.wait(timeout=10)
        assert worker.returncode == -9
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=10)
    code = '''
import json, sys
from pathlib import Path
from tests.test_versions_process_recovery import summary
print(json.dumps(summary(Path(sys.argv[1]))))
'''
    fresh = subprocess.run([sys.executable, '-c', code, str(path)],
                           capture_output=True, text=True, timeout=30)
    assert fresh.returncode == 0, fresh.stderr
    assert json.loads(fresh.stdout) == before
    versions = store.list_versions()
    assert versions['active_version_id'] == ids[-1]
    assert sum(v['is_active'] for v in versions['versions']) == 1
    assert {v['id'] + '.json' for v in versions['versions']} == {
        p.name for p in store.directory.glob('*.json')}
    upload(store, tmp_path, 21)  # SIGKILL did not leave a stale lock.
    assert len(store.list_versions()['versions']) == 4
