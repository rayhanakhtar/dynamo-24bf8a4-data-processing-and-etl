"""Focused oracle test: IDP status -> is_active mapping.

The parser normalizes IDP status using name normalization (title case),
then maps 'active' -> true, everything else -> false.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from etl.parse import Raw, _parse_idp  # noqa: E402
from etl.normalize import Normalizer  # noqa: E402
from etl.link import link  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402


def _load_rules():
    return load_rules(str(ROOT / "config" / "dictionary.toml"))


def _run_full_link(idp_rows: list[dict]) -> list:
    """Run the full parse + link pipeline on IDP rows."""
    rules = _load_rules()
    raw = Raw()
    nm = Normalizer(rules)
    _parse_idp(idp_rows, nm, raw)
    customers, _, _ = link(raw, rules)
    return customers


def test_idp_active_status_maps_to_true():
    """Normalized status 'active' -> is_active = true."""
    customers = _run_full_link([{
        "sub": "idp_1",
        "email": "a@x.com", "phone": None,
        "given_name": "Ann", "family_name": "Able",
        "dob": None, "gender": None, "household": None,
        "created_ts": "2024-01-01T00:00:00Z", "updated_ts": "2024-01-15T00:00:00Z",
        "status": "active", "email_verified": None, "profile_completeness": None,
    }])
    assert len(customers) == 1
    assert customers[0].is_active is True


def test_idp_inactive_status_maps_to_false():
    """Normalized status 'inactive' -> is_active = false."""
    customers = _run_full_link([{
        "sub": "idp_2",
        "email": "b@x.com", "phone": None,
        "given_name": "Bob", "family_name": "Baker",
        "dob": None, "gender": None, "household": None,
        "created_ts": "2024-01-01T00:00:00Z", "updated_ts": "2024-01-15T00:00:00Z",
        "status": "inactive", "email_verified": None, "profile_completeness": None,
    }])
    assert len(customers) == 1
    assert customers[0].is_active is False


def test_idp_marked_for_deletion_maps_to_false():
    """Normalized status 'marked_for_deletion' -> is_active = false."""
    customers = _run_full_link([{
        "sub": "idp_3",
        "email": "c@x.com", "phone": None,
        "given_name": "Carol", "family_name": "Carter",
        "dob": None, "gender": None, "household": None,
        "created_ts": "2024-01-01T00:00:00Z", "updated_ts": "2024-01-15T00:00:00Z",
        "status": "marked_for_deletion", "email_verified": None, "profile_completeness": None,
    }])
    assert len(customers) == 1
    assert customers[0].is_active is False


def test_idp_random_status_maps_to_false():
    """Any non-active normalized status -> is_active = false."""
    customers = _run_full_link([{
        "sub": "idp_4",
        "email": "d@x.com", "phone": None,
        "given_name": "Dave", "family_name": "Davis",
        "dob": None, "gender": None, "household": None,
        "created_ts": "2024-01-01T00:00:00Z", "updated_ts": "2024-01-15T00:00:00Z",
        "status": "suspended", "email_verified": None, "profile_completeness": None,
    }])
    assert len(customers) == 1
    assert customers[0].is_active is False


def test_idp_status_case_insensitive_after_normalization():
    """Status normalization applies title case: 'ACTIVE' -> 'Active' -> true."""
    customers = _run_full_link([{
        "sub": "idp_5",
        "email": "e@x.com", "phone": None,
        "given_name": "Eve", "family_name": "Evans",
        "dob": None, "gender": None, "household": None,
        "created_ts": "2024-01-01T00:00:00Z", "updated_ts": "2024-01-15T00:00:00Z",
        "status": "ACTIVE", "email_verified": None, "profile_completeness": None,
    }])
    assert len(customers) == 1
    assert customers[0].is_active is True


def test_idp_status_whitespace_trimmed():
    """Status whitespace is trimmed before normalization."""
    customers = _run_full_link([{
        "sub": "idp_6",
        "email": "f@x.com", "phone": None,
        "given_name": "Frank", "family_name": "Frank",
        "dob": None, "gender": None, "household": None,
        "created_ts": "2024-01-01T00:00:00Z", "updated_ts": "2024-01-15T00:00:00Z",
        "status": "  active  ", "email_verified": None, "profile_completeness": None,
    }])
    assert len(customers) == 1
    assert customers[0].is_active is True