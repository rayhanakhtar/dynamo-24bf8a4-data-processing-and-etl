"""Verifier: the `customers.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort by
primary key, contiguous unique PKs, canonical id uniqueness, normalized email,
normalized E.164 phone, ISO timestamps, uppercased address codes, and byte
equality with the committed fixture.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

from conftest import (artifact_bytes, artifact_path, is_sorted,
                      validate_cells)


def test_header_columns_match_dictionary(dictionary, customers):
    """Column names and order must exactly match the canonical schema."""
    header, _ = customers
    expected = [c for c, _ in dictionary.output_columns("customers")]
    assert header == expected


def test_rows_sorted_by_customer_key(dictionary, customers):
    """Deterministic ascending ordering by the declared sort column."""
    _, rows = customers
    columns = dict(dictionary.output_columns("customers"))
    bad = is_sorted(rows, ["customer_key"], columns)
    assert bad is None, f"misordered at row {bad[0]}: {bad[1]} -> {bad[2]}"


def test_customer_key_unique_and_contiguous(customers):
    """Primary key is unique, non-empty and occupies 1..N with no gaps."""
    _, rows = customers
    keys = [int(r["customer_key"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate customer_key"
    assert sorted(keys) == keys, "customer_key not already sorted ascending"
    assert keys == list(range(1, len(rows) + 1)), "customer_key not contiguous from 1"


def test_gid_unique(customers):
    """Canonical global id (gid) is present and unique for every row."""
    _, rows = customers
    gids = [r["gid"] for r in rows]
    assert all(gids), "empty gid present"
    assert len(gids) == len(set(gids)), "duplicate gid"


def test_cells_match_declared_formats(dictionary, customers):
    """Every cell satisifies its dictionary format (email/phone/ts/postal/...)."""
    header, rows = customers
    columns = dict(dictionary.output_columns("customers"))
    errors = validate_cells(header, rows, columns)
    assert not errors, "format violations:\n" + "\n".join(errors[:10])


def test_email_normalized(dictionary, customers):
    """Canonical email is lowercase and contains a single '@'."""
    _, rows = customers
    bad = [r["customer_key"] for r in rows
           if r["email"] != r["email"].lower() or r["email"].count("@") != 1]
    assert not bad, f"non-canonical emails for customers {bad[:5]}"


def test_phone_normalized_when_present(customers):
    """Any non-empty phone is normalized E.164 (intl prefix, digits only)."""
    _, rows = customers
    import re
    e164 = re.compile(r"^\+\d{7,15}$")
    bad = [r["customer_key"] for r in rows
           if r["phone"] and not e164.fullmatch(r["phone"])]
    assert not bad, f"non-E.164 phones for customers {bad[:5]}"


def test_timestamps_are_iso(dictionary, customers):
    """created_ts/updated_ts are ISO8601 UTC (or empty when nullable)."""
    _, rows = customers
    cols = dict(dictionary.output_columns("customers"))
    errors = validate_cells(["created_ts", "updated_ts"], rows, cols)
    assert not errors, "\n".join(errors[:10])


def test_address_codes_uppercased(dictionary, customers):
    """region/postal/country are uppercased per the canonical normalization."""
    _, rows = customers
    cols = dict(dictionary.output_columns("customers"))
    for col in ("region", "postal", "country"):
        for r in rows:
            v = r[col]
            if v and v != v.upper():
                raise AssertionError(f"customer {r['customer_key']} {col}={v!r} not uppercased")


def test_fixture_byte_equality(output_dir, expected_dir, dictionary):
    """Committed fixture and produced artifact are byte-for-byte identical."""
    produced = artifact_path(output_dir, "customers")
    expected = artifact_path(expected_dir, "customers")
    if produced == expected:
        return
    assert produced.read_bytes() == expected.read_bytes(), "customers.csv drifted"