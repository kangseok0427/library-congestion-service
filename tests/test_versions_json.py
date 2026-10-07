"""JSON authority, bootstrap and commit boundaries with disposable records."""
import json
import os
import re
import subprocess
import sys
from datetime import date

import pytest

from library_etl.versions import VersionError, VersionStore
from scripts.rebuild import rebuild
from scripts.sample import generate
from tests.test_versions import persisted, upload


def baseline(tmp_path):
    path = tmp_path / 'records.json'
    records = generate(end=date(2026, 9, 10), days=2)
    records[0]['quality_note'] = 'synthetic preserved quality'
    rebuild(records, path)
    return VersionStore(path)


def test_register_initial_snapshot_once_without_sqlite(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('SQLite connection'))
    store = baseline(tmp_path)
    original = store.path.read_bytes()
    first = store.list_versions()
    assert re.fullmatch(r'[0-9]{8}T[0-9]{6}\+0900', first['active_version_id'])
    assert len(first['versions']) == 1
    assert first['versions'][0]['is_active']
    for _ in range(3):
        assert VersionStore(store.path).list_versions() == first
    assert store.path.read_bytes() == original
    assert (store.directory / (first['active_version_id'] + '.json')).read_bytes() == original
    upload(store, tmp_path)
    store.rollback(first['active_version_id'])
    assert VersionStore(store.path).list_versions()['active_version_id'] == first['active_version_id']
    assert (store.directory / 'metadata.json').exists()
    assert not list(tmp_path.rglob('*.sqlite*'))
    assert not list(tmp_path.rglob('*.db'))


@pytest.mark.parametrize('payload', [None, b'broken', b'[]'])
def test_no_normal_active_snapshot_is_not_success(tmp_path, payload):
    store = VersionStore(tmp_path / 'records.json')
    if payload is not None:
        store.path.write_bytes(payload)
    with pytest.raises(VersionError) as caught:
        store.list_versions()
    assert caught.value.code == 'PUBLISH_FAILED'
    assert caught.value.status_code == 500
    assert (store.path.read_bytes() if store.path.exists() else None) == payload
    assert not [p for p in store.directory.glob('*.json') if p.name != 'metadata.json']


