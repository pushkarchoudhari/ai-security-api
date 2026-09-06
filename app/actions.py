"""Business actions and the authorization dispatcher.

The security-relevant property of this module: the model proposes an action, but
`execute` is what decides whether it runs. Authorization happens here, on the
server, immediately before the effect -- never by trusting the classifier.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.models import Intent
from app.security.audit import AuditChain
from app.security.guardrails import sanitize_params
from app.security.keystore import KeyRecord
from app.security.rbac import authorize

MIN_LOOKUP_CHARS = 2

EMPLOYEES: list[dict[str, str]] = [
    {"id": "E001", "name": "Priya Sharma", "department": "Engineering",
     "email": "priya@company.com"},
    {"id": "E002", "name": "Rahul Mehta", "department": "Sales", "email": "rahul@company.com"},
    {"id": "E003", "name": "Ananya Iyer", "department": "Marketing", "email": "ananya@company.com"},
    {"id": "E004", "name": "Vikram Singh", "department": "Engineering",
     "email": "vikram@company.com"},
]

SALES_DATA: list[dict[str, Any]] = [
    {"week": "2026-W27", "region": "North", "revenue": 125000, "deals_closed": 8},
    {"week": "2026-W27", "region": "South", "revenue": 98000, "deals_closed": 5},
    {"week": "2026-W27", "region": "East", "revenue": 110000, "deals_closed": 7},
    {"week": "2026-W27", "region": "West", "revenue": 87000, "deals_closed": 4},
]

TICKETS: list[dict[str, Any]] = []


def create_ticket(subject: str, user: str) -> dict[str, Any]:
    ticket = {
        "ticket_id": f"TKT-{uuid.uuid4().hex[:6].upper()}",
        "subject": subject,
        "created_by": user,
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    TICKETS.append(ticket)
    return ticket


def generate_report() -> dict[str, Any]:
    return {
        "report_title": "Weekly Sales Report - 2026-W27",
        "total_revenue": sum(s["revenue"] for s in SALES_DATA),
        "total_deals_closed": sum(s["deals_closed"] for s in SALES_DATA),
        "breakdown": SALES_DATA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def lookup_employee(query: str) -> list[dict[str, str]]:
    """Substring search over the directory.

    An empty or near-empty query is rejected rather than treated as "match all".
    `"" in anything` is True, so without this guard a vague question would return
    the entire directory -- including email addresses -- to the lowest-privilege
    role that can reach this action.
    """
    needle = query.strip().lower()
    if len(needle) < MIN_LOOKUP_CHARS:
        raise HTTPException(
            status_code=422,
            detail="Employee lookup needs a name, department, or ID of at least "
            f"{MIN_LOOKUP_CHARS} characters.",
        )
    return [
        e
        for e in EMPLOYEES
        if needle in e["name"].lower()
        or needle in e["department"].lower()
        or needle in e["id"].lower()
    ]


def execute(
    intent: Intent,
    caller: KeyRecord,
    audit: AuditChain,
    request_id: str,
) -> dict[str, Any]:
    params = sanitize_params(intent.params)
    action = intent.action

    if action == "create_ticket":
        authorize(caller.role, "create_ticket")
        subject = params.get("subject") or "General support request"
        ticket = create_ticket(subject, caller.user)
        audit.append(
            "TICKET_CREATED",
            {"request_id": request_id, "user": caller.user, "ticket_id": ticket["ticket_id"]},
        )
        return {"message": "Support ticket created", "ticket": ticket}

    if action == "generate_report":
        authorize(caller.role, "generate_report")
        audit.append("REPORT_GENERATED", {"request_id": request_id, "user": caller.user})
        return {"message": "Sales report generated", "report": generate_report()}

    if action == "lookup_employee":
        authorize(caller.role, "lookup_employee")
        query = params.get("query", "")
        results = lookup_employee(query)
        audit.append(
            "EMPLOYEE_LOOKUP",
            {
                "request_id": request_id,
                "user": caller.user,
                "query": query,
                "results_count": len(results),
            },
        )
        return {"message": f"Found {len(results)} employee(s)", "employees": results}

    return {
        "message": (
            "I can help with creating tickets, generating reports, and looking up "
            "employees. Please ask about one of these."
        )
    }
