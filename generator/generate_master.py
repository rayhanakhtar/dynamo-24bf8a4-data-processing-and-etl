"""Builds the hidden master dataset deterministically.

SINGLE entry point at defend: build_master(). It produces not just the canonical
customers/orders/payments/tickets but also the CRM accounts+contacts+opportunities
and every per-source identifier, plus the historical change events that later
sources will encode. All variation comes from per-record PRNGs (master_seed.py);
there is no global random state, so output is byte-stable.

The authoritative "current" values are what IDP reports; lower-priority sources
carry either the current value or the OLD value for an event's attribute. This is
what makes the oracle's history reconstruction provably exact.

PRIVATE module: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

from generator.master_model import (
    CrmAccount, CrmContact, CrmOpportunity, Customer, HistoryEvent, Master,
    Order, Payment, Ticket,
)
from generator.master_seed import hash_hex, numeric_id, prng
from etl.rules import load as load_rules

_RULES = load_rules()
_CAD_RATE = _RULES.currency_rate("CAD")
_ROUNDING = _RULES.rounding()

N_CUSTOMERS = 400
WINDOW_START = dt.datetime(2023, 1, 1, 0, 0, 0)
WINDOW_END = dt.datetime(2024, 8, 1, 0, 0, 0)
MRK_SNAPSHOT = dt.datetime(2024, 5, 1, 0, 0, 0)   # MRK export is 3 months old

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "Michael", "Jennifer", "William",
    "Linda", "David", "Elizabeth", "Richard", "Barbara", "Joseph", "Susan",
    "Thomas", "Jessica", "Charles", "Sarah", "Christopher", "Karen", "Daniel",
    "Nancy", "Matthew", "Lisa", "Anthony", "Margaret", "Mark", "Betty",
    "Jordan", "Emily", "Grace", "Noah", "Olivia", "Liam", "Emma", "Mason",
    "Ava", "Lucas", "Mia", "Ethan", "Amelia",
]
LAST_NAMES = [
    "Miller", "Johnson", "Smith", "Williams", "Brown", "Jones", "Garcia",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Hall", "Allen",
    "King", "Wright", "Quinn", "Reed", "Brooks", "Carter", "Hayes",
]
CITIES = {
    "OR": [("Portland", "97201", "97209"), ("Bend", "97701", "97702")],
    "WA": [("Seattle", "98101", "98109"), ("Spokane", "99201", "99202")],
    "CA": [("San Francisco", "94102", "94107"), ("Sacramento", "95814", "95819")],
    "NY": [("New York", "10001", "10025")],
    "IL": [("Chicago", "60601", "60611")],
    "TX": [("Austin", "73301", "78701")],
}
REGIONS = list(CITIES)
STREETS = ["Harbor Rd", "Main St", "Oak Ave", "Sunset Blvd", "Park Ln",
           "Cedar Ct", "River Dr", "Mountain View", "Elm St", "Maple Ave",
           "Lake Shore", "Grange Way"]
COMPANIES = ["Trailside Distributors", "Northpeak Trading", "Blue Harbor LLC",
             "Summit Procurement", "Lakeview Outfitters", "Cascade Supply Co"]
INDUSTRIES = ["Wholesale", "Retail", "Manufacturing", "Logistics", "Hospitality"]
TICKET_CATEGORY = ["fulfillment", "billing", "product", "account", "shipping"]
TICKET_PRIORITY = ["low", "medium", "high", "urgent"]
CHANNELS = ["web", "app", "phone", "retail"]


def _iso(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_half_up(x: float) -> int:
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def _usd(amount_cents: int, currency: str) -> int:
    if currency == "USD":
        return amount_cents
    if currency == "CAD":
        return _round_half_up(amount_cents * _CAD_RATE)
    raise ValueError(currency)


class UsedGuard:
    """Guarantees identity fields never collide across clusters (prevents
    false entity merges) — deterministically avoids unless regenerating."""

    def __init__(self) -> None:
        self.emails: set[str] = set()
        self.phones: set[str] = set()

    def reserve_email(self, email: str) -> None:
        self.emails.add(email.lower())

    def reserve_phone(self, phone: str) -> None:
        self.phones.add(phone)


def _email(first: str, last: str, i: int, rng) -> str:
    domain = rng.choice(["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"])
    local = f"{first.lower()}.{last.lower()}"
    if rng.random() < 0.75:
        sep = rng.choice([".", "_", "-", ""])
        local = f"{local}{sep}{rng.randint(2, 999)}"
    return f"{local}@{domain}"


def _phone(i: int, guard: UsedGuard) -> str:
    rng = prng(f"phone:{i}")
    area = rng.choice(["503", "206", "415", "917", "312", "512"])
    while True:
        mid = rng.randint(200, 999)
        last = rng.randint(1000, 9999)
        digits = f"{area}{mid}{last}"
        if digits not in guard.phones:
            guard.phones.add(digits)
            return f"+1 {area} {mid} {last}"


# ------------------------------------------------------------------ Builder --
class Builder:
    def __init__(self) -> None:
        self.guard = UsedGuard()
        self.used_postals: set = set()

    def _current_postal(self, region: str, rng) -> Tuple[str, str]:
        city = rng.choice(CITIES[region])[0]
        while True:
            candidate = f"{rng.randint(2, 97):02d}{rng.randint(10, 99):02d}{rng.randint(1, 9)}"
            if candidate not in self.used_postals:
                self.used_postals.add(candidate)
                return city, candidate

    # ------------------------------------------------------------ customers
    def build_customers(self) -> List[Customer]:
        customers: List[Customer] = []
        for i in range(N_CUSTOMERS):
            rng = prng(f"customer:{i}")
            customer_key = i + 1
            gid = f"CUS{1000 + i}"
            region = rng.choice(REGIONS)
            city, postal = self._current_postal(region, rng)
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            email = _email(first, last, i, rng)
            while email.lower() in self.guard.emails:
                email = _email(first, last, i, rng)
            self.guard.reserve_email(email)
            phone = None if rng.random() < 0.06 else _phone(i, self.guard)
            is_business = rng.random() < 0.30
            company = rng.choice(COMPANIES) if is_business else None
            is_active = True
            has_idp = rng.random() < 0.60
            household = f"hh_{hash_hex(f'hh:{customer_key}', '', 8)}" if rng.random() < 0.35 else None
            loyalty = rng.choice(["bronze", "silver", "gold", "platinum", "none"])
            dob = dt.datetime(rng.randint(1965, 2003), rng.randint(1, 12), rng.randint(1, 28))
            gender = rng.choice(["M", "F", "X"])
            created_ts = dt.datetime(rng.randint(2017, 2023), rng.randint(1, 12), rng.randint(1, 28), rng.randint(8, 20), rng.randint(0, 59))
            updated_ts = created_ts
            address = {
                "line1": f"{rng.randint(1, 4800)} {rng.choice(STREETS)}",
                "city": city, "region": region, "postal": postal, "country": "US",
            }
            current = {
                "first_name": first, "last_name": last, "email": email, "phone": phone,
                "dob": _iso(dob), "gender": gender, "line1": address["line1"],
                "city": city, "region": region, "postal": postal, "country": "US",
                "household": household, "loyalty_tier": loyalty,
                "is_business": is_business, "company": company, "is_active": is_active,
                "created_ts": _iso(created_ts), "updated_ts": _iso(updated_ts),
                "email_verified": has_idp, "profile_completeness": round(rng.random(), 2),
                "consent_email": rng.random() < 0.8, "consent_sms": rng.random() < 0.4,
                "opt_in_ts": _iso(created_ts) if rng.random() < 0.8 else None,
            }
            c = Customer(
                customer_key=customer_key, gid=gid, current=current, has_idp=has_idp,
                active=is_active, ids=self._source_ids(customer_key, is_business),
                has_marketing=rng.random() < 0.85,
                store_customer_id=hash_hex(f"store-cus:{customer_key}", "sc_", 12),
            )
            if is_business:
                c.crm_account = hash_hex(f"acct:{customer_key}", "acct_", 8)
                c.crm_contact = hash_hex(f"crm-contact:{customer_key}", "ct_", 8)
            customers.append(c)

        # a handful of inactive customers (with IDP, no orders)
        for pos, idx in enumerate((17, 88, 233, 341)):
            c = customers[idx]
            c.active = False
            c.current["is_active"] = False
            c.has_idp = True
            r = prng(f"deact:{idx}")
            deact = dt.datetime(2022, r.randint(1, 12), r.randint(1, 28), 12, 0, 0)
            c.current["updated_ts"] = _iso(deact)
        return customers

    def _source_ids(self, customer_key: int, is_business: bool) -> Dict[str, str]:
        ids = {
            "pay_customer": hash_hex(f"pay-cus:{customer_key}", "cus_", 8),
            "erp_internal": numeric_id(customer_key, 5),
            "retail": numeric_id(customer_key + 60000, 5),
            "idp": hash_hex(f"idp:{customer_key}", "idp_", 16),
            "marketing": hash_hex(f"mk:{customer_key}", "mk_", 8),
        }
        if is_business:
            ids["crm_account"] = hash_hex(f"acct:{customer_key}", "acct_", 8)
            ids["crm_contact"] = hash_hex(f"crm-contact:{customer_key}", "ct_", 8)
        return ids

    # ---------------------------------------------------------------- events
    def assign_events(self, customers: List[Customer]) -> None:
        candidates = [c for c in customers if c.has_idp and c.active]
        rng = prng("events")
        n_events = int(len(candidates) * 0.20)
        chosen = rng.sample(candidates, n_events)
        for c in chosen:
            attr = self._pick_event_attr(c, rng)
            if attr is None:
                continue
            created = dt.datetime.fromisoformat(c.current["created_ts"][:-1])
            low = max(created, WINDOW_START)
            if attr == "email":
                # email changes land inside the MRK stale window so the old email
                # is observable in MRK (subscriber postal bridges the clusters).
                low = max(low, MRK_SNAPSHOT)
            span = max(1, int((WINDOW_END - low).total_seconds() / 60))
            ts = low + dt.timedelta(minutes=rng.randint(0, span))
            old = self._unique_old_value(c, attr, ts, rng)
            c.event = HistoryEvent(
                attribute=attr, old_value=old, new_value=c.current[attr], ts=_iso(ts),
            )
            c.current["updated_ts"] = _iso(ts)
        # backfill updated_ts for remaining IDP customers from created_ts
        for c in customers:
            if c.has_idp and c.event is None:
                c.current["updated_ts"] = c.current["created_ts"]

    def _pick_event_attr(self, c: Customer, rng) -> Optional[str]:
        pool = ["last_name", "last_name", "postal"]
        if c.current.get("email") and c.has_marketing:
            pool += ["email", "email"]
        if c.current.get("phone"):
            pool.append("phone")
        return rng.choice(pool)

    def _unique_old_value(self, c: Customer, attr: str, ts: dt.datetime, rng) -> str:
        for _ in range(50):
            old = self._old_value(c, attr, ts, rng)
            if attr == "email":
                if old.lower() not in self.guard.emails:
                    self.guard.reserve_email(old)
                    return old
            elif attr == "phone":
                digits = "".join(ch for ch in old if ch.isdigit())
                if digits not in self.guard.phones:
                    self.guard.reserve_phone(digits)
                    return old
            else:
                return old
        raise RuntimeError(f"could not find unique old {attr} for {c.gid}")

    def _old_value(self, c: Customer, attr: str, ts: dt.datetime, rng) -> str:
        if attr == "last_name":
            return rng.choice([x for x in LAST_NAMES if x != c.current["last_name"]])
        if attr == "email":
            local = c.current["email"].split("@")[0]
            domain = c.current["email"].split("@")[1]
            return f"prior.{local}@{domain}"
        if attr == "phone":
            cur = c.current["phone"]
            if not cur:
                return f"+1 206 {'5'*3} {str(rng.randint(1000,9999))}"
            digits = "".join(x for x in cur if x.isdigit())
            return f"+1 {digits[2:5]} {'5'*3} {str(rng.randint(1000,9999))}"
        if attr == "postal":
            while True:
                p = f"{rng.randint(2, 97):02d}{rng.randint(10, 99):02d}{rng.randint(1, 9)}"
                if p not in self.used_postals:
                    self.used_postals.add(p)
                    return p
        raise ValueError(attr)

    # ---------------------------------------------------- orders + payments
    def build_orders(self, customers: List[Customer]) -> Tuple[List[Order], List[Payment]]:
        orders: List[Order] = []
        payments: List[Payment] = []
        n_orders = 0
        for c in customers:
            if not c.active:
                continue
            rng = prng(f"orders:{c.customer_key}")
            n = self._n_orders(rng)
            if n == 0 and c.event and c.event.attribute == "postal":
                n = 1  # current postal must be observable via STORE (priority 1)
            for j in range(n):
                n_orders += 1
                o = self._one_order(c, j, n_orders)
                orders.append(o)
                self._payments_for(c, o, rng, payments)
        # compute order.amount_usd after payments created
        by_order: Dict[int, List[Payment]] = {}
        for p in payments:
            by_order.setdefault(p.order_key, []).append(p)
        for o in orders:
            pays = by_order.get(o.order_key, [])
            real = [p for p in pays if not p.is_duplicate_row]
            o.amount_usd = sum(p.amount_usd for p in real)
        self._enforce_erp_uniqueness(orders)
        return orders, payments

    def _enforce_erp_uniqueness(self, orders: List[Order]) -> None:
        """Guarantee each ERP invoice is identifiable: the tuple
        (bill_to, total_cents, currency, issue_date) must be unique so payment
        `inv_` references resolve to exactly one order."""
        seen = set()
        nudge = 0
        for o in orders:
            name = f"{o.ship_to['name']}"
            key = (name, o.total_cents, o.currency, o.erp_issue_date)
            while key in seen:
                nudge += 1
                base = dt.datetime.fromisoformat(o.erp_issue_date[:-1])
                newd = base + dt.timedelta(days=1 + (nudge % 5))
                o.erp_issue_date = _iso(newd)
                key = (name, o.total_cents, o.currency, o.erp_issue_date)
            seen.add(key)

    def _n_orders(self, rng) -> int:
        r = rng.random()
        if r < 0.14:
            return 0
        if r < 0.45:
            return 1
        if r < 0.75:
            return 2
        if r < 0.92:
            return 3
        if r < 0.98:
            return 4
        return 5

    def _one_order(self, c: Customer, j: int, n: int) -> Order:
        rng = prng(f"order:{c.customer_key}:{j}")
        placed = WINDOW_START + dt.timedelta(minutes=rng.randint(0, int((WINDOW_END - WINDOW_START).total_seconds() / 60)))
        status_r = rng.random()
        if status_r < 0.76:
            status = "completed"
        elif status_r < 0.83:
            status = "partially_refunded"
        elif status_r < 0.88:
            status = "refunded"
        elif status_r < 0.94:
            status = "processing"
        else:
            status = "cancelled"
        currency = "CAD" if rng.random() < 0.08 else "USD"
        subtotal = rng.randint(2500, 120000)
        tax = _round_half_up(subtotal * rng.choice([0.0, 0.07, 0.08, 0.1]))
        shipping = 0 if rng.random() < 0.4 else rng.randint(400, 1500)
        total = subtotal + tax + shipping
        email_at = c.current["email"]
        if c.event and c.event.attribute == "email":
            ev_ts = dt.datetime.fromisoformat(c.event.ts[:-1])
            email_at = c.event.old_value if placed < ev_ts else c.current["email"]
        return Order(
            order_key=n, gid=f"ORD{n:05d}", customer_key=c.customer_key,
            store_order_id=hash_hex(f"order-id:{n}", "or_", 12),
            erp_invoice_no=hash_hex(f"erp-inv:{n}", "inv_", 8),
            placed_ts=_iso(placed), status=status, channel=rng.choice(CHANNELS),
            subtotal_cents=subtotal, tax_cents=tax, shipping_cents=None if shipping == 0 and rng.random() < 0.05 else shipping,
            total_cents=total, currency=currency, ship_to=self._ship_to(c),
            email_at_order=email_at, erp_issue_date=_iso(placed + dt.timedelta(days=rng.randint(1, 3))),
        )

    def _ship_to(self, c: Customer) -> Dict[str, str]:
        return {
            "name": f"{c.current['first_name']} {c.current['last_name']}",
            "line1": c.current["line1"], "city": c.current["city"],
            "region": c.current["region"], "postal": c.current["postal"],
            "country": c.current["country"],
        }

    def _payments_for(self, c: Customer, o: Order, rng, payments: List[Payment]) -> None:
        if o.status == "cancelled":
            return
        paid_ts = dt.datetime.fromisoformat(o.placed_ts[:-1]) + dt.timedelta(seconds=rng.randint(40, 420))
        method = rng.choice(["card", "card", "apple_pay", "bank", "klarna"])
        if o.status == "completed":
            self._emit_payment(c, o, paid_ts, o.total_cents, o.currency, method, "succeeded", "or", o.store_order_id, None, payments)
        elif o.status == "processing":
            self._emit_payment(c, o, paid_ts, o.total_cents, o.currency, method, "processing", "or", o.store_order_id, None, payments)
        elif o.status == "refunded":
            self._emit_payment(c, o, paid_ts, o.total_cents, o.currency, method, "succeeded", "or", o.store_order_id, None, payments)
            refund_ts = paid_ts + dt.timedelta(days=rng.randint(1, 6), hours=rng.randint(0, 12))
            self._emit_payment(c, o, refund_ts, -o.total_cents, o.currency, method, "refunded", "inv", o.erp_invoice_no, None, payments)
        elif o.status == "partially_refunded":
            self._emit_payment(c, o, paid_ts, o.total_cents, o.currency, method, "succeeded", "or", o.store_order_id, None, payments)
            r = _round_half_up(o.total_cents * rng.uniform(0.1, 0.4))
            refund_ts = paid_ts + dt.timedelta(days=rng.randint(1, 4))
            self._emit_payment(c, o, refund_ts, -r, o.currency, method, "partially_refunded", "inv", o.erp_invoice_no, None, payments)

    def _emit_payment(self, c, o, paid_ts, amount, currency, method, status, ref_kind, ref_val, dup_src, payments):
        txn = hash_hex(f"txn:{o.order_key}:{len(payments)}:{paid_ts.year}:{paid_ts.hour}", "txn_", 8)
        cust_ref = f"cus_{hash_hex(f'pay-cus:{c.customer_key}', '', 8)}"
        # deterministic corruption decisions keyed off the payment's own key
        key = f"paycorr:{o.order_key}:{len(payments)}"
        r = prng(key)
        roll = r.random()
        ref = ref_kind
        ref_val_out = ref_val
        obsolete = False
        if roll < 0.04:
            ref = "none"; ref_val_out = ""
        elif roll < 0.07:
            ref = "obsolete"; ref_val_out = random_order_ref_for_obsolete(r)
            obsolete = True
        p = Payment(
            payment_key=len(payments) + 1, gid=f"PAY{len(payments)+1:05d}", order_key=o.order_key,
            customer_key=c.customer_key, paid_ts=_iso(paid_ts), amount_cents=amount,
            currency=currency, method=method, status=status,
            pay_txn_id=txn, pay_customer_ref=cust_ref, order_ref=ref,
            order_ref_value=ref_val_out, amount_usd=_usd(amount, currency),
            is_duplicate_row=False, obsolete=obsolete,
        )
        payments.append(p)
        # duplicate row (~1.5% of captures, only for positive 'or' refs)
        if amount > 0 and ref == "or" and r.random() < 0.015:
            dup = Payment(
                payment_key=payments[-1].payment_key, gid=f"PAY{len(payments)+1:05d}",
                order_key=o.order_key, customer_key=c.customer_key, paid_ts=p.paid_ts,
                amount_cents=amount, currency=currency, method=method, status=status,
                pay_txn_id=hash_hex(f"txn-dup:{o.order_key}:{len(payments)}", "txn_", 8),
                pay_customer_ref=cust_ref, order_ref="or", order_ref_value=ref_val,
                amount_usd=_usd(amount, currency), is_duplicate_row=True, obsolete=False,
            )
            payments.append(dup)

    # ---------------------------------------------------------------- tickets
    def build_tickets(self, customers: List[Customer]) -> List[Ticket]:
        tickets: List[Ticket] = []
        n = 600
        active = [c for c in customers if c.active]
        for i in range(n):
            rng = prng(f"ticket:{i}")
            c = active[rng.randrange(len(active))]
            t = self._one_ticket(c, i, rng)
            tickets.append(t)
        return tickets

    def _one_ticket(self, c, i, rng) -> Ticket:
        created = WINDOW_START + dt.timedelta(minutes=rng.randint(0, int((WINDOW_END - WINDOW_START).total_seconds() / 60)))
        open_ticket = created > WINDOW_END - dt.timedelta(days=10) or rng.random() < 0.25
        status = "open" if open_ticket else rng.choice(["closed", "resolved", "pending"])
        closed = None if open_ticket else created + dt.timedelta(hours=rng.randint(2, 72))
        # requester email: current, old (email event), or business billing email
        email = c.current["email"]
        if c.event and c.event.attribute == "email":
            ev_ts = dt.datetime.fromisoformat(c.event.ts[:-1])
            if created < ev_ts:
                email = c.event.old_value
            elif rng.random() < 0.15:
                email = c.event.old_value
        elif rng.random() < 0.08 and c.current.get("company"):
            email = f"ap@{c.current['company'].replace(' ', '').lower()}.example"
        # order ref
        order = self._pick_order(c, rng)
        order_ref, order_ref_value, order_key = self._ticket_ref(c, order, rng)
        ticket_id = hash_hex(f"tkt:{i}", "tk_", 8)
        reject_row = i in {13, 314, 419}
        if reject_row:
            ticket_id = ""
        implausible = i in {97}
        malformed_created = i % 61 == 0
        created_s = _iso(created)
        if implausible:
            created_s = "2024-02-30T09:15:00Z"
        elif malformed_created:
            created_s = created.strftime("%m/%d/%Y") + " 09:15:00"
        return Ticket(
            ticket_key=i + 1, gid=f"TKT{i+1:05d}", customer_key=c.customer_key,
            ticket_id=ticket_id, requester_email=email,
            subject=f"{rng.choice(TICKET_CATEGORY).title()} issue",
            category=rng.choice(TICKET_CATEGORY), priority=rng.choice(TICKET_PRIORITY),
            status=status, created_ts=created_s,
            closed_ts=None if closed is None else _iso(closed),
            order_ref=order_ref, order_ref_value=order_ref_value, order_key=order_key,
            first_response_minutes=rng.randint(3, 240),
            resolution_minutes=None if open_ticket else rng.randint(60, 1200),
            reject_row=reject_row, malformed_created=malformed_created,
            implausible_date=implausible,
        )

    def _pick_order(self, c, rng):
        os = [o for o in self._orders_by_customers.get(c.customer_key, [])]
        if not os or rng.random() < 0.55:
            return None
        return rng.choice(os)

    def _ticket_ref(self, c, order, rng):
        if order is None:
            return "none", "", None
        roll = rng.random()
        if roll < 0.82:
            return "or", order.store_order_id, order.order_key
        if roll < 0.90:
            return "inv", order.erp_invoice_no, order.order_key
        if roll < 0.96:
            # obsolete: ref an order that isn't in the universe
            bogus = hash_hex("obsolete-sentinel", "or_", 12)
            return "obsolete", bogus, None
        # malformed: uppercase dash form that normalizes to the real order
        return "malformed", f"OR-{order.store_order_id[3:].upper()}", order.order_key

    # ------------------------------------------------------------------- CRM
    def build_crm(self, customers: List[Customer]) -> Tuple[List[CrmAccount], List[CrmContact], List[CrmOpportunity]]:
        accounts: List[CrmAccount] = []
        contacts: List[CrmContact] = []
        opportunities: List[CrmOpportunity] = []
        opp_index = 0
        for c in customers:
            if c.current["is_business"] and c.crm_contact:
                acct_id = c.crm_account
                accounts.append(CrmAccount(
                    account_id=acct_id, account_name=c.current["company"] or c.gid,
                    industry=prng(f"ind:{c.customer_key}").choice(INDUSTRIES),
                    size_segment=prng(f"seg:{c.customer_key}").choice(["small", "mid", "large"]),
                    billing_email=f"ap@{(c.current['company'] or c.gid).replace(' ','').lower()}.example",
                ))
                phone = c.current["phone"]
                contacts.append(CrmContact(
                    contact_id=c.crm_contact, account_id=acct_id, customer_key=c.customer_key,
                    first_name=c.current["first_name"], last_name=c.current["last_name"],
                    email=c.current["email"], phone=phone, title=rng_title(c),
                ))
                # a couple of opportunities per business customer
                for _ in range(prng(f"oppn:{c.customer_key}").randrange(0, 4)):
                    opp_index += 1
                    close = WINDOW_END + dt.timedelta(days=prng(f"oppc:{opp_index}").randint(-30, 90))
                    opportunities.append(CrmOpportunity(
                        opp_id=hash_hex(f"opp:{opp_index}", "opp_", 8), contact_id=c.crm_contact,
                        amount_cents=prng(f"oppa:{opp_index}").randint(5000, 250000),
                        stage=prng(f"opps:{opp_index}").choice(["open", "negotiation", "won", "lost"]),
                        close_date=_iso(close),
                    ))
        return accounts, contacts, opportunities

    # ------------------------------------------------------------------ runs
    def build(self) -> Master:
        customers = self.build_customers()
        self.assign_events(customers)
        orders, payments = self.build_orders(customers)
        self._orders_by_customers = {}
        for o in orders:
            self._orders_by_customers.setdefault(o.customer_key, []).append(o)
        tickets = self.build_tickets(customers)
        accounts, contacts, opportunities = self.build_crm(customers)
        return Master(
            customers=customers, orders=orders, payments=payments, tickets=tickets,
            crm_accounts=accounts, crm_contacts=contacts, crm_opportunities=opportunities,
        ).index()


def random_order_ref_for_obsolete(rng) -> str:
    # a well-formed but non-existent order reference
    if rng.random() < 0.5:
        return hash_hex("obsolete-sentinel", "or_", 12)
    return hash_hex("obsolete-invoice-sentinel", "inv_", 8)


def rng_title(c) -> str:
    return prng(f"title:{c.customer_key}").choice(
        ["Purchasing", "Procurement Lead", "Operations", "Director", "Buyer", None])


def build_master() -> Master:
    return Builder().build()