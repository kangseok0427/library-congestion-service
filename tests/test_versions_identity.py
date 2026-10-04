"""External journal loss/partial restore and transaction identity regressions."""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import FileProvider, create_app
from backend.domain import DataError
from backend.library_hours import KST
from library_etl.versions import VersionError, VersionStore
from tests.test_versions import upload
from tests.test_versions_durable import version_files


def initialized(tmp_path):
    store = VersionStore(tmp_path / 'records.json')
    ids = [upload(store, tmp_path, n)['version']['id'] for n in range(1, 5)]
    return store, ids


def snapshot(store):
    return (store.path.read_bytes(), store.metadata.read_bytes(),
            store.recovery.read_bytes() if store.recovery.exists() else None,
            version_files(store))


def visitor(store):
    return TestClient(create_app(FileProvider(store.path),
                      clock=lambda: datetime(2026, 9, 10, 12, tzinfo=KST)))


def assert_rejected(store, tmp_path, target, client=None):
    before = snapshot(store)
    code = """
import json,sys
from library_etl.versions import VersionStore,VersionError
try:
    VersionStore(sys.argv[1]).list_versions()
except VersionError as exc:
    print(json.dumps({'code':exc.code, 'status':exc.status_code}))
else:
    print(json.dumps({'accepted':True}))
"""
    child = subprocess.run([sys.executable, '-B', '-c', code, str(store.path)],
                           capture_output=True, text=True, check=True)
    assert json.loads(child.stdout) == {'code': 'PUBLISH_FAILED', 'status': 500}
    for operation in (store.list_versions, lambda: store.rollback(target),
                      lambda: upload(store, tmp_path, 21)):
        with pytest.raises(VersionError) as caught:
            operation()
        assert caught.value.code == 'PUBLISH_FAILED' and caught.value.status_code == 500
        assert snapshot(store) == before
    with pytest.raises(DataError):
        store.read_snapshot()
    assert snapshot(store) == before
    client = client or visitor(store)
    for endpoint in ('/api/v1/stats?date=2026-09-10', '/api/v1/patterns',
                     '/api/v1/congestion/today?date=2026-09-17'):
        assert client.get(endpoint).status_code != 200
    assert snapshot(store) == before


def failed_transition(store, tmp_path, ids, monkeypatch, operation):
    old_active = store.list_versions()['active_version_id']
    replace0, fsync0 = os.replace, os.fsync
    hit = False
    def replace(source, destination):
        nonlocal hit
        replace0(source, destination)
        if (Path(destination) == store.metadata
                and json.loads(store.metadata.read_bytes())['active_version_id'] != old_active):
            hit = True
            if os.name == 'nt':
                raise OSError('after new metadata replacement')
    def fsync(fd):
        if hit:
            raise OSError('continuous fsync failure including restoration')
        return fsync0(fd)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', replace)
        fault.setattr(os, 'fsync', fsync)
        with pytest.raises(VersionError) as caught:
            if operation == 'publish':
                upload(store, tmp_path, 20)
            else:
                store.rollback(ids[0])
        assert hit and caught.value.code == 'PUBLISH_FAILED'
    assert store.recovery.exists()


@pytest.mark.parametrize('operation', ['publish', 'rollback'])
def test_external_missing_recovery_record_rejects_failed_transition(tmp_path, monkeypatch, operation):
    store, ids = initialized(tmp_path)
    old_files = version_files(store)
    client = visitor(store)
    assert client.get('/api/v1/stats?date=2026-09-10').json()['hourly_total_in'] == 108
    failed_transition(store, tmp_path, ids, monkeypatch, operation)
    # Explicit external deletion; not attributed to SIGKILL.
    store.recovery.unlink()
    assert_rejected(store, tmp_path, ids[0], client)
    assert all((store.directory / name).read_bytes() == data for name, data in old_files.items())