def test_parallel_initialization_registers_one_version(tmp_path):
    store = baseline(tmp_path)
    code = '''
import json, sys, time
from library_etl.versions import VersionStore
from library_etl.versions import VersionError
for _ in range(100):
    try:
        print(json.dumps(VersionStore(sys.argv[1]).list_versions()))
        break
    except VersionError as exc:
        if exc.code != 'PUBLISH_IN_PROGRESS':
            raise
        time.sleep(.02)
else:
    raise RuntimeError('initialization timed out')
'''
    workers = [subprocess.Popen([sys.executable, '-c', code, str(store.path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for _ in range(4)]
    states = []
    for worker in workers:
        out, err = worker.communicate(timeout=30)
        assert worker.returncode == 0, err
        states.append(json.loads(out))
    assert all(s == states[0] for s in states)
    assert len(store.list_versions()['versions']) == 1


def test_rollback_oldest_then_upload_prunes_only_after_commit(tmp_path):
    store = baseline(tmp_path)
    initial = store.list_versions()['active_version_id']
    for value in range(3):
        upload(store, tmp_path, value)
    store.rollback(initial)
    new = upload(store, tmp_path, 20)['version']['id']
    assert len(store.list_versions()['versions']) == 4
    assert store.list_versions()['active_version_id'] == new
    assert not (store.directory / (initial + '.json')).exists()
    assert store.path.read_bytes() == (store.directory / (new + '.json')).read_bytes()


def test_metadata_replace_exception_preserves_old_commit(tmp_path, monkeypatch):
    import library_etl.versions as module
    store = baseline(tmp_path)
    before = persisted(store)
    real = module.os.replace
    failed = False
    def replace(source, destination):
        nonlocal failed
        real(source, destination)
        if destination == store.directory / 'metadata.json' and not failed:
            failed = True
            raise OSError('failure after metadata rename')
    with monkeypatch.context() as patch:
        patch.setattr(module.os, 'replace', replace)
        with pytest.raises(VersionError):
            upload(store, tmp_path)
    assert persisted(store) == before


def test_prune_error_keeps_new_commit_and_retries(tmp_path, monkeypatch):
    store = baseline(tmp_path)
    initial = store.list_versions()['active_version_id']
    for value in range(3):
        upload(store, tmp_path, value)
    oldest = store.directory / (initial + '.json')
    original = type(oldest).unlink
    def unlink(path, *args, **kwargs):
        if path == oldest:
            raise PermissionError('deferred garbage collection')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(type(oldest), 'unlink', unlink)
        result = upload(store, tmp_path, 20)
        versions = store.list_versions()
        assert versions['active_version_id'] == result['version']['id']
        assert len(versions['versions']) == 4
        assert oldest.exists()  # Unreferenced garbage, never returned as a normal version.
    assert VersionStore(store.path).list_versions() == versions
    assert not oldest.exists()


@pytest.mark.parametrize('stage', ['rebuild', 'version', 'metadata'])
def test_initial_registration_exception_keeps_original_bytes(tmp_path, monkeypatch, stage):
    import library_etl.versions as module
    store = baseline(tmp_path)
    original = store.path.read_bytes()
    replace = module.os.replace
    def broken(source, destination):
        if ((stage == 'metadata' and destination == store.metadata)
                or (stage == 'version' and destination.parent == store.directory
                    and destination.suffix == '.json')):
            raise OSError('bootstrap replace')
        return replace(source, destination)
    with monkeypatch.context() as patch:
        if stage == 'rebuild':
            patch.setattr(store, 'rebuild_fn', lambda *a: (_ for _ in ()).throw(OSError('bootstrap rebuild')))
        else:
            patch.setattr(module.os, 'replace', broken)
        with pytest.raises(VersionError):
            store.list_versions()
    assert store.path.read_bytes() == original
    assert len(VersionStore(store.path).list_versions()['versions']) == 1


@pytest.mark.skipif(os.name != 'posix', reason='Linux SIGKILL bootstrap boundaries')
@pytest.mark.parametrize('stage', ['rebuild', 'version', 'metadata'])
def test_sigkill_initial_registration_preserves_original_and_registers_once(tmp_path, stage):
    import select
    store = baseline(tmp_path)
    original = store.path.read_bytes()
    code = '''
import sys
from pathlib import Path
import library_etl.versions as module
from scripts.rebuild import rebuild
store = module.VersionStore(sys.argv[1])
stage = sys.argv[2]
def pause():
    print('paused', flush=True)
    sys.stdin.readline()
if stage == 'rebuild':
    def prepare(rows, destination):
        pause()
        return rebuild(rows, destination)
    store.rebuild_fn = prepare
else:
    real = module.os.replace
    def replace(source, destination):
        real(source, destination)
        dest = Path(destination)
        if ((stage == 'metadata' and dest == store.metadata)
                or (stage == 'version' and dest.parent == store.directory and module.VERSION_ID.fullmatch(dest.stem))):
            pause()
    module.os.replace = replace
store.list_versions()
'''
    worker = subprocess.Popen([sys.executable, '-c', code, str(store.path), stage],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
    try:
        ready, _, _ = select.select([worker.stdout], [], [], 15)
        assert ready and worker.stdout.readline().strip() == 'paused'
        worker.kill()
        worker.wait(timeout=10)
        assert worker.returncode == -9
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=10)
    fresh = subprocess.run([sys.executable, '-c',
                            'import json,sys; from library_etl.versions import VersionStore; '
                            'print(json.dumps(VersionStore(sys.argv[1]).list_versions()))',
                            str(store.path)], capture_output=True, text=True, timeout=30)
    assert fresh.returncode == 0, fresh.stderr
    result = json.loads(fresh.stdout)
    assert len(result['versions']) == 1
    assert VersionStore(store.path).list_versions() == result
    assert store.path.read_bytes() == original


def test_first_upload_crash_without_baseline_recovers_absence(tmp_path):
    from tests.test_json_refresh import excel
    store = VersionStore(tmp_path / 'records.json')
    source = excel(tmp_path / 'crash.xlsx')
    code = '''
import os,sys
from library_etl.versions import VersionStore
class Crash(VersionStore):
    def _commit(self, state):
        os._exit(19)
Crash(sys.argv[1]).upload_excel(sys.argv[2], partial_dates=[])
'''
    fresh = subprocess.run([sys.executable, '-c', code, str(store.path), str(source)], timeout=30)
    assert fresh.returncode == 19
    with pytest.raises(VersionError):
        VersionStore(store.path).list_versions()
    assert not store.path.exists()
    assert list(store.directory.glob('*.json')) == [store.metadata]
    assert upload(store, tmp_path)['status'] == 'published'
