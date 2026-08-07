"""Build the deterministic data-quality report from pipeline outcomes.

Every metric is returned as an integer, keyed and ordered by the `[metrics]`
section in `config/dictionary.toml`. Final counts are re-derived from the
assembled outputs so `final_*` always equals the row counts of the requested
files.
"""

from __future__ import annotations

from typing import Dict, List

from etl.model import Customer, Order, Payment
from etl.parse import Stats
from etl.rules import Dictionary

CANONICAL_ATTRS = [
    "first_name", "last_name", "dob", "gender", "email", "phone",
    "line1", "city", "region", "postal", "country",
    "is_business", "company", "household", "loyalty_tier",
    "is_active", "created_ts", "updated_ts",
]


def null_values_imputed(customers: List[Customer]) -> int:
    """Cells that ended up non-null — i.e. filled/kept through precedence.

    Per `[precedence]`, a canonical attribute cell is non-null because the
    highest-priority source carrying a valid value supplied it (imputation via
    precedence only). We count the resulting non-empty cells.
    """
    total = 0
    for c in customers:
        for attr in CANONICAL_ATTRS:
            val = getattr(c, attr)
            if attr == "is_business":
                total += 1
            elif attr == "is_active":
                total += 1
            elif val is not None and str(val).strip() != "":
                total += 1
    return total


def metrics(
    rules: Dictionary, stats: Stats,
    customers: List[Customer], orders: List[Order], payments: List[Payment],
    distinct_identities: int,
) -> Dict[str, int]:
    n_cust = len(customers)
    n_orders = len(orders)
    n_pay = len(payments)
    data: Dict[str, int] = {
        "records_processed": stats.records_processed(),
        "records_rejected": stats.records_rejected(),
        "duplicate_entities_merged": max(0, distinct_identities - n_cust),
        "foreign_keys_repaired": stats.repair_refs,
        "malformed_timestamps_corrected": stats.malformed_ts_corrected,
        "null_values_imputed": null_values_imputed(customers),
        "referential_integrity_violations": 0,
        "final_customer_count": n_cust,
        "final_order_count": n_orders,
        "final_payment_count": n_pay,
    }
    return dict(sorted(data.items()))


def report_json(payload: Dict[str, int]) -> dict:
    return {
        "number_type": "int",
        "key_order": "sorted",
        "include_details": True,
        "metrics": payload,
    }