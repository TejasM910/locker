"""REQ-NFR-002: the running API must match specs/openapi.yaml."""
import pytest
import yaml

from tests.conftest import SPECS

METHODS = {"get", "post", "put", "patch", "delete"}


def _ops(paths: dict, skip_planned: bool) -> set[tuple[str, str]]:
    out = set()
    for path, item in paths.items():
        for method, op in item.items():
            if method in METHODS and not (skip_planned and op.get("x-status") == "planned"):
                out.add((method, path))
    return out


@pytest.mark.req("REQ-NFR-002")
def test_api_matches_openapi_spec():
    from app.main import app
    spec = yaml.safe_load((SPECS / "openapi.yaml").read_text())
    expected = _ops(spec["paths"], skip_planned=True)
    actual = _ops(app.openapi()["paths"], skip_planned=False)
    assert actual == expected, f"missing in code: {expected - actual}; not in spec: {actual - expected}"
