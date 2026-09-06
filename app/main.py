"""Secure AI Enterprise API.

Request pipeline -- each layer runs before the next and every transition is
written to a tamper-evident audit chain:

    rate limit -> schema validation -> authentication -> injection analysis
    -> intent classification -> output guardrails -> authorization -> action

See docs/THREAT_MODEL.md for what each layer is and is not claimed to stop.
"""

# NOTE: deliberately no `from __future__ import annotations` here. slowapi's
# @limiter.limit wrapper does not carry the original function's __globals__, so
# with postponed annotations FastAPI cannot resolve `AskRequest` and silently
# demotes the body parameter to a query parameter -- every POST then 422s.
import json
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import __version__
from app.actions import execute
from app.config import get_settings
from app.llm.client import IntentClassifier
from app.models import AskRequest
from app.security.audit import AuditChain
from app.security.auth import authenticate
from app.security.injection import analyze

settings = get_settings()
STATIC_DIR = Path(__file__).parent / "static"

# How many audit entries the admin console may pull in one request.
RECENT_AUDIT_LIMIT = 40

audit = AuditChain(path=settings.audit_log_path, echo=settings.audit_log_to_console)
classifier = IntentClassifier(settings)


def client_identity(request: Request) -> str:
    """Rate-limit key.

    X-Forwarded-For is only honoured when explicitly configured, because on a
    directly exposed service the header is caller-controlled and would let
    anyone reset their own rate-limit bucket at will.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=client_identity)


@asynccontextmanager
async def lifespan(app: FastAPI):
    audit.append(
        "SERVICE_STARTED",
        {"version": __version__, "llm_mode": "live" if settings.llm_live else "mock"},
    )
    yield


app = FastAPI(
    title="Secure AI Enterprise API",
    version=__version__,
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.state.limiter = limiter


# ---------------------------------------------------------------------------
# Error handling -- responses never carry internal detail or stack traces.
# ---------------------------------------------------------------------------
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    audit.append("RATE_LIMIT_EXCEEDED", {"client": client_identity(request)})
    return JSONResponse(
        status_code=429, content={"detail": "Rate limit exceeded. Try again later."}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    audit.append("UNHANDLED_ERROR", {"error_type": type(exc).__name__})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "version": __version__,
        "llm_mode": "live" if settings.llm_live else "mock",
    }


@app.get("/", include_in_schema=False)
async def homepage() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/ask")
@limiter.limit(settings.rate_limit)
async def ask(request: Request, body: AskRequest) -> dict:
    request_id = uuid.uuid4().hex[:8]

    # --- 1. Authenticate ---------------------------------------------------
    caller = authenticate(body.api_key)
    audit.append(
        "AUTH_SUCCESS",
        {
            "request_id": request_id,
            "user": caller.user,
            "role": caller.role,
            "client": client_identity(request),
        },
    )

    # --- 2. Injection analysis --------------------------------------------
    detection = analyze(body.question, threshold=settings.injection_risk_threshold)
    if detection.blocked:
        audit.append(
            "INJECTION_BLOCKED",
            {
                "request_id": request_id,
                "user": caller.user,
                "risk_score": detection.score,
                "signals": detection.signals,
                "nearest_known_attack": detection.nearest_attack,
                "similarity": detection.similarity,
                "question_preview": body.question[:120],
            },
        )
        raise HTTPException(
            status_code=400, detail="Request blocked: potentially malicious input detected"
        )

    # --- 3. Intent classification (+ output guardrails) --------------------
    result = classifier.classify(body.question)

    if result.verdict and result.verdict.canary_leaked:
        # The model reproduced a token that only exists in the system prompt.
        audit.append(
            "CANARY_LEAKED",
            {
                "request_id": request_id,
                "user": caller.user,
                "severity": "critical",
                "findings": result.verdict.findings,
                "question_preview": body.question[:120],
            },
        )
    elif result.verdict and not result.verdict.safe:
        audit.append(
            "OUTPUT_GUARDRAIL_TRIPPED",
            {
                "request_id": request_id,
                "user": caller.user,
                "findings": result.verdict.findings,
            },
        )

    if result.degraded and result.reason != "mock_mode":
        audit.append(
            "LLM_DEGRADED",
            {"request_id": request_id, "reason": result.reason, "fallback": "keyword_classifier"},
        )

    audit.append(
        "ACTION_DETERMINED",
        {
            "request_id": request_id,
            "user": caller.user,
            "action": result.intent.action,
            "classifier": result.intent.source,
        },
    )

    # --- 4. Authorize and execute -----------------------------------------
    try:
        payload = execute(result.intent, caller, audit, request_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            audit.append(
                "AUTH_DENIED",
                {
                    "request_id": request_id,
                    "user": caller.user,
                    "role": caller.role,
                    "action": result.intent.action,
                },
            )
        raise

    audit.append("REQUEST_COMPLETED", {"request_id": request_id, "action": result.intent.action})

    return {
        "request_id": request_id,
        "user": caller.user,
        "role": caller.role,
        "classifier": result.intent.source,
        **payload,
    }


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------
class AdminRequest(BaseModel):
    api_key: str = Field(..., min_length=10)

    @field_validator("api_key")
    @classmethod
    def validate_key_format(cls, v: str) -> str:
        if not v.startswith("sk-"):
            raise ValueError("API key must start with 'sk-'")
        return v


def _require_admin(api_key: str):
    caller = authenticate(api_key)
    if caller.role != "admin":
        raise HTTPException(status_code=403, detail="This endpoint requires the admin role")
    return caller


@app.post("/admin/audit/verify")
@limiter.limit("30/minute")
async def verify_audit_chain(request: Request, body: AdminRequest) -> dict:
    """Recompute the audit hash chain and report whether it is intact."""
    _require_admin(body.api_key)

    result = AuditChain.verify(settings.audit_log_path)
    return {
        "valid": result.valid,
        "entries_checked": result.entries_checked,
        "first_bad_line": result.first_bad_line,
        "reason": result.reason,
    }


@app.post("/admin/audit/recent")
@limiter.limit("60/minute")
async def recent_audit_entries(request: Request, body: AdminRequest) -> dict:
    """Return the tail of the audit chain, newest last.

    Admin-only. The entries carry the detection internals -- risk scores, matched
    signals -- that are deliberately withheld from the caller of /ask. That
    asymmetry is the point: the defender sees why a request was blocked, the
    attacker does not.
    """
    _require_admin(body.api_key)

    tail: deque = deque(maxlen=RECENT_AUDIT_LIMIT)
    total = 0
    try:
        for _, entry in AuditChain.read(settings.audit_log_path):
            tail.append(entry)
            total += 1
    except (FileNotFoundError, json.JSONDecodeError):
        return {"entries": [], "total": 0, "truncated": False}

    return {
        "entries": list(tail),
        "total": total,
        "truncated": total > RECENT_AUDIT_LIMIT,
    }


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
