"""Synthetic inputs and HTTP doubles; no live endpoint or credentials."""
import json
import queue
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from backend.domain import validate_records
from openpyxl import load_workbook
from scripts.sample import generate
from tests.test_json_refresh import excel
from windows_uploader.core import process
from windows_uploader.storage import LocalStore
from windows_uploader.upload import UploadClient, UploadError, validate_endpoint


class Response:
    def __init__(self, body=b"", status=204): self.body = body; self.status = status
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return self.body


class Credentials:
    def get(self): return "SENSITIVE_TEST_VALUE"


def live_records(*, end, days):
    records = generate(end=end, days=days)
    for record in records:
        record["source_file"] = "server.xlsx"
    return records


class Transport:
    def __init__(self, sequence, baseline=None):
        self.sequence = iter(sequence)
        self.calls = []
        self.download_calls = []
        self.baseline = baseline if baseline is not None else live_records(
            end=date(2026, 9, 9), days=1)

    def __call__(self, request, timeout):
        if request.get_method() == "GET":
            self.download_calls.append((request, timeout))
            return Response(json.dumps(self.baseline).encode("utf-8"), status=200)
        self.calls.append((request, timeout))
        result = next(self.sequence)
        if isinstance(result, Exception):
            raise result
        return result


def client(sequence, retries=2, baseline=None):
    transport = Transport(sequence, baseline)
    return UploadClient("https://example.test/private", mode="mock", transport=transport,
                        sleeper=lambda _: None, retries=retries), transport


def http_error(code):
    return HTTPError("https://example.test/private", code, "bad", {}, None)


@pytest.mark.parametrize("url", ["http://example.test/upload", "https://user:pass@example.test/u",
                                      "https://@example.test/u", "https://example.test/u#part",
                                      "https://example.test/u?token=secret", "https://example.test/u?",
                                      "https://example.test/u#",
                                      "not-a-url", "https://"])
def test_https_required(url):
    with pytest.raises(UploadError): validate_endpoint(url)


def test_upload_success_and_token_not_in_error_or_logs(tmp_path):
    store = LocalStore(tmp_path / "user")
    upload, transport = client([Response()])
    records = generate(end=date(2026, 9, 10), days=1)
    assert upload.send(records, Credentials().get()) == 204
    request, timeout = transport.calls[0]
    assert request.get_header("Authorization") == "Bearer SENSITIVE_TEST_VALUE"
    assert timeout == 60
    assert json.loads(request.data) == records
    store.log("Authorization: Bearer SENSITIVE_TEST_VALUE")
    assert "SENSITIVE_TEST_VALUE" not in (store.logs / "uploader.log").read_text(encoding="utf-8")


def test_download_success_and_token_not_exposed():
    records = generate(end=date(2026, 9, 10), days=2)
    upload, transport = client([], baseline=records)
    assert upload.fetch("SENSITIVE_TEST_VALUE") == records
    request, timeout = transport.download_calls[0]
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer SENSITIVE_TEST_VALUE"
    assert timeout == 60


def test_upload_contract_hooks_can_follow_t12():
    transport = Transport([Response()])
    upload = UploadClient("https://example.test/custom", mode="mock", transport=transport,
                          auth_headers=lambda token: {"X-Upload-Key": token},
                          method="PUT", encode=lambda records: b"custom-body")
    assert upload.send([], "mock-key") == 204
    request, _ = transport.calls[0]
    assert request.get_method() == "PUT"
    assert request.get_header("X-upload-key") == "mock-key"
    assert request.data == b"custom-body"


def test_production_requires_exact_approved_endpoint_and_host():
    transport = Transport([Response()])
    with pytest.raises(UploadError, match="T12"):
        UploadClient("https://example.test/custom", transport=transport)
    with pytest.raises(UploadError, match="Mock"):
        UploadClient("https://example.test/custom", mode="mock")
    with pytest.raises(UploadError, match="Mock"):
        UploadClient("https://example.test/custom", mode="mock", transport=urlopen)
    with pytest.raises(UploadError, match="T12"):
        UploadClient("https://other.test/custom", approved_endpoint="https://other.test/custom",
                     transport=transport)
    with pytest.raises(UploadError, match="T12"):
        UploadClient("https://example.test/other", approved_endpoint="https://example.test/custom",
                     approved_hosts={"example.test"}, transport=transport)
    approved = UploadClient("https://example.test/custom", approved_endpoint="https://example.test/custom",
                            approved_hosts={"example.test"}, transport=transport)
    assert approved.send([], "synthetic") == 204


