import json
import os
import tempfile
import pytest
from pathlib import Path

from app import app
from backend import ai_service as ai_mod

PROMPTS_PATH = Path(__file__).parent.parent / "prompts.toml"


def read_prompts():
    return PROMPTS_PATH.read_text()


def write_prompts(data: str):
    PROMPTS_PATH.write_text(data)


@pytest.fixture(autouse=True)
def backup_prompts():
    """Save/restore prompts.toml around each test to avoid polluting config."""
    orig = read_prompts()
    try:
        yield
    finally:
        write_prompts(orig)
        # reload the global service so it reflects the restored file
        from app import ai_service

        ai_service._load_prompts()


def test_list_modes(client=None):
    if client is None:
        client = app.test_client()
    resp = client.get("/api/ai/modes")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert "modes" in data and "order" in data
    modes = data["modes"]
    order = data["order"]
    assert isinstance(modes, dict)
    assert isinstance(order, list)
    # must at least include the builtin summarize mode
    assert "summarize" in modes
    assert "summarize" in order
    # order elements should match dict keys sequence
    assert order == list(modes.keys())


def test_add_new_mode(client=None):
    if client is None:
        client = app.test_client()
    # create a unique mode name
    mode = "testmode"
    config = {
        "instruction": "Do the test",
        "rules": ["Only do the thing."],
    }
    resp = client.post(
        "/api/ai/modes",
        data=json.dumps({"mode": mode, "config": config}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("success")

    # subsequent GET should list the new mode
    resp2 = client.get("/api/ai/modes")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    modes2 = data2.get("modes", {})
    order2 = data2.get("order", [])
    assert mode in modes2
    assert modes2[mode]["instruction"] == "Do the test"
    # new mode should appear at end of order list
    assert order2[-1] == mode

    # running again with same name should return 400
    resp3 = client.post(
        "/api/ai/modes",
        data=json.dumps({"mode": mode, "config": config}),
        content_type="application/json",
    )
    assert resp3.status_code == 400
    assert "already exists" in resp3.get_json().get("error", "")
