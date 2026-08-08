"""Focused oracle test: ERP invoice -> order matching must respect the
declared ``<= 10 day`` gap rule in assemble()'s _erp_match().

The graded dataset only exercises gaps in {1, 2, 3}, so a regression that
tightens the rejection threshold from ``gap > 10`` to ``gap > 3`` would slip
past the existing verifier. These tests exercise the REAL assemble()/erp-match
behavior with synthetic Raw inputs spanning the untested 4..10 band plus the
exact 10/11 boundary.
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
        created_ts=None, updated_ts=None, exposures={}, has_idp=False,
    )


def _order_obs(order_id: str, placed_ts: str, email: str, ship_name: str) -> dict:
    return {
        "type": "order", "src": "STORE", "order_id": order_id,
        "customer_id": "sc_1", "email": email, "name": "Ann Able",
        "postal": "10001", "region": "NY", "ship_name": ship_name,
        "ship_line1": "1 Main St", "ship_city": "New York",
        "ship_region": "NY", "ship_postal": "10001", "ship_country": "US",
        "placed": placed_ts, "status": "Completed", "channel": "Online",
        "subtotal_cents": 1000, "tax_cents": 100, "shipping_cents": 0,
        "total_cents": 1100, "currency": "USD",
    }


def _invoice_obs(invoice_no: str, issue_date: str, bill_to: str) -> dict:
    return {
        "type": "erp_invoice", "src": "ERP", "invoice_no": invoice_no,
        "bill_to": bill_to, "total_cents": 1100, "currency": "USD",
        "issue": issue_date,
    }


def _payment_obs(txn_id: str, order_ref: str, email: str) -> dict:
    return {
        "type": "payment", "src": "PAY", "txn_id": txn_id,
        "customer_ref": "cus_1", "order_ref": order_ref,
        "paid": "2024-01-22T12:00:00Z", "amount_cents": 1100,
        "currency": "USD", "method": "Card", "status": "succeeded",
    }


def _run(placed_ts: str, issue_date: str, rules) -> tuple:
    email = "a@x.com"
    raw = Raw(
        orders=[_order_obs("or_aaa", placed_ts, email, "Acme Corp")],
        erp_invoices=[_invoice_obs("inv_aaaa", issue_date, "Acme Corp")],
        payments=[_payment_obs("txn_1", "inv_aaaa", email)],
    )
    cust_map = {email: 7}
    stats = Stats()
    orders, payments = assemble(raw, [_customer(email)], cust_map, rules, stats)
    return orders, payments, stats


def test_erp_match_resolves_seven_day_gap(rules):
    """A 7-day gap (inside 4..10) must still resolve invoice -> order.

    Regression guard: mutating the rejection threshold from ``gap > 10`` to
    ``gap > 3`` makes this 7-day match disappear, and the ERP-referenced
    payment is dropped instead of being kept.
    """
    orders, payments, stats = _run(
        placed_ts="2024-01-15T10:00:00Z",
        issue_date="2024-01-22T00:00:00Z",
        rules=rules,
    )
    assert len(orders) == 1
    assert len(payments) == 1, "ERP-referenced payment must survive resolution"
    assert stats.pay_dropped == 0


def test_erp_match_accepts_exactly_ten_day_gap(rules):
    """A 10-day gap is the inclusive upper bound and must be accepted."""
    orders, payments, stats = _run(
        placed_ts="2024-01-15T10:00:00Z",
        issue_date="2024-01-25T00:00:00Z",
        rules=rules,
    )
    assert len(orders) == 1
    assert len(payments) == 1
    assert stats.pay_dropped == 0


def test_erp_match_rejects_eleven_day_gap(rules):
    """An 11-day gap exceeds the <=10 rule and must be rejected."""
    orders, payments, stats = _run(
        placed_ts="2024-01-15T10:00:00Z",
        issue_date="2024-01-26T00:00:00Z",
        rules=rules,
    )
    assert len(orders) == 1
    assert len(payments) == 0
    assert stats.pay_dropped == 1
