import importlib

import pytest


def _load(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("EXPENSES_DATA_DIR", str(tmp_path))
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "ALLOWED_EMAILS",
              "SESSION_SECRET", "WEBSITE_SITE_NAME", "PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from expenses import db, main
    importlib.reload(db)
    importlib.reload(main)
    db.init_db()
    return db, main


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    return _load(tmp_path, monkeypatch)


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    return lambda **env: _load(tmp_path, monkeypatch, **env)
