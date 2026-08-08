"""Verifier: the `customer_history.csv` artifact only.

Independently validates schema/column order, deterministic ascending sort,
SCD reconstruction (changeable attributes only, old != new), timestamp
ordering, customer linkage, source provenance, and deterministic output.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

from conftest import (artifact_path, is_sorted, validate_cells)

_CHANGEABLE = {"email", "last_name", "phone", "postal"}
_ALLOWED_SOURCES = {"STORE", "MRK", "CAT", "IDP", "CRM"}


def test_header_columns_match_dictionary(dictionary, history):
    """Column names and order must exactly match the canonical schema."""
    header, _ = history
    expected = [c for c, _ in dictionary.output_columns("customer_history")]
    assert header == expected


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
    assert sorted(keys) == keys, "history_key not already sorted ascending"
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
    the produced output itself — no fixture.
    """
    _, rows = history
    states = [(r["customer_key"], r["attribute"], r["effective_from"]) for r in rows]
    assert len(states) == len(set(states)), \
        "duplicate (customer_key, attribute, effective_from) change state"


def test_effective_from_is_iso_and_to_is_empty_or_iso(history):
    """effective_from is ISO8601; effective_to is empty (open) or ISO."""
    import re
    iso = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    for r in history[1]:
        assert iso.fullmatch(r["effective_from"]), \
            f"history {r['history_key']}: bad effective_from {r['effective_from']!r}"
        et = r["effective_to"]
        if et:
            assert iso.fullmatch(et), \
                f"history {r['history_key']}: bad effective_to {et!r}"


def test_source_is_declared_provenance(history):
    """source is an uppercased, recognized source code."""
    _, rows = history
    for r in rows:
        assert r["source"] in _ALLOWED_SOURCES, \
            f"history {r['history_key']}: source {r['source']!r} unexpected"
        assert r["source"] == r["source"].upper()


def test_fixture_byte_equality(output_dir, expected_dir, dictionary):
    """Committed fixture and produced artifact are byte-for-byte identical."""
    produced = artifact_path(output_dir, "history")
    expected = artifact_path(expected_dir, "history")
    if produced == expected:
        return
    assert produced.read_bytes() == expected.read_bytes(), "customer_history.csv drifted"