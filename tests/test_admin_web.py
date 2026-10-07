"""ADE-47: the admin page stays on the ADE-43 contract without running a browser."""
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
CONTRACTS = ROOT / "contracts"


def text(name):
    return (FRONTEND / name).read_text(encoding="utf-8")


def contract_error_codes():
    codes = set()

    def walk(value):
        if isinstance(value, dict):
            error = value.get("error")
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                codes.add(error["code"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(yaml.safe_load((CONTRACTS / "openapi-v2.yaml").read_text(encoding="utf-8")))
    for path in (CONTRACTS / "fixtures").glob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return codes


def test_every_contract_error_code_has_korean_admin_message():
    script = text("admin.js")
    block = script[script.index("const ERROR_MESSAGES"):script.index("};", script.index("const ERROR_MESSAGES"))]
    mapped = set(re.findall(r"^\s+([A-Z_]+):", block, re.M))
    missing = contract_error_codes() - mapped
    assert not missing, f"admin.js has no Korean message for {sorted(missing)}"


def test_transport_uses_only_frozen_admin_paths():
    spec = yaml.safe_load((CONTRACTS / "openapi-v2.yaml").read_text(encoding="utf-8"))
    frozen = {path for path in spec["paths"] if path.startswith("/api/v1/admin/")}
    cloud = yaml.safe_load((CONTRACTS / "openapi-cloud.yaml").read_text(encoding="utf-8"))
    frozen |= set(cloud["paths"])
    source = text("admin-api.js")
    used = set(re.findall(r"'(/api/v1/admin/[^'`]*)'", source))
    used |= {p.replace("${encodeURIComponent(versionId)}", "{version_id}")
             for p in re.findall(r"`(/api/v1/admin/[^`]*)`", source)}
    assert used == frozen


def test_admin_page_has_no_token_input_or_storage():
    html = text("admin.html")
    assert not re.search(r"<input[^>]*(token|authorization)", html, re.I)
    for name in ("admin.js", "admin-api.js"):
        source = text(name)
        assert "Authorization" not in source
        assert "localStorage" not in source and "sessionStorage" not in source


def test_admin_assets_are_relative_for_static_and_mock_hosting():
    html = text("admin.html")
    for asset in ("admin.css", "admin-api.js", "admin.js"):
        assert f'"{asset}"' in html, asset
    assert not re.search(r'(?:src|href)="/', html)
