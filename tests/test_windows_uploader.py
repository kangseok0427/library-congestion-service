"""Synthetic inputs and HTTP doubles; no live endpoint or credentials."""
import json
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError

import pytest

from backend.domain import validate_records
from openpyxl import load_workbook
from scripts.sample import generate
from tests.test_json_refresh import excel
from windows_uploader.core import process
from windows_uploader.storage import LocalStore
from windows_uploader.upload import UploadClient, UploadError, validate_endpoint


class Response:
    status = 204
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return b""


class Credentials:
    def get(self): return "SENSITIVE_TEST_VALUE"


class Transport:
    def __init__(self, sequence):
        self.sequence = iter(sequence)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        result = next(self.sequence)
        if isinstance(result, Exception):
            raise result
        return result


def client(sequence, retries=2):
    transport = Transport(sequence)
    return UploadClient("https://example.test/private", transport=transport,
                        sleeper=lambda _: None, retries=retries), transport


def http_error(code):
    return HTTPError("https://example.test/private", code, "bad", {}, None)


@pytest.mark.parametrize("url", ["http://example.test/upload", "https://user:pass@example.test/u",
                                      "https://example.test/u#part", "not-a-url", "https://"])
def test_https_required(url):
    with pytest.raises(UploadError): validate_endpoint(url)


def test_upload_success_and_token_not_in_error_or_logs(tmp_path):
    store = LocalStore(tmp_path / "user")
    upload, transport = client([Response()])
    records = generate(end=date(2026, 9, 10), days=1)
    assert upload.send(records, Credentials().get()) == 204
    request, timeout = transport.calls[0]
    assert request.get_header("Authorization") == "Bearer SENSITIVE_TEST_VALUE"
    assert timeout == 20
    assert json.loads(request.data) == records
    store.log("Authorization: Bearer SENSITIVE_TEST_VALUE")
    assert "SENSITIVE_TEST_VALUE" not in (store.logs / "uploader.log").read_text(encoding="utf-8")


def test_upload_contract_hooks_can_follow_t12():
    transport = Transport([Response()])
    upload = UploadClient("https://example.test/custom", transport=transport,
                          auth_headers=lambda token: {"X-Upload-Key": token},
                          method="PUT", encode=lambda records: b"custom-body")
    assert upload.send([], "mock-key") == 204
    request, _ = transport.calls[0]
    assert request.get_method() == "PUT"
    assert request.get_header("X-upload-key") == "mock-key"
    assert request.data == b"custom-body"


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


def test_process_convert_null_backup_and_repeat(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "user")
    source = excel(tmp_path / "한글.xlsx")
    upload, transport = client([Response(), Response()])
    first = process(source, store, Credentials(), upload)
    rows = json.loads(store.records.read_text(encoding="utf-8"))
    assert validate_records(rows) == rows
    assert all(r["out_count"] is None for r in rows if r["hour"] == 11)
    assert first["backup"].read_bytes() == store.records.read_bytes()
    assert store.last_success() == first["last_success"]
    second = process(source, store, Credentials(), upload)
    assert json.loads(store.records.read_text(encoding="utf-8")) == rows
    assert second["backup"].exists() and len(transport.calls) == 2


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
