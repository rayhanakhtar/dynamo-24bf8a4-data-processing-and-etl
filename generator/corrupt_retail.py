"""Corrupt master customers into the CAT (retail customer master / POS) export.

Corruption:
  * numeric local ids (cust_no);
  * US-format phone;
  * email case/whitespace noise on a subset;
  * event customers carry the OLD value for their event attribute (CAT hasn't
    been told about the change — renamed name, old phone, old postal);
  * ~2% duplicate-household profiles: an extra row with the SAME phone+postal
    and a name variant but a different email, which must be merged by entity
    resolution.

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile, noise_email, us_phone
from generator.master_seed import prng


def _dup_candidates(master: Master):
    with_phone = [c for c in master.customers
                  if c.customer_key % 37 == 5 and c.current.get("phone")]
    return with_phone[:8]


def generate(master: Master) -> SourceBundle:
    rows = []
    for c in master.customers:
        rng = prng(f"retail:{c.customer_key}")
        last = c.current["last_name"]
        phone = us_phone(c.current["phone"])
        postal = c.current["postal"]
        if c.event and c.event.attribute == "last_name":
            last = c.event.old_value
        if c.event and c.event.attribute == "phone":
            phone = us_phone(c.event.old_value)
        if c.event and c.event.attribute == "postal":
            postal = c.event.old_value
        rows.append({
            "cust_no": c.ids["retail"],
            "email": noise_email(c.current["email"], rng),
            "first_name": c.current["first_name"],
            "last_name": last,
            "phone": phone,
            "line1": c.current["line1"],
            "city": c.current["city"],
            "region": c.current["region"],
            "postal_code": postal,
            "country": c.current["country"],
            "loyalty_tier": c.current["loyalty_tier"],
            "created_ts": c.current["created_ts"],
        })

    # duplicate-household profiles (share phone + postal, differ by email/name)
    for pos, c in enumerate(_dup_candidates(master)):
        alt_email = f"{c.current['first_name'][0].lower()}.{c.current['last_name'].lower()}.{c.customer_key}@corp.example"
        rows.append({
            "cust_no": str(int(c.ids["retail"]) + 90000),
            "email": alt_email,
            "first_name": f"{c.current['first_name'][0]}.",
            "last_name": c.current["last_name"],
            "phone": us_phone(c.current["phone"]),
            "line1": c.current["line1"],
            "city": c.current["city"],
            "region": c.current["region"],
            "postal_code": c.current["postal"],
            "country": c.current["country"],
            "loyalty_tier": c.current["loyalty_tier"],
            "created_ts": c.current["created_ts"],
        })

    return SourceBundle(
        code="CAT",
        files=[SourceFile("retail/customers.csv", rows)],
        stats={
            "customers": len(master.customers),
            "duplicate_households": len(_dup_candidates(master)),
        },
    )