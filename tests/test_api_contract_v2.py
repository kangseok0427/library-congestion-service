"""ADE-43: the OpenAPI file and frontend fixtures move together."""
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
FIXTURES = CONTRACTS / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def spec():
    return yaml.safe_load((CONTRACTS / "openapi-v2.yaml").read_text(encoding="utf-8"))


def test_openapi_has_every_frozen_endpoint_and_method():
    document = spec()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "2.0.0"
    expected = {
        "/api/v1/admin/session": {"post", "get", "delete"},
        "/api/v1/admin/uploads": {"post"},
        "/api/v1/admin/versions": {"get"},
        "/api/v1/admin/versions/{version_id}/rollback": {"post"},
        "/api/v1/congestion/today": {"get"},
    }
    assert {path: set(item) for path, item in document["paths"].items()} == expected
    session = document["components"]["securitySchemes"]["adminSession"]
    assert session == {"type": "apiKey", "in": "cookie", "name": "library_admin_session"}


def test_every_external_fixture_reference_exists_and_is_json():
    document = spec()
    references = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "externalValue":
                    references.append(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    assert references
    for reference in references:
        target = (CONTRACTS / reference).resolve()
        assert target.is_relative_to(CONTRACTS.resolve())
        assert target.exists(), reference
        json.loads(target.read_text(encoding="utf-8"))


def test_admin_session_and_error_envelope_are_fixed():
    assert load("admin-session-success.json") == {
        "authenticated": True,
        "user": {"role": "admin"},
    }
    for name in ("admin-session-invalid.json", "admin-upload-invalid.json"):
        error = load(name)["error"]
        assert set(error) == {"code", "message", "details"}
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]
        assert isinstance(error["details"], list)


def test_upload_success_and_four_version_rotation_shape():
    upload = load("admin-upload-success.json")
    assert upload["status"] == "published"
    assert upload["version"]["is_active"] is True
    assert upload["version"]["record_count"] > 0
    assert upload["validation"]["warning_count"] == len(upload["validation"]["warnings"])

    data = load("admin-versions-four.json")
    versions = data["versions"]
    assert data["max_versions"] == 4
    assert len(versions) == 4
    assert [v["created_at"] for v in versions] == sorted(
        (v["created_at"] for v in versions), reverse=True
    )
    active = [v for v in versions if v["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == data["active_version_id"]

    rollback = load("admin-rollback-success.json")
    assert rollback["status"] == "rolled_back"
    assert rollback["active_version_id"] in {v["id"] for v in versions}


def test_congestion_fixtures_expose_only_the_required_frontend_contract():
    for name in (
        "congestion-open.json",
        "congestion-closed.json",
        "congestion-insufficient.json",
    ):
        data = load(name)
        assert {"date", "data_status", "updated_at", "congestion", "recommendation", "hourly"} <= data.keys()
        assert {"level", "label"} <= data["congestion"].keys()
        assert isinstance(data["recommendation"]["message"], str)
        for row in data["hourly"]:
            assert {"hour", "estimated_present", "level", "label", "quality_status"} == row.keys()

    assert load("congestion-closed.json")["hourly"] == []
    assert all(
        row["estimated_present"] is None and row["level"] is None
        for row in load("congestion-insufficient.json")["hourly"]
    )
    open_rows = load("congestion-open.json")["hourly"]
    assert len(open_rows) == 12
    assert {row["level"] for row in open_rows} == {"quiet", "normal", "busy"}


def test_contract_guards_the_decisions_that_remove_api_bottlenecks():
    document_text = (CONTRACTS / "openapi-v2.yaml").read_text(encoding="utf-8")
    readme = (CONTRACTS / "README.md").read_text(encoding="utf-8")
    assert "10 MB" in document_text
    assert "maxItems: 4" in document_text
    assert "Mock-first" in readme
    assert "must not wait" in readme
    assert "token input" in readme

