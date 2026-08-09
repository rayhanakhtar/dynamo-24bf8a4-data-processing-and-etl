"""Focused oracle test: foreign_keys_repaired metric counts SUP tickets correctly.

The dictionary defines this metric as:
"foreign_keys_repaired: support tickets whose order_ref, after uppercasing,
starts with OR-, the tail is lowercased and prepended with 'or_', and that
or_ value exists in STORE orders."

This test independently verifies the counting logic against synthetic data.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from etl.parse import Raw, Stats  # noqa: E402
from etl.link import link  # noqa: E402
from etl.assemble import assemble  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402
from etl.quality import metrics  # noqa: E402


@pytest.fixture(scope="session")
def rules():
    return load_rules(str(ROOT / "config" / "dictionary.toml"))


def _run_full_pipeline(raw: Raw, rules):
    """Run the full link + assemble + metrics pipeline on a synthetic Raw."""
    customers, distinct_identities, cust_map = link(raw, rules)
    orders, payments = assemble(raw, customers, cust_map, rules, raw.stats)
    from etl.link import history
    hist = history(customers)
    m = metrics(rules, raw.stats, customers, orders, payments, distinct_identities)
    return m, customers, orders, payments, hist


def test_sup_ticket_uppercase_or_prefix_counted(rules):
    """OR- prefix (uppercase) that resolves to a STORE order -> counted."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_abc123",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        tickets=[{
            "type": "ticket", "src": "SUP", "ticket_id": "tk_1",
            "requester_email": "a@x.com",
            "order_ref": "OR-abc123",  # uppercase OR- -> or_abc123
        }],
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    assert m["foreign_keys_repaired"] == 1


def test_sup_ticket_lowercase_or_prefix_counted(rules):
    """or- prefix (lowercase) uppercased to OR- then resolved -> counted."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_abc123",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        tickets=[{
            "type": "ticket", "src": "SUP", "ticket_id": "tk_2",
            "requester_email": "a@x.com",
            "order_ref": "or-abc123",  # lowercase or- -> OR- -> or_abc123
        }],
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    assert m["foreign_keys_repaired"] == 1


def test_sup_ticket_mixed_case_or_prefix_counted(rules):
    """Mixed-case Or- prefix -> uppercased -> counted."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_abc123",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        tickets=[{
            "type": "ticket", "src": "SUP", "ticket_id": "tk_3",
            "requester_email": "a@x.com",
            "order_ref": "Or-Abc123",  # mixed case
        }],
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    assert m["foreign_keys_repaired"] == 1


def test_sup_ticket_empty_ref_not_counted(rules):
    """Empty order_ref -> not counted."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_abc123",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        tickets=[{
            "type": "ticket", "src": "SUP", "ticket_id": "tk_4",
            "requester_email": "a@x.com",
            "order_ref": "",
        }],
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    assert m["foreign_keys_repaired"] == 0


def test_sup_ticket_non_or_prefix_not_counted(rules):
    """Non-OR- prefix (e.g., INV-, REF-) -> not counted."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_abc123",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        tickets=[{
            "type": "ticket", "src": "SUP", "ticket_id": "tk_5",
            "requester_email": "a@x.com",
            "order_ref": "INV-abc123",
        }],
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    assert m["foreign_keys_repaired"] == 0


def test_sup_ticket_unresolved_or_not_counted(rules):
    """OR- prefix that does NOT resolve to any STORE order -> not counted."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_xyz999",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        tickets=[{
            "type": "ticket", "src": "SUP", "ticket_id": "tk_6",
            "requester_email": "a@x.com",
            "order_ref": "OR-abc123",  # resolves to or_abc123 which doesn't exist
        }],
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    assert m["foreign_keys_repaired"] == 0


def test_payment_order_ref_never_included_in_metric(rules):
    """Payment order_ref references are NOT counted in foreign_keys_repaired."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_abc123",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        payments=[{
            "type": "payment", "src": "PAY", "txn_id": "txn_1",
            "customer_ref": "cus_1", "order_ref": "OR-abc123",
            "paid": "2024-01-15T10:00:00Z", "amount_cents": 1100,
            "currency": "USD", "method": "Card", "status": "succeeded",
        }],
        tickets=[],  # no tickets
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    assert m["foreign_keys_repaired"] == 0


def test_multiple_tickets_only_resolving_ones_counted(rules):
    """Multiple tickets: only those with resolvable OR- prefixes count."""
    raw = Raw(
        orders=[{
            "type": "order", "src": "STORE", "order_id": "or_abc123",
            "customer_id": "sc_1", "email": "a@x.com",
            "name": "Test", "postal": "10001", "region": "NY",
            "ship_name": "Test", "ship_line1": "1 Main",
            "ship_city": "NYC", "ship_region": "NY",
            "ship_postal": "10001", "ship_country": "US",
            "placed": "2024-01-15T10:00:00Z", "status": "Completed",
            "channel": "Online", "subtotal_cents": 1000,
            "tax_cents": 100, "shipping_cents": 0,
            "total_cents": 1100, "currency": "USD",
        }],
        tickets=[
            {"type": "ticket", "src": "SUP", "ticket_id": "tk_a", "requester_email": "a@x.com", "order_ref": "OR-abc123"},
            {"type": "ticket", "src": "SUP", "ticket_id": "tk_b", "requester_email": "a@x.com", "order_ref": "OR-xyz999"},  # unresolvable
            {"type": "ticket", "src": "SUP", "ticket_id": "tk_c", "requester_email": "a@x.com", "order_ref": ""},  # empty
            {"type": "ticket", "src": "SUP", "ticket_id": "tk_d", "requester_email": "a@x.com", "order_ref": "INV-123"},  # non-OR
            {"type": "ticket", "src": "SUP", "ticket_id": "tk_e", "requester_email": "a@x.com", "order_ref": "or-abc123"},  # lowercase, counts
        ],
    )
    m, _, _, _, _ = _run_full_pipeline(raw, rules)
    # tk_a and tk_e should count = 2
    assert m["foreign_keys_repaired"] == 2