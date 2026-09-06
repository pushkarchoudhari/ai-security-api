"""Role-based access control.

Authorization is enforced server-side, immediately before an action executes --
never by trusting what the model proposed. The LLM decides *intent*; this module
decides *permission*. See docs/THREAT_MODEL.md.
"""

from __future__ import annotations

from fastapi import HTTPException

# Actions that require an explicit grant. "general_answer" is deliberately
# absent: it touches no business data and is available to any authenticated
# caller.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"create_ticket", "generate_report", "lookup_employee"}),
    "analyst": frozenset({"generate_report", "lookup_employee"}),
    "viewer": frozenset({"lookup_employee"}),
}


def is_allowed(role: str, action: str) -> bool:
    return action in ROLE_PERMISSIONS.get(role, frozenset())


def authorize(role: str, action: str) -> None:
    """Raise 403 unless the role holds the permission.

    Unknown roles fail closed -- an unrecognised role has no permissions rather
    than defaulting to any baseline access.
    """
    if not is_allowed(role, action):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' does not have permission for '{action}'",
        )
