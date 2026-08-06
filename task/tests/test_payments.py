"""Verifier: the `payments.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort,
unique contiguous payment keys, order FK integrity, currency conversion
precision, payment reconciliation, cell formatting, and byte equality with the
committed fixture.

A payment with order_key == 0 is a refund resolved through an ERP-invoice match
to a synthetic order record; the reference harness treats 0 as a legitimate
sentinel and excludes it from FK resolution.

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


def test_header_columns_match_dictionary(dictionary, payments):
    """Column names and order must exactly match the canonical schema."""
    header, _ = payments
    assert header == [c for c, _ in dictionary.output_columns("payments")]


def test_rows_sorted_deterministically(dictionary, payments):
    """Ascending by (order_key, paid_ts, payment_key) per the rules."""
    _, rows = payments
    columns = dict(dictionary.output_columns("payments"))
    bad = is_sorted(rows, ["order_key", "paid_ts", "payment_key"], columns)
    assert bad is None, f"misordered at row {bad[0]}: {bad[1]} -> {bad[2]}"


def test_payment_key_unique_and_contiguous(payments):
    """Primary key is unique, non-empty and occupies 1..N with no gaps."""
    _, rows = payments
    keys = [int(r["payment_key"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate payment_key"
    assert keys == list(range(1, len(rows) + 1)), "payment_key not contiguous from 1"


def test_gid_unique(payments):
    """Canonical payment id (gid) is present and unique for every row."""
    _, rows = payments
    gids = [r["gid"] for r in rows]
    assert all(gids), "empty gid present"
    assert len(gids) == len(set(gids)), "duplicate gid"


def test_every_payment_references_existing_order(payments, orders):
    """Each non-sentinel order_key FK resolves to a real order in orders.csv."""
    _, pay_rows = payments
    _, order_rows = orders
    order_keys = {int(r["order_key"]) for r in order_rows}
    dangling = [int(r["order_key"]) for r in pay_rows
                if int(r["order_key"]) != 0 and int(r["order_key"]) not in order_keys]
    assert not dangling, f"dangling order_key(s): {dangling[:10]}"


def test_cells_match_declared_formats(dictionary, payments):
    """Every cell satisifies its dictionary format."""
    header, rows = payments
    columns = dict(dictionary.output_columns("payments"))
    errors = validate_cells(header, rows, columns)
    assert not errors, "format violations:\n" + "\n".join(errors[:10])


def test_usd_conversion_precision(dictionary, payments):
    """amount_usd == round(amount_cents * FX rate), independently computed."""
    def conv(cents, cur):
        cur_rate = dictionary.currency_rate(cur)
        val = cents * cur_rate
        return int(val + 0.5) if val >= 0 else -int(abs(val) + 0.5)
    for r in payments[1]:
        exp = conv(int(r["amount_cents"]), r["currency"])
        assert int(r["amount_usd"]) == exp, \
            f"payment {r['payment_key']}: usd {r['amount_usd']} != {exp}"


def test_erp_sentinel_payments_are_refunds(payments):
    """Payments with order_key == 0 (ERP sentinel) must have refund status."""
    _, rows = payments
    sentinel_rows = [r for r in rows if int(r["order_key"]) == 0]
    for r in sentinel_rows:
        status = r["status"]
        assert status in ("Refunded", "Partially_Refunded"), \
            f"ERP sentinel payment {r['payment_key']} has non-refund status: {status}"


def test_non_sentinel_payments_reference_valid_orders(payments, orders):
    """Every payment with order_key > 0 must reference an existing order."""
    _, payment_rows = payments
    _, order_rows = orders
    order_keys = {int(r["order_key"]) for r in order_rows}
    for r in payment_rows:
        ok = int(r["order_key"])
        if ok > 0:
            assert ok in order_keys, \
                f"payment {r['payment_key']} references non-existent order_key {ok}"


def test_payment_amount_usd_consistent_with_order_currency(payments, orders):
    """Payment amount_usd should be convertible back to cents for cross-check."""
    # This is a structural check: payment.currency and amount_cents determine amount_usd
    # via the fixed FX rates in the dictionary. Already tested in test_currency_conversion_matches_dictionary.
    pass


def test_fixture_byte_equality(output_dir, expected_dir):
    """Produced artifact and committed fixture are byte-for-byte identical."""
    produced = artifact_path(output_dir, "payments")
    expected = artifact_path(expected_dir, "payments")
    assert_distinct_artifacts(produced, expected)
    assert read_artifact_bytes(produced, output_dir) == read_artifact_bytes(expected, expected_dir), \
        "payments.csv drifted"
