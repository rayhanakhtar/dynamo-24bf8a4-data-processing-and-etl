"""Deterministic PRNG for the hidden dataset generator.

Every piece of randomness in the benchmark is a PURE FUNCTION of a per-record key
and a fixed master seed, so regeneration is byte-stable regardless of iteration
order or code path. There is NO global random state anywhere in the generator.

This module is PRIVATE: it ships only with `generator/` (→ `tests/`), never with
`environment/`. The seed is never written into any agent-visible file.
"""

from __future__ import annotations

import hashlib
import random

# Fixed, hard-coded master seed. Changing it produces a different (but still
# fully deterministic) dataset instance. It lives only here.
MASTER_SEED = "northstar-commerce-master-seed-24bf8a4"

# Pre-computed derived seed bytes (avoids re-hashing on every call).
_SEED_BYTES = hashlib.sha256(MASTER_SEED.encode("utf-8")).digest()


def _digest16(key: str) -> bytes:
    """16 deterministic bytes derived from a record key + the master seed."""
    if not isinstance(key, str):
        key = str(key)
    return hashlib.sha256(_SEED_BYTES + key.encode("utf-8")).digest()[:16]


def prng(key: str) -> random.Random:
    """Return an independent, reproducible Random for a given record key."""
    return random.Random(_digest16(key))


def hash_hex(key: str, prefix: str, length: int) -> str:
    """Deterministic hex identifier `prefix` + `length` hex chars for a key."""
    raw = _digest16(key).hex()
    return f"{prefix}{raw[:length]}"


def hash_int(key: str, max_exclusive: int) -> int:
    """Deterministic integer in [0, max_exclusive) for a key."""
    return int.from_bytes(_digest16(key), "big") % max_exclusive


def numeric_id(key: str, length: int) -> str:
    """Deterministic zero-padded numeric id of `length` digits (no prefix)."""
    return str(hash_int(key, 10**length)).zfill(length)
