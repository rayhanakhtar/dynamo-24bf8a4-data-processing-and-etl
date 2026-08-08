"""Assemble canonical orders + payments from parsed observations."""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional, Tuple

from etl.model import Customer, Order, Payment
from etl.parse import Raw, Stats
from etl.rules import Dictionary


def _usd(cents: int, currency: str, rules: Dictionary) -> int:
    val = cents * rules.currency_rate(currency)
    if val >= 0:
        return int(math.floor(val + 0.5))
    return int(math.ceil(val - 0.5))


def _d(iso: Optional[str]) -> Optional[dt.date]:
    if not iso:
        return None
    try:
        return dt.datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def assemble(
    raw: Raw, customers: List[Customer], cust_map: Dict[str, int],
    rules: Dictionary, stats: Stats,
) -> Tuple[List[Order], List[Payment]]:
    by_store_id: Dict[str, Order] = {}
    for ob in raw.orders:
        ck = cust_map.get(ob.get("email"))
        if ck is None:
            continue
        o = _mk_order(ob, ck)
        by_store_id[ob["order_id"]] = o

    inv_to_order = _erp_match(raw, cust_map, rules)

    # SUP malformed refs that resolve (metrics only)
    for t in raw.tickets:
        ref = t["order_ref"]
        if ref and ref.upper().startswith("OR-"):
            if "or_" + ref[3:].lower() in by_store_id:
                stats.repair_refs += 1

    kept_map: List[Tuple[Payment, Order]] = []
    seen_dup = set()
    for p in raw.payments:
        ded = (p.get("customer_ref"), p.get("order_ref"), p.get("paid"),
               p.get("amount_cents"), p.get("currency"), p.get("method"), p.get("status"))
        if ded in seen_dup:
            stats.pay_dedup += 1
            continue
        seen_dup.add(ded)
        order = _resolve_order(p, by_store_id, inv_to_order, rules)
        if order is None:
            stats.pay_dropped += 1
            continue
        amount_usd = _usd(p["amount_cents"] or 0, p["currency"] or "USD", rules)
        pmt = Payment(payment_key=0, order_key=0, customer_key=0, gid="",
                      paid_ts=p["paid"], amount_cents=p["amount_cents"],
                      currency=p["currency"], amount_usd=amount_usd,
                      method=p["method"], status=p["status"])
        kept_map.append((pmt, order))

    per_order: Dict[int, int] = {}
    for pmt, o in kept_map:
        per_order[id(o)] = per_order.get(id(o), 0) + pmt.amount_usd
    orders = list(by_store_id.values())
    for o in orders:
        o.amount_usd = per_order.get(id(o), 0)

    orders.sort(key=lambda o: (o.customer_key, o.placed_ts or "", o.order_id))
    for idx, o in enumerate(orders):
        o.order_key = idx + 1
        o.gid = f"ORD{o.order_key:06d}"

    payments: List[Payment] = []
    for pmt, o in kept_map:
        pmt.order_key = o.order_key
        pmt.customer_key = o.customer_key
        payments.append(pmt)

    payments.sort(key=lambda p: (p.order_key, p.paid_ts or "", p.amount_cents))
    for idx, pmt in enumerate(payments):
        pmt.payment_key = idx + 1
        pmt.gid = f"PAY{pmt.payment_key:06d}"
    return orders, payments


def _mk_order(ob: Dict, ck: int) -> Order:
    return Order(order_key=0, customer_key=ck, gid="", placed_ts=ob["placed"],
                 status=ob["status"], channel=ob["channel"],
                 subtotal_cents=ob["subtotal_cents"], tax_cents=ob["tax_cents"],
                 shipping_cents=ob["shipping_cents"], total_cents=ob["total_cents"],
                 currency=ob["currency"], ship_to_name=ob["ship_name"],
                 ship_to_line1=ob["ship_line1"], ship_to_city=ob["ship_city"],
                 ship_to_region=ob["ship_region"], ship_to_postal=ob["ship_postal"],
                 ship_to_country=ob["ship_country"], amount_usd=0,
                 order_id=ob["order_id"])


def _erp_match(raw: Raw, cust_map: Dict[str, int], rules: Dictionary) -> Dict[str, Order]:
    cands = [(ob, cust_map.get(ob.get("email"), 0))
             for ob in raw.orders if ob.get("email") in cust_map]
    result: Dict[str, Order] = {}
    for inv in raw.erp_invoices:
        idate = _d(inv["issue"])
        best, best_gap = None, None
        for ob, ck in cands:
            if ob["total_cents"] != inv["total_cents"] or ob["currency"] != inv["currency"]:
                continue
            if ob["ship_name"] != inv["bill_to"]:
                continue
            pdate = _d(ob["placed"])
            if pdate is None or idate is None:
                continue
            gap = abs((idate - pdate).days)
            if gap > 10:
                continue
            if best_gap is None or gap < best_gap or (gap == best_gap and ob["order_id"] < best.order_id):
                best_gap, best = gap, _mk_order(ob, ck)
        if best is not None:
            result[inv["invoice_no"]] = best
    return result


def _resolve_order(p: Dict, by_store_id, inv_to_order, rules) -> Optional[Order]:
    ref = p.get("order_ref")
    if ref:
        if ref.startswith("or_"):
            return by_store_id.get(ref)
        if ref.startswith("inv_"):
            return inv_to_order.get(ref)
    amount = p["amount_cents"] or 0
    cur = p["currency"]
    paid = p["paid"]
    if amount <= 0 or not paid:
        return None
    gap_min = rules.raw().get("payment_linkage", {}).get("max_gap_minutes", 30)
    try:
        pd = dt.datetime.strptime(paid, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    best, best_gap = None, None
    for o in by_store_id.values():
        if o.total_cents != amount or o.currency != cur:
            continue
        if not o.placed_ts:
            continue
        od = dt.datetime.strptime(o.placed_ts, "%Y-%m-%dT%H:%M:%SZ")
        if od > pd:
            continue
        gap = (pd - od).total_seconds() / 60.0
        if gap > gap_min:
            continue
        if best_gap is None or gap < best_gap or (gap == best_gap and o.order_id < best.order_id):
            best_gap, best = gap, o
    return best