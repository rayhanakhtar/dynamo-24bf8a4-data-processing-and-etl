"""Corrupt master tickets into the SUP (support helpdesk) CSV export.

Corruption:
  * `created_ts` occasionally in US MM/DD/YYYY (recovered, secondary format).
  * one implausible date (2024-02-30) -> date becomes NULL, row kept.
  * `closed_ts` empty for open tickets.
  * `order_ref` may be or_/inv_/obsolete/none/malformed (uppercase dash form).
  * reject rows: ticket_id left blank (must be rejected on parse).
  * requester_email may be a prior/old email or the account billing email,
    which exercises entity resolution.

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile


def generate(master: Master) -> SourceBundle:
    cust_of = {o.order_key: o for o in master.orders}
    rows = []
    for t in master.tickets:
        cust = master.indexed["customer_by_key"][t.customer_key]
        ref_val = t.order_ref_value
        rows.append({
            "ticket_id": t.ticket_id,
            "requester_email": t.requester_email,
            "subject": t.subject,
            "category": t.category,
            "priority": t.priority,
            "status": t.status,
            "created_ts": t.created_ts,
            "closed_ts": t.closed_ts,
            "order_ref": ref_val,
            "first_response_minutes": t.first_response_minutes,
            "resolution_minutes": t.resolution_minutes,
        })
    return SourceBundle(
        code="SUP",
        files=[SourceFile("support/tickets.csv", rows)],
        stats={
            "tickets": len(master.tickets),
            "open": sum(1 for t in master.tickets if t.status == "open"),
            "malformed_timestamps": sum(1 for t in master.tickets if t.malformed_created),
            "implausible": sum(1 for t in master.tickets if t.implausible_date),
            "obsolete_refs": sum(1 for t in master.tickets if t.order_ref == "obsolete"),
            "malformed_refs": sum(1 for t in master.tickets if t.order_ref == "malformed"),
            "rejected": sum(1 for t in master.tickets if t.reject_row),
        },
    )