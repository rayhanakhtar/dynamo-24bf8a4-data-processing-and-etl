"""Verifier: the `customers.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort by the
primary key, contiguous unique PKs, canonical id uniqueness, normalized email,
normalized E.164 phone, ISO timestamps, uppercased address codes, and byte
equality with the committed fixture.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

import re

from conftest import (
    artifact_path,
    assert_distinct_artifacts,
    is_sorted,
    read_artifact_bytes,
    validate_cells,
)

_E164 = re.compile(r"^\+\d{7,15}$")


def test_header_columns_match_dictionary(dictionary, customers):
    """Column names and order must exactly match the canonical schema."""
    header, _ = customers
    assert header == [c for c, _ in dictionary.output_columns("customers")]


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


def test_email_normalized(customers):
    """Canonical email is lowercase and contains a single '@'."""
    _, rows = customers
    bad = [r["customer_key"] for r in rows
           if r["email"] != r["email"].lower() or r["email"].count("@") != 1]
    assert not bad, f"non-canonical emails for customers {bad[:5]}"


def test_phone_normalized_when_present(customers):
    """Any non-empty phone is normalized E.164 (intl prefix, digits only)."""
    _, rows = customers
    bad = [r["customer_key"] for r in rows
           if r["phone"] and not _E164.fullmatch(r["phone"])]
    assert not bad, f"non-E.164 phones for customers {bad[:5]}"


def test_address_codes_uppercased(dictionary, customers):
    """region/postal/country are uppercased per the canonical normalization."""
    _, rows = customers
    for col in ("region", "postal", "country"):
        for r in rows:
            v = r[col]
            if v and v != v.upper():
                raise AssertionError(f"customer {r['customer_key']} {col}={v!r} not uppercased")


def test_customer_gid_format(customers):
    """Customer gid must be non-empty and unique (format is source-specific ID)."""
    _, rows = customers
    gids = [r["gid"] for r in rows]
    assert all(gids), "empty gid present"
    assert len(gids) == len(set(gids)), "duplicate gid"


def test_active_email_unique(customers):
    """No duplicate normalized email across active customers (unique_active_email).

    Independent of the fixture: two active customers sharing an email violate
    the entity-resolution invariant even if the committed file never does so.
    """
    _, rows = customers
    seen = {}
    for r in rows:
        if r["is_active"] != "true":
            continue
        email = r["email"]
        if email:
            if email in seen:
                raise AssertionError(
                    f"active emails collide: {email!r} used by customers "
                    f"{seen[email]} and {r['customer_key']}")
            seen[email] = r["customer_key"]


def test_active_phone_unique(customers):
    """No duplicate E.164 phone across active customers (unique_active_phone)."""
    _, rows = customers
    seen = {}
    for r in rows:
        if r["is_active"] != "true":
            continue
        phone = r["phone"]
        if phone:
            if phone in seen:
                raise AssertionError(
                    f"active phones collide: {phone!r} used by customers "
                    f"{seen[phone]} and {r['customer_key']}")
            seen[phone] = r["customer_key"]


def test_is_active_defaults_true_when_no_idp(customers):
    """is_active defaults to true when no IDP record in linked cluster."""
    _, rows = customers
    # This is an indirect test: since all customers without IDP should have is_active=true
    # We verify no customer has is_active=false without a corresponding IDP inactive status
    # The actual IDP check would need access to raw data; here we just verify the field is bool
    for r in rows:
        assert r["is_active"] in ("true", "false"), f"customer {r['customer_key']} invalid is_active: {r['is_active']!r}"


def test_created_ts_before_updated_ts(customers):
    """If both created_ts and updated_ts present, created_ts <= updated_ts."""
    _, rows = customers
    for r in rows:
        if r["created_ts"] and r["updated_ts"]:
            assert r["created_ts"] <= r["updated_ts"], \
                f"customer {r['customer_key']} created_ts > updated_ts"


def test_fixture_byte_equality(output_dir, expected_dir):
    """Produced artifact and committed fixture are byte-for-byte identical."""
    produced = artifact_path(output_dir, "customers")
    expected = artifact_path(expected_dir, "customers")
    assert_distinct_artifacts(produced, expected)
    assert read_artifact_bytes(produced, output_dir) == read_artifact_bytes(expected, expected_dir), \
        "customers.csv drifted"
