"""Corrupt master CRM data into three relational CSVs (accounts/contacts/opportunities).

Corruption: US phone formatting, nullable title, and stale values for event
customers (old last_name / phone / email on the contact — the sources haven't
been told the customer changed). One reject row (blank contact_id).

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

from generator.master_model import Master
from generator.sources_base import SourceBundle, SourceFile, us_phone
from generator.master_seed import prng


def generate(master: Master) -> SourceBundle:
    contacts: list = []
    titles_missing = 0
    for c in master.crm_contacts:
        cust = master.indexed["customer_by_key"][c.customer_key]
        last = cust.current["last_name"]
        email = cust.current["email"]
        phone = us_phone(cust.current["phone"])
        if cust.event and cust.event.attribute == "last_name":
            last = cust.event.old_value
        if cust.event and cust.event.attribute == "email":
            email = cust.event.old_value
        if cust.event and cust.event.attribute == "phone":
            phone = us_phone(cust.event.old_value)
        title = c.title if c.title else None
        if title is None:
            titles_missing += 1
        contacts.append({
            "contact_id": c.contact_id,
            "account_id": c.account_id,
            "first_name": cust.current["first_name"],
            "last_name": last,
            "email": email,
            "phone": phone,
            "title": title,
        })

    accounts = [{
        "account_id": a.account_id, "account_name": a.account_name,
        "industry": a.industry, "size_segment": a.size_segment,
        "billing_email": a.billing_email,
    } for a in master.crm_accounts]

    opps = [{
        "opp_id": o.opp_id, "contact_id": o.contact_id,
        "amount_cents": o.amount_cents, "stage": o.stage,
        "close_date": o.close_date,
    } for o in master.crm_opportunities]

    # one reject row: blank contact_id
    contacts = [["", "acct_zz", "No", "Contact", "nobody@example.invalid",
                 None, "N/A"]] + contacts

    return SourceBundle(
        code="CRM",
        files=[
            SourceFile("crm/accounts.csv", accounts),
            SourceFile("crm/contacts.csv", contacts),
            SourceFile("crm/opportunities.csv", opps),
        ],
        stats={
            "accounts": len(accounts),
            "contacts": len(contacts) - 1,
            "opportunities": len(opps),
            "titles_missing": titles_missing,
            "rejected": 1,
        },
    )