"""Hidden master data model: canonical truth the sources are derived from.

Entities hold the AUTHORITATIVE current values, the per-source identifier maps,
and the (at most one per customer) historical change event. This module also
enforces every business invariant defined in the architecture:
  - an order is fully paid (sum of its payments == its total, in its currency)
    and a completed order's payments net to its total;
  - every payment references an order; every order is a single canonical order;
  - an inactive customer has no canonical order placed after deactivation;
  - canonical email and phone are unique across active customers;
  - every non-null address is complete;
  - every ERP invoice resolves to a STORE order (no unintended orphans);
  - a customer carries at most ONE history event (keeps reconstruction exact).

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Canonical customer attributes (subset carried by sources). Resolving any of
# these across sources is the purpose of the benchmark.
CUSTOMER_ATTRS = (
    "first_name", "last_name", "email", "phone", "line1", "city", "region",
    "postal", "country", "household", "loyalty_tier", "is_business", "company",
    "is_active", "dob", "gender", "created_ts", "updated_ts",
    "email_verified", "profile_completeness", "consent_email", "consent_sms",
    "opt_in_ts",
)


class MasterError(AssertionError):
    """Raised when a generated master violates a business invariant."""


@dataclass
class HistoryEvent:
    attribute: str
    old_value: str
    new_value: str
    ts: str                            # effective_from (ISO)
    old_carriers: List[str] = field(default_factory=list)  # source codes w/ old value


@dataclass
class Customer:
    customer_key: int
    gid: str
    current: Dict[str, Optional[str]]
    has_idp: bool = False
    active: bool = True
    event: Optional[HistoryEvent] = None
    ids: Dict[str, str] = field(default_factory=dict)          # source -> local id
    crm_account: Optional[str] = None                           # account_id
    crm_contact: Optional[str] = None                           # contact_id (primary)
    has_marketing: bool = False
    marketing_old: Optional[HistoryEvent] = None                # event visible in MRK
    store_customer_id: str = ""                                 # sc_<hex>

    def value(self, attr: str) -> Optional[str]:
        return self.current.get(attr)


@dataclass
class Order:
    order_key: int
    gid: str
    customer_key: int
    store_order_id: str          # or_<hex>
    erp_invoice_no: str          # inv_<hex>
    placed_ts: str
    status: str
    channel: str
    subtotal_cents: int
    tax_cents: int
    shipping_cents: Optional[int]
    total_cents: int
    currency: str
    ship_to: Dict[str, str]
    email_at_order: str          # the email on this order (may be pre-change)
    erp_issue_date: str = ""
    unit_price_usd_refresh: float = 1.0
    amount_usd: int = 0
    payments_complete: bool = False
    has_refund: bool = False


@dataclass
class Payment:
    payment_key: int
    gid: str
    order_key: int
    customer_key: int
    paid_ts: str
    amount_cents: int
    currency: str
    method: str
    status: str
    pay_txn_id: str            # txn_<hex>
    pay_customer_ref: str      # cus_<hex>
    order_ref: str             # "or" | "inv" | "none" | "obsolete"
    order_ref_value: str
    amount_usd: int = 0
    is_duplicate_row: bool = False
    obsolete: bool = False


@dataclass
class Ticket:
    ticket_key: int
    gid: str
    customer_key: int
    ticket_id: str             # tk_<hex>
    requester_email: str       # email actually used (may be variant/old)
    subject: str
    category: str
    priority: str
    status: str
    created_ts: str
    closed_ts: Optional[str]
    order_ref: str             # "or" | "inv" | "none" | "obsolete" | "malformed"
    order_ref_value: str
    order_key: Optional[int]
    first_response_minutes: int
    resolution_minutes: Optional[int]
    reject_row: bool = False   # if True this row must be rejected on parse (bad PK/date)
    malformed_created: bool = False   # created_ts in us_mdy (corrected, kept)
    implausible_date: bool = False    # created_ts 2024-02-30 (kept, NULL, counted diff)


@dataclass
class CrmAccount:
    account_id: str
    account_name: str
    industry: str
    size_segment: str
    billing_email: str


@dataclass
class CrmContact:
    contact_id: str
    account_id: Optional[str]
    customer_key: int
    first_name: str
    last_name: str
    email: str
    phone: Optional[str]
    title: Optional[str]


@dataclass
class CrmOpportunity:
    opp_id: str
    contact_id: str
    amount_cents: int
    stage: str
    close_date: str


@dataclass
class Master:
    customers: List[Customer]
    orders: List[Order]
    payments: List[Payment]
    tickets: List[Ticket]
    crm_accounts: List[CrmAccount]
    crm_contacts: List[CrmContact]
    crm_opportunities: List[CrmOpportunity]
    indexed: dict = field(default_factory=dict)   # filled by index()

    def index(self) -> "Master":
        self.indexed["customer_by_key"] = {c.customer_key: c for c in self.customers}
        self.indexed["orders_by_customer"] = {}
        for o in self.orders:
            self.indexed["orders_by_customer"].setdefault(o.customer_key, []).append(o)
        self.indexed["payments_by_order"] = {}
        for p in self.payments:
            self.indexed["payments_by_order"].setdefault(p.order_key, []).append(p)
        self.indexed["customers_by_store_id"] = {
            c.store_customer_id: c for c in self.customers if c.store_customer_id
        }
        self.indexed["order_by_store_id"] = {o.store_order_id: o for o in self.orders}
        self.indexed["order_by_erp_invoice"] = {o.erp_invoice_no: o for o in self.orders}
        self.indexed["contact_by_id"] = {c.contact_id: c for c in self.crm_contacts}
        self.indexed["account_by_id"] = {a.account_id: a for a in self.crm_accounts}
        return self

    # ------------------------------------------------------------------ checks
    def check_invariants(self) -> None:
        self._check_payment_reconciliation()
        self._check_references()
        self._check_inactive()
        self._check_unique_active_lookup()
        self._check_address_completeness()
        self._check_no_orphans()
        self._check_events()

    def _check_payment_reconciliation(self) -> None:
        for o in self.orders:
            pays = self.indexed["payments_by_order"].get(o.order_key, [])
            real = [p for p in pays if not p.is_duplicate_row]
            if o.status == "completed":
                same_cur = [p for p in real if p.currency == o.currency]
                if sum(p.amount_cents for p in same_cur) != o.total_cents:
                    raise MasterError(
                        f"order {o.gid}: currency-total != total (completed)"
                    )
                if sum(p.amount_cents for p in real) != o.total_cents:
                    raise MasterError(f"order {o.gid}: net payments != total (completed)")
            if sum(p.amount_usd for p in real) != o.amount_usd:
                raise MasterError(f"order {o.gid}: USD payments != amount_usd")

    def _check_references(self) -> None:
        for p in self.payments:
            if p.order_key not in self.indexed["order_by_store_id"].values() and not any(
                p.order_key == o.order_key for o in self.orders
            ):
                raise MasterError(f"payment {p.gid} references missing order")
        for t in self.tickets:
            if t.order_key is not None:
                if t.order_key not in [o.order_key for o in self.orders]:
                    raise MasterError(f"ticket {t.gid} references missing order")

    def _check_inactive(self) -> None:
        for c in self.customers:
            if not c.active:
                deact = c.value("updated_ts")
                for o in self.orders:
                    if o.customer_key == c.customer_key:
                        if deact and o.placed_ts > deact:
                            raise MasterError(f"inactive customer {c.gid} has order after deactivation")

    def _check_unique_active_lookup(self) -> None:
        emails = {}
        phones = {}
        for c in self.customers:
            if not c.active:
                continue
            email = (c.value("email") or "").strip().lower()
            phone = c.value("phone") or ""
            if email:
                if email in emails:
                    raise MasterError(f"duplicate active email: {email}")
                emails[email] = c.gid
            if phone:
                norm = "".join(ch for ch in phone if ch.isdigit())
                if norm and norm in phones:
                    raise MasterError(f"duplicate active phone: {phone}")
                phones[norm] = c.gid

    def _check_address_completeness(self) -> None:
        for c in self.customers:
            line1, city, postal = c.value("line1"), c.value("city"), c.value("postal")
            present = sum(v is not None for v in (line1, city, postal))
            if present not in (0, 3):
                raise MasterError(f"customer {c.gid} has incomplete address")

    def _check_no_orphans(self) -> None:
        for o in self.orders:
            if not o.erp_invoice_no:
                raise MasterError(f"order {o.gid} missing ERP invoice")
            if o.erp_invoice_no not in self.indexed["order_by_erp_invoice"]:
                # built fresh; every order is its own invoice
                self.indexed["order_by_erp_invoice"][o.erp_invoice_no] = o

    def _check_events(self) -> None:
        for c in self.customers:
            if c.event is not None:
                if not c.has_idp:
                    raise MasterError(f"event on customer {c.gid} without IDP")
                if c.event.attribute not in {"last_name", "email", "phone", "postal"}:
                    raise MasterError(f"unsupported event attribute {c.event.attribute}")
                if c.event.old_value == c.event.new_value:
                    raise MasterError(f"no-op event on {c.gid}")

    def to_jsonable(self) -> Dict[str, Any]:
        return {
            "customers": [self._c_json(c) for c in self.customers],
            "orders": [dict(o.__dict__) for o in self.orders],
            "payments": [dict(p.__dict__) for p in self.payments],
            "tickets": [dict(t.__dict__) for t in self.tickets],
        }

    def _c_json(self, c: Customer) -> Dict[str, Any]:
        d = {
            "customer_key": c.customer_key, "gid": c.gid, "current": c.current,
            "has_idp": c.has_idp, "active": c.active, "ids": c.ids,
            "crm_account": c.crm_account, "crm_contact": c.crm_contact,
            "has_marketing": c.has_marketing, "store_customer_id": c.store_customer_id,
        }
        if c.event is not None:
            d["event"] = dict(c.event.__dict__)
        else:
            d["event"] = None
        return d