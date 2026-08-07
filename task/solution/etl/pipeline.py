"""Oracle ETL pipeline: parse → link → history → assemble → quality → write."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List

from etl.assemble import assemble
from etl.link import history, link
from etl.model import Customer, History, Order, Payment
from etl.parse import parse
from etl.quality import metrics, report_json
from etl.rules import Dictionary


class PipelineResult:
    def __init__(self) -> None:
        self.customers: List[Customer] = []
        self.orders: List[Order] = []
        self.payments: List[Payment] = []
        self.history: List[History] = []
        self.report: Dict[str, Any] = {}


def pipeline_main(data_dir: str, out_dir: str, rules: Dictionary) -> PipelineResult:
    raw = parse(data_dir, rules)
    customers, distinct_identities, cust_map = link(raw, rules)
    orders, payments = assemble(raw, customers, cust_map, rules, raw.stats)
    hist = history(customers)
    report = report_json(metrics(rules, raw.stats, customers, orders, payments, distinct_identities))

    write_customers(out_dir, rules, customers)
    write_orders(out_dir, rules, orders)
    write_payments(out_dir, rules, payments)
    write_history(out_dir, rules, hist)
    write_report(out_dir, rules, report)

    res = PipelineResult()
    res.customers, res.orders, res.payments, res.history, res.report = customers, orders, payments, hist, report
    return res


def run(data_dir: str, rules: Dictionary) -> PipelineResult:
    """Run the pipeline in-memory (no file writes); report still computed."""
    raw = parse(data_dir, rules)
    customers, distinct_identities, cust_map = link(raw, rules)
    orders, payments = assemble(raw, customers, cust_map, rules, raw.stats)
    hist = history(customers)
    report = report_json(metrics(rules, raw.stats, customers, orders, payments, distinct_identities))
    res = PipelineResult()
    res.customers, res.orders, res.payments, res.history, res.report = customers, orders, payments, hist, report
    return res


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def write_customers(out_dir: str, rules: Dictionary, customers: List[Customer]) -> None:
    _write_csv(out_dir, rules, "customers", [_customer_row(rules, c) for c in customers])


def write_orders(out_dir: str, rules: Dictionary, orders: List[Order]) -> None:
    _write_csv(out_dir, rules, "orders", [_order_row(rules, o) for o in orders])


def write_payments(out_dir: str, rules: Dictionary, payments: List[Payment]) -> None:
    _write_csv(out_dir, rules, "payments", [_payment_row(p) for p in payments])


def write_history(out_dir: str, rules: Dictionary, hist: List[History]) -> None:
    _write_csv(out_dir, rules, "customer_history", [_history_row(h) for h in hist])


def write_report(out_dir: str, rules: Dictionary, report: Dict[str, Any]) -> None:
    target = _target(out_dir, rules, "data_quality_report")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _target(out_dir: str, rules: Dictionary, name: str) -> Path:
    path = rules.output_path(name)
    base = Path(path).name
    return Path(out_dir) / base


def _write_csv(out_dir: str, rules: Dictionary, name: str, rows: List[List[str]]) -> None:
    cols = rules.output_columns(name)
    target = _target(out_dir, rules, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow([c for c, _ in cols])
    for row in rows:
        writer.writerow(row)
    target.write_text(buf.getvalue(), encoding="utf-8", newline="")


def _fmt(meta: Dict[str, Any], value: Any) -> str:
    if value is None:
        return ""
    if meta.get("format") == "bool":
        return "true" if value else "false"
    return str(value)


def _bind(cols) -> Dict[str, Dict[str, Any]]:
    return {name: meta for name, meta in cols}


def _customer_row(rules: Dictionary, c: Customer) -> List[str]:
    keys = ["customer_key", "gid", "first_name", "last_name", "dob", "gender",
            "email", "phone", "line1", "city", "region", "postal", "country",
            "is_business", "company", "household", "loyalty_tier", "is_active",
            "created_ts", "updated_ts"]
    m = {"customer_key": c.customer_key, "gid": c.gid, "first_name": c.first_name,
         "last_name": c.last_name, "dob": c.dob, "gender": c.gender, "email": c.email,
         "phone": c.phone, "line1": c.line1, "city": c.city, "region": c.region,
         "postal": c.postal, "country": c.country, "is_business": c.is_business,
         "company": c.company, "household": c.household, "loyalty_tier": c.loyalty_tier,
         "is_active": c.is_active, "created_ts": c.created_ts, "updated_ts": c.updated_ts}
    cols = _bind(rules.output_columns("customers"))
    return [_format(m[k], cols.get(k, {})) for k in keys]


def _order_row(rules: Dictionary, o: Order) -> List[str]:
    keys = ["customer_key", "order_key", "gid", "placed_ts", "status", "channel",
            "subtotal_cents", "tax_cents", "shipping_cents", "total_cents", "currency",
            "ship_to_name", "ship_to_line1", "ship_to_city", "ship_to_region",
            "ship_to_postal", "ship_to_country", "amount_usd"]
    m = {"customer_key": o.customer_key, "order_key": o.order_key, "gid": o.gid,
         "placed_ts": o.placed_ts, "status": o.status, "channel": o.channel,
         "subtotal_cents": o.subtotal_cents, "tax_cents": o.tax_cents,
         "shipping_cents": o.shipping_cents, "total_cents": o.total_cents,
         "currency": o.currency, "ship_to_name": o.ship_to_name,
         "ship_to_line1": o.ship_to_line1, "ship_to_city": o.ship_to_city,
         "ship_to_region": o.ship_to_region, "ship_to_postal": o.ship_to_postal,
         "ship_to_country": o.ship_to_country, "amount_usd": o.amount_usd}
    cols = _bind(rules.output_columns("orders"))
    return [_format(m[k], cols.get(k, {})) for k in keys]


def _format(value, meta) -> str:
    if value is None:
        return ""
    if meta.get("format") == "bool":
        return "true" if value else "false"
    return str(value)


def _payment_row(p: Payment) -> List[str]:
    m = {"order_key": p.order_key, "payment_key": p.payment_key, "gid": p.gid,
         "paid_ts": p.paid_ts, "amount_cents": p.amount_cents, "currency": p.currency,
         "amount_usd": p.amount_usd, "method": p.method, "status": p.status}
    return [str(v) if v is not None else "" for v in m.values()]


def _history_row(h: History) -> List[str]:
    m = {"customer_key": h.customer_key, "history_key": h.history_key,
         "attribute": h.attribute, "old_value": h.old_value, "new_value": h.new_value,
         "effective_from": h.effective_from, "effective_to": h.effective_to,
         "source": h.source}
    return [str(v) if v is not None else "" for v in m.values()]