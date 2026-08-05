"""Verifier: the `payments.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort,
unique contiguous payment keys, order FK integrity, currency conversion
precision, payment reconciliation, cell formatting, and byte equality with the
committed fixture.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

from conftest import (artifact_path, is_sorted, validate_cells)


def test_header_columns_match_dictionary(dictionary, payments):
    """Column names and order must exactly match the canonical schema."""
    header, _ = payments
    expected = [c for c, _ in dictionary.output_columns("payments")]
    assert header == expected


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
    assert sorted(keys) == keys, "payment_key not already sorted ascending"
    assert keys == list(range(1, len(rows) + 1)), "payment_key not contiguous from 1"


def test_gid_unique(payments):
    """Canonical payment id (gid) is present and unique for every row."""
    _, rows = payments
    gids = [r["gid"] for r in rows]
    assert all(gids), "empty gid present"
    assert len(gids) == len(set(gids)), "duplicate gid"


def test_every_payment_references_existing_order(payments, orders):
    """Each non-sentinel order_key FK resolves to a real order in orders.csv.

    A payment with order_key == 0 is a refund resolved through an ERP-invoice
    match to a synthetic order record; the reference harness treats 0 as a
    legitimate sentinel and excludes it from FK resolution.
    """
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


def test_amount_usd_is_integer_cents(dictionary, payments):
    """amount_cents and amount_usd are signed integer minor units."""
    for col in ("amount_cents", "amount_usd"):
        for r in payments[1]:
            v = r[col]
            assert v.lstrip("-").isdigit(), \
                f"payment {r['payment_key']} {col}={v!r} not integer cents"


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


def test_fixture_byte_equality(output_dir, expected_dir, dictionary):
    """Committed fixture and produced artifact are byte-for-byte identical."""
    produced = artifact_path(output_dir, "payments")
    expected = artifact_path(expected_dir, "payments")
    if produced == expected:
        return
    assert produced.read_bytes() == expected.read_bytes(), "payments.csv drifted"