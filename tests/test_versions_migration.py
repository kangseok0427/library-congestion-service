"""Existing normal snapshots and recovery failures, with synthetic data only."""
import json
import subprocess
import sys
from datetime import date, datetime

import pytest

from library_etl.versions import VersionError, VersionStore
from scripts.rebuild import rebuild
from scripts.sample import generate
from backend.app import FileProvider, create_app
from backend.library_hours import KST
from fastapi.testclient import TestClient
from tests.test_json_refresh import excel
from tests.test_versions import persisted, upload


def test_existing_normal_snapshot_can_be_rolled_back(tmp_path):
    path = tmp_path / 'records.json'
    rebuild(generate(end=date(2026, 9, 10), days=14), path)
    original = path.read_bytes()
    client = TestClient(create_app(FileProvider(path),
                       clock=lambda: datetime(2026, 9, 10, 12, tzinfo=KST)))
    old_forecast = client.get('/api/v1/congestion/today?date=2026-09-17').json()['hourly']
    old_patterns = client.get('/api/v1/patterns').json()
    store = VersionStore(path)
    result = upload(store, tmp_path)
    versions = store.list_versions()['versions']
    assert len(versions) == 2
    assert versions[0]['id'] == result['version']['id']
    assert client.get('/api/v1/congestion/today?date=2026-09-17').json()['hourly'] != old_forecast
    assert client.get('/api/v1/patterns').json() != old_patterns
    store.rollback(versions[1]['id'])
    assert json.loads(path.read_bytes()) == json.loads(original)
    assert client.get('/api/v1/congestion/today?date=2026-09-17').json()['hourly'] == old_forecast
    assert client.get('/api/v1/patterns').json() == old_patterns


def test_first_publication_crash_keeps_existing_snapshot(tmp_path):
    path = tmp_path / 'records.json'
    rebuild(generate(end=date(2026, 9, 10), days=14), path)
    original = path.read_bytes()
    source = excel(tmp_path / 'crash.xlsx')
    code = '''
import os, sys
from library_etl.versions import VersionStore
class Crash(VersionStore):
    def _commit(self, connection):
        os._exit(19)
Crash(sys.argv[1]).upload_excel(sys.argv[2], partial_dates=[])
'''
    result = subprocess.run([sys.executable, '-c', code, str(path), str(source)], timeout=30)
    assert result.returncode == 19
    store = VersionStore(path)
    payload, _ = store.read_snapshot()
    assert payload == original
    assert store.list_versions()['versions'] == []
    assert not list(store.directory.glob('*.json'))


def test_corrupt_retained_json_is_recovered_from_committed_payload(tmp_path):
    store = VersionStore(tmp_path / 'records.json')
    version = upload(store, tmp_path)['version']['id']
    before = persisted(store)
    (store.directory / (version + '.json')).write_bytes(b'broken')
    store.rollback(version)
    assert persisted(store) == before


def test_corrupt_authority_is_rejected_without_activating_it(tmp_path):
    import sqlite3
    store = VersionStore(tmp_path / 'records.json')
    first = upload(store, tmp_path)['version']['id']
    upload(store, tmp_path, 8)
    before = store.path.read_bytes()
    with sqlite3.connect(store.database) as connection:
        connection.execute('UPDATE versions SET payload=? WHERE id=?', (b'broken', first))
    with pytest.raises(VersionError) as caught:
        store.rollback(first)
    assert caught.value.code == 'PUBLISH_FAILED'
    assert store.path.read_bytes() == before


def test_rollback_failure_and_invalid_rebuild_hook_preserve_snapshots(tmp_path, monkeypatch):
    import library_etl.versions as module
    store = VersionStore(tmp_path / 'records.json')
    first = upload(store, tmp_path)['version']['id']
    upload(store, tmp_path, 10)
    before = persisted(store)
    real_replace = module.os.replace
    with monkeypatch.context() as patch:
        def broken(source, destination):
            if destination == store.path:
                raise PermissionError('injected live replace')
            return real_replace(source, destination)
        patch.setattr(module.os, 'replace', broken)
        with pytest.raises(VersionError):
            store.rollback(first)
    assert persisted(store) == before
    def mutate(records, destination):
        records[0]['in_count'] += 1
        rebuild(records, destination)
    store.rebuild_fn = mutate
    with pytest.raises(VersionError):
        upload(store, tmp_path, 20)
    assert persisted(store) == before
