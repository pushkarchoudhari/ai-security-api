"""Tamper-evident audit log.

Every entry embeds the SHA-256 digest of the entry before it, forming a hash
chain. Editing, reordering, or deleting any historical entry invalidates every
digest from that point forward, so tampering is detectable without needing a
second copy of the log.

This is deliberately *detective*, not preventive: an attacker with write access
can still destroy the file. What they cannot do is quietly rewrite one line.

Verify a log from the command line:

    python -m app.security.audit audit.log
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64

# Audit details are attacker-influenced (question previews, upstream error
# strings). Cap them so a single event cannot flood the log.
MAX_FIELD_CHARS = 300


def _canonical(payload: dict[str, Any]) -> str:
    """Deterministic serialisation. Key order must not affect the digest."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def digest(entry: dict[str, Any]) -> str:
    """SHA-256 over every field except the digest itself."""
    payload = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"...[+{len(value) - MAX_FIELD_CHARS} chars]"
    return value


@dataclass
class ChainVerification:
    valid: bool
    entries_checked: int
    first_bad_line: int | None = None
    reason: str | None = None

    def __str__(self) -> str:  # pragma: no cover - presentation only
        if self.valid:
            return f"OK: {self.entries_checked} entries, chain intact"
        return f"BROKEN at line {self.first_bad_line}: {self.reason}"


@dataclass
class AuditChain:
    """Append-only, hash-chained event log."""

    path: Path
    echo: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _head: str = field(default=GENESIS_HASH, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._head = self._recover_head()

    def _recover_head(self) -> str:
        """Resume the chain across restarts by reading the last entry's digest."""
        if not self.path.exists():
            return GENESIS_HASH
        last = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return GENESIS_HASH
        try:
            return str(json.loads(last)["hash"])
        except (json.JSONDecodeError, KeyError, TypeError):
            # A pre-existing non-chained log: start a fresh chain rather than
            # silently pretending the older entries were verified.
            return GENESIS_HASH

    def append(self, event: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{k: _truncate(v) for k, v in (details or {}).items()},
        }
        with self._lock:
            entry["prev_hash"] = self._head
            entry["hash"] = digest(entry)
            line = json.dumps(entry, default=str)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._head = entry["hash"]
        if self.echo:
            print(line, file=sys.stderr)
        return entry

    # -- verification ------------------------------------------------------
    @staticmethod
    def read(path: Path | str) -> Iterator[tuple[int, dict[str, Any]]]:
        with Path(path).open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if line.strip():
                    yield lineno, json.loads(line)

    @classmethod
    def verify(cls, path: Path | str) -> ChainVerification:
        expected_prev = GENESIS_HASH
        checked = 0
        try:
            for lineno, entry in cls.read(path):
                if entry.get("prev_hash") != expected_prev:
                    return ChainVerification(
                        False, checked, lineno, "prev_hash does not match preceding entry"
                    )
                recomputed = digest(entry)
                if recomputed != entry.get("hash"):
                    return ChainVerification(
                        False, checked, lineno, "entry content does not match its digest"
                    )
                expected_prev = str(entry["hash"])
                checked += 1
        except json.JSONDecodeError as exc:
            return ChainVerification(False, checked, checked + 1, f"malformed JSON: {exc.msg}")
        except FileNotFoundError:
            return ChainVerification(False, 0, None, "log file not found")
        return ChainVerification(True, checked)


if __name__ == "__main__":  # pragma: no cover
    target = sys.argv[1] if len(sys.argv) > 1 else "audit.log"
    result = AuditChain.verify(target)
    print(result)
    sys.exit(0 if result.valid else 1)
