"""API key authentication."""

from __future__ import annotations

from fastapi import HTTPException

from app.security.keystore import KeyRecord, lookup


def authenticate(api_key: str) -> KeyRecord:
    """Resolve an API key to its user record.

    401 for an unknown key, 403 for a known-but-deactivated one. The distinction
    is intentional and safe: confirming that a *revoked* key was once valid tells
    an attacker nothing they could not learn by presenting it before revocation,
    and it makes deactivation debuggable in the audit trail.
    """
    record = lookup(api_key)
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not record.active:
        raise HTTPException(status_code=403, detail="API key has been deactivated")
    return record
