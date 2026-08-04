"""Corrupt the master payments into the PAY (payment gateway) CSV export.

Corruption applied (deterministic, decided during master generation and tracked
on each Payment):
  * schema drift: months 2023 use header `created_ts`, months 2024 use
    `processed_at` — a naive reader breaks.
  * ~4% of payments carry NO order reference (order_reference empty) — must be
    linked via customer_ref + timestamp proximity.
  * ~3% carry an OBSOLETE order reference (points to an order not in the loaded
    universe) — repaired to NULL and counted, not invented.
  * ~1.5% of captures are followed by an EXACT duplicate row (different txn_id)
    — must be deduplicated.
  * negative amount_cents = refunds.
  * two comment header lines per file, plus one reject row (unparseable amount)
    and one reject row (blank txn_id) spread over the files.

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile
from generator.master_seed import prng

FIELDS = ["txn_id", "customer_ref", "order_reference", "ts", "amount_cents",
          "currency", "method", "status"]


def generate(master: Master) -> SourceBundle:
    by_month: dict = {}
    reject_a = True
    reject_b = True
    dup = 0
    obsolete = 0
    none_ref = 0
    for idx, p in enumerate(master.payments):
        month = p.paid_ts[:7]
        ts_field = "created_ts" if p.paid_ts[:4] == "2023" else "processed_at"
        ref = p.order_ref_value
        if p.order_ref == "none":
            ref = ""
            none_ref += 1
        if p.order_ref == "obsolete":
            obsolete += 1
        if p.order_ref_value and p.order_ref == "or":
            ref = p.order_ref_value
        row = {
            "txn_id": p.pay_txn_id,
            "customer_ref": p.pay_customer_ref,
            "order_reference": ref,
            ts_field: p.paid_ts,
            "amount_cents": p.amount_cents,
            "currency": p.currency,
            "method": p.method,
            "status": p.status,
        }
        if p.is_duplicate_row:
            dup += 1
        by_month.setdefault(month, []).append(row)

    files: list = []
    for i, month in enumerate(sorted(by_month)):
        units: list = ["# export generated 2024-08-01", "# gateway: northstar-pay", ""]
        rows = by_month[month]
        # inject reject rows once each across the file set
        if reject_a:
            reject_a = False
            rows = rows[:2] + [["txn_bad00", "cus_bad00", "or_000000000000",
                                "2024-01-05T10:00:00Z", "N/A", "USD", "card", "succeeded"]] + rows[2:]
        if reject_b and i == max(0, len(by_month) - 1):
            reject_b = False
            rows = rows[:1] + [["", "cus_bad01", "or_000000000001",
                                "2024-01-06T10:00:00Z", "500", "USD", "card", "succeeded"]] + rows[1:]
        units += rows
        files.append(SourceFile(path=f"pay/payments_{month}.csv", units=units))

    return SourceBundle(
        code="PAY",
        files=files,
        stats={
            "payments": sum(len(x) for x in by_month.values()) - 0,
            "duplicate_rows": dup,
            "obsolete_refs": obsolete,
            "none_refs": none_ref,
            "rejected": 2,
        },
    )