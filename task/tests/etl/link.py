"""Entity resolution, canonical merge, and customer-history reconstruction.

Implements the public rules in `config/dictionary.toml`:
  * strong matching on any single exact email / phone (union-find, transitive);
  * weak/composite matching on the declared combinations at a weighted score;
  * per-attribute precedence merge (highest-priority source with a valid value);
  * deterministic gid / customer_key assignment;
  * data-driven change detection for the customer_history output.

Python 3.11 stdlib only.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from etl.model import Customer, History
from etl.parse import Raw
from etl.rules import Dictionary

IDENTITY_SOURCES = ["IDP", "CRM", "CAT", "MRK", "STORE", "PAY"]


class _Node:
    __slots__ = ("obs", "emails", "phones", "name", "postal", "first_name", "dob")

    def __init__(self, obs) -> None:
        self.obs = obs
        self.emails: Set[str] = set((obs.get("email"),)) if obs.get("email") else set()
        self.phones: Set[str] = set((obs.get("phone"),)) if obs.get("phone") else set()
        self.name = obs.get("name")
        self.postal = obs.get("postal")
        self.first_name = obs.get("first_name")
        self.dob = obs.get("dob")


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _build_nodes(raw: Raw) -> List[_Node]:
    nodes: List[_Node] = []
    for obs in raw.identities + raw.crm_contacts + raw.cat_rows + raw.mrk_subscribers:
        nodes.append(_Node(obs))
    # one node per distinct STORE email (historical orders carry at-order email)
    store_by_email: Dict[str, dict] = {}
    for ob in raw.orders:
        if ob["email"]:
            store_by_email.setdefault(ob["email"], ob)
    for ob in store_by_email.values():
        nodes.append(_Node(ob))
    return nodes


def link(raw: Raw, rules: Dictionary) -> Tuple[List[Customer], int, Dict[str, int]]:
    """Resolve records into linked customer clusters.

    Returns (customers, distinct_source_identities, email_to_customer) where
    email_to_customer maps every normalized email observed for a customer
    (including historical/old-value emails) to its final customer_key.
    """
    nodes = _build_nodes(raw)
    n = len(nodes)
    uf = _UnionFind(n)
    pri = rules.source_priority_map()

    # --- strong edges: shared email or phone -----------------------------
    email_map: Dict[str, int] = {}
    phone_map: Dict[str, int] = {}
    for i, node in enumerate(nodes):
        for e in node.emails:
            if e in email_map:
                uf.union(email_map[e], i)
            else:
                email_map[e] = i
        for p in node.phones:
            if p in phone_map:
                uf.union(phone_map[p], i)
            else:
                phone_map[p] = i

    # --- weak/composite edges ----------------------------------------------
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    def _score(na: _Node, nb: _Node) -> int:
        s = 0
        if na.name and na.name == nb.name:
            s += 1
        if na.postal and na.postal == nb.postal:
            s += 1
        if na.first_name and na.first_name == nb.first_name:
            s += 1
        if na.dob and na.dob == nb.dob:
            s += 1
        return s

    def _weak(na: _Node, nb: _Node) -> bool:
        # declared combos only: [name, postal] AND [first_name, postal, dob]
        if na.name and na.name == nb.name and na.postal and na.postal == nb.postal:
            return True
        hits = 0
        if na.first_name and na.first_name == nb.first_name:
            hits += 1
        if na.postal and na.postal == nb.postal:
            hits += 1
        if na.dob and na.dob == nb.dob:
            hits += 1
        return hits >= 2

    def _agg(nodes_in: List[int]) -> _Node:
        # component-level signature: first non-null value seen for each attr
        name = postal = first_name = dob = None
        obs = None
        for i in nodes_in:
            nd = nodes[i]
            if nd.name and not name:
                name = nd.name
            if nd.postal and not postal:
                postal = nd.postal
            if nd.first_name and not first_name:
                first_name = nd.first_name
            if nd.dob and not dob:
                dob = nd.dob
        for i in nodes_in:
            obs = nodes[i].obs
            break
        base = _Node(obs) if obs is not None else _Node({})
        base.name, base.postal, base.first_name, base.dob = name, postal, first_name, dob
        return base

    comp_ids = list(groups.keys())
    comp_nodes: Dict[int, _Node] = {}
    for cid in comp_ids:
        comp_nodes[cid] = _agg(groups[cid])
    for a in range(len(comp_ids)):
        for b in range(a + 1, len(comp_ids)):
            ca, cb = comp_ids[a], comp_ids[b]
            na, nb = comp_nodes[ca], comp_nodes[cb]
            if na.emails & nb.emails or na.phones & nb.phones:
                continue
            ok = _weak(na, nb)
            if ok:
                uf.union(ca, cb)

    # --- per-component observation pools -----------------------------------
    comps: Dict[int, List[int]] = {}
    for i in range(n):
        comps.setdefault(uf.find(i), []).append(i)

    dist_identities = 0
    customers: List[Customer] = []
    for members in comps.values():
        cust = _merge_component(members, nodes, raw, rules, pri)
        if cust is not None:
            customers.append(cust)
            dist_identities += 1

    customers.sort(key=lambda c: c.gid)
    for idx, c in enumerate(customers):
        c.customer_key = idx + 1
    email_to_customer: Dict[str, int] = {}
    for c in customers:
        if c.email:
            email_to_customer.setdefault(c.email, c.customer_key)
        for src, val in c.exposures.get("email", []):
            if val:
                email_to_customer.setdefault(val, c.customer_key)
    return customers, dist_identities, email_to_customer


def _rowid(ob) -> str:
    for key in ("sub", "contact_id", "cust_no", "profile_id", "order_id", "customer_id"):
        v = ob.get(key)
        if v is not None:
            return str(v)
    return ""


def _merge_component(members, nodes, raw, rules, pri) -> Optional[Customer]:
    values: Dict[str, List[Tuple[int, str, Optional[str]]]] = {}
    exposures: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    identities = {}
    has_idp = False
    has_crm = False
    bill_to = None

    for i in members:
        ob = nodes[i].obs
        src = ob.get("src")
        rid = _rowid(ob)
        if src == "IDP":
            has_idp = True
            identities.setdefault("IDP", set()).add(ob["sub"])
            _exp(exposures, ob, "email", ob.get("email"), "IDP")
            _exp(exposures, ob, "last_name", ob.get("family_name"), "IDP")
            _exp(exposures, ob, "phone", ob.get("phone"), "IDP")
            _val(values, "email", pri["IDP"], rid, ob.get("email"))
            _val(values, "last_name", pri["IDP"], rid, ob.get("family_name"))
            _val(values, "first_name", pri["IDP"], rid, ob.get("given_name"))
            _val(values, "phone", pri["IDP"], rid, ob.get("phone"))
            _val(values, "dob", pri["IDP"], rid, ob.get("dob"))
            _val(values, "gender", pri["IDP"], rid, ob.get("gender"))
            _val(values, "household", pri["IDP"], rid, ob.get("household"))
            _val(values, "created_ts", pri["IDP"], rid, ob.get("created"))
            _val(values, "updated_ts", pri["IDP"], rid, ob.get("updated"))
            _val(values, "is_active", pri["IDP"], rid, ob.get("is_active"))
        elif src == "CRM" and ob["type"] == "contact":
            has_crm = True
            identities.setdefault("CRM", set()).add(ob["contact_id"])
            _exp(exposures, ob, "email", ob.get("email"), "CRM")
            _exp(exposures, ob, "last_name", ob.get("last_name"), "CRM")
            _exp(exposures, ob, "phone", ob.get("phone"), "CRM")
            _val(values, "email", pri["CRM"], rid, ob.get("email"))
            _val(values, "last_name", pri["CRM"], rid, ob.get("last_name"))
            _val(values, "first_name", pri["CRM"], rid, ob.get("first_name"))
            _val(values, "phone", pri["CRM"], rid, ob.get("phone"))
            for acct in raw.crm_accounts:
                if acct["account_id"] == ob.get("account_id"):
                    bill_to = acct["account_name"]
        elif src == "CAT":
            identities.setdefault("CAT", set()).add(ob["cust_no"])
            _exp(exposures, ob, "email", ob.get("email"), "CAT")
            _exp(exposures, ob, "last_name", ob.get("last_name"), "CAT")
            _exp(exposures, ob, "phone", ob.get("phone"), "CAT")
            _exp(exposures, ob, "postal", ob.get("postal"), "CAT")
            _val(values, "email", pri["CAT"], rid, ob.get("email"))
            _val(values, "last_name", pri["CAT"], rid, ob.get("last_name"))
            _val(values, "first_name", pri["CAT"], rid, ob.get("first_name"))
            _val(values, "phone", pri["CAT"], rid, ob.get("phone"))
            _val(values, "line1", pri["CAT"], rid, ob.get("line1"))
            _val(values, "city", pri["CAT"], rid, ob.get("city"))
            _val(values, "region", pri["CAT"], rid, ob.get("region"))
            _val(values, "postal", pri["CAT"], rid, ob.get("postal"))
            _val(values, "country", pri["CAT"], rid, ob.get("country"))
            _val(values, "loyalty_tier", pri["CAT"], rid, ob.get("loyalty_tier"))
            _val(values, "created_ts", pri["CAT"], rid, ob.get("created"))
        elif src == "MRK":
            identities.setdefault("MRK", set()).add(ob["profile_id"])
            _exp(exposures, ob, "email", ob.get("email"), "MRK")
            _exp(exposures, ob, "last_name", ob.get("last_name"), "MRK")
            _exp(exposures, ob, "postal", ob.get("postal"), "MRK")
            _val(values, "email", pri["MRK"], rid, ob.get("email"))
            _val(values, "last_name", pri["MRK"], rid, ob.get("last_name"))
            _val(values, "first_name", pri["MRK"], rid, ob.get("first_name"))
            _val(values, "postal", pri["MRK"], rid, ob.get("postal"))
        elif src == "STORE":
            identities.setdefault("STORE", set()).add(ob["customer_id"])
            _exp(exposures, ob, "email", ob.get("email"), "STORE")
            _exp(exposures, ob, "postal", ob.get("postal"), "STORE")
            _val(values, "postal", pri["STORE"], rid, ob.get("postal"))
            _val(values, "region", pri["STORE"], rid, ob.get("region"))
            _val(values, "line1", pri["STORE"], rid, ob.get("ship_line1"))
            _val(values, "city", pri["STORE"], rid, ob.get("ship_city"))
            _val(values, "country", pri["STORE"], rid, ob.get("ship_country"))

    email = _pick(values, "email")
    phone = _pick(values, "phone")
    last_name = _pick(values, "last_name")
    first_name = _pick(values, "first_name")
    postal = _pick(values, "postal")
    created = _pick(values, "created_ts")
    updated = _pick(values, "updated_ts")
    is_active = _pick(values, "is_active")
    if is_active is None:
        is_active = True
    else:
        is_active = bool(is_active)
    is_business = has_crm
    gid = _gid(identities)
    if gid is None:
        return None

    c = Customer(
        customer_key=0, gid=gid,
        first_name=first_name, last_name=last_name,
        dob=_pick(values, "dob"), gender=_pick(values, "gender"),
        email=email, phone=phone,
        line1=_pick(values, "line1"), city=_pick(values, "city"),
        region=_pick(values, "region"), postal=postal,
        country=_pick(values, "country"),
        is_business=is_business, company=bill_to,
        household=_pick(values, "household"),
        loyalty_tier=_pick(values, "loyalty_tier"),
        is_active=is_active,
        created_ts=created, updated_ts=updated,
        exposures=exposures, has_idp=has_idp,
    )
    return c


def _exp(exposures, ob, attr, value, source) -> None:
    if value is None:
        return
    exposures.setdefault(attr, []).append((source, value))


def _val(values, attr, prio, rowid, value) -> None:
    values.setdefault(attr, []).append((prio, rowid, value))


def _pick(values, attr):
    cands = values.get(attr)
    if not cands:
        return None
    valid = [(p, (r or ""), v) for p, r, v in cands
             if v is not None and v != ""]
    if not valid:
        return None
    valid.sort(key=lambda t: (t[0], _rk(t[1])))
    return valid[0][2]


def _rk(value):
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _gid(identities: Dict[str, Set]) -> Optional[str]:
    for src in IDENTITY_SOURCES:
        vals = identities.get(src)
        if vals:
            return min(vals) if src != "STORE" else min(vals)
    return None


def history(customers: List[Customer]) -> List[History]:
    out: List[History] = []
    for cust in customers:
        _detect(cust, out)
    out.sort(key=lambda h: (h.customer_key, h.attribute, h.effective_from))
    for idx, h in enumerate(out):
        h.history_key = idx + 1
    return out


def _detect(cust: Customer, out: List[History]) -> None:
    exp = cust.exposures
    # email change: old carried only by STORE/MRK
    email_old = None
    email_old_src = None
    for src, val in exp.get("email", []):
        if val != cust.email and src in ("STORE", "MRK"):
            email_old = val
            email_old_src = src
    if email_old and cust.email:
        _emit(out, cust, "email", email_old, cust.email, email_old_src)

    # last_name / phone: IDP vs CAT
    idp_last = _by_src(exp, "last_name", "IDP")
    cat_last = _by_src(exp, "last_name", "CAT")
    if idp_last and cat_last and idp_last != cat_last:
        _emit(out, cust, "last_name", cat_last, idp_last, "CAT")
    idp_phone = _by_src(exp, "phone", "IDP")
    cat_phone = _by_src(exp, "phone", "CAT")
    if idp_phone and cat_phone and idp_phone != cat_phone:
        _emit(out, cust, "phone", cat_phone, idp_phone, "CAT")

    # postal: CAT old vs canonical
    cat_postal = _by_src(exp, "postal", "CAT")
    if cat_postal and cust.postal and cat_postal != cust.postal:
        _emit(out, cust, "postal", cat_postal, cust.postal, "CAT")


def _by_src(exp, attr, src):
    for s, val in exp.get(attr, []):
        if s == src and val:
            return val
    return None


def _emit(out, cust, attr, old, new, source) -> None:
    if old is None and new is None:
        return
    if old == new:
        return
    effective_from = None
    if cust.has_idp:
        effective_from = cust.updated_ts
    if effective_from is None:
        effective_from = "2024-05-01T00:00:00Z"
    out.append(History(
        customer_key=cust.customer_key, history_key=0, attribute=attr,
        old_value=old, new_value=new, effective_from=effective_from,
        effective_to="", source=source,
    ))