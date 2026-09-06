"""Deterministic keyword intent classifier.

Serves two roles: the graceful-degradation path when Gemini is unavailable, and
the entire classifier in mock mode. Because it is deterministic and offline, it
is also what makes the CI red-team and eval gates runnable without a network or
an API budget.
"""

from __future__ import annotations

import re

from app.models import Intent

_TICKET_KW = ("ticket", "support", "issue", "bug", "complaint", "broken", "not working", "raise a")
_REPORT_KW = (
    "report", "sales", "revenue", "analytics", "summary", "numbers", "figures",
    "performance", "deals", "closed", "quarter",
)
_EMPLOYEE_KW = (
    "employee", "find", "lookup", "look up", "search", "who is", "staff",
    "colleague", "contact",
)

_SUBJECT_MARKERS = ("for ", "about ", "regarding ", "because ", "saying ")
_QUERY_MARKERS = ("employee ", "find ", "search for ", "search ", "lookup ", "look up ", "who is ")

# Words that survive marker-stripping but carry no search value. A query built
# only from these is a request for the whole directory, not for a person.
_EMPTY_QUERY_TOKENS = {
    "all", "everyone", "everybody", "anyone", "any", "employees", "employee",
    "staff", "people", "person", "team", "them", "us", "list", "directory",
    "the", "a", "an", "our", "my", "me", "everything", "names", "name",
    "details", "info", "information", "records", "contacts", "contact",
}


def _after_marker(question: str, markers: tuple[str, ...]) -> str | None:
    lowered = question.lower()
    for marker in markers:
        idx = lowered.find(marker)
        if idx != -1:
            return question[idx + len(marker) :].strip(" ?.!,")
    return None


def classify(question: str) -> Intent:
    q = question.lower()

    if any(kw in q for kw in _TICKET_KW):
        subject = _after_marker(question, _SUBJECT_MARKERS) or question.strip()
        return Intent(action="create_ticket", params={"subject": subject}, source="keyword")

    if any(kw in q for kw in _REPORT_KW):
        return Intent(action="generate_report", params={}, source="keyword")

    if any(kw in q for kw in _EMPLOYEE_KW):
        query = _after_marker(question, _QUERY_MARKERS)
        if query is None:
            query = re.sub(r"\b(find|search|lookup|look up|show|get|me|the|a|an)\b", " ", q).strip()
        # A bare "list all employees" must not become an empty query -- an empty
        # query matches every record and would dump the whole directory. Checked
        # per token so multi-word filler ("all employees", "the team") is caught
        # as well as single words.
        tokens = {t for t in re.split(r"\W+", query.lower()) if t}
        if not tokens or tokens <= _EMPTY_QUERY_TOKENS:
            return Intent(action="general_answer", params={}, source="keyword")
        return Intent(action="lookup_employee", params={"query": query}, source="keyword")

    return Intent(action="general_answer", params={}, source="keyword")
