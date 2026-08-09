"""Focused oracle test: ERP invoice with invalid total_cents is rejected.

The ERP parser skips invoices where total_cents cannot be parsed as money.
This test exercises the REAL parser behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from etl.parse import Raw, _parse_erp  # noqa: E402
from etl.normalize import Normalizer  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402


def _load_rules():
    return load_rules(str(ROOT / "config" / "dictionary.toml"))


def _make_erp_raw(rules, invoice_rows: list[dict]) -> Raw:
    """Create a Raw object with ERP invoice data and return the parsed result."""
    raw = Raw()
    nm = Normalizer(rules)
    rows = {"invoices.csv": invoice_rows}
    kept = _parse_erp(rows, nm, raw)
    return raw


def test_erp_invalid_total_cents_rejected_by_parser():
    """Invoice with non-numeric total_cents ('oops') should be rejected by parser."""
    rules = _load_rules()
    raw = _make_erp_raw(rules, [
        {"invoice_no": "inv_aaaa", "customer_internal_id": "00001",
         "bill_to": "Acme Corp", "total_cents": "oops", "currency": "USD",
         "issue_date": "01/22/2024"},  # us_mdy format
    ])
    # Invoice should be skipped due to unparseable total_cents
    assert len(raw.erp_invoices) == 0


def test_erp_valid_total_cents_kept_by_parser():
    """Invoice with valid numeric total_cents should be kept."""
    rules = _load_rules()
    raw = _make_erp_raw(rules, [
        {"invoice_no": "inv_aaaa", "customer_internal_id": "00001",
         "bill_to": "Acme Corp", "total_cents": "1100", "currency": "USD",
         "issue_date": "01/22/2024"},
    ])
    assert len(raw.erp_invoices) == 1
    assert raw.erp_invoices[0]["total_cents"] == 1100
    assert raw.erp_invoices[0]["bill_to"] == "Acme Corp"


def test_erp_missing_total_cents_rejected():
    """Invoice with missing/empty total_cents should be rejected."""
    rules = _load_rules()
    raw = _make_erp_raw(rules, [
        {"invoice_no": "inv_aaaa", "customer_internal_id": "00001",
         "bill_to": "Acme Corp", "total_cents": "", "currency": "USD",
         "issue_date": "01/22/2024"},
    ])
    assert len(raw.erp_invoices) == 0


def test_erp_missing_invoice_no_skipped():
    """Invoice with no invoice_no should be skipped."""
    rules = _load_rules()
    raw = _make_erp_raw(rules, [
        {"invoice_no": "", "customer_internal_id": "00001",
         "bill_to": "Acme Corp", "total_cents": "1100", "currency": "USD",
         "issue_date": "01/22/2024"},
    ])
    assert len(raw.erp_invoices) == 0


def test_erp_malformed_timestamp_corrected():
    """Invoice with secondary timestamp format should be corrected."""
    rules = _load_rules()
    raw = _make_erp_raw(rules, [
        {"invoice_no": "inv_aaaa", "customer_internal_id": "00001",
         "bill_to": "Acme Corp", "total_cents": "1100", "currency": "USD",
         "issue_date": "01/22/2024 10:30:00"},  # us_mdy_time
    ])
    assert len(raw.erp_invoices) == 1
    assert raw.erp_invoices[0]["issue"] == "2024-01-22T10:30:00Z"
    assert raw.stats.malformed_ts_corrected >= 1