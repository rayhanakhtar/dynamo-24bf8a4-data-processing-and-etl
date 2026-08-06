"""Verifier: the `data_quality_report.json` artifact only.

Every published metric is checked independently for type/presence and for
internal consistency against the row counts of the other artifacts, and the
metrics must exactly equal the committed fixture.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

import json

from conftest import (
    artifact_path,
    assert_distinct_artifacts,
    read_artifact_bytes,
    read_csv,
    read_report,
)

INTEGER_METRICS = [
    "records_processed",
    "records_rejected",
    "duplicate_entities_merged",
    "foreign_keys_repaired",
    "malformed_timestamps_corrected",
    "null_values_imputed",
    "referential_integrity_violations",
    "final_customer_count",
    "final_order_count",
    "final_payment_count",
]


def test_report_is_valid_json(output_dir):
    """The report artifact parses as a JSON object."""
    assert isinstance(read_report(artifact_path(output_dir, "report"), output_dir), dict)


def test_report_shape(report):
    """The report exposes the published envelope fields."""
    assert report.get("number_type") == "int"
    assert report.get("key_order") == "sorted"
    assert report.get("include_details") is True
    assert isinstance(report.get("metrics"), dict)


def test_every_metric_present(report):
    """Every declared metric key is present in the metrics payload."""
    missing = set(INTEGER_METRICS) - set(report.get("metrics", {}))
    assert not missing, f"missing metrics: {missing}"


def test_every_metric_is_integer(report):
    """All metrics are integers (never bool)."""
    metrics = report.get("metrics", {})
    for key in INTEGER_METRICS:
        val = metrics[key]
        assert isinstance(val, int) and not isinstance(val, bool), \
            f"metric {key} = {val!r} is not an integer"


def test_metrics_are_non_negative(report):
    """Count-like metrics are not negative."""
    metrics = report.get("metrics", {})
    for key in INTEGER_METRICS:
        assert metrics[key] >= 0, f"metric {key} is negative"


def test_final_customer_count_matches(output_dir, report):
    """final_customer_count == number of customer rows."""
    header, rows = read_csv(artifact_path(output_dir, "customers"), output_dir)
    assert report["metrics"]["final_customer_count"] == len(rows)


def test_final_order_count_matches(output_dir, report):
    """final_order_count == number of order rows."""
    header, rows = read_csv(artifact_path(output_dir, "orders"), output_dir)
    assert report["metrics"]["final_order_count"] == len(rows)


def test_final_payment_count_matches(output_dir, report):
    """final_payment_count == number of payment rows."""
    header, rows = read_csv(artifact_path(output_dir, "payments"), output_dir)
    assert report["metrics"]["final_payment_count"] == len(rows)


def test_referential_integrity_violations_zero(report):
    """Per the published invariant no_resolvable_null_fk, violations == 0."""
    assert report["metrics"]["referential_integrity_violations"] == 0


def test_metrics_exactly_equal_committed_fixture(report, expected_dir):
    """Every published metric exactly equals the committed fixture."""
    committed = read_report(artifact_path(expected_dir, "report"), expected_dir)
    assert report.get("metrics") == committed.get("metrics"), \
        "metrics differ from committed fixture"


def test_report_byte_equality(output_dir, expected_dir):
    """Produced report and committed fixture are byte-for-byte identical."""
    produced = artifact_path(output_dir, "report")
    expected = artifact_path(expected_dir, "report")
    assert_distinct_artifacts(produced, expected)
    assert read_artifact_bytes(produced, output_dir) == read_artifact_bytes(expected, expected_dir), \
        "data_quality_report.json drifted"
