"""Corrupt the master orders into the STORE (storefront OMS) NDJSON export.

Corruption applied (all deterministic, per-order PRNG):
  * placed_at jitter +0..120s on ~30% of orders (recovery trivial; ETL reads
    the file value verbatim).
  * email has upper-case + leading/trailing whitespace noise.
  * ~1.5% orders have shipping_cents missing (null) — total unaffected.
  * ~0.5% orders have ship_to.region missing.
  * a few orders carry a us_mdy placed_at (non-canonical format, recoverable).
  * two raw reject lines (malformed JSON) are placed in the first month file so
    the parser must reject them (records_rejected).

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

import datetime as dt

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile, noise_email
from generator.master_seed import prng


def generate(master: Master) -> SourceBundle:
    by_month: dict = {}
    order_idx = 0
    count = 0
    missing_ship = 0
    missing_region = 0
    malformed = 0
    for o in master.orders:
        order_idx += 1
        rng = prng(f"store:{order_idx}")
        placed = dt.datetime.fromisoformat(o.placed_ts[:-1])
        placed_at = o.placed_ts
        if rng.random() < 0.30:
            placed_at = (placed + dt.timedelta(seconds=rng.randint(0, 120))).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        if order_idx % 133 == 0:
            placed_at = placed.strftime("%m/%d/%Y %H:%M:%S")
            malformed += 1
        month = placed.strftime("%Y-%m")
        ship = dict(o.ship_to)
        if order_idx % 211 == 0:
            ship.pop("region", None)
            missing_region += 1
        row = {
            "id": o.store_order_id,
            "customer_id": master.indexed["customer_by_key"][o.customer_key].store_customer_id,
            "placed_at": placed_at,
            "status": o.status,
            "channel": o.channel,
            "subtotal_cents": o.subtotal_cents,
            "tax_cents": o.tax_cents,
            "shipping_cents": o.shipping_cents,
            "total_cents": o.total_cents,
            "currency": o.currency,
            "ship_to": ship,
            "email": noise_email(o.email_at_order, rng),
        }
        if o.shipping_cents is None:
            missing_ship += 1
        by_month.setdefault(month, []).append(row)
        count += 1

    files: list = []
    first = True
    for month in sorted(by_month):
        units: list = by_month[month]
        if first:
            first = False
            # two malformed reject lines in the first month
            units = [
                '{"id":"or_000000000000","customer_id":"sc_',
                '{"id":or_000000000001,"placed_at":"not a ts"}',
            ] + units
        files.append(SourceFile(path=f"store/orders_{month}.ndjson", units=units))

    return SourceBundle(
        code="STORE",
        files=files,
        stats={
            "orders": count,
            "missing_shipping_cents": missing_ship,
            "missing_region": missing_region,
            "malformed_timestamps": malformed,
            "rejected": 2,
        },
    )