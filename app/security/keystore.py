"""API key store.

Keys are stored as SHA-256 digests only -- the plaintext values do not appear in
this source file. Lookup hashes the presented key and compares digests, so a
read-only leak of this module (or of the database that would replace it in
production) does not yield usable credentials.

Why SHA-256 rather than bcrypt/argon2: those are deliberately slow to make
*low-entropy* human passwords expensive to brute force. An API key is 128+ bits
of machine-generated entropy, so there is no dictionary to run and the slow KDF
would only add latency to every request. Fast digest + high entropy is the
correct trade-off here.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KeyRecord:
    user: str
    role: str
    active: bool


# Digest -> record. Demo credentials are documented in the README and the test
# UI; only their hashes live here.
_KEY_DIGESTS: dict[str, KeyRecord] = {
    "dc0d24f901f5f1ecb5482ba3ba0345710290e1bed740584eabd5cc4b7db1933c": KeyRecord(
        user="alice", role="admin", active=True
    ),
    "a7b95677fdb5639bc03575336f00a3832ca408c0b3ab290f4ac41a974cef071f": KeyRecord(
        user="bob", role="analyst", active=True
    ),
    "6d89f6299f2d86e2d94ede8c3700298e6bc11aafca0f5cf88f3519bbe53c0d1f": KeyRecord(
        user="charlie", role="viewer", active=True
    ),
    "dc74b17e033f663dfb9e85283c747b483428da8358b3842639caa01e0b540d7c": KeyRecord(
        user="dave", role="admin", active=False
    ),
}


def lookup(api_key: str) -> KeyRecord | None:
    """Constant-time-ish lookup of a presented key.

    The dict lookup itself is not constant time, but it is keyed on the digest
    rather than the secret, so timing reveals nothing about the plaintext. The
    compare_digest call guards the final confirmation.
    """
    presented = hash_key(api_key)
    for stored, record in _KEY_DIGESTS.items():
        if secrets.compare_digest(presented, stored):
            return record
    return None