def held_committed(store, target, monkeypatch):
    unlink0 = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == store.recovery:
            raise PermissionError('deferred recovery record removal')
        return unlink0(path, *args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(Path, 'unlink', unlink)
        assert store.rollback(target)['status'] == 'rolled_back'
    return json.loads(store.recovery.read_bytes())


def test_external_stale_committed_rollback_record_is_not_replayed(tmp_path, monkeypatch):
    store, ids = initialized(tmp_path)
    stale = held_committed(store, ids[0], monkeypatch)
    store.list_versions()  # Finish the old operation normally.
    store.rollback(ids[-1])
    original = store.path.read_bytes()
    old_files = version_files(store)
    client = visitor(store)
    assert client.get('/api/v1/stats?date=2026-09-10').json()['hourly_total_in'] == 108
    store.recovery.write_text(json.dumps(stale), encoding='utf-8')
    assert_rejected(store, tmp_path, ids[0], client)
    assert store.path.read_bytes() == original and version_files(store) == old_files


@pytest.mark.parametrize('damage', ['record_id', 'record_generation', 'metadata_id',
                                  'metadata_generation', 'record_state', 'broken_json'])
def test_identity_mismatch_rejects_without_writes(tmp_path, monkeypatch, damage):
    store, ids = initialized(tmp_path)
    record = held_committed(store, ids[0], monkeypatch)
    metadata = json.loads(store.metadata.read_bytes())
    if damage == 'record_id':
        record['transaction_id'] = 'f' * 32
    elif damage == 'record_generation':
        record['generation'] += 1
    elif damage == 'metadata_id':
        metadata['transaction_id'] = 'f' * 32
    elif damage == 'metadata_generation':
        metadata['generation'] += 1
    elif damage == 'record_state':
        record['next']['active_version_id'] = ids[1]
    if damage == 'broken_json':
        store.recovery.write_bytes(b'broken')
    else:
        store.recovery.write_text(json.dumps(record), encoding='utf-8')
    store.metadata.write_text(json.dumps(metadata), encoding='utf-8')
    assert_rejected(store, tmp_path, ids[0])


def test_format_one_without_decision_information_is_not_guessed(tmp_path):
    store, ids = initialized(tmp_path)
    state = json.loads(store.metadata.read_bytes())
    legacy = {key: state[key] for key in ('active_version_id', 'versions')}
    legacy['format_version'] = 1
    store.metadata.write_text(json.dumps(legacy), encoding='utf-8')
    assert_rejected(store, tmp_path, ids[0])


@pytest.mark.parametrize('operation', ['publish', 'rollback'])
def test_pending_intent_digest_rejects_body_mismatch(tmp_path, monkeypatch, operation):
    store, ids = initialized(tmp_path)
    failed_transition(store, tmp_path, ids, monkeypatch, operation)
    record = json.loads(store.recovery.read_bytes())
    # Schema, ID and generation remain valid; immutable intent has changed.
    record['previous']['active_version_id'] = ids[0]
    store.recovery.write_text(json.dumps(record), encoding='utf-8')
    assert_rejected(store, tmp_path, ids[0])


def test_format_one_journal_is_not_automatically_upgraded(tmp_path, monkeypatch):
    store, ids = initialized(tmp_path)
    record = held_committed(store, ids[0], monkeypatch)
    def legacy(state):
        return dict(format_version=1, active_version_id=state['active_version_id'],
                    versions=state['versions'])
    old_record = dict(format_version=1, phase='committed', initialize=False,
                      previous=legacy(record['previous']), next=legacy(record['next']))
    store.metadata.write_text(json.dumps(legacy(record['next'])), encoding='utf-8')
    store.recovery.write_text(json.dumps(old_record), encoding='utf-8')
    assert_rejected(store, tmp_path, ids[0])


@pytest.mark.skipif(os.name != 'posix', reason='Linux directory fsync and SIGKILL')
@pytest.mark.parametrize('operation', ['publish', 'rollback'])
@pytest.mark.parametrize('boundary', ['marker_before', 'marker_after', 'settle_before',
                                     'settle_after', 'record_removed'])
def test_identity_marker_and_settlement_sigkill(tmp_path, operation, boundary):
    import select
    from tests.test_json_refresh import excel
    from tests.test_versions_process_recovery import summary
    store, ids = initialized(tmp_path)
    before = summary(store.path)
    original_files = version_files(store)
    source = excel(tmp_path / 'worker.xlsx', value=20)
    code = """
import os,sys,json
from pathlib import Path
from library_etl.versions import VersionStore
s=VersionStore(sys.argv[1]);src,op,boundary,target=sys.argv[2:]
old=s.list_versions()['active_version_id']
replace0,unlink0=os.replace,Path.unlink
def pause():
    print('paused',flush=True);sys.stdin.readline()
def replace(source,destination):
    value=json.loads(Path(source).read_bytes()) if Path(destination)==s.metadata else None
    marker=value is not None and value['recovery_required'] and value['active_version_id']==old
    settled=value is not None and not value['recovery_required'] and value['active_version_id']!=old
    if (boundary=='marker_before' and marker) or (boundary=='settle_before' and settled):pause()
    replace0(source,destination)
    if (boundary=='marker_after' and marker) or (boundary=='settle_after' and settled):pause()
def unlink(path,*a,**kw):
    result=unlink0(path,*a,**kw)
    if boundary=='record_removed' and path==s.recovery:pause()
    return result
os.replace,Path.unlink=replace,unlink
if op=='publish':s.upload_excel(src,partial_dates=[])
else:s.rollback(target)
"""
    worker = subprocess.Popen([sys.executable, '-B', '-c', code, str(store.path), str(source),
                               operation, boundary, ids[0]], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
    child_code = "from pathlib import Path;import sys,json;from tests.test_versions_process_recovery import summary;print(json.dumps(summary(Path(sys.argv[1]))))"
    child = subprocess.run([sys.executable, '-B', '-c', child_code, str(store.path)],
                           capture_output=True, text=True, check=True)
    recovered = json.loads(child.stdout)
    if boundary.startswith('marker'):
        assert recovered == before
        assert version_files(store) == original_files
    else:
        assert recovered['stats']['hourly_total_in'] == (492 if operation == 'publish' else 36)
        assert recovered['versions']['active_version_id'] == (ids[0] if operation == 'rollback'
                                                             else recovered['versions']['versions'][0]['id'])
        assert len(version_files(store)) == 4
    metadata = json.loads(store.metadata.read_bytes())
    assert metadata['recovery_required'] is False
    assert not store.recovery.exists()
    upload(store, tmp_path, 21)


@pytest.mark.parametrize('operation', ['publish', 'rollback'])
@pytest.mark.parametrize('boundary', ['marker', 'settlement'])
def test_identity_marker_and_settlement_continuous_errors(tmp_path, monkeypatch, operation, boundary):
    store, ids = initialized(tmp_path)
    before = store.list_versions()
    original = store.path.read_bytes()
    old_files = version_files(store)
    replace0, fsync0 = os.replace, os.fsync
    hit = False
    def replace(source, destination):
        nonlocal hit
        replace0(source, destination)
        if Path(destination) == store.metadata:
            state = json.loads(store.metadata.read_bytes())
            if ((boundary == 'marker' and state['recovery_required']
                    and state['active_version_id'] == before['active_version_id'])
                    or (boundary == 'settlement' and not state['recovery_required']
                        and state['active_version_id'] != before['active_version_id'])):
                hit = True
                if os.name == 'nt':
                    raise OSError('after metadata marker/settlement replacement')
    def fsync(fd):
        if hit:
            raise OSError('persistent fsync including recovery retry')
        return fsync0(fd)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', replace)
        fault.setattr(os, 'fsync', fsync)
        if boundary == 'marker':
            with pytest.raises(VersionError):
                upload(store, tmp_path, 20) if operation == 'publish' else store.rollback(ids[0])
        else:
            result = upload(store, tmp_path, 20) if operation == 'publish' else store.rollback(ids[0])
            assert result['status'] == ('published' if operation == 'publish' else 'rolled_back')
        assert hit and store.recovery.exists()
        assert all((store.directory / name).read_bytes() == value for name, value in old_files.items())
        for action in (store.list_versions, lambda: store.rollback(ids[0]),
                       lambda: upload(store, tmp_path, 21)):
            with pytest.raises(VersionError):
                action()
    recovered = VersionStore(store.path).list_versions()
    if boundary == 'marker':
        assert recovered == before and store.path.read_bytes() == original
        assert version_files(store) == old_files
    else:
        assert recovered['active_version_id'] == (result['version']['id'] if operation == 'publish' else ids[0])
    assert not store.recovery.exists()


def test_committed_record_with_prechange_metadata_is_rejected(tmp_path, monkeypatch):
    import hashlib
    store, ids = initialized(tmp_path)
    record = held_committed(store, ids[0], monkeypatch)
    intent = {key: value for key, value in record.items() if key != 'phase'}
    digest = hashlib.sha256(json.dumps(intent, ensure_ascii=False, allow_nan=False,
                                      sort_keys=True).encode('utf-8')).hexdigest()
    # Partial restore of the prechange marker from the same transaction.
    marker = {**record['previous'], 'generation': record['generation'],
              'transaction_id': record['transaction_id'], 'recovery_required': True,
              'recovery_digest': digest}
    store.metadata.write_text(json.dumps(marker), encoding='utf-8')
    assert_rejected(store, tmp_path, ids[0])
