import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aig import store
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GRAPH_CLIENT_ID", "ALLOWED_EMAILS",
              "WEBSITE_SITE_NAME", "WEBSITE_INSTANCE_ID", "CONFIG_PATH"):
        monkeypatch.delenv(k, raising=False)
    store.reset_store(store.LocalStore(tmp_path))
    from aig.app import app
    with TestClient(app) as c:
        c.root = tmp_path
        yield c
    store.reset_store(None)
