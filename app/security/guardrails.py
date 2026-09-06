"""Output-side guardrails.

Input filtering alone is half a control. Everything here runs on what the model
*returns*, before that output is trusted or shown to a caller.

Two jobs:

  * Canary detection. A unique token is planted in the system prompt. The model
    is never asked to emit it and no legitimate classification contains it, so
    if it ever appears in output we have positive evidence that the system
    prompt leaked -- a signal that is otherwise very hard to observe.

  * Parameter sanitisation. Model output is untrusted input to the business
    layer. `params` reaches ticket subjects and employee queries, so it is
    clamped and stripped here rather than being passed through verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_PARAM_CHARS = 200

# Shapes that should never appear in a classifier response.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("demo_api_key", re.compile(r"\bsk-[a-z]+-\d{6}\b", re.IGNORECASE)),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class OutputVerdict:
    safe: bool
    canary_leaked: bool = False
    findings: list[str] = field(default_factory=list)


def scan_model_output(raw: str, canary: str) -> OutputVerdict:
    findings: list[str] = []

    canary_leaked = bool(canary) and canary in raw
    if canary_leaked:
        findings.append("canary_token_in_output")

    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(raw):
            findings.append(f"secret_shaped:{name}")

    return OutputVerdict(safe=not findings, canary_leaked=canary_leaked, findings=findings)


def redact(text: str) -> str:
    """Mask secret-shaped substrings before they reach a log or a response."""
    for _, pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def sanitize_params(params: dict) -> dict[str, str]:
    """Clamp model-proposed parameters to safe, bounded strings.

    Non-string values are coerced, control characters removed, length capped.
    Keys outside the known set are dropped -- the model does not get to invent
    parameters that downstream code might one day read.
    """
    allowed = {"subject", "query"}
    clean: dict[str, str] = {}
    for key, value in (params or {}).items():
        if key not in allowed:
            continue
        text = value if isinstance(value, str) else str(value)
        text = _CONTROL_CHARS.sub("", text).strip()
        clean[key] = text[:MAX_PARAM_CHARS]
    return clean
