"""Focused regression test for assemble() order sorting by source order_id.

The production sort in assemble() uses:
    (customer_key, placed_ts, order_id)

where order_id is the source order identifier (e.g., "or_aaa", "or_bbb").
The existing verifier test checks (customer_key, placed_ts, order_key) but
order_key is assigned sequentially AFTER the sort, so it cannot detect a
regression where order_id is sorted descending.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.assemble import assemble  # noqa: E402
from etl.model import Customer, Order  # noqa: E402
from etl.parse import Raw, Stats  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402


def _minimal_customer(customer_key: int, email: str) -> Customer:
    return Customer(
        customer_key=customer_key,
        gid=f"CUST{customer_key:06d}",
        first_name=None, last_name=None, dob=None, gender=None,
        email=email, phone=None, line1=None, city=None, region=None,
        postal=None, country=None, is_business=False, company=None,
        household=None, loyalty_tier=None, is_active=True,
        created_ts=None, updated_ts=None,
        exposures={}, has_idp=False,
    )


def _minimal_order_obs(order_id: str, email: str, placed_ts: str) -> dict:
    """Minimal store order observation with required fields for _mk_order."""
    return {
        "type": "order", "src": "STORE",
        "order_id": order_id,
        "customer_id": "cust_123",
        "email": email,
        "name": "Test User",
        "postal": "10001",
        "region": "NY",
        "ship_name": "Test User",
        "ship_line1": "123 Test St",
        "ship_city": "New York",
        "ship_region": "NY",
        "ship_postal": "10001",
        "ship_country": "US",
        "placed": placed_ts,
        "status": "Completed",
        "channel": "Online",
        "subtotal_cents": 1000,
        "tax_cents": 100,
        "shipping_cents": 50,
        "total_cents": 1150,
        "currency": "USD",
    }


def test_assemble_sorts_by_source_order_id_ascending():
    """
    Regression test for C3 held-out coverage gap.

    Two orders with same customer_key and same placed_ts must be sorted
    ascending by their source order_id (e.g., "or_aaa" before "or_bbb").

    This test exercises the REAL assemble() function, not a duplicated
    sorting expression. It will fail if the production comparator is
    mutated to reverse the order_id tertiary key.
    """
    rules = load_rules(str(ROOT / "config" / "dictionary.toml"))

    # Same customer, same timestamp, different source order IDs
    email = "test@example.com"
    customer_key = 42
    placed_ts = "2024-01-15T10:30:00Z"

    raw = Raw(orders=[
        _minimal_order_obs("or_bbb", email, placed_ts),  # comes second alphabetically
        _minimal_order_obs("or_aaa", email, placed_ts),  # comes first alphabetically
    ])

    customers = [_minimal_customer(customer_key, email)]
    # cust_map maps email -> customer_key (as produced by link())
    cust_map = {email: customer_key}
    stats = Stats()

    orders, _payments = assemble(raw, customers, cust_map, rules, stats)

    # Should have exactly 2 orders
    assert len(orders) == 2

    # order_key is assigned sequentially AFTER sort: 1, 2
    # The sort key is (customer_key, placed_ts, order_id)
    # Since customer_key and placed_ts are equal, order_id decides:
    # "or_aaa" < "or_bbb" -> or_aaa gets order_key=1, or_bbb gets order_key=2
    assert orders[0].order_key == 1
    assert orders[0].order_id == "or_aaa"
    assert orders[1].order_key == 2
    assert orders[1].order_id == "or_bbb"

    # Explicit assertion on the tertiary sort key behavior:
    # The sequence must be ascending by order_id when customer_key and placed_ts tie.
    order_ids = [o.order_id for o in orders]
    assert order_ids == ["or_aaa", "or_bbb"], \
        f"Expected ascending order_id, got {order_ids}"