def test_gui_copies_source_before_worker_and_worker_uses_no_tk(monkeypatch):
    from windows_uploader import app as ui

    class Variable:
        def __init__(self, value): self.value = value; self.readable = True
        def get(self):
            assert self.readable, "Tk variable accessed by worker"
            return self.value
        def set(self, value): self.value = value

    class Widget:
        def configure(self, **kwargs): pass

    class Root:
        def __init__(self): self.readable = True
        def after(self, *args):
            assert self.readable, "Tk root accessed by worker"

    launched = []
    class Thread:
        def __init__(self, *, target, args, daemon):
            launched.append((target, args))
        def start(self): pass

    fake = ui.UploaderApp.__new__(ui.UploaderApp)
    fake.root = Root()
    fake.source = Variable("C:/한글/first.xlsx")
    fake.endpoint = Variable("https://example.test/upload")
    fake.status = Variable("")
    fake.run_button = Widget()
    fake.busy = False
    fake.events = queue.Queue()
    fake.credentials = type("Creds", (), {"exists": lambda self: True})()
    fake.store = type("Store", (), {"save_config": lambda self, endpoint: None})()
    monkeypatch.setattr(ui.threading, "Thread", Thread)
    monkeypatch.setattr(ui, "UploadClient", lambda endpoint: object())
    captured = []
    def process_double(source, store, credentials, client, progress):
        captured.append(source)
        progress("진행 중")
        return {"last_success": "2026-09-22T12:00:00+09:00"}
    monkeypatch.setattr(ui, "process", process_double)
    fake.start()
    assert launched[0][1] == ("C:/한글/first.xlsx", "https://example.test/upload")
    fake.source.readable = fake.endpoint.readable = fake.root.readable = False
    fake.source.value = "C:/changed.xlsx"
    target, args = launched[0]
    target(*args)
    assert captured == ["C:/한글/first.xlsx"]
    assert fake.events.get_nowait() == ("progress", "진행 중")
    assert fake.events.get_nowait()[0:2] == ("finish", True)


def test_gui_blocks_unapproved_endpoint_before_work(monkeypatch):
    from windows_uploader import app as ui
    class Variable:
        def __init__(self, value): self.value = value
        def get(self): return self.value
    fake = ui.UploaderApp.__new__(ui.UploaderApp)
    fake.busy = False
    fake.source = Variable("C:/source.xlsx")
    fake.endpoint = Variable("https://evil.test/upload")
    fake.credentials = type("Creds", (), {"exists": lambda self: True})()
    fake.store = type("Store", (), {"save_config": lambda self, endpoint: pytest.fail("saved")})()
    errors = []
    monkeypatch.setattr(ui.messagebox, "showerror", lambda *args: errors.append(args))
    fake.start()
    assert errors and "T12" in errors[0][1]


@pytest.mark.parametrize("code", [400, 401, 403, 413, 422])
def test_non_retryable_status(code):
    upload, transport = client([http_error(code), Response()])
    with pytest.raises(UploadError, match=str(code)):
        upload.send([], "secret")
    assert len(transport.calls) == 1


def test_retry_5xx_timeout_network_and_exhaustion():
    upload, transport = client([http_error(503), TimeoutError("secret"), Response()])
    assert upload.send([], "secret") == 204
    assert len(transport.calls) == 3
    upload, transport = client([URLError("secret"), URLError("secret"), URLError("secret")])
    with pytest.raises(UploadError) as error:
        upload.send([], "secret")
    assert "secret" not in str(error.value) and len(transport.calls) == 3
    upload, transport = client([http_error(500)] * 3)
    with pytest.raises(UploadError, match="500"):
        upload.send([], "secret")
    assert len(transport.calls) == 3


