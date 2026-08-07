"""Verifier: the `customer_history.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort,
SCD reconstruction (changeable attributes only, old != new), timestamp
formatting, customer linkage, source provenance, and byte equality with the
committed fixture.

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

_CHANGEABLE = {"email", "last_name", "phone", "postal"}
_ALLOWED_SOURCES = {"STORE", "MRK", "CAT", "IDP", "CRM"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_header_columns_match_dictionary(dictionary, history):
    """Column names and order must exactly match the canonical schema."""
    header, _ = history
    assert header == [c for c, _ in dictionary.output_columns("customer_history")]


def test_rows_sorted_deterministically(dictionary, history):
    """Ascending by (customer_key, attribute, effective_from) per the rules."""
    _, rows = history
    columns = dict(dictionary.output_columns("customer_history"))
    bad = is_sorted(rows, ["customer_key", "attribute", "effective_from"], columns)
    assert bad is None, f"misordered at row {bad[0]}: {bad[1]} -> {bad[2]}"


def test_history_key_unique_and_contiguous(history):
    """Primary key is unique, non-empty and occupies 1..N with no gaps."""
    _, rows = history
    keys = [int(r["history_key"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate history_key"
    assert keys == list(range(1, len(rows) + 1)), "history_key not contiguous from 1"


def test_every_history_row_links_to_customer(history, customers):
    """customer_key FK resolves to a real customer in customers.csv."""
    _, history_rows = history
    _, customer_rows = customers
    customer_keys = {int(r["customer_key"]) for r in customer_rows}
    dangling = [int(r["customer_key"]) for r in history_rows
                if int(r["customer_key"]) not in customer_keys]
    assert not dangling, f"dangling customer_key(s): {dangling[:10]}"


def test_cells_match_declared_formats(dictionary, history):
    """Every cell satisifies its dictionary format (iso / value / source)."""
    header, rows = history
    columns = dict(dictionary.output_columns("customer_history"))
    errors = validate_cells(header, rows, columns)
    assert not errors, "format violations:\n" + "\n".join(errors[:10])


def test_attributes_are_changeable_only(history):
    """Reconstructed changes are limited to the declared changeable set."""
    _, rows = history
    attrs = {r["attribute"] for r in rows}
    assert attrs.issubset(_CHANGEABLE), f"unexpected attributes: {attrs - _CHANGEABLE}"


def test_old_and_new_values_differ(history):
    """Each reconstructed change records a genuine value transition."""
    _, rows = history
    for r in rows:
        assert r["old_value"] != r["new_value"], \
            f"history {r['history_key']}: old == new"


def test_change_states_unique(history):
    """No duplicate (customer_key, attribute, effective_from) change states.

    The sort key uniquely identifies each emitted change; a duplicated state is
    either a duplicated row or a broken effective_from assignment. Derived from
    the data itself — no fixture.
    """
    _, rows = history
    states = [(r["customer_key"], r["attribute"], r["effective_from"]) for r in rows]
    assert len(states) == len(set(states)), "duplicate (customer, attribute, effective_from)"


def test_timestamps_are_iso(history):
    """effective_from is ISO8601; effective_to is empty (open) or ISO."""
    _, rows = history
    for r in rows:
        assert _ISO.fullmatch(r["effective_from"]), \
            f"history {r['history_key']}: bad effective_from {r['effective_from']!r}"
        if r["effective_to"]:
            assert _ISO.fullmatch(r["effective_to"]), \
                f"history {r['history_key']}: bad effective_to {r['effective_to']!r}"


def test_source_is_declared_provenance(history):
    """source is an uppercased, recognized source code."""
    _, rows = history
    for r in rows:
        assert r["source"] in _ALLOWED_SOURCES, \
            f"history {r['history_key']}: source {r['source']!r} unexpected"
        assert r["source"] == r["source"].upper()


def test_email_history_source_is_store_or_mrk(history):
    """Email history rows must have source STORE or MRK per dictionary spec."""
    _, rows = history
    for r in rows:
        if r["attribute"] == "email":
            assert r["source"] in ("STORE", "MRK"), \
                f"history {r['history_key']}: email source must be STORE or MRK, got {r['source']!r}"


def test_postal_history_source_is_cat(history):
    """Postal history rows must have source CAT per dictionary spec."""
    _, rows = history
    for r in rows:
        if r["attribute"] == "postal":
            assert r["source"] == "CAT", \
                f"history {r['history_key']}: postal source must be CAT, got {r['source']!r}"


def test_last_name_phone_history_source_is_cat(history):
    """last_name and phone history rows must have source CAT per dictionary spec."""
    _, rows = history
    for r in rows:
        if r["attribute"] in ("last_name", "phone"):
            assert r["source"] == "CAT", \
                f"history {r['history_key']}: {r['attribute']} source must be CAT, got {r['source']!r}"


def test_effective_from_rule(history, customers):
    """effective_from is customer's updated_ts if IDP present, else fallback 2024-05-01T00:00:00Z."""
    _, history_rows = history
    _, customer_rows = customers
    cust_by_key = {int(r["customer_key"]): r for r in customer_rows}
    for r in history_rows:
        ck = int(r["customer_key"])
        cust = cust_by_key.get(ck)
        if cust:
            updated_ts = cust["updated_ts"]
            # effective_from should be updated_ts if present, else fallback
            expected = updated_ts if updated_ts else "2024-05-01T00:00:00Z"
            assert r["effective_from"] == expected, \
                f"history {r['history_key']}: effective_from {r['effective_from']} != expected {expected}"


def test_fixture_byte_equality(output_dir, expected_dir):
    """Produced artifact and committed fixture are byte-for-byte identical."""
    produced = artifact_path(output_dir, "history")
    expected = artifact_path(expected_dir, "history")
    assert_distinct_artifacts(produced, expected)
    assert read_artifact_bytes(produced, output_dir) == read_artifact_bytes(expected, expected_dir), \
        "customer_history.csv drifted"
