"""Corrupt master customers into the MRK (marketing automation) export.

MRK is a STALE snapshot (3 months old) exported as subscribers.csv + sends.csv.
Subscriber values are shown as of the snapshot date, so any event inside the
stale window appears with its OLD value. Email gets case/whitespace noise.

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

import datetime as dt

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile, noise_email
from generator.master_seed import hash_hex, prng

MRK_SNAPSHOT = dt.datetime(2024, 5, 1, 0, 0, 0)
CAMPAIGNS = ["loyalty-reward", "welcome-series", "restock-alert", "seasonal-sale",
             "cross-sell", "winback"]


def _as_of(c, attribute: str) -> str:
    """Value of `attribute` as it appeared at the MRK snapshot (stale window)."""
    current = c.current.get(attribute)
    if c.event and c.event.attribute == attribute:
        ev_ts = dt.datetime.fromisoformat(c.event.ts[:-1])
        if ev_ts >= MRK_SNAPSHOT:
            return c.event.old_value
    return current


def generate(master: Master) -> SourceBundle:
    subscribers = []
    for c in master.customers:
        if not c.has_marketing:
            continue
        rng = prng(f"mrk-sub:{c.customer_key}")
        subscribers.append({
            "profile_id": c.ids["marketing"],
            "email": noise_email(_as_of(c, "email") or c.current["email"], rng),
            "first": _as_of(c, "first_name") or c.current["first_name"],
            "last": _as_of(c, "last_name") or c.current["last_name"],
            "consent_email": bool(c.current.get("consent_email")),
            "consent_sms": bool(c.current.get("consent_sms")),
            "opt_in_ts": c.current.get("opt_in_ts"),
            "list": rng.choice(["loyalty", "newsletter", "prospects"]),
            "postal_code": _as_of(c, "postal") or c.current["postal"],
        })

    sends = []
    for c in master.customers:
        rng = prng(f"mrk-send:{c.customer_key}")
        for j in range(rng.randint(1, 8)):
            pid = c.ids["marketing"]
            if not c.has_marketing:
                break
            sent = dt.datetime.fromisoformat(c.current["created_ts"][:-1]) + dt.timedelta(days=rng.randint(1, 600))
            opened = rng.random() < 0.5
            sends.append({
                "send_id": hash_hex(f"send:{c.customer_key}:{j}", "snd_", 8),
                "profile_id": pid,
                "campaign": rng.choice(CAMPAIGNS),
                "sent_ts": sent.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "opened": opened,
                "clicked": opened and rng.random() < 0.4,
                "converted_amount_cents": rng.randint(1000, 40000) if rng.random() < 0.08 else None,
            })

    return SourceBundle(
        code="MRK",
        files=[
            SourceFile("marketing/subscribers.csv", subscribers),
            SourceFile("marketing/sends.csv", sends),
        ],
        stats={
            "subscribers": len(subscribers),
            "sends": len(sends),
            "stale_window_events": sum(
                1 for c in master.customers
                if c.event and dt.datetime.fromisoformat(c.event.ts[:-1]) >= MRK_SNAPSHOT),
        },
    )