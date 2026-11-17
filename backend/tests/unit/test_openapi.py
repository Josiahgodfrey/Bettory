"""The frontend contract: the committed OpenAPI schema (docs/api/openapi.json, the source of the frontend's
TypeScript types) is current, and every endpoint listed in docs/API.md exists with that method."""

from __future__ import annotations

import json
import re

from app.main import OPENAPI_FILE, openapi_json

API_DOC = OPENAPI_FILE.parent.parent / "API.md"


def test_committed_schema_is_current() -> None:
    assert OPENAPI_FILE.read_text(encoding="utf-8") == openapi_json(), (
        "the API changed: run `python -m app.cli openapi` and commit docs/api/openapi.json")


def test_documented_endpoints_exist() -> None:
    served = json.loads(openapi_json())["paths"]
    paths = {re.sub(r"\{[^}]+\}", "{}", p): ops for p, ops in served.items()}   # placeholder names may differ
    table = API_DOC.read_text(encoding="utf-8").split("## 3. Endpoint list", 1)[1].split("## 4.", 1)[0]
    documented = re.findall(r"^\| (GET|POST) \| `([^`?]+)", table, flags=re.MULTILINE)
    assert len(documented) >= 20
    for method, doc_path in documented:
        path = re.sub(r"\{[^}]+\}", "{}", doc_path)
        assert path in paths and method.lower() in paths[path], f"{method} {doc_path} is documented but not served"


def test_frontend_endpoints_have_typed_responses() -> None:
    """Every frontend endpoint except the three open JSON objects declares its response type."""
    paths = json.loads(openapi_json())["paths"]
    untyped_on_purpose = {"/matches/{match_id}/analysis", "/settings", "/paper/report"}
    frontend = [p for p in paths if p.split("/")[1] in {"sessions", "fixtures", "matches", "bets", "performance",
                                                          "paper", "dashboard", "me", "bookmakers"}]
    for p in frontend:
        if p in untyped_on_purpose:
            continue
        for op in paths[p].values():
            ok = next(r for code, r in op["responses"].items() if code.startswith("2"))
            schema = ok["content"]["application/json"]["schema"]
            assert "$ref" in schema or "$ref" in schema.get("items", {}), f"{p} has no response type"
