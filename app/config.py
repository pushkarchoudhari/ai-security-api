"""Application configuration.

All runtime behaviour is driven from environment variables so the same image can
run locally, in CI (offline), and on a publicly reachable host without code
changes.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


def _default_canary() -> str:
    """Per-process canary. Overridable via env so tests can assert on a fixed value."""
    return f"CANARY-{secrets.token_hex(8)}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM -----------------------------------------------------------------
    # "mock" runs the deterministic keyword classifier and never leaves the
    # process. This is the default so that CI, tests, and any publicly reachable
    # instance cost nothing and cannot be abused into draining a real API quota.
    llm_mode: Literal["live", "mock"] = "mock"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"
    llm_timeout_seconds: float = 10.0

    # Hard ceiling on live LLM calls per process lifetime. Trips into the
    # deterministic fallback rather than failing the request.
    llm_daily_call_budget: int = 500

    # --- Security ------------------------------------------------------------
    # Injected into the system prompt; if it ever appears in model output we
    # have proof of system-prompt leakage.
    canary_token: str = Field(default_factory=_default_canary)
    injection_risk_threshold: int = 70
    max_question_length: int = 500
    rate_limit: str = "10/minute"

    # Only enable behind a proxy you control (Fly.io, an ALB, nginx). When off,
    # the socket peer is used. Trusting X-Forwarded-For on a directly exposed
    # service lets any caller forge their identity and bypass rate limiting.
    trust_proxy_headers: bool = False

    # --- Audit ---------------------------------------------------------------
    audit_log_path: Path = ROOT_DIR / "audit.log"
    audit_log_to_console: bool = True

    @property
    def llm_live(self) -> bool:
        """Live mode requires an actual key; otherwise we degrade to mock."""
        return self.llm_mode == "live" and bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
