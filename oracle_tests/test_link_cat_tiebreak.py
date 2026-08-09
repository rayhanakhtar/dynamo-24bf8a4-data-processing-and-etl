"""Focused oracle test: CAT same-priority tie-break with lowest cust_no wins.

The implementation uses `_pick()` which sorts by (priority, rowid) where
numeric row IDs are compared numerically (lower wins) and non-numeric
lexicographically. For CAT records with same priority, the lowest cust_no wins.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from etl.link import link  # noqa: E402
from etl.model import Customer  # noqa: E402
from etl.parse import Raw  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402


@pytest.fixture(scope="session")
def rules():
    return load_rules(str(ROOT / "config" / "dictionary.toml"))


def _cat(cust_no: str, **kw):
    base = {"type": "cat", "src": "CAT", "cust_no": cust_no, "email": None,
            "first_name": None, "last_name": None, "name": None, "phone": None,
            "line1": None, "city": None, "region": None, "postal": None,
            "country": None, "loyalty_tier": None, "created": None}
    base.update(kw)
    return base


# The tie-break applies only to records that resolve to the same canonical
# customer. The two CAT records below share a phone, which is a strong
# entity-matching attribute (dictionary [entity_matching]), so they merge into
# one customer; the conflicting attributes are then resolved by lowest cust_no.
SHARED_PHONE = "+15551234567"


def test_cat_same_priority_tie_break_lowest_cust_no_wins_email(rules):
    """Two CAT records with same priority, conflicting email: lowest cust_no wins."""
    raw = Raw(cat_rows=[
        _cat("00100", email="first@example.com", first_name="John", last_name="Doe", phone=SHARED_PHONE),
        _cat("00050", email="second@example.com", first_name="Jane", last_name="Smith", phone=SHARED_PHONE),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1
    # cust_no 00050 is lower, its email should win
    assert customers[0].email == "second@example.com"


def test_cat_same_priority_tie_break_lowest_cust_no_wins_first_name(rules):
    """Two CAT records with same priority, conflicting first_name: lowest cust_no wins."""
    raw = Raw(cat_rows=[
        _cat("00200", first_name="First", last_name="A", email="a@example.com", phone=SHARED_PHONE),
        _cat("00100", first_name="Second", last_name="B", email="b@example.com", phone=SHARED_PHONE),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1
    # cust_no 00100 is lower, its first_name should win
    assert customers[0].first_name == "Second"


def test_cat_same_priority_tie_break_order_independent(rules):
    """Result must be deterministic regardless of input order."""
    # Order 1: higher cust_no first
    cust1, _, _ = link(Raw(cat_rows=[
        _cat("00200", email="first@example.com", phone=SHARED_PHONE),
        _cat("00100", email="second@example.com", phone=SHARED_PHONE),
    ]), rules)

    # Order 2: lower cust_no first
    cust2, _, _ = link(Raw(cat_rows=[
        _cat("00100", email="second@example.com", phone=SHARED_PHONE),
        _cat("00200", email="first@example.com", phone=SHARED_PHONE),
    ]), rules)

    assert len(cust1) == len(cust2) == 1
    assert cust1[0].email == cust2[0].email == "second@example.com"


def test_cat_same_priority_tie_break_numeric_comparison(rules):
    """Numeric comparison: 00009 < 00010 (not lexicographic where '9' > '10')."""
    raw = Raw(cat_rows=[
        _cat("00009", email="nine@example.com", phone=SHARED_PHONE),
        _cat("00010", email="ten@example.com", phone=SHARED_PHONE),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1
    assert customers[0].email == "nine@example.com"  # 9 < 10 numerically


def test_cat_same_priority_tie_break_different_attributes_merged(rules):
    """Different attributes from lower cust_no should win when conflict."""
    raw = Raw(cat_rows=[
        _cat("00200", first_name="Alice", last_name="Smith", email="alice@example.com", phone=SHARED_PHONE),
        _cat("00100", first_name="Bob", last_name="Jones", email="bob@example.com", phone=SHARED_PHONE),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1
    # cust_no 00100 wins all conflicts
    assert customers[0].first_name == "Bob"
    assert customers[0].last_name == "Jones"
    assert customers[0].email == "bob@example.com"