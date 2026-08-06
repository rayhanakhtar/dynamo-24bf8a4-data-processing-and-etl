"""Verifier: the `orders.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort,
unique contiguous order keys, canonical customer reference FK integrity,
monetary precision (integer cents, internal arithmetic), payment reconciliation,
and byte equality with the committed fixture.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

from conftest import (
    artifact_path,
    assert_distinct_artifacts,
    is_sorted,
    read_artifact_bytes,
    validate_cells,
)


def test_header_columns_match_dictionary(dictionary, orders):
    """Column names and order must exactly match the canonical schema."""
    header, _ = orders
    assert header == [c for c, _ in dictionary.output_columns("orders")]


def test_rows_sorted_deterministically(dictionary, orders):
    """Ascending by (customer_key, placed_ts, order_key) per the rules."""
    _, rows = orders
    columns = dict(dictionary.output_columns("orders"))
    bad = is_sorted(rows, ["customer_key", "placed_ts", "order_key"], columns)
    assert bad is None, f"misordered at row {bad[0]}: {bad[1]} -> {bad[2]}"


def test_order_key_unique_and_contiguous(orders):
    """Primary key is unique, non-empty and occupies 1..N with no gaps."""
    _, rows = orders
    keys = [int(r["order_key"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate order_key"
    assert keys == list(range(1, len(rows) + 1)), "order_key not contiguous from 1"


def test_gid_unique(orders):
    """Canonical order id (gid) is present and unique for every row."""
    _, rows = orders
    gids = [r["gid"] for r in rows]
    assert all(gids), "empty gid present"
    assert len(gids) == len(set(gids)), "duplicate gid"


def test_every_order_references_existing_customer(orders, customers):
    """customer_key FK resolves to a real customer in customers.csv."""
    _, order_rows = orders
    _, customer_rows = customers
    customer_keys = {int(r["customer_key"]) for r in customer_rows}
    dangling = [int(r["customer_key"]) for r in order_rows
                if int(r["customer_key"]) not in customer_keys]
    assert not dangling, f"dangling customer_key(s): {dangling[:10]}"


def test_cells_match_declared_formats(dictionary, orders):
    """Every cell satisifies its dictionary format (money / iso / idstring)."""
    header, rows = orders
    columns = dict(dictionary.output_columns("orders"))
    errors = validate_cells(header, rows, columns)
    assert not errors, "format violations:\n" + "\n".join(errors[:10])


def test_order_line_arithmetic_holds(orders):
    """total_cents == subtotal_cents + tax_cents + shipping_cents."""
    _, rows = orders
    for r in rows:
        subtotal = int(r["subtotal_cents"] or 0)
        tax = int(r["tax_cents"] or 0)
        shipping = int(r["shipping_cents"]) if r["shipping_cents"] else 0
        total = subtotal + tax + shipping
        assert int(r["total_cents"]) == total, \
            f"order {r['order_key']}: total {r['total_cents']} != {total}"


def test_completed_orders_reconcile_with_payments(orders, payments):
    """For completed orders, sum(payments.amount_usd) == orders.amount_usd."""
    _, order_rows = orders
    _, pay_rows = payments
    by_order: dict = {}
    for p in pay_rows:
        if p["order_key"]:
            by_order[int(p["order_key"])] = by_order.get(int(p["order_key"]), 0) \
                + int(p["amount_usd"])
    mismatches = []
    for r in order_rows:
        if r["status"] == "Completed":
            key = int(r["order_key"])
            if int(r["amount_usd"]) != by_order.get(key, 0):
                mismatches.append((key, r["amount_usd"], by_order.get(key, 0)))
    assert not mismatches, f"reconciliation mismatches: {mismatches[:5]}"


def test_orders_customer_fk_integrity(orders, customers):
    """Every order.customer_key must reference an existing customer."""
    _, order_rows = orders
    _, customer_rows = customers
    customer_keys = {int(r["customer_key"]) for r in customer_rows}
    for r in order_rows:
        ck = int(r["customer_key"])
        assert ck in customer_keys, f"order {r['order_key']} references non-existent customer_key {ck}"


def test_order_gid_format(orders):
    """Order gid must match pattern ORDxxxxxx."""
    import re
    pattern = re.compile(r"^ORD\d{6}$")
    _, rows = orders
    for r in rows:
        assert pattern.match(r["gid"]), f"order {r['order_key']} gid {r['gid']!r} does not match ORDxxxxxx"


def test_order_status_values(orders):
    """Order status must be one of the canonical values from the dictionary."""
    _, rows = orders
    valid_statuses = {"Processing", "Completed", "Pending", "Cancelled", "Returned", "Shipped", "Refunded", "Partially_Refunded"}
    for r in rows:
        assert r["status"] in valid_statuses, f"order {r['order_key']} invalid status: {r['status']!r}"


def test_fixture_byte_equality(output_dir, expected_dir):
    """Produced artifact and committed fixture are byte-for-byte identical."""
    produced = artifact_path(output_dir, "orders")
    expected = artifact_path(expected_dir, "orders")
    assert_distinct_artifacts(produced, expected)
    assert read_artifact_bytes(produced, output_dir) == read_artifact_bytes(expected, expected_dir), \
        "orders.csv drifted"