def test_store_state_backup_and_directories(tmp_path):
    store = LocalStore(tmp_path / "user")
    assert all(p.is_dir() for p in (store.logs, store.backups, store.temp))
    assert store.last_success() is None
    store.save_success(datetime(2026, 9, 22, tzinfo=timezone.utc))
    assert LocalStore(store.home).last_success().startswith("2026-09-22")
    source = tmp_path / "source.json"
    source.write_bytes(b"[]")
    assert store.backup(source).read_bytes() == b"[]"
    store.save_config("https://example.test/private")
    assert "token" not in store.config.read_text(encoding="utf-8")


def test_backup_limit_deletes_oldest_first_and_keeps_unrelated_files(tmp_path, monkeypatch):
    from windows_uploader import storage
    store = LocalStore(tmp_path / "user")
    monkeypatch.setattr(storage, "MAX_BACKUPS", 3)
    names = [f"records-2020010{day}-000000-000000.json" for day in range(1, 5)]
    for name in names:
        (store.backups / name).write_bytes(b"old")
    unrelated = store.backups / "manual.json"
    unrelated.write_bytes(b"keep")
    source = tmp_path / "source.json"
    source.write_bytes(b"latest")
    removed = []
    original_unlink = type(unrelated).unlink
    def tracked_unlink(path, *args, **kwargs):
        if path.parent == store.backups:
            removed.append(path.name)
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(type(unrelated), "unlink", tracked_unlink)
    latest = store.backup(source)
    assert removed == names[:2]
    assert sorted(path.name for path in store.backups.glob("records-*.json")) == names[2:] + [latest.name]
    assert unrelated.read_bytes() == b"keep"


def test_backup_cleanup_failure_does_not_discard_new_backup(tmp_path, monkeypatch):
    from windows_uploader import storage
    store = LocalStore(tmp_path / "user")
    monkeypatch.setattr(storage, "MAX_BACKUPS", 1)
    old = store.backups / "records-20200101-000000-000000.json"
    old.write_bytes(b"old")
    source = tmp_path / "source.json"
    source.write_bytes(b"new")
    original_unlink = type(old).unlink
    def denied(path, *args, **kwargs):
        if path == old:
            raise PermissionError("cleanup denied")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(type(old), "unlink", denied)
    latest = store.backup(source)
    assert latest.read_bytes() == b"new"
    assert old.exists()


def test_backup_cleanup_failure_does_not_block_successful_refresh(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "user")
    source = excel(tmp_path / "valid.xlsx")
    def failed_cleanup():
        raise OSError("cleanup")
    monkeypatch.setattr(store, "_prune_backups", failed_cleanup)
    upload, transport = client([Response()])
    result = process(source, store, Credentials(), upload)
    assert result["backup"].exists()
    assert store.records.exists() and store.last_success() == result["last_success"]
    assert len(transport.calls) == 1


def test_process_convert_null_backup_and_repeat(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "user")
    source = excel(tmp_path / "한글.xlsx")
    upload, transport = client([Response(), Response()])
    first = process(source, store, Credentials(), upload)
    rows = json.loads(store.records.read_text(encoding="utf-8"))
    assert validate_records(rows) == rows
    assert all(r["out_count"] is None for r in rows
               if r["date"] == "2026-09-10" and r["hour"] == 11)
    assert first["backup"].read_bytes() == store.records.read_bytes()
    assert store.last_success() == first["last_success"]
    second = process(source, store, Credentials(), upload)
    assert json.loads(store.records.read_text(encoding="utf-8")) == rows
    assert second["backup"].exists() and len(transport.calls) == 2


def test_first_run_merges_excel_into_server_baseline_before_upload(tmp_path):
    store = LocalStore(tmp_path / "user")
    baseline = live_records(end=date(2026, 9, 9), days=3)
    source = excel(tmp_path / "latest.xlsx", value=17, day="2026-09-10")
    upload, transport = client([Response()], baseline=baseline)
    result = process(source, store, Credentials(), upload)
    uploaded = json.loads(transport.calls[0][0].data)
    assert len({r["date"] for r in uploaded}) == 4
    assert {r["date"] for r in uploaded} == {r["date"] for r in baseline} | {"2026-09-10"}
    assert any(r["date"] == "2026-09-10" and r["in_count"] == 17 for r in uploaded)
    assert json.loads(store.records.read_text(encoding="utf-8")) == uploaded
    assert result["report"]["record_count"] == len(uploaded)


def test_completed_server_date_is_not_replaced_by_partial_excel(tmp_path):
    store = LocalStore(tmp_path / "user")
    baseline = live_records(end=date(2026, 9, 10), days=1)
    source = excel(tmp_path / "overlap.xlsx", value=17, day="2026-09-10")
    upload, transport = client([Response()], baseline=baseline)
    with pytest.raises(ValueError):
        process(source, store, Credentials(), upload)
    assert not transport.calls
    assert not store.records.exists()


def test_first_real_excel_replaces_synthetic_server_sample(tmp_path):
    store = LocalStore(tmp_path / "user")
    baseline = generate(end=date(2026, 9, 10), days=3)
    source = excel(tmp_path / "real.xlsx", value=17, day="2026-09-10")
    upload, transport = client([Response()], baseline=baseline)
    progress = []
    result = process(source, store, Credentials(), upload, progress.append)
    uploaded = json.loads(transport.calls[0][0].data)
    assert {record["date"] for record in uploaded} == {"2026-09-10"}
    assert {record["source_file"] for record in uploaded} == {"real.xlsx"}
    assert result["report"]["replaced_synthetic_baseline"] is True
    assert "서버 샘플 데이터 확인 · 실데이터로 교체 중" in progress


def test_failed_conversion_backup_and_upload_preserve_live_and_state(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "user")
    source = excel(tmp_path / "good.xlsx")
    upload, _ = client([Response()])
    process(source, store, Credentials(), upload)
    before = store.records.read_bytes()
    state = store.last_success()
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"broken")
    with pytest.raises(ValueError): process(bad, store, Credentials(), upload)
    assert store.records.read_bytes() == before
    upload, _ = client([http_error(401)])
    with pytest.raises(UploadError): process(source, store, Credentials(), upload)
    assert store.records.read_bytes() == before and store.last_success() == state
    def broken_backup(source): raise OSError("secret disk detail")
    monkeypatch.setattr(store, "backup", broken_backup)
    with pytest.raises(ValueError) as error: process(source, store, Credentials(), upload)
    assert "secret" not in str(error.value)
    assert store.records.read_bytes() == before and store.last_success() == state
    assert not list(store.temp.glob("*.json"))


def test_bad_extension_and_missing_column_rejected(tmp_path):
    from library_etl import preprocess
    invalid = tmp_path / "wrong.xls"
    invalid.write_bytes(b"not xlsx")
    with pytest.raises(ValueError): preprocess(invalid)
    source = excel(tmp_path / "missing.xlsx")
    book = load_workbook(source)
    book.active["A1"] = "removed"
    book.save(source)
    book.close()
    with pytest.raises(ValueError): preprocess(source)


@pytest.mark.parametrize("change", [lambda r: r.pop("date"),
                                    lambda r: r.update(date="2026-02-30"),
                                    lambda r: r.update(hour="11"),
                                    lambda r: r.update(in_count=True)])
def test_common_records_invalid_fields_rejected(change):
    rows = generate(end=date(2026, 9, 10), days=1)
    change(rows[0])
    with pytest.raises(ValueError): validate_records(rows)


def test_empty_records_never_uploaded(tmp_path, monkeypatch):
    from windows_uploader import core
    store = LocalStore(tmp_path / "user")
    source = excel(tmp_path / "valid.xlsx")
    upload, transport = client([Response()])
    monkeypatch.setattr(core, "read_records", lambda _: [])
    with pytest.raises(ValueError): process(source, store, Credentials(), upload)
    assert not transport.calls and not store.records.exists()
