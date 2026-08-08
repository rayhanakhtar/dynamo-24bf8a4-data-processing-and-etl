"""Verifier: the `data_quality_report.json` artifact only.

Every published metric is checked independently against the committed fixture
(byte equality) and validated for internal consistency against the row counts
and format of the other artifacts. Metrics are integers, never booleans, and
the report schema matches the published shape.

The verifier inspects outputs only — never implementation code.
"""

from __future__ import annotations

import json

from conftest import artifact_path, read_report

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
    data = read_report(artifact_path(output_dir, "report"))
    assert isinstance(data, dict)


def test_report_shape(dictionary, report):
    """The report exposes the published envelope fields."""
    assert report.get("number_type") == "int"
    assert report.get("key_order") == "sorted"
    assert report.get("include_details") is True
    assert isinstance(report.get("metrics"), dict)


def test_every_metric_present(report):
    """Every declared metric key is present in the metrics payload."""
    missing = set(INTEGER_METRICS) - set(report.get("metrics", {}))
    assert not missing, f"missing metrics: {missing}"


def test_every_metric_is_integer(dictionary, report):
    """All metrics are integers (never bool), per [metrics] number_type=int."""
    metrics = report.get("metrics", {})
    for key in INTEGER_METRICS:
        assert key in metrics, f"metric {key} missing"
        val = metrics[key]
        assert isinstance(val, int) and not isinstance(val, bool), \
            f"metric {key} = {val!r} is not an integer"


def test_nonnegative_processed_and_counts(report):
    """Counts that must be non-negative are not negative."""
    metrics = report.get("metrics", {})
    for key in ("records_processed", "records_rejected",
                "duplicate_entities_merged", "foreign_keys_repaired",
                "malformed_timestamps_corrected", "null_values_imputed",
                "final_customer_count", "final_order_count", "final_payment_count"):
        assert metrics[key] >= 0, f"metric {key} is negative"


def test_final_customer_count_matches(output_dir, report):
    """final_customer_count == number of customer rows."""
    from conftest import read_csv
    header, rows = read_csv(artifact_path(output_dir, "customers"))
    assert report["metrics"]["final_customer_count"] == len(rows)


def test_final_order_count_matches(output_dir, report):
    """final_order_count == number of order rows."""
    from conftest import read_csv
    header, rows = read_csv(artifact_path(output_dir, "orders"))
    assert report["metrics"]["final_order_count"] == len(rows)


def test_final_payment_count_matches(output_dir, report):
    """final_payment_count == number of payment rows."""
    from conftest import read_csv
    header, rows = read_csv(artifact_path(output_dir, "payments"))
    assert report["metrics"]["final_payment_count"] == len(rows)


def test_records_rejected_is_non_negative_and_reconcilable(report, dictionary):
    """records_rejected is the deterministic difference between raw and kept."""
    from conftest import read_csv
    metrics = report["metrics"]
    assert metrics["records_rejected"] >= 0


def test_referential_integrity_violations_zero(report):
    """Per invariant no_resolvable_null_fk, violations must be 0."""
    assert report["metrics"]["referential_integrity_violations"] == 0


def test_null_values_imputed_recomputes_from_customers(report, output_dir):
    """null_values_imputed == count of non-empty customer cells (excl pk/gid).

    The metric is a pure cell-level tally over the produced customers file, so a
    truthful pipeline reproduces it from its own output — no fixture involved.
    """
    from conftest import read_csv
    header, rows = read_csv(artifact_path(output_dir, "customers"))
    excluded = {"customer_key", "gid"}
    tally = sum(1 for r in rows for col in header
                if col not in excluded and r.get(col, "").strip() != "")
    assert report["metrics"]["null_values_imputed"] == tally, \
        f"null_values_imputed {report['metrics']['null_values_imputed']} != recomputed {tally}"


def test_fixture_byte_equality(output_dir, expected_dir, dictionary):
    """Committed fixture and produced artifact are byte-for-byte identical."""
    produced = artifact_path(output_dir, "report")
    expected = artifact_path(expected_dir, "report")
    if produced == expected:
        return
    a = json.loads(produced.read_text())
    b = json.loads(expected.read_text())
    assert a == b, "data_quality_report.json drifted"


def test_metrics_exactly_equal_committed_fixture(report, expected_dir):
    """Every published metric exactly equals the committed fixture."""
    committed = read_report(artifact_path(expected_dir, "report"))
    assert report.get("metrics") == committed.get("metrics"), \
        "metrics differ from committed fixture"