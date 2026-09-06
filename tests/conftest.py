"""Shared test configuration.

Environment is set *before* the app is imported: app.main builds its audit chain
and classifier at module scope, so the settings have to be in place first.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ai-sec-tests-"))

os.environ["LLM_MODE"] = "mock"
os.environ["GEMINI_API_KEY"] = ""
os.environ["AUDIT_LOG_PATH"] = str(_TMP / "audit.log")
os.environ["AUDIT_LOG_TO_CONSOLE"] = "false"
os.environ["CANARY_TOKEN"] = "CANARY-testtoken0123"
os.environ["RATE_LIMIT"] = "5/minute"
os.environ["TRUST_PROXY_HEADERS"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

ADMIN_KEY = "sk-admin-999888"
ANALYST_KEY = "sk-analyst-777666"
VIEWER_KEY = "sk-viewer-555444"
DISABLED_KEY = "sk-disabled-111000"
UNKNOWN_KEY = "sk-invalid-000000"


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    """Rate limiting is off by default so unrelated tests do not exhaust the
    bucket. The rate-limit test re-enables it explicitly."""
    main.limiter.enabled = False
    yield
    main.limiter.enabled = False


@pytest.fixture
def client():
    # Context manager form so the lifespan handler runs and the audit chain is
    # opened exactly as it is in production.
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def audit_path() -> Path:
    return Path(os.environ["AUDIT_LOG_PATH"])


def ask(client: TestClient, question: str, api_key: str):
    return client.post("/ask", json={"question": question, "api_key": api_key})
