"""Corrupt master orders into ERP back-office exports: nested month folders of
invoices.csv + shipments.csv, with US MM/DD/YYYY dates.

Corruption: US dates (recovered via secondary format), one reject row (bad
total), one invoice with its total in CAD while customer is CAD (currency is
per-row, correct). Shipments generated one-per-order for non-cancelled orders.

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

import datetime as dt

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile, iso_to_mdy
from generator.master_seed import hash_hex, prng


def generate(master: Master) -> SourceBundle:
    invoices: dict = {}
    shipments: dict = {}
    n_inv = 0
    n_shp = 0
    for o in master.orders:
        cust = master.indexed["customer_by_key"][o.customer_key]
        month = o.erp_issue_date[:7]
        issue = o.erp_issue_date
        irow = {
            "invoice_no": o.erp_invoice_no,
            "customer_internal_id": cust.ids["erp_internal"],
            "bill_to": o.ship_to["name"],
            "total_cents": o.total_cents,
            "currency": o.currency,
            "issue_date": iso_to_mdy(issue),
        }
        invoices.setdefault(month, []).append(irow)
        n_inv += 1

        if o.status != "cancelled":
            rng = prng(f"erp-shp:{o.order_key}")
            ship_date = dt.datetime.fromisoformat(o.erp_issue_date[:-1])
            srow = {
                "shipment_no": hash_hex(f"shp:{o.order_key}", "shp_", 8),
                "invoice_no": o.erp_invoice_no,
                "ship_date": ship_date.strftime("%m/%d/%Y"),
                "carrier": rng.choice(["UPS", "FedEx", "USPS", "DHL"]),
                "tracking_no": rng.choice(["1Z" + "".join(str(rng.randint(0, 9)) for _ in range(16)),
                                           "9400" + "".join(str(rng.randint(0, 9)) for _ in range(16))]),
                "deliver_to": o.ship_to["name"],
            }
            shipments.setdefault(month, []).append(srow)
            n_shp += 1

    # inject one reject row (bad total) into the earliest month's invoice list
    reject_row = {
        "invoice_no": hash_hex("reject-inv", "inv_", 8),
        "customer_internal_id": "00000", "bill_to": "REJECTED",
        "total_cents": "oops", "currency": "USD", "issue_date": "07/15/2024",
    }
    first_month = min(invoices)
    invoices[first_month].insert(0, reject_row)

    files: list = []
    for month in sorted(invoices):
        files.append(SourceFile(f"erp/{month}/invoices.csv", invoices[month]))
        files.append(SourceFile(f"erp/{month}/shipments.csv", shipments.get(month, [])))

    return SourceBundle(
        code="ERP",
        files=files,
        stats={"invoices": n_inv, "shipments": n_shp, "rejected": 1},
    )