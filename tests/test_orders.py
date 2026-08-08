"""Verifier: the `orders.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort,
unique contiguous order keys, canonical customer reference FK integrity,
monetary precision (integer cents, internal arithmetic), payment reconciliation,
and byte equality with the committed fixture.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

from conftest import (artifact_path, is_sorted, read_csv, validate_cells)


def test_header_columns_match_dictionary(dictionary, orders):
    """Column names and order must exactly match the canonical schema."""
    header, _ = orders
    expected = [c for c, _ in dictionary.output_columns("orders")]
    assert header == expected


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
    assert sorted(keys) == keys, "order_key not already sorted ascending"
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


def test_monetary_precision_integer_cents(dictionary, orders):
    """All money fields are integer minor units (no decimals/sign ambiguity)."""
    _, rows = orders
    for col in ("subtotal_cents", "tax_cents", "shipping_cents",
                "total_cents", "amount_usd"):
        for r in rows:
            v = r[col]
            if v == "":
                continue
            assert v.lstrip("-").isdigit(), \
                f"order {r['order_key']} {col}={v!r} not integer cents"


def test_order_line_arithmetic_holds(dictionary, orders):
    """total_cents == subtotal_cents + tax_cents + shipping_cents."""
    _, rows = orders
    for r in rows:
        subtotal = int(r["subtotal_cents"] or 0)
        tax = int(r["tax_cents"] or 0)
        shipping = int(r["shipping_cents"]) if r["shipping_cents"] else 0
        total = subtotal + tax + shipping
        assert int(r["total_cents"]) == total, \
            f"order {r['order_key']}: total {r['total_cents']} != {total}"


def test_every_order_with_payments_reconciles(orders, payments):
    """For every order carrying any non-sentinel payment,
    sum(payments.amount_usd) == orders.amount_usd.

    Broadened beyond status == "Completed": Refunded / Partially_Refunded /
    Processing orders also fall on money and must balance. A deferred
    implementation honoring only Completed rows would be caught here. Derived
    from produced outputs only — no fixture.
    """
    _, order_rows = orders
    _, pay_rows = payments
    by_order: dict = {}
    for p in pay_rows:
        if p["order_key"]:
            by_order[int(p["order_key"])] = by_order.get(int(p["order_key"]), 0) \
                + int(p["amount_usd"])
    mismatches = []
    for r in order_rows:
        key = int(r["order_key"])
        if key in by_order and int(r["amount_usd"]) != by_order.get(key, 0):
            mismatches.append((key, r["amount_usd"], by_order.get(key, 0)))
    assert not mismatches, f"reconciliation mismatches: {mismatches[:5]}"


def test_global_money_conservation(orders, payments):
    """sum(orders.amount_usd) == sum(payments.amount_usd) over non-sentinel rows.

    Guards against whole-table drift that per-row reconciliation could hide;
    catches global monetary leakage regardless of order status. Derived from
    produced outputs only.
    """
    _, order_rows = orders
    _, pay_rows = payments
    order_total = sum(int(r["amount_usd"]) for r in order_rows)
    pay_total = sum(int(p["amount_usd"]) for p in pay_rows
                    if int(p["order_key"]) != 0)
    assert order_total == pay_total, \
        f"money conservation: orders {order_total} != payments {pay_total}"


def test_ship_to_address_is_all_or_nothing(orders):
    """A ship_to block is either fully populated or fully empty.

    Mirrors the complete_address invariant on the orders shipping block,
    independent of the fixture: a stitched/hardcoded subset fails.
    """
    _, rows = orders
    fields = ("ship_to_name", "ship_to_line1", "ship_to_city",
              "ship_to_postal", "ship_to_country")
    partial = []
    for r in rows:
        present = sum(1 for f in fields if r[f].strip())
        if 0 < present < len(fields):
            partial.append((r["order_key"], present))
    assert not partial, f"partial ship-to addresses: {partial[:5]}"


def test_fixture_byte_equality(output_dir, expected_dir, dictionary):
    """Committed fixture and produced artifact are byte-for-byte identical."""
    produced = artifact_path(output_dir, "orders")
    expected = artifact_path(expected_dir, "orders")
    if produced == expected:
        return
    assert produced.read_bytes() == expected.read_bytes(), "orders.csv drifted"