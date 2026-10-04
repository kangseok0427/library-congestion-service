"""Persistent decision and bounded orphan regression tests, synthetic data only."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from library_etl.versions import VERSION_ID, VersionError, VersionStore
from tests.test_versions import upload


def version_files(store):
    return {p.name: p.read_bytes() for p in store.directory.glob('*.json')
            if VERSION_ID.fullmatch(p.stem)}


def fresh(path):
    code = "from library_etl.versions import VersionStore; import json,sys; print(json.dumps(VersionStore(sys.argv[1]).list_versions()))"
    result = subprocess.run([sys.executable, '-B', '-c', code, str(path)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('operation', ['publish', 'rollback'])
def test_metadata_replace_and_recovery_fsync_failures(tmp_path, monkeypatch, operation):
    store = VersionStore(tmp_path / 'records.json')
    ids = [upload(store, tmp_path, n)['version']['id'] for n in range(1, 5)]
    before = store.list_versions()
    payload = store.path.read_bytes()
    files = version_files(store)
    from tests.test_versions_process_recovery import summary
    before_api = summary(store.path)
    real_replace, real_fsync = os.replace, os.fsync
    replaced = False
    def replace(source, destination):
        nonlocal replaced
        real_replace(source, destination)
        if Path(destination) == store.metadata:
            replaced = True
            if os.name == 'nt':
                raise OSError('after metadata rename (Windows has no directory fsync)')
    def fsync(fd):
        if replaced:
            raise OSError('persistent fsync fault after metadata replacement')
        return real_fsync(fd)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', replace)
        fault.setattr(os, 'fsync', fsync)
        with pytest.raises(VersionError) as caught:
            if operation == 'publish':
                upload(store, tmp_path, 20)
            else:
                store.rollback(ids[0])
        assert caught.value.code == 'PUBLISH_FAILED'
        assert caught.value.status_code == 500
        assert all((store.directory / name).read_bytes() == data for name, data in files.items())
        # Neither reads nor another mutation may accept provisional new data.
        with pytest.raises(Exception):
            store.read_snapshot()
        with pytest.raises(VersionError):
            upload(store, tmp_path, 21)
        assert len(version_files(store)) <= 5
    assert fresh(store.path) == before
    assert store.path.read_bytes() == payload
    assert version_files(store) == files
    assert summary(store.path) == before_api
    assert before_api['stats']['hourly_total_in'] == 108
    if operation == 'publish':
        result = upload(store, tmp_path, 20)
        assert fresh(store.path)['active_version_id'] == result['version']['id']
        active_bytes = store.path.read_bytes()
        assert sum(data == active_bytes for data in version_files(store).values()) == 1


def test_persistent_delete_fault_blocks_growth_and_recovers(tmp_path, monkeypatch):
    store = VersionStore(tmp_path / 'records.json')
    for n in range(1, 5):
        upload(store, tmp_path, n)
    real_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path.parent == store.directory and VERSION_ID.fullmatch(path.stem):
            raise PermissionError('persistent deletion fault')
        return real_unlink(path, *args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(Path, 'unlink', unlink)
        upload(store, tmp_path, 20)
        before = store.list_versions()
        payload = store.path.read_bytes()
        files = version_files(store)
        assert len(files) == 5
        for n in range(21, 25):
            with pytest.raises(VersionError) as caught:
                upload(store, tmp_path, n)
            assert caught.value.code == 'PUBLISH_FAILED'
            assert version_files(store) == files
            assert store.path.read_bytes() == payload
            assert VersionStore(store.path).list_versions() == before
        target = before['versions'][-1]['id']
        store.rollback(target)
        assert len(version_files(store)) == 5
        assert store.read_snapshot()[0] == files[target + '.json']
    assert fresh(store.path)['active_version_id'] == target
    assert len(version_files(store)) == 4
    upload(store, tmp_path, 25)
    assert len(version_files(store)) == 4


@pytest.mark.skipif(os.name != 'posix', reason='Linux directory fsync and SIGKILL')
@pytest.mark.parametrize('operation', ['publish', 'rollback'])
@pytest.mark.parametrize('boundary', ['active', 'metadata', 'remove_record'])
def test_recovery_sigkill_is_repeatable(tmp_path, monkeypatch, operation, boundary):
    import select
    store = VersionStore(tmp_path / 'records.json')
    ids = [upload(store, tmp_path, n)['version']['id'] for n in range(1, 5)]
    before = fresh(store.path)
    original = store.path.read_bytes()
    files = version_files(store)
    real_replace, real_fsync = os.replace, os.fsync
    replaced = False
    def replace(source, destination):
        nonlocal replaced
        real_replace(source, destination)
        if Path(destination) == store.metadata:
            replaced = True
    def fsync(fd):
        if replaced:
            raise OSError('persistent fsync failure')
        return real_fsync(fd)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', replace)
        fault.setattr(os, 'fsync', fsync)
        with pytest.raises(VersionError):
            if operation == 'publish':
                upload(store, tmp_path, 20)
            else:
                store.rollback(ids[0])
    assert json.loads(store.recovery.read_bytes())['phase'] == 'prepared'
    code = """
import sys, os
from pathlib import Path
from library_etl.versions import VersionStore
store = VersionStore(sys.argv[1])
boundary = sys.argv[2]
def pause():
    print('paused', flush=True)
    sys.stdin.readline()
real_replace, real_unlink = os.replace, Path.unlink
def replace(source, destination):
    real_replace(source, destination)
    if ((boundary == 'active' and Path(destination) == store.path)
            or (boundary == 'metadata' and Path(destination) == store.metadata)):
        pause()
def unlink(path, *a, **kw):
    if boundary == 'remove_record' and path == store.recovery:
        pause()
    return real_unlink(path, *a, **kw)
os.replace, Path.unlink = replace, unlink
store.list_versions()
"""
    for _ in range(2):
        worker = subprocess.Popen([sys.executable, '-B', '-c', code, str(store.path), boundary],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
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
        assert store.recovery.exists()
        assert all((store.directory / name).read_bytes() == data for name, data in files.items())
    assert fresh(store.path) == before
    assert store.path.read_bytes() == original
    assert version_files(store) == files
    assert not store.recovery.exists()
    assert fresh(store.path) == before


@pytest.mark.skipif(os.name != 'posix', reason='Linux directory fsync')
def test_final_decision_fsync_failure_is_success_without_pruning(tmp_path, monkeypatch):
    store = VersionStore(tmp_path / 'records.json')
    for n in range(1, 5):
        upload(store, tmp_path, n)
    files = version_files(store)
    real_replace, real_fsync = os.replace, os.fsync
    decided = False
    real_read = Path.read_bytes
    def read(path):
        if decided and path == store.recovery:
            raise OSError("decision temporarily unreadable")
        return real_read(path)
    def replace(source, destination):
        nonlocal decided
        real_replace(source, destination)
        if (Path(destination) == store.recovery
                and json.loads(real_read(store.recovery))['phase'] == 'committed'):
            decided = True
    def fsync(fd):
        if decided:
            raise OSError('final decision directory fsync failed')
        return real_fsync(fd)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', replace)
        fault.setattr(os, 'fsync', fsync)
        fault.setattr(Path, 'read_bytes', read)
        result = upload(store, tmp_path, 20)
        assert result['status'] == 'published'
        assert json.loads(real_read(store.recovery))['phase'] == 'committed'
        assert len(version_files(store)) == 5
        assert all((store.directory / name).read_bytes() == data for name, data in files.items())
        with pytest.raises(VersionError):
            upload(store, tmp_path, 21)
        assert len(version_files(store)) == 5
    assert fresh(store.path)['active_version_id'] == result['version']['id']
    assert len(version_files(store)) == 4
    assert not store.recovery.exists()


def test_recovery_record_corruption_rejects_without_deleting_versions(tmp_path):
    store = VersionStore(tmp_path / 'records.json')
    upload(store, tmp_path, 1)
    files = version_files(store)
    payload = store.path.read_bytes()
    store.recovery.write_bytes(b'{"phase":"committed"}')
    with pytest.raises(VersionError):
        store.list_versions()
    with pytest.raises(Exception):
        store.read_snapshot()
    assert version_files(store) == files
    assert store.path.read_bytes() == payload


def test_failed_initial_registration_retries_without_changing_original(tmp_path, monkeypatch):
    from tests.test_versions_json import baseline
    store = baseline(tmp_path)
    original = store.path.read_bytes()
    real_replace, real_fsync = os.replace, os.fsync
    replaced = False
    real_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if os.name == "nt" and replaced and path == store.metadata:
            raise PermissionError("persistent bootstrap metadata restoration fault")
        return real_unlink(path, *args, **kwargs)
    def replace(source, destination):
        nonlocal replaced
        real_replace(source, destination)
        if Path(destination) == store.metadata:
            replaced = True
            if os.name == 'nt':
                raise OSError('failure after bootstrap metadata replacement')
    def fsync(fd):
        if replaced:
            raise OSError('persistent bootstrap/recovery fsync fault')
        return real_fsync(fd)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', replace)
        fault.setattr(os, 'fsync', fsync)
        fault.setattr(Path, 'unlink', unlink)
        for _ in range(3):
            with pytest.raises(VersionError):
                store.list_versions()
            assert store.path.read_bytes() == original
            assert len(version_files(store)) <= 1
        assert store.recovery.exists()
    assert len(fresh(store.path)['versions']) == 1
    assert store.path.read_bytes() == original
    assert len(version_files(store)) == 1
    assert not store.recovery.exists()


def test_recovery_without_metadata_blocks_legacy_writer_bypass(tmp_path):
    from backend.admin_upload import RecordsPublisher, UploadAPIError
    from library_etl import refresh
    from tests.test_versions_json import baseline
    from tests.test_json_refresh import excel
    store = baseline(tmp_path)
    # Represents a pending initial registration; no metadata has been committed.
    store.directory.mkdir(exist_ok=True)
    store.recovery.write_bytes(b'{}')
    original = store.path.read_bytes()
    with pytest.raises(UploadAPIError) as caught:
        RecordsPublisher(store.path).publish(json.loads(original))
    assert caught.value.status_code == 409
    with pytest.raises(VersionError):
        refresh(excel(tmp_path / 'fixture.xlsx'), store.path, partial_dates=[])
    source = tmp_path / 'source.json'
    source.write_bytes(original)
    result = subprocess.run([sys.executable, '-B', '-m', 'scripts.rebuild', str(source),
                             '--output', str(store.path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert store.path.read_bytes() == original
    assert store.recovery.read_bytes() == b'{}'
    assert not store.metadata.exists()


def test_unreadable_directory_rejects_new_candidate(tmp_path, monkeypatch):
    store = VersionStore(tmp_path / 'records.json')
    for n in range(1, 5):
        upload(store, tmp_path, n)
    files = version_files(store)
    payload = store.path.read_bytes()
    real_scandir = os.scandir
    def scandir(path):
        if Path(path) == store.directory:
            raise PermissionError('cannot enumerate version files')
        return real_scandir(path)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'scandir', scandir)
        with pytest.raises(VersionError):
            upload(store, tmp_path, 20)
        assert store.path.read_bytes() == payload
    assert version_files(store) == files


@pytest.mark.skipif(os.name != 'posix', reason='Linux directory fsync')
def test_pending_initial_decision_is_recovered_before_same_call_upload(tmp_path, monkeypatch):
    import stat
    from tests.test_versions_json import baseline
    store = baseline(tmp_path)
    original = store.path.read_bytes()
    real_replace, real_fsync = os.replace, os.fsync
    decided = False
    def replace(source, destination):
        nonlocal decided
        real_replace(source, destination)
        if (Path(destination) == store.recovery
                and json.loads(store.recovery.read_bytes())['phase'] == 'committed'):
            decided = True
    def fsync(fd):
        if decided and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('persistent directory fsync after initial decision')
        return real_fsync(fd)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', replace)
        fault.setattr(os, 'fsync', fsync)
        with pytest.raises(VersionError):
            upload(store, tmp_path, 20)
        record = json.loads(store.recovery.read_bytes())
        assert record['phase'] == 'committed'
        assert record['initialize'] is True
        assert len(version_files(store)) == 1
        assert store.path.read_bytes() == original
    assert len(fresh(store.path)['versions']) == 1
    upload(store, tmp_path, 20)
    assert len(version_files(store)) == 2
