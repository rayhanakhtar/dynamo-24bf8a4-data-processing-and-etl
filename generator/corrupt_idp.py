"""Corrupt master customers into the IDP (identity platform) NDJSON export.

IDP is the HIGHEST-priority source and always reports the canonical CURRENT
value (post-event). One record per customer that has an IDP account (~60%).

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile


def generate(master: Master) -> SourceBundle:
    rows = []
    for c in master.customers:
        if not c.has_idp:
            continue
        status = "active" if c.active else "marked_for_deletion"
        rows.append({
            "sub": c.ids["idp"],
            "email": c.current["email"],
            "email_verified": bool(c.current.get("email_verified")),
            "phone": c.current["phone"],
            "given_name": c.current["first_name"],
            "family_name": c.current["last_name"],
            "dob": c.current["dob"],
            "gender": c.current["gender"],
            "household": c.current.get("household"),
            "created_ts": c.current["created_ts"],
            "updated_ts": c.current["updated_ts"],
            "status": status,
            "profile_completeness": c.current.get("profile_completeness"),
        })

    return SourceBundle(
        code="IDP",
        files=[SourceFile("idp/identities.ndjson", rows)],
        stats={"identities": len(rows), "marked_for_deletion": sum(
            1 for c in master.customers if c.has_idp and not c.active)},
    )