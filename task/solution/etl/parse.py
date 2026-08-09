"""Read + parse all eight degraded source exports into normalized observations.

The readers mirror the byte layout used by the generator's `render_file`
(NDJSON: one JSON object per line; CSV: header from the first dict keys,
comment/blank lines verbatim) so the oracle consumes exactly what an agent sees
under `/app/data`.  Every row is either kept (a normalized observation) or
counted as rejected on the `Stats` object the parse returns.

Python 3.11 stdlib only.
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from etl.normalize import Normalizer
from etl.rules import Dictionary


PROFILE_STORE = "iso8601_z"
PROFILE_IDP = "iso8601_z"
PROFILE_CAT = "iso8601_z"
PROFILE_PAY = "iso8601_z"
PROFILE_SUP = "iso8601_z"
PROFILE_ERP = "us_mdy"
PROFILE_CRM = "iso8601_z"
PROFILE_MRK = "iso8601_z"


@dataclass
class Stats:
    """Deterministic counters backing the data-quality metrics."""
    processed: Dict[str, int] = field(default_factory=dict)   # valid rows kept
    rejected: Dict[str, int] = field(default_factory=dict)    # raw - kept
    malformed_ts_corrected: int = 0
    repair_refs: int = 0            # fk values repaired to a resolvable key
    pay_dedup: int = 0
    pay_dropped: int = 0

    def add_processed(self, src: str, n: int) -> None:
        self.processed[src] = self.processed.get(src, 0) + n

    def add_rejected(self, src: str, n: int) -> None:
        self.rejected[src] = self.rejected.get(src, 0) + n

    def records_processed(self) -> int:
        return sum(self.processed.values())

    def records_rejected(self) -> int:
        return sum(self.rejected.values())


@dataclass
class Raw:
    orders: List[Dict[str, Any]] = field(default_factory=list)
    payments: List[Dict[str, Any]] = field(default_factory=list)
    erp_invoices: List[Dict[str, Any]] = field(default_factory=list)
    identities: List[Dict[str, Any]] = field(default_factory=list)
    crm_contacts: List[Dict[str, Any]] = field(default_factory=list)
    crm_accounts: List[Dict[str, Any]] = field(default_factory=list)
    cat_rows: List[Dict[str, Any]] = field(default_factory=list)
    mrk_subscribers: List[Dict[str, Any]] = field(default_factory=list)
    tickets: List[Dict[str, Any]] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)


# ---------------------------------------------------------------------------
# low-level readers
# ---------------------------------------------------------------------------
def _ndjson_rows(path: Path) -> Tuple[List[Optional[Dict[str, Any]]], int]:
    rows: List[Optional[Dict[str, Any]]] = []
    count = 0
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            count += 1
            try:
                obj = json.loads(line)
            except ValueError:
                rows.append(None)
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                rows.append(None)
    return rows, count


def _csv_rows(path: Path, min_columns: int = 1) -> Tuple[List[Optional[Dict[str, str]]], int]:
    rows: List[Optional[Dict[str, str]]] = []
    count = 0
    header: Optional[List[str]] = None
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        for record in reader:
            if not record or all(not c.strip() for c in record):
                continue
            if record[0].startswith("#"):
                continue
            if header is None:
                header = [c.strip() for c in record]
                continue
            count += 1
            if len(record) != len(header) or len(record) < min_columns:
                rows.append(None)
                continue
            rows.append(dict(zip(header, record)))
    return rows, count


def _discover_files(data_dir: Path, source: Dict[str, Any]) -> List[Path]:
    root = data_dir / source["path"]
    files: List[str] = source.get("files")
    fmt = source["format"]
    if fmt == "csv_months":
        out: List[Path] = []
        if root.is_dir():
            for month in sorted(p for p in root.iterdir() if p.is_dir()):
                for fname in ("invoices.csv", "shipments.csv"):
                    p = month / fname
                    if p.exists():
                        out.append(p)
        return sorted(out)
    if files:
        return [root / f for f in files if (root / f).exists()]
    g = source.get("file_glob")
    if g and root.is_dir():
        return sorted(root.glob(g))
    return []


# ---------------------------------------------------------------------------
# per-source normalizers
# ---------------------------------------------------------------------------
def _parse_store(rows: List[Optional[Dict[str, Any]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    for r in rows:
        if r is None:
            continue
        order_id = r.get("id")
        if not order_id:
            raise RuntimeError("store: unrecoverable structure")
        ship = r.get("ship_to") or {}
        placed_iso, prov = nm.parse_ts(r.get("placed_at"), PROFILE_STORE)
        if prov == "corrected":
            raw.stats.malformed_ts_corrected += 1
        email = nm.email(r.get("email"))
        raw.orders.append({
            "type": "order", "src": "STORE",
            "order_id": order_id, "customer_id": r.get("customer_id"),
            "email": email,
            "name": nm.name(ship.get("name")),
            "postal": nm.postal(ship.get("postal")),
            "region": nm.currency(ship.get("region")),
            "ship_name": nm.name(ship.get("name")),
            "ship_line1": nm.name(ship.get("line1")),
            "ship_city": nm.name(ship.get("city")),
            "ship_region": nm.currency(ship.get("region")),
            "ship_postal": nm.postal(ship.get("postal")),
            "ship_country": nm.currency(ship.get("country")),
            "placed": placed_iso,
            "status": nm.name(r.get("status")),
            "channel": nm.name(r.get("channel")),
            "subtotal_cents": nm.money(r.get("subtotal_cents")),
            "tax_cents": nm.money(r.get("tax_cents")),
            "shipping_cents": nm.money(r.get("shipping_cents")),
            "total_cents": nm.money(r.get("total_cents")),
            "currency": nm.currency(r.get("currency")),
        })
        kept += 1
    return kept


def _parse_idp(rows: List[Optional[Dict[str, Any]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    for r in rows:
        if r is None:
            continue
        if not r.get("sub"):
            continue
        created_iso, _ = nm.parse_ts(r.get("created_ts"), PROFILE_IDP)
        updated_iso, _ = nm.parse_ts(r.get("updated_ts"), PROFILE_IDP)
        status = nm.name(r.get("status"))
        first = nm.name(r.get("given_name"))
        last = nm.name(r.get("family_name"))
        raw.identities.append({
            "type": "identity", "src": "IDP",
            "sub": r.get("sub"),
            "email": nm.email(r.get("email")),
            "email_verified": nm.bool(r.get("email_verified")),
            "phone": nm.phone(r.get("phone")),
            "given_name": first,
            "family_name": last,
            "name": " ".join(x for x in (first, last) if x) or None,
            "dob": nm.currency(r.get("dob")) if r.get("dob") else None,
            "gender": nm.name(r.get("gender")),
            "household": r.get("household"),
            "created": created_iso,
            "updated": updated_iso,
            "is_active": bool(status) and status.lower() == "active",
            "profile_completeness": r.get("profile_completeness"),
        })
        kept += 1
    return kept


def _parse_crm(rows: Dict[str, List[Optional[Dict[str, Any]]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    acct_email = {}
    for r in rows.get("accounts.csv", []):
        if r is None or not r.get("account_id"):
            continue
        acct_email[r["account_id"]] = nm.email(r.get("billing_email"))
        raw.crm_accounts.append({
            "type": "account", "src": "CRM",
            "account_id": r["account_id"],
            "account_name": nm.name(r.get("account_name")),
            "billing_email": acct_email[r["account_id"]],
        })
        kept += 1
    for r in rows.get("contacts.csv", []):
        if r is None or not r.get("contact_id"):
            continue
        first = nm.name(r.get("first_name"))
        last = nm.name(r.get("last_name"))
        raw.crm_contacts.append({
            "type": "contact", "src": "CRM",
            "contact_id": r["contact_id"],
            "account_id": r.get("account_id"),
            "first_name": first,
            "last_name": last,
            "name": " ".join(x for x in (first, last) if x) or None,
            "email": nm.email(r.get("email")),
            "phone": nm.phone(r.get("phone")),
            "title": nm.name(r.get("title")),
        })
        kept += 1
    for r in rows.get("opportunities.csv", []):
        if r is not None and r.get("opp_id"):
            kept += 1
    return kept


def _parse_cat(rows: List[Optional[Dict[str, Any]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    for r in rows:
        if r is None:
            continue
        cust_no = r.get("cust_no")
        if cust_no is None or str(cust_no) == "":
            continue
        first = nm.name(r.get("first_name"))
        last = nm.name(r.get("last_name"))
        created_iso, _ = nm.parse_ts(r.get("created_ts"), PROFILE_CAT)
        raw.cat_rows.append({
            "type": "cat", "src": "CAT",
            "cust_no": str(cust_no).strip(),
            "email": nm.email(r.get("email")),
            "first_name": first,
            "last_name": last,
            "name": " ".join(x for x in (first, last) if x) or None,
            "phone": nm.phone(r.get("phone")),
            "line1": nm.name(r.get("line1")),
            "city": nm.name(r.get("city")),
            "region": nm.currency(r.get("region")),
            "postal": nm.postal(r.get("postal_code")),
            "country": nm.currency(r.get("country")),
            "loyalty_tier": nm.name(r.get("loyalty_tier")),
            "created": created_iso,
        })
        kept += 1
    return kept


def _parse_mrk(rows: Dict[str, List[Optional[Dict[str, Any]]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    for r in rows.get("subscribers.csv", []):
        if r is None or not r.get("profile_id"):
            continue
        first = nm.name(r.get("first"))
        last = nm.name(r.get("last"))
        raw.mrk_subscribers.append({
            "type": "subscriber", "src": "MRK",
            "profile_id": r["profile_id"],
            "email": nm.email(r.get("email")),
            "first_name": first,
            "last_name": last,
            "name": " ".join(x for x in (first, last) if x) or None,
            "postal": nm.postal(r.get("postal_code")),
        })
        kept += 1
    for r in rows.get("sends.csv", []):
        if r is not None and r.get("send_id"):
            kept += 1
    return kept


def _parse_pay(rows: List[Optional[Dict[str, Any]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    for r in rows:
        if r is None:
            continue
        txn = r.get("txn_id")
        if not txn:
            continue
        amount = nm.money(r.get("amount_cents"), signed=True)
        if amount is None:
            # reject row (unparseable amount) -> rejected, not processed
            continue
        ts_raw = r.get("processed_at") if r.get("processed_at") is not None else r.get("created_ts")
        paid_iso, prov = nm.parse_ts(ts_raw, PROFILE_PAY)
        if prov == "corrected":
            raw.stats.malformed_ts_corrected += 1
        raw.payments.append({
            "type": "payment", "src": "PAY",
            "txn_id": txn,
            "customer_ref": r.get("customer_ref"),
            "order_ref": (r.get("order_reference") or "").strip() or None,
            "paid": paid_iso,
            "amount_cents": amount,
            "currency": nm.currency(r.get("currency")),
            "method": nm.name(r.get("method")),
            "status": nm.name(r.get("status")),
        })
        kept += 1
    return kept


def _parse_erp(rows: Dict[str, List[Optional[Dict[str, Any]]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    for r in rows.get("invoices.csv", []):
        if r is None or not r.get("invoice_no"):
            continue
        total = nm.money(r.get("total_cents"))
        if total is None:
            continue
        issue, prov = nm.parse_ts(r.get("issue_date"), PROFILE_ERP)
        if prov == "corrected":
            raw.stats.malformed_ts_corrected += 1
        raw.erp_invoices.append({
            "type": "erp_invoice", "src": "ERP",
            "invoice_no": r["invoice_no"],
            "bill_to": nm.name(r.get("bill_to")),
            "total_cents": total,
            "currency": nm.currency(r.get("currency")),
            "issue": issue,
        })
        kept += 1
    for r in rows.get("shipments.csv", []):
        if r is not None and r.get("shipment_no"):
            kept += 1
    return kept


def _parse_sup(rows: List[Optional[Dict[str, Any]]], nm: Normalizer, raw: Raw) -> int:
    kept = 0
    for r in rows:
        if r is None:
            continue
        if not r.get("ticket_id"):
            continue
        created, prov = nm.parse_ts(r.get("created_ts"), PROFILE_SUP)
        if prov == "corrected":
            raw.stats.malformed_ts_corrected += 1
        ref = (r.get("order_ref") or "").strip() or None
        raw.tickets.append({
            "type": "ticket", "src": "SUP",
            "ticket_id": r["ticket_id"],
            "requester_email": nm.email(r.get("requester_email")),
            "order_ref": ref,
        })
        kept += 1
    return kept


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def parse(data_dir: str, rules: Dictionary) -> Raw:
    nm = Normalizer(rules)
    raw = Raw()
    root = Path(data_dir)

    for source in rules.sources():
        code = source["code"]
        fmt = source["format"]
        paths = _discover_files(root, source)
        if code in ("CRM", "MRK", "ERP"):
            by_file: Dict[str, List[Optional[Dict[str, Any]]]] = {}
            total_raw = 0
            for p in paths:
                rows, cnt = (_ndjson_rows(p) if fmt == "ndjson" else _csv_rows(p, int(source.get("min_csv_columns", 1))))
                by_file.setdefault(p.name, []).extend(rows)
                total_raw += cnt
            if code == "CRM":
                kept = _parse_crm(by_file, nm, raw)
            elif code == "MRK":
                kept = _parse_mrk(by_file, nm, raw)
            else:
                kept = _parse_erp(by_file, nm, raw)
        else:
            total_rows = []
            total_raw = 0
            for p in paths:
                rows, cnt = (_ndjson_rows(p) if fmt == "ndjson" else _csv_rows(p, int(source.get("min_csv_columns", 1))))
                total_rows.extend(rows)
                total_raw += cnt
            if code == "STORE":
                kept = _parse_store(total_rows, nm, raw)
            elif code == "IDP":
                kept = _parse_idp(total_rows, nm, raw)
            elif code == "PAY":
                kept = _parse_pay(total_rows, nm, raw)
            elif code == "SUP":
                kept = _parse_sup(total_rows, nm, raw)
            elif code == "CAT":
                kept = _parse_cat(total_rows, nm, raw)
            else:
                kept = 0

        raw.stats.add_processed(code, kept)
        raw.stats.add_rejected(code, max(0, total_raw - kept))
    return raw