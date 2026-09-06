"""Tamper-evidence tests for the hash-chained audit log."""

from __future__ import annotations

import json

import pytest

from app.security.audit import GENESIS_HASH, AuditChain, digest


@pytest.fixture
def chain(tmp_path):
    return AuditChain(path=tmp_path / "audit.log")


def test_first_entry_links_to_genesis(chain):
    entry = chain.append("SERVICE_STARTED", {"version": "test"})
    assert entry["prev_hash"] == GENESIS_HASH
    assert entry["hash"] == digest(entry)


def test_entries_form_a_chain(chain):
    first = chain.append("A", {"n": 1})
    second = chain.append("B", {"n": 2})
    third = chain.append("C", {"n": 3})

    assert second["prev_hash"] == first["hash"]
    assert third["prev_hash"] == second["hash"]
    assert AuditChain.verify(chain.path).valid


def test_verify_reports_entry_count(chain):
    for i in range(5):
        chain.append("EVENT", {"i": i})
    result = AuditChain.verify(chain.path)
    assert result.valid
    assert result.entries_checked == 5


def test_editing_an_entry_breaks_the_chain(chain):
    chain.append("AUTH_SUCCESS", {"user": "alice", "role": "admin"})
    chain.append("EMPLOYEE_LOOKUP", {"user": "charlie", "results_count": 1})
    chain.append("REQUEST_COMPLETED", {"action": "lookup_employee"})

    lines = chain.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["user"] = "mallory"  # privilege laundering: rewrite who did it
    lines[1] = json.dumps(tampered)
    chain.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = AuditChain.verify(chain.path)
    assert not result.valid
    assert result.first_bad_line == 2
    assert "digest" in (result.reason or "")


def test_deleting_an_entry_breaks_the_chain(chain):
    for i in range(4):
        chain.append("EVENT", {"i": i})

    lines = chain.path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # excise the inconvenient event
    chain.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = AuditChain.verify(chain.path)
    assert not result.valid
    assert result.first_bad_line == 2


def test_reordering_entries_breaks_the_chain(chain):
    for i in range(3):
        chain.append("EVENT", {"i": i})

    lines = chain.path.read_text(encoding="utf-8").splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    chain.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert not AuditChain.verify(chain.path).valid


def test_appending_a_forged_entry_breaks_the_chain(chain):
    chain.append("EVENT", {"i": 0})
    forged = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "event": "TICKET_CREATED",
        "user": "mallory",
    }
    forged["prev_hash"] = GENESIS_HASH
    forged["hash"] = digest(forged)  # internally consistent, but wrong link
    with chain.path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(forged) + "\n")

    result = AuditChain.verify(chain.path)
    assert not result.valid
    assert "prev_hash" in (result.reason or "")


def test_chain_resumes_across_restarts(tmp_path):
    path = tmp_path / "audit.log"
    first = AuditChain(path=path)
    first.append("EVENT", {"i": 0})
    last = first.append("EVENT", {"i": 1})

    reopened = AuditChain(path=path)
    resumed = reopened.append("EVENT", {"i": 2})

    assert resumed["prev_hash"] == last["hash"]
    assert AuditChain.verify(path).valid


def test_digest_is_order_independent():
    a = {"timestamp": "t", "event": "E", "x": 1, "y": 2}
    b = {"y": 2, "event": "E", "x": 1, "timestamp": "t"}
    assert digest(a) == digest(b)


def test_long_fields_are_truncated(chain):
    entry = chain.append("UNHANDLED_ERROR", {"error": "x" * 5000})
    assert len(entry["error"]) < 400
    assert AuditChain.verify(chain.path).valid


def test_verify_on_missing_file(tmp_path):
    result = AuditChain.verify(tmp_path / "nope.log")
    assert not result.valid
    assert result.reason == "log file not found"


def test_no_duplicate_entries_written(chain):
    """Regression: the previous logging.Logger setup attached two FileHandlers
    to the same global logger, writing every event to disk twice."""
    for i in range(10):
        chain.append("EVENT", {"i": i})
    lines = [ln for ln in chain.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 10
    assert len({json.loads(ln)["hash"] for ln in lines}) == 10
