"""Focused oracle tests for weak/composite entity matching and output
date_only serialization.

These tests exercise the private oracle implementation (etl.link / etl.pipeline)
directly. They deliberately live OUTSIDE ``tests/`` (the byte-equality verifier
surface, which must not import implementation code).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from etl.link import link  # noqa: E402
from etl.model import Customer  # noqa: E402
from etl.parse import Raw  # noqa: E402
from etl.pipeline import _format  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402


@pytest.fixture(scope="session")
def rules():
    return load_rules(str(ROOT / "config" / "dictionary.toml"))


def _identity(sub, **kw):
    base = {"type": "identity", "src": "IDP", "sub": sub, "email": None,
            "phone": None, "given_name": None, "family_name": None,
            "name": None, "dob": None, "gender": None, "household": None,
            "created": None, "updated": None, "is_active": True}
    base.update(kw)
    return base


def _cat(cust_no, **kw):
    base = {"type": "cat", "src": "CAT", "cust_no": cust_no, "email": None,
            "first_name": None, "last_name": None, "name": None, "phone": None,
            "line1": None, "city": None, "region": None, "postal": None,
            "country": None, "loyalty_tier": None, "created": None}
    base.update(kw)
    return base


def _emails(rows):
    return sorted(c.email for c in rows if c.email)


def _mrk(profile_id, **kw):
    base = {"type": "subscriber", "src": "MRK", "profile_id": profile_id,
            "email": None, "first_name": None, "last_name": None,
            "name": None, "postal": None}
    base.update(kw)
    return base


def test_strong_email_alone_merges(rules):
    raw = Raw(identities=[
        _identity("idp_a", email="a@x.com", given_name="Ann", family_name="Able"),
        _identity("idp_b", email="a@x.com", given_name="Bob", family_name="Baker"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1


def test_weak_name_plus_postal_merges(rules):
    raw = Raw(identities=[
        _identity("idp_a", name="Ann Able", postal="10001"),
        _identity("idp_b", name="Ann Able", postal="10001"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1


def test_weak_first_name_postal_dob_merges(rules):
    raw = Raw(identities=[
        _identity("idp_a", first_name="Ann", postal="10001", dob="1990-01-01"),
        _identity("idp_b", first_name="Ann", postal="10001", dob="1990-01-01"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1


def test_two_of_three_alone_does_not_merge(rules):
    raw = Raw(identities=[
        _identity("idp_a", first_name="Ann", postal="10001", dob=None),
        _identity("idp_b", first_name="Ann", postal="20002", dob=None),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_partial_combo_missing_postal_does_not_merge(rules):
    raw = Raw(identities=[
        _identity("idp_a", first_name="Ann", postal=None, dob="1990-01-01"),
        _identity("idp_b", first_name="Ann", postal=None, dob="1990-01-01"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_name_postal_but_three_field_combo_only_partial(rules):
    raw = Raw(identities=[
        _identity("idp_a", name="Ann", postal="10001"),
        _identity("idp_b", name="Bob", postal="10001"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_mixed_sources_partial_fields_do_not_merge(rules):
    raw = Raw(
        identities=[_identity("idp_a", first_name="Ann", dob="1990-01-01")],
        cat_rows=[_cat("C1", first_name="Ann", postal="10001")],
    )
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_all_three_present_in_both_merges_across_sources(rules):
    raw = Raw(
        identities=[_identity("idp_a", first_name="Ann", postal="10001", dob="1990-01-01")],
        cat_rows=[_cat("C1", first_name="Ann", postal="10001", dob="1990-01-01")],
    )
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1


def test_cat_only_same_name_postal_merges(rules):
    raw = Raw(cat_rows=[
        _cat("C1", name="Ann Able", postal="10001"),
        _cat("C2", name="Ann Able", postal="10001"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 1


def test_cat_only_same_postal_only_does_not_merge(rules):
    raw = Raw(cat_rows=[
        _cat("C1", name="Ann Able", postal="10001"),
        _cat("C2", name="Bob Baker", postal="10001"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_weak_needs_both_present_in_both_nodes(rules):
    raw = Raw(identities=[
        _identity("idp_a", name="Ann", postal=None),
        _identity("idp_b", name="Ann", postal="10001"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_no_shared_signals_keeps_distinct(rules):
    raw = Raw(identities=[
        _identity("idp_a", name="Ann", postal="10001"),
        _identity("idp_b", name="Bob", postal="20002"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_weak_not_merged_when_only_first_name_and_dob(rules):
    raw = Raw(identities=[
        _identity("idp_a", first_name="Ann", dob="1990-01-01"),
        _identity("idp_b", first_name="Ann", dob="1990-01-01"),
    ])
    customers, _, _ = link(raw, rules)
    assert len(customers) == 2


def test_weak_singleton_attaches_to_existing_strong_component(rules):
    raw = Raw(
        identities=[_identity("idp_a", email="sarah@example.com",
                              name="Sarah Lee")],
        cat_rows=[_cat("C1", email="sarah@example.com",
                       name="Sarah Lee", postal="10001")],
        mrk_subscribers=[_mrk("mk_a", email="prior.sarah@example.com",
                              first_name="Sarah", last_name="Lee",
                              name="Sarah Lee", postal="10001")],
    )
    customers, _, email_to_customer = link(raw, rules)
    assert len(customers) == 1
    assert email_to_customer["sarah@example.com"] == 1
    assert email_to_customer["prior.sarah@example.com"] == 1


def test_weak_singleton_without_shared_combo_stays_separate(rules):
    raw = Raw(
        identities=[_identity("idp_a", email="sarah@example.com",
                              name="Sarah Lee")],
        cat_rows=[_cat("C1", email="sarah@example.com",
                       name="Sarah Lee", postal="10001")],
        mrk_subscribers=[_mrk("mk_a", email="prior.sarah@example.com",
                              first_name="Sarah", last_name="Lee",
                              name="Sarah Lee", postal="20002")],
    )
    customers, _, email_to_customer = link(raw, rules)
    assert len(customers) == 2
    assert email_to_customer["sarah@example.com"] == 1
    assert email_to_customer["prior.sarah@example.com"] == 2
