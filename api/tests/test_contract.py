"""Keep `contracts/openapi.yaml` from drifting away from the running service.

The contract is a curated subset (paths, methods, identity) rather than a dump of the
generated schema, so these checks lock the shape clients depend on: the same paths and
methods on both sides, the same versions, and an allocation-strategy enum that matches
what the kernel actually implements. Request/response *bodies* stay documented by hand —
only the parts that can be derived are asserted here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pro_wms_cli.allocation import STRATEGIES

from app import __version__
from app.main import app

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
ALLOCATE_PATH = "/outbounds/{outbound_id}/allocate"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
USER_HEADER_REF = "#/components/parameters/UserHeader"


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    """The hand-written contract, parsed."""
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """The schema FastAPI generates from the app."""
    return app.openapi()


def _operations(paths: dict[str, Any]) -> set[tuple[str, str]]:
    """Flatten a `paths` section into `{(METHOD, path)}` pairs."""
    return {
        (method.upper(), path)
        for path, item in paths.items()
        for method in item
        if method in HTTP_METHODS
    }


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Follow a local `$ref` and unwrap `anyOf`, down to a concrete object schema.

    Optional bodies are generated as `anyOf: [{$ref: Model}, {type: null}]`, so a plain
    `$ref` lookup is not enough.
    """
    if "$ref" in node:
        target = schema["components"]["schemas"][node["$ref"].rsplit("/", 1)[-1]]
        return _resolve(schema, target)
    for candidate in node.get("anyOf", []):
        if candidate.get("type") != "null":
            return _resolve(schema, candidate)
    return node


def _documented_strategy_enum(contract: dict[str, Any]) -> list[str]:
    """The `strategy` enum the contract documents for the allocate endpoint."""
    body = contract["paths"][ALLOCATE_PATH]["post"]["requestBody"]["content"]["application/json"]
    return body["schema"]["properties"]["strategy"]["enum"]


def test_documented_paths_match_the_app(contract: dict[str, Any], schema: dict[str, Any]) -> None:
    """A path or method added, renamed or removed on either side must fail here."""
    documented = _operations(contract["paths"])
    generated = _operations(schema["paths"])
    assert documented == generated, (
        f"missing from contract: {sorted(generated - documented)}\n"
        f"stale in contract: {sorted(documented - generated)}"
    )


def test_versions_match_the_app(contract: dict[str, Any], schema: dict[str, Any]) -> None:
    assert contract["openapi"] == schema["openapi"]
    assert contract["info"]["version"] == schema["info"]["version"] == __version__


def test_documented_strategies_are_implemented(contract: dict[str, Any]) -> None:
    """The documented enum must be the kernel's real strategy registry."""
    assert set(_documented_strategy_enum(contract)) == set(STRATEGIES)


def test_documented_strategies_match_the_request_model(contract: dict[str, Any], schema: dict[str, Any]) -> None:
    """The same enum, read back from the schema FastAPI generated for the request body."""
    body = schema["paths"][ALLOCATE_PATH]["post"]["requestBody"]["content"]["application/json"]["schema"]
    generated = _resolve(schema, body)["properties"]["strategy"]["enum"]
    assert set(_documented_strategy_enum(contract)) == set(generated)


def test_identity_header_is_documented_on_every_post(contract: dict[str, Any]) -> None:
    """Every mutating endpoint must document the header that carries the identity."""
    for path, item in contract["paths"].items():
        for method, operation in item.items():
            if method != "post":
                continue
            refs = {parameter.get("$ref") for parameter in operation.get("parameters", [])}
            assert USER_HEADER_REF in refs, f"{method.upper()} {path} does not document X-User"
