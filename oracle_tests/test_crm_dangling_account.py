"""Focused oracle test: CRM contact with dangling account_id -> empty company.

The dictionary specifies that company maps to the CRM account_name referenced
by the contact's account_id. If the account_id does not exist in accounts.csv,
the company field must be empty (not invented).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from etl.parse import Raw, Stats  # noqa: E402
from etl.link import link  # noqa: E402
from etl.model import Customer  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402


@pytest.fixture(scope="session")
def rules():
    return load_rules(str(ROOT / "config" / "dictionary.toml"))


def _crm_contact(contact_id: str, account_id: str | None = None, **kw) -> dict:
    base = {"type": "contact", "src": "CRM", "contact_id": contact_id,
            "account_id": account_id, "first_name": None, "last_name": None,
            "name": None, "email": None, "phone": None, "title": None}
    base.update(kw)
    if base["first_name"] and base["last_name"]:
        base["name"] = f"{base['first_name']} {base['last_name']}"
    return base


def _crm_account(account_id: str, account_name: str) -> dict:
    return {"type": "account", "src": "CRM", "account_id": account_id,
            "account_name": account_name, "billing_email": None}


def test_crm_contact_with_valid_account_id_gets_company(rules):
    """Contact with existing account_id -> company = account_name."""
    raw = Raw(
        crm_contacts=[_crm_contact("c1", "acc_1", first_name="John", last_name="Doe",
                                   email="john@example.com")],
        crm_accounts=[_crm_account("acc_1", "Acme Corp")],
    )
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1
    assert customers[0].company == "Acme Corp"


def test_crm_contact_with_dangling_account_id_empty_company(rules):
    """Contact with non-existent account_id -> company = empty."""
    raw = Raw(
        crm_contacts=[_crm_contact("c2", "acc_missing", first_name="Jane", last_name="Smith",
                                   email="jane@example.com")],
        crm_accounts=[_crm_account("acc_1", "Acme Corp")],  # different account_id
    )
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1
    assert customers[0].company == "" or customers[0].company is None


def test_crm_contact_without_account_id_empty_company(rules):
    """Contact with no account_id -> company = empty."""
    raw = Raw(
        crm_contacts=[_crm_contact("c3", None, first_name="Bob", last_name="Brown",
                                   email="bob@example.com")],
        crm_accounts=[_crm_account("acc_1", "Acme Corp")],
    )
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1
    assert customers[0].company == "" or customers[0].company is None


def test_multiple_contacts_same_account_different_customers(rules):
    """Different contacts, same account -> each gets company (separate customers)."""
    raw = Raw(
        crm_contacts=[
            _crm_contact("c4", "acc_2", first_name="Alice", last_name="Adams",
                         email="alice@example.com"),
            _crm_contact("c5", "acc_2", first_name="Carol", last_name="Carter",
                         email="carol@example.com"),
        ],
        crm_accounts=[_crm_account("acc_2", "Beta Inc")],
    )
    customers, _, _ = link(raw, rules)
    # Two separate contacts -> two customers (different emails)
    assert len(customers) == 2
    for c in customers:
        assert c.company == "Beta Inc"


def test_crm_dangling_account_id_with_other_sources_merges(rules):
    """Dangling account_id should not break merging with other sources."""
    raw = Raw(
        crm_contacts=[_crm_contact("c6", "acc_missing", first_name="Dave", last_name="Davis",
                                   email="dave@example.com")],
        crm_accounts=[_crm_account("acc_1", "Acme Corp")],
        identities=[{
            "type": "identity", "src": "IDP", "sub": "idp_1",
            "email": "dave@example.com", "phone": None,
            "given_name": "Dave", "family_name": "Davis",
            "name": "Dave Davis", "dob": None, "gender": None,
            "household": None, "created": None, "updated": None,
            "status": "active", "email_verified": None,
        }],
    )
    customers, _, _ = link(raw, rules)
    # Should merge into one customer (IDP + CRM share email)
    assert len(customers) == 1
    # Company should be empty because CRM account_id doesn't resolve
    assert customers[0].company == "" or customers[0].company is None