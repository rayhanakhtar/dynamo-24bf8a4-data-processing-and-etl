"""Focused oracle test: Payment fallback linkage 30-minute boundary.

The dictionary specifies max_gap_minutes = 30 for fallback linkage.
The boundary is inclusive: <= 30 minutes should match, > 30 minutes should not.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from etl.assemble import assemble  # noqa: E402
from etl.model import Customer  # noqa: E402
from etl.parse import Raw, Stats  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402


@pytest.fixture(scope="session")
def rules():
    return load_rules(str(ROOT / "config" / "dictionary.toml"))


def _customer(email: str, customer_key: int = 7) -> Customer:
    return Customer(
        customer_key=customer_key, gid=f"CUST{customer_key:06d}",
        first_name="Ann", last_name="Able", dob=None, gender=None,
        email=email, phone=None, line1=None, city=None, region=None,
        postal=None, country=None, is_business=False, company=None,
        household=None, loyalty_tier=None, is_active=True,
        created_ts=None, updated_ts=None,
        exposures={}, has_idp=False,
    )


def _order_obs(order_id: str, placed_ts: str, email: str) -> dict:
    return {
        "type": "order", "src": "STORE", "order_id": order_id,
        "customer_id": "sc_1", "email": email,
        "name": "Ann Able", "postal": "10001", "region": "NY",
        "ship_name": "Ann Able", "ship_line1": "1 Main St",
        "ship_city": "New York", "ship_region": "NY",
        "ship_postal": "10001", "ship_country": "US",
        "placed": placed_ts, "status": "Completed", "channel": "Online",
        "subtotal_cents": 1000, "tax_cents": 100, "shipping_cents": 0,
        "total_cents": 1100, "currency": "USD",
    }


def _payment_obs(txn_id: str, order_ref: str, paid_ts: str,
                 amount_cents: int = 1100, currency: str = "USD") -> dict:
    return {
        "type": "payment", "src": "PAY", "txn_id": txn_id,
        "customer_ref": "cus_1", "order_ref": order_ref,
        "paid": paid_ts, "amount_cents": amount_cents,
        "currency": currency, "method": "Card", "status": "succeeded",
    }


def _make_raw(placed_ts: str, paid_ts: str) -> tuple:
    """Create a Raw object with one order and one fallback payment."""
    email = "a@x.com"
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_aaa",
            "customer_id": "sc_1", "email": email,
            "name": "Ann Able", "postal": "10001", "region": "NY",
            "ship_name": "Ann Able", "ship_line1": "1 Main St",
            "ship_city": "New York", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": placed_ts, "status": "Completed", "channel": "Online",
            "subtotal_cents": 1000, "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        payments=[{
            "type": "payment", "src": "PAY", "txn_id": "txn_1",
            "customer_ref": "cus_1", "order_ref": "",  # empty -> fallback
            "paid": paid_ts, "amount_cents": 1100, "currency": "USD",
            "method": "Card", "status": "succeeded",
        }],
    )
    cust_map = {email: 7}
    stats = Stats()
    return raw, [_customer(email)], cust_map, stats


def test_fallback_29_minutes_accepted(rules):
    """29:59 gap (within 30 min) should be accepted."""
    raw, customers, cust_map, stats = _make_raw(
        placed_ts="2024-01-15T10:00:00Z",
        paid_ts="2024-01-15T10:29:59Z",  # 29:59 after placed
    )
    orders, payments = assemble(raw, customers, cust_map, rules, stats)
    assert len(orders) == 1
    assert len(payments) == 1
    assert stats.pay_dropped == 0


def test_fallback_30_minutes_accepted(rules):
    """Exactly 30:00 gap (boundary) should be accepted."""
    raw, customers, cust_map, stats = _make_raw(
        placed_ts="2024-01-15T10:00:00Z",
        paid_ts="2024-01-15T10:30:00Z",  # exactly 30:00
    )
    orders, payments = assemble(raw, customers, cust_map, rules, stats)
    assert len(orders) == 1
    assert len(payments) == 1
    assert stats.pay_dropped == 0


def test_fallback_30_minutes_01_seconds_rejected(rules):
    """30:01 gap (just over 30 min) should be rejected."""
    raw, customers, cust_map, stats = _make_raw(
        placed_ts="2024-01-15T10:00:00Z",
        paid_ts="2024-01-15T10:30:01Z",  # 30:01 after placed
    )
    orders, payments = assemble(raw, customers, cust_map, rules, stats)
    assert len(orders) == 1
    assert len(payments) == 0
    assert stats.pay_dropped == 1


def test_fallback_31_minutes_rejected(rules):
    """31 minutes gap (clearly over) should be rejected."""
    raw, customers, cust_map, stats = _make_raw(
        placed_ts="2024-01-15T10:00:00Z",
        paid_ts="2024-01-15T10:31:00Z",
    )
    orders, payments = assemble(raw, customers, cust_map, rules, stats)
    assert len(orders) == 1
    assert len(payments) == 0
    assert stats.pay_dropped == 1