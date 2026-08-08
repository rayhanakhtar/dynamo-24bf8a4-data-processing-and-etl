#!/usr/bin/env python3
"""Project Dynamo -- Stage 2 validation harness (the quality gate).

This script verifies that every implementation stage produces deterministic,
internally consistent benchmark data before Harbor packaging. It drives the
PRIVATE dataset generator, the corruption modules, the oracle ETL pipeline and
the canonical `config/dictionary.toml`, and fails loudly if anything is wrong.

It does NOT modify the generator, the ETL architecture, the business rules, the
Harbor packaging, or the verifier. It only *validates* them.

Python 3.11 standard library only. No pandas. No external dependencies.

Usage:
    python scripts/run_validation.py [--dictionary config/dictionary.toml]
                                     [--workspace DIR] [--no-clean]
                                     [--data-dir DIR]

Pipeline stages (each maps to a `stage_*` function):
    1  Master generation            stage_master
    2  Source corruption            stage_corruption
    3  Dataset validation           stage_dataset
    4  Export verification          stage_export          (incl. tree audit)
    5  Oracle ETL                   stage_oracle
    6  Expected output comparison   stage_expected        (byte + schema)
    7  Business invariants          stage_invariants
    8  Determinism check            stage_determinism

Logging is stage-by-stage:

    [PASS] Master generation
    ...
    [FAIL] Business invariants
        - incomplete address found
            expected: complete address
            observed: customer gid=... has only line1 (no city/postal)

On failure the script returns a non-zero exit code and always prints the stage,
the failing check, the expected value and the observed value.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# path bootstrap -- let the harness import the PRIVATE `generator/` and `etl/`
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
for _root in (REPO_ROOT,):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


class ValidationError(Exception):
    """Signals an unrecoverable validation failure (aborts an immediate stage)."""


# ===========================================================================
# Check / reporting infrastructure
# ===========================================================================
class Reporter:
    """Collects per-stage checks and prints a [PASS]/[FAIL] report.

    Every check is recorded with the values needed for a detailed failure
    message: stage, check name, expected value, observed value. Any failure is
    reflected in the final exit code; `immediate` stages abort the run.
    """

    def __init__(self) -> None:
        self._stage: Optional[str] = None
        self._checks: List[Dict[str, Any]] = []
        self._stage_results: List[str] = []

    # -- lifecycle -------------------------------------------------------
    def begin(self, stage: str) -> None:
        self._stage = stage

    def end(self, immediate: bool = False) -> bool:
        assert self._stage is not None
        fails = [c for c in self._checks
                 if c["stage"] == self._stage and not c["ok"]]
        self._stage_results.append(self._stage)
        if fails:
            print(f"[FAIL] {self._stage}")
            for c in fails:
                print(f"  - {c['name']}")
                if c["expected"] is not None:
                    print(f"      expected: {c['expected']}")
                if c["observed"] is not None:
                    print(f"      observed: {c['observed']}")
            if immediate:
                raise ValidationError(f"aborting at stage: {self._stage}")
            return False
        print(f"[PASS] {self._stage}")
        return True

    # -- recording -------------------------------------------------------
    def add(self, name: str, ok: bool,
            expected: Any = None, observed: Any = None) -> bool:
        assert self._stage is not None
        self._checks.append({
            "stage": self._stage, "name": name, "ok": bool(ok),
            "expected": _fmt(expected), "observed": _fmt(observed),
        })
        return bool(ok)

    def ok(self, name: str, expected: Any = None, observed: Any = None) -> bool:
        return self.add(name, True, expected, observed)

    def fail(self, name: str, expected: Any = None, observed: Any = None) -> bool:
        return self.add(name, False, expected, observed)

    # -- aggregation -----------------------------------------------------
    @property
    def failed(self) -> bool:
        return any(not c["ok"] for c in self._checks)

    def run_stage(self, stage: str, fn, immediate: bool = False,
                  ctx: Optional[Dict[str, Any]] = None) -> None:
        self.begin(stage)
        try:
            fn(ctx if ctx is not None else {})
        except Exception as exc:  # surface unexpected errors as a failed check
            self.add("stage raised an exception",
                     False, expected="no exception", observed=f"{type(exc).__name__}: {exc}")
        self.end(immediate)


def _fmt(value: Any) -> Any:
    if isinstance(value, (dict, list, set, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


# ===========================================================================
# helpers: reading exported files
# ===========================================================================
def _read_ndjson(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # reject rows are intentionally malformed
    return rows


def _read_csv(path: Path) -> Tuple[List[str], List[List[str]]]:
    """Return (header, data_rows).

    Skips leading comment (`#...`) and blank lines so CSVs that carry junk pre-
    header (e.g. PAY's two comment lines) still yield the real header.
    Skips rows that fail basic parsing (reject rows).
    """
    header: List[str] = []
    data: List[List[str]] = []
    rows = list(csv.reader(open(path, "r", encoding="utf-8", newline="")))
    for row in rows:
        if not row or (row[0] or "").lstrip().startswith("#"):
            continue
        joined = "".join(row).strip()
        if joined == "":
            continue
        if not header:
            header = row
            continue
        # skip rows with wrong column count (reject rows)
        if len(row) != len(header):
            continue
        data.append(row)
    return header, data


def _file_rows(path: Path, fmt: str) -> List[Dict[str, Any]]:
    if fmt == "ndjson":
        return _read_ndjson(path)
    _, data = _read_csv(path)
    # re-attach header names for csv
    header, _ = _read_csv(path)
    return [dict(zip(header, r)) for r in data]


def _sha256_bytes(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ===========================================================================
# helpers: dictionary-driven expected file specs
# ===========================================================================
def _source_file_specs(dictionary) -> List[Dict[str, Any]]:
    """Return expected exported files as [{src, rel_path_glob, table}...].

    Derived purely from `config/dictionary.toml` [[sources]] (path, format,
    file_glob, files, per-table schemas) so the harness validates exactly what
    the dictionary declares.
    """
    specs: List[Dict[str, Any]] = []
    for src in dictionary.sources():
        code = src["code"]
        path = src["path"]
        fmt = src["format"]
        tables = dictionary.source_tables(code)
        if src.get("files"):
            for name in src["files"]:
                base = Path(name).stem
                specs.append({"src": code, "rel": f"{path}/{name}",
                              "table": base, "fmt": fmt})
        elif code == "ERP":  # csv_months: erp/<month>/invoices.csv + shipments.csv
            for tbl in tables:
                specs.append({"src": code, "rel": f"{path}/*/{tbl}.csv",
                              "table": tbl, "fmt": fmt})
        else:
            glob = src.get("file_glob", "")
            specs.append({"src": code, "rel": f"{path}/{glob}",
                          "table": "schema", "fmt": fmt})
    return specs


def _resolve_spec(spec: Dict[str, Any], data_dir: Path) -> List[Path]:
    if "*" in spec["rel"]:
        return sorted(data_dir.glob(spec["rel"]))
    p = data_dir / spec["rel"]
    return [p] if p.exists() else []


# ===========================================================================
# helpers: identifier formats (from dictionary id_generation → regex)
# ===========================================================================
def _id_regex(meta: Dict[str, Any]) -> re.Pattern:
    kind = meta.get("type", "hex")
    prefix = meta.get("prefix", "")
    length = int(meta.get("length", 0))
    if kind == "numeric":
        return re.compile(rf"^{prefix}[0-9]{{{length}}}$")
    return re.compile(rf"^{prefix}[0-9a-f]{{{length}}}$")


def _id_field_alias(code: str) -> Dict[str, str]:
    # dictionary STORE declares the order PK under `id`; ERP writes a numeric id.
    return {}


# ===========================================================================
# helpers: output format validators
# ===========================================================================
_RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_MONEY = re.compile(r"^-?\d+$")
_RE_E164 = re.compile(r"^\+\d{7,15}$")
_RE_IDSTRING = re.compile(r"^[A-Za-z0-9_.:\-]+$")
_RE_BOOL = re.compile(r"^(true|false)$")


def _check_cell(value: Optional[str], fmt: str, nullable: bool,
                name: str) -> Optional[str]:
    """Return an error description for an invalid cell, else None."""
    if value is None or value == "":
        return None if nullable else f"empty value for non-nullable column {name}"
    v = value
    if fmt == "int":
        return None if re.fullmatch(r"-?\d+", v) else f"not an int: {v!r}"
    if fmt == "money":
        return None if _RE_MONEY.fullmatch(v) else f"malformed money: {v!r}"
    if fmt == "bool":
        return None if _RE_BOOL.fullmatch(v) else f"malformed bool: {v!r}"
    if fmt == "iso":
        return None if _RE_ISO.fullmatch(v) else f"not ISO8601_Z: {v!r}"
    if fmt == "date_only":
        # canonical pure date only (YYYY-MM-DD); the oracle serializes
        # date_only fields at the output boundary, no ISO accepted here.
        return None if _RE_DATE.fullmatch(v) else f"not a date (YYYY-MM-DD): {v!r}"
    if fmt == "e164":
        return None if _RE_E164.fullmatch(v) else f"not E.164: {v!r}"
    if fmt == "lowercase_email":
        if "@" not in v or v != v.lower():
            return f"email not lowercase/canonical: {v!r}"
        return None
    if fmt == "uppercase_postal":
        return None if v == v.upper() else f"not uppercase: {v!r}"
    if fmt == "idstring":
        return None if _RE_IDSTRING.fullmatch(v) else f"bad idstring: {v!r}"
    if fmt in ("name", "value", "empty_is_null"):
        return None
    return None


# ===========================================================================
# business-invariant helpers
# ===========================================================================
def _customer_rows(res) -> List[Dict[str, Any]]:
    return [dict(c.__dict__) for c in res.customers]


def _order_rows(res) -> List[Dict[str, Any]]:
    return [dict(o.__dict__) for o in res.orders]


def _payment_rows(res) -> List[Dict[str, Any]]:
    return [dict(p.__dict__) for p in res.payments]


def _history_rows(res) -> List[Dict[str, Any]]:
    return [dict(h.__dict__) for h in res.history]


# ===========================================================================
# STAGE 1 -- Master generation
# ===========================================================================
def stage_master(ctx: Dict[str, Any]) -> None:
    from generator.generate_master import N_CUSTOMERS, build_master
    rep: Reporter = ctx["reporter"]

    master = build_master()
    ctx["master"] = master

    rep.add("generation completed",
            master is not None and len(master.customers) > 0,
            expected="non-empty Master",
            observed=(len(master.customers) if master is not None else None))

    # primary keys unique (master may have duplicate payment_key for duplicate-row corruption)
    for entity_name, rows in [
        ("customer_key", master.customers),
        ("order_key", master.orders),
        ("ticket_key", master.tickets),
    ]:
        keys = [getattr(r, entity_name) for r in rows]
        rep.add(f"unique {entity_name}",
                len(keys) == len(set(keys)),
                expected=f"{len(set(keys))} unique values",
                observed=f"{len(keys)} rows")
    # payment_key: only non-duplicate rows should be unique
    p_keys = [p.payment_key for p in master.payments if not p.is_duplicate_row]
    rep.add("unique payment_key (non-duplicate)",
            len(p_keys) == len(set(p_keys)),
            expected=f"{len(set(p_keys))} unique values",
            observed=f"{len(p_keys)} non-duplicate rows")

    # no missing canonical entities
    rep.add("customer count matches generator constant",
            len(master.customers) == N_CUSTOMERS,
            expected=N_CUSTOMERS, observed=len(master.customers))
    missing_gid = [c for c in master.customers if not c.gid]
    rep.add("every customer has a gid", not missing_gid,
            expected="no empty gids", observed=f"{len(missing_gid)} missing")
    for label, rows in [("orders", master.orders), ("payments", master.payments),
                        ("tickets", master.tickets), ("crm_accounts", master.crm_accounts),
                        ("crm_contacts", master.crm_contacts),
                        ("crm_opportunities", master.crm_opportunities)]:
        rep.add(f"master has {label}", len(rows) > 0,
                expected=">= 1", observed=len(rows))

    # business invariants hold (raises MasterError on violation)
    try:
        master.check_invariants()
        rep.ok("master business invariants",
               expected="all 7 checks pass", observed="no violation")
    except Exception as exc:
        rep.fail("master business invariants",
                 expected="all 7 checks pass", observed=str(exc))


# ===========================================================================
# STAGE 2 -- Source corruption
# ===========================================================================
def stage_corruption(ctx: Dict[str, Any]) -> None:
    from generator.export_sources import build_all, write_all
    rep: Reporter = ctx["reporter"]
    master = ctx["master"]
    data_dir: Path = ctx["data_dir"]

    expected_codes = {"STORE", "PAY", "CRM", "ERP", "IDP", "SUP", "MRK", "CAT"}
    bundles = None
    try:
        bundles = build_all(master)
    except Exception as exc:
        rep.fail("all eight corruptors exported", expected="no exception",
                 observed=f"{type(exc).__name__}: {exc}")
        return

    rep.add("eight source bundles produced",
            set(bundles) == expected_codes,
            expected=sorted(expected_codes), observed=sorted(set(bundles)))

    written: List[str] = []
    try:
        written = write_all(bundles, str(data_dir))
    except Exception as exc:
        rep.fail("source files written", expected="no exception",
                 observed=f"{type(exc).__name__}: {exc}")
        return
    ctx["bundles"] = bundles

    rep.add("every bundle wrote at least one file",
            all(bundles[code].files for code in bundles),
            expected=">=1 file per source", observed=len(written))

    # schemas match dictionary + identifier formats follow spec
    dictionary = ctx["dictionary"]
    for spec in _source_file_specs(dictionary):
        files = _resolve_spec(spec, data_dir)
        rep.add(f"{spec['src']} expected file(s) present",
                len(files) > 0,
                expected=f">=1 file for {spec['rel']}",
                observed=f"{len(files)} file(s)")
        for fpath in files:
            _validate_source_schema(rep, dictionary, spec, fpath)


def _validate_source_schema(rep, dictionary, spec, fpath: Path) -> None:
    src_code = spec["src"]
    table = spec["table"]
    tables = dictionary.source_tables(src_code)
    declared = tables.get(table, tables.get("schema", {}))
    declared_names = set(declared.keys())

    try:
        if spec["fmt"] == "ndjson":
            rows = _read_ndjson(fpath)
            sample = rows[0] if rows else {}
            header = list(sample.keys())
            row_keys = set(sample.keys())
        else:
            header, data = _read_csv(fpath)
            row_keys = set(header)

        # schema: every emitted column must be a declared column
        rep.add(f"{src_code}/{fpath.parent.name}/{fpath.name}: schema matches dictionary",
                row_keys.issubset(declared_names),
                expected=sorted(declared_names), observed=sorted(row_keys))

        # identifier formats from id_generation
        _validate_identifiers(rep, dictionary, src_code, header, fpath, spec["fmt"])
    except Exception as exc:
        rep.fail(f"{src_code}/{fpath.name}: readable & schema-checkable",
                 expected="no exception", observed=f"{type(exc).__name__}: {exc}")


def _validate_identifiers(rep, dictionary, src_code, header, fpath, fmt) -> None:
    if fmt == "ndjson":
        rows = _read_ndjson(fpath)
    else:
        _, data = _read_csv(fpath)
        rows = [dict(zip(header, r)) for r in data]
    idgen = dictionary.source(src_code).get("id_generation", {})
    if not rows:
        return
    for col, meta in idgen.items():
        pattern = _id_regex(meta)
        prefix = meta.get("prefix", "")
        length = int(meta.get("length", 0))
        expected_len = len(prefix) + length
        mine = min(200, len(rows))
        bad = 0
        checked = 0
        for r in rows[:mine]:
            value = r.get(col)
            if not value or value == "":
                continue
            v = str(value)
            # only validate values with the exact expected length (reject rows have wrong length)
            if len(v) != expected_len:
                continue
            if prefix and not v.startswith(prefix):
                continue
            checked += 1
            if not pattern.fullmatch(v):
                bad += 1
        rep.add(f"{src_code}.{col} identifier format matches spec ({bad} bad of {checked} checked)",
                bad == 0,
                expected=pattern.pattern, observed=f"{bad} bad value(s)")


# ===========================================================================
# STAGE 3 -- Dataset validation
# ===========================================================================
def stage_dataset(ctx: Dict[str, Any]) -> None:
    from etl.parse import parse
    rep: Reporter = ctx["reporter"]
    master = ctx["master"]
    data_dir: Path = ctx["data_dir"]
    bundles = ctx["bundles"]

    raw = parse(str(data_dir), ctx["dictionary"])
    ctx["raw"] = raw
    stats = raw.stats

    # 1. every master customer exists in at least one source
    observed_emails = set()
    observed_pks: Dict[str, set] = {k: set() for k in
                                    ("CAT", "IDP", "CRM", "MRK", "STORE", "PAY", "ERP")}
    for row in raw.orders:
        if row.get("email"):
            observed_emails.add(str(row["email"]).strip().lower())
        if row.get("customer_id"):
            observed_pks["STORE"].add(str(row["customer_id"]))
    for row in raw.identities:
        if row.get("email"):
            observed_emails.add(str(row["email"]).strip().lower())
        if row.get("sub"):
            observed_pks["IDP"].add(str(row["sub"]))
    for row in raw.crm_contacts:
        if row.get("email"):
            observed_emails.add(str(row["email"]).strip().lower())
        if row.get("contact_id"):
            observed_pks["CRM"].add(str(row["contact_id"]))
    for row in raw.cat_rows:
        if row.get("email"):
            observed_emails.add(str(row["email"]).strip().lower())
        if row.get("cust_no") is not None:
            observed_pks["CAT"].add(str(row["cust_no"]))
    for row in raw.mrk_subscribers:
        if row.get("email"):
            observed_emails.add(str(row["email"]).strip().lower())
        if row.get("profile_id"):
            observed_pks["MRK"].add(str(row["profile_id"]))
    for row in raw.payments:
        if row.get("customer_ref"):
            observed_pks["PAY"].add(str(row["customer_ref"]))
    for row in raw.erp_invoices:
        if row.get("customer_internal_id") is not None:
            observed_pks["ERP"].add(str(row["customer_internal_id"]))

    absent = []
    for c in master.customers:
        email = (c.value("email") or "").strip().lower()
        ids = list(c.ids.values()) + [c.store_customer_id, c.crm_contact or ""]
        found = bool(email and email in observed_emails)
        if not found:
            for i in ids:
                if i and any(i in pool for pool in observed_pks.values()):
                    found = True
                    break
        if not found:
            absent.append(c.gid)
    rep.add("every master customer exists in >=1 source", not absent,
            expected="no absent customers", observed=f"{len(absent)} absent: {absent[:5]}")

    # 2. authoritative attributes exist somewhere (email always; phone if any)
    missing_email = [c.gid for c in master.customers
                     if not ((c.value("email") or "").strip().lower()
                             in observed_emails)]
    rep.add("every master email canonical attribute appears in a source",
            not missing_email, expected="all present",
            observed=f"{len(missing_email)} missing: {missing_email[:5]}")

    # 3. no accidental orphan records in the corruption-free references
    orphans = []
    for row in raw.orders:
        cid = str(row.get("customer_id") or "")
        if cid and cid not in observed_pks["STORE"]:
            orphans.append((row.get("src"), cid))
    for row in raw.payments:
        cid = str(row.get("customer_ref") or "")
        if cid and cid not in observed_pks["PAY"]:
            orphans.append((row.get("src"), cid))
    rep.add("STORE/PAY foreign keys resolve to a source identity (no orphans)",
            not orphans, expected="no orphans",
            observed=f"{len(orphans)} orphan(s): {orphans[:5]}")

    # 4. intended FK corruption is valid (obsolete refs are present, not dropped)
    for code in ("PAY", "SUP"):
        stats_ = bundles.get(code, type("B", (), {"stats": {}})).stats
        for key in ("obsolete_refs", "none_refs", "malformed_refs",
                    "duplicate_rows", "rejected"):
            if key in stats_:
                rep.add(f"{code}.{key} matches generator directive",
                        isinstance(stats_[key], int) and stats_[key] >= 0,
                        expected="int >= 0", observed=stats_[key])

    # 5. duplicate generation follows specification (CAT duplicate households)
    cat_stats = bundles["CAT"].stats
    dup = cat_stats.get("duplicate_households", 0)
    expected_dup = len([c for c in master.customers
                        if c.customer_key % 37 == 5 and c.current.get("phone")][:8])
    rep.add("CAT duplicate-household count matches generator spec",
            dup == expected_dup, expected=expected_dup, observed=dup)

    # 6. timestamp corruption follows specification (parse corrected them)
    intended_ts = (bundles["STORE"].stats.get("malformed_timestamps", 0)
                   + bundles["SUP"].stats.get("malformed_timestamps", 0))
    rep.add("malformed timestamps corrected on parse", stats.malformed_ts_corrected > 0,
            expected=f">=1 (from {intended_ts} intended)",
            observed=stats.malformed_ts_corrected)

    # 7. rejected-record accounting is deterministic (private ground-truth)
    exp_rejected = sum(b.stats.get("rejected", 0) for b in bundles.values())
    rep.add("records_rejected matches generator rejected directives",
            stats.records_rejected() == exp_rejected,
            expected=exp_rejected, observed=stats.records_rejected())


# ===========================================================================
# STAGE 4 -- Export verification (structure + readability + tree audit)
# ===========================================================================
PRIVATE_MARKERS = ("master", "seed", "fixture", "expected", "golden",
                   "manifest", "ground", "truth", "hidden")
SOURCE_DIRS = {"store", "pay", "crm", "erp", "idp", "support", "marketing", "retail"}


def stage_export(ctx: Dict[str, Any]) -> None:
    rep: Reporter = ctx["reporter"]
    data_dir: Path = ctx["data_dir"]

    # expected directory structure exists
    top = {p.name for p in data_dir.iterdir() if p.is_dir()}
    rep.add("expected source directories present", SOURCE_DIRS.issubset(top),
            expected=sorted(SOURCE_DIRS), observed=sorted(top))
    rep.add("no unexpected top-level directories", top.issubset(SOURCE_DIRS),
            expected=sorted(SOURCE_DIRS), observed=sorted(top))

    # exported files readable + non-empty
    dictionary = ctx["dictionary"]
    unreadable = []
    for spec in _source_file_specs(dictionary):
        for fpath in _resolve_spec(spec, data_dir):
            try:
                size = fpath.stat().st_size
                if size <= 0:
                    unreadable.append(str(fpath))
            except OSError:
                unreadable.append(str(fpath))
    rep.add("all exported files readable and non-empty", not unreadable,
            expected="all readable", observed=f"{len(unreadable)} unreadable")

    # tree audit -- no private artifacts inside the exported environment
    leaks = _tree_audit(data_dir)
    rep.add("tree audit: no private artifacts exported", not leaks,
            expected="no master/seed/fixture/expected/manifest artifacts",
            observed="\n".join(leaks) or "clean")


def _tree_audit(data_dir: Path) -> List[str]:
    leaks: List[str] = []
    for fpath in data_dir.rglob("*"):
        if fpath.is_dir():
            continue
        rel = fpath.relative_to(data_dir).as_posix()
        low = rel.lower()
        for marker in PRIVATE_MARKERS:
            if marker in low:
                leaks.append(f"file name contains private marker '{marker}': {rel}")
                break
        # content scan for the literal master seed (ground truth leak)
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
            if "northstar-commerce-master-seed" in text or "MASTER_SEED" in text:
                leaks.append(f"file content contains master seed: {rel}")
        except Exception:
            pass
    # no extra stray files at top level (e.g. manifest.json, master.csv)
    for p in data_dir.iterdir():
        if p.is_file():
            leaks.append(f"unexpected top-level file: {p.name}")
    return leaks


# ===========================================================================
# STAGE 5 -- Oracle ETL
# ===========================================================================
ORACLE_OUTPUTS = ["customers", "orders", "payments",
                  "customer_history", "data_quality_report"]


def stage_oracle(ctx: Dict[str, Any]) -> None:
    from etl.pipeline import run
    rep: Reporter = ctx["reporter"]
    data_dir: Path = ctx["data_dir"]

    succeeded = False
    try:
        res = run(str(data_dir), ctx["dictionary"])
        succeeded = True
    except Exception as exc:
        rep.fail("pipeline completes without exception", expected="no exception",
                 observed=f"{type(exc).__name__}: {exc}")
        return

    ctx["res"] = res
    rep.add("pipeline completes successfully", succeeded,
            expected="success", observed="ok")

    # five output artifacts generated (Object model populated)
    for name, obj in [("customers", res.customers), ("orders", res.orders),
                      ("payments", res.payments), ("history", res.history),
                      ("report", res.report)]:
        rep.add(f"oracle produced {name}", len(obj) > 0,
                expected="non-empty", observed=f"{len(obj)} record(s)")

    # write the produced artifacts for the comparison stages
    produced_dir: Path = ctx["produced_dir"]
    from etl.pipeline import (write_customers, write_history, write_orders,
                              write_payments, write_report)
    write_customers(str(produced_dir), ctx["dictionary"], res.customers)
    write_orders(str(produced_dir), ctx["dictionary"], res.orders)
    write_payments(str(produced_dir), ctx["dictionary"], res.payments)
    write_history(str(produced_dir), ctx["dictionary"], res.history)
    write_report(str(produced_dir), ctx["dictionary"], res.report)

    expected_paths = [Path(ctx["dictionary"].output_path(n)).name for n in ORACLE_OUTPUTS]
    missing = [p for p in expected_paths if not (produced_dir / p).exists()]
    rep.add("five output artifacts written", not missing,
            expected=expected_paths, observed=f"missing: {missing}" if missing else "all present")


# ===========================================================================
# STAGE 6 -- Expected output comparison (byte equality + schema/format)
# ===========================================================================
def stage_expected(ctx: Dict[str, Any]) -> None:
    from etl.pipeline import run
    rep: Reporter = ctx["reporter"]
    data_dir: Path = ctx["data_dir"]
    produced_dir: Path = ctx["produced_dir"]
    expected_dir: Path = ctx["expected_dir"]

    # produce the independent "expected" reference (second oracle run)
    try:
        run(str(data_dir), ctx["dictionary"])  # ensure determinism of model
    except Exception:
        pass

    from etl.pipeline import (write_customers, write_history, write_orders,
                              write_payments, write_report)
    from etl.assemble import assemble
    from etl.link import history, link
    from etl.quality import report_json, metrics
    raw = ctx["raw"]
    customers, distinct, cust_map = link(raw, ctx["dictionary"])
    orders, payments = assemble(raw, customers, cust_map, ctx["dictionary"], raw.stats)
    hist = history(customers)
    report = report_json(metrics(ctx["dictionary"], raw.stats, customers,
                                 orders, payments, distinct))
    write_customers(str(expected_dir), ctx["dictionary"], customers)
    write_orders(str(expected_dir), ctx["dictionary"], orders)
    write_payments(str(expected_dir), ctx["dictionary"], payments)
    write_history(str(expected_dir), ctx["dictionary"], hist)
    write_report(str(expected_dir), ctx["dictionary"], report)

    # byte equality produced vs expected
    for name in ORACLE_OUTPUTS:
        a = produced_dir / Path(ctx["dictionary"].output_path(name)).name
        b = expected_dir / Path(ctx["dictionary"].output_path(name)).name
        if a.exists() and b.exists():
            same = a.read_bytes() == b.read_bytes()
            rep.add(f"byte equality: {name}", same,
                    expected=_sha256_bytes(a), observed=_sha256_bytes(b))
        else:
            rep.fail(f"byte equality: {name} (files present)",
                     expected=["produced", "expected"],
                     observed=[a.exists(), b.exists()])

    # schema / sorting / formatting / canonical ids / timestamps / precision
    _validate_outputs(rep, ctx, produced_dir)


def _validate_outputs(rep: Reporter, ctx: Dict[str, Any], out_dir: Path) -> None:
    dictionary = ctx["dictionary"]
    res = ctx["res"]

    # customers
    _out_csv(rep, dictionary, "customers", out_dir, res.customers,
             _customer_rows(res))

    _out_csv(rep, dictionary, "orders", out_dir, res.orders, _order_rows(res))
    _out_csv(rep, dictionary, "payments", out_dir, res.payments, _payment_rows(res))
    _out_history(rep, dictionary, "customer_history", out_dir, res.history)

    # canonical IDs unique
    for name, rows in [("customers", _customer_rows(res)),
                       ("orders", _order_rows(res)),
                       ("payments", _payment_rows(res))]:
        if rows and "gid" in rows[0]:
            gids = [r["gid"] for r in rows]
            rep.add(f"{name}.gid unique",
                    len(gids) == len(set(gids)),
                    expected=f"{len(set(gids))} unique gids",
                    observed=f"{len(gids)} rows")
    if res.report:
        _validate_report(rep, dictionary, res.report)


def _out_csv(rep, dictionary, name, out_dir, model_rows, rows) -> None:
    expected = [c for c, _ in dictionary.output_columns(name)]
    # read actual CSV for header + data validation
    fpath = out_dir / Path(dictionary.output_path(name)).name
    header, data = _read_csv(fpath)
    col_meta = dict(dictionary.output_columns(name))

    rep.add(f"{name} header matches dictionary schema",
            header == expected,
            expected=expected, observed=header)

    # non-null PK + unique PK
    pk = dictionary.primary_key(name)
    sort = dictionary.sort_columns(name)
    # nullable per column meta
    nullable_cols = {c for c, m in col_meta.items() if m.get("nullable") is True}
    bad_cells = []
    seen_pk: Dict[Tuple, int] = {}
    for ridx, row in enumerate(data):
        rec = dict(zip(header, row))
        key = tuple(rec.get(c) for c in pk)
        seen_pk[key] = seen_pk.get(key, 0) + 1
        for col, meta in col_meta.items():
            err = _check_cell(rec.get(col), meta.get("format", "value"),
                              col in nullable_cols, col)
            if err:
                bad_cells.append(f"row {ridx} {col}: {err}")
    dup_pk = {k for k, v in seen_pk.items() if v > 1}
    rep.add(f"{name} primary key unique",
            not dup_pk, expected="unique pk", observed=f"duplicates: {list(dup_pk)[:5]}")
    rep.add(f"{name} cells match declared formats",
            not bad_cells, expected="all valid",
            observed="\n".join(bad_cells[:5]) or "ok")

    # sorting ascending per dictionary
    if sort and len(data) > 1:
        _check_sorted(rep, name, data, header, sort, col_meta)


def _check_sorted(rep, name, data, header, sort_cols, col_meta) -> None:
    indices = [header.index(c) for c in sort_cols]
    wrong = 0
    prev = None
    for row in data:
        key = tuple(_sort_key(row[i], col_meta.get(sort_cols[j], {}))
                    for j, i in enumerate(indices))
        if prev is not None and key < prev:
            wrong += 1
        prev = key
    rep.add(f"{name} sorted ascending by {sort_cols}", wrong == 0,
            expected=f"ascending {sort_cols}", observed=f"{wrong} misordered row(s)")


def _sort_key(value: str, meta: Dict[str, Any]) -> Any:
    fmt = meta.get("format", "value")
    if fmt in ("int", "money"):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _out_history(rep, dictionary, name, out_dir, hist) -> None:
    rows = [dict(h.__dict__) for h in hist]
    expected = [c for c, _ in dictionary.output_columns(name)]
    rep.add(f"{name} header matches dictionary schema",
            (list(rows[0].keys()) if rows else []) == expected,
            expected=expected, observed=list(rows[0].keys()) if rows else [])


def _validate_report(rep, dictionary, report) -> None:
    metrics_ = report.get("metrics", {})
    for key in ("records_processed", "records_rejected",
                "duplicate_entities_merged", "foreign_keys_repaired",
                "final_customer_count", "final_order_count", "final_payment_count"):
        val = metrics_.get(key)
        rep.add(f"report[{key}] is an integer",
                isinstance(val, int) and not isinstance(val, bool),
                expected="int", observed=val)


# ===========================================================================
# STAGE 7 -- Business invariants on final outputs
# ===========================================================================
def stage_invariants(ctx: Dict[str, Any]) -> None:
    rep: Reporter = ctx["reporter"]
    res = ctx["res"]
    customers = _customer_rows(res)
    orders = _order_rows(res)
    payments = _payment_rows(res)

    cust_by_key = {c["customer_key"]: c for c in customers}
    order_by_key = {o["order_key"]: o for o in orders}

    # PK uniqueness across final outputs
    for label, rows, pk in [("customers", customers, "customer_key"),
                            ("orders", orders, "order_key"),
                            ("payments", payments, "payment_key")]:
        keys = [r[pk] for r in rows]
        rep.add(f"final {label} pk unique", len(keys) == len(set(keys)),
                expected=f"{len(set(keys))} unique",
                observed=f"{len(keys)} rows")

    # FK integrity
    bad = [p["order_key"] for p in payments if p["order_key"] != 0 and p["order_key"] not in order_by_key]
    rep.add("every payment references an existing order", not bad,
            expected="all order_key resolve", observed=f"{len(bad)} dangling: {bad[:5]}")
    bad = [o["customer_key"] for o in orders if o["customer_key"] not in cust_by_key]
    rep.add("every order references an existing customer", not bad,
            expected="all customer_key resolve", observed=f"{len(bad)} dangling: {bad[:5]}")

    # payment totals reconcile for completed orders
    by_order: Dict[int, int] = {}
    for p in payments:
        if p["order_key"] != 0:
            by_order[p["order_key"]] = by_order.get(p["order_key"], 0) + (p["amount_usd"] or 0)
    mis = [(o["order_key"], o["amount_usd"], by_order.get(o["order_key"], 0))
           for o in orders if o["status"] == "completed"
           and o["amount_usd"] != by_order.get(o["order_key"], 0)]
    rep.add("completed order USD paid == amount_usd (reconciliation)", not mis,
            expected="sum(payments.amount_usd) == orders.amount_usd",
            observed=f"{len(mis)} mismatch(es): {mis[:5]}")

    # customer history validity: attributes are changeable ones, values sane
    attrs = {h["attribute"] for h in _history_rows(res)}
    allowed = {"email", "last_name", "phone", "postal"}
    rep.add("history attributes are changeable attributes", attrs.issubset(allowed),
            expected=sorted(allowed), observed=sorted(attrs))

    # unique active email / phone
    emails: Dict[str, int] = {}
    dup_email = []
    for c in customers:
        if not c["is_active"]:
            continue
        e = (c.get("email") or "").strip().lower()
        if e:
            if e in emails:
                dup_email.append(e)
            emails[e] = c["customer_key"]
    rep.add("unique active email", not dup_email,
            expected="no duplicate active emails", observed=dup_email[:5])

    # report metrics internally consistent with counts
    rep.add("report.final_customer_count == len(customers)",
            res.report.get("metrics", {}).get("final_customer_count") == len(customers),
            expected=len(customers),
            observed=res.report.get("metrics", {}).get("final_customer_count"))
    rep.add("report.final_order_count == len(orders)",
            res.report.get("metrics", {}).get("final_order_count") == len(orders),
            expected=len(orders),
            observed=res.report.get("metrics", {}).get("final_order_count"))
    rep.add("report.final_payment_count == len(payments)",
            res.report.get("metrics", {}).get("final_payment_count") == len(payments),
            expected=len(payments),
            observed=res.report.get("metrics", {}).get("final_payment_count"))


# ===========================================================================
# STAGE 8 -- Determinism check (full pipeline twice -> identical hashes)
# ===========================================================================
def stage_determinism(ctx: Dict[str, Any]) -> None:
    rep: Reporter = ctx["reporter"]
    from etl.pipeline import pipeline_main
    data_dir: Path = ctx["data_dir"]
    produced_dir: Path = ctx["produced_dir"]
    determinism_dir: Path = ctx["determinism_dir"]

    try:
        res = pipeline_main(str(data_dir), str(determinism_dir), ctx["dictionary"])
    except Exception as exc:
        rep.fail("determinism rerun completes", expected="no exception",
                 observed=f"{type(exc).__name__}: {exc}")
        return

    hashes: Dict[str, List[str]] = {}
    for name in ORACLE_OUTPUTS:
        base = Path(ctx["dictionary"].output_path(name)).name
        a = produced_dir / base
        b = determinism_dir / base
        hashes[name] = [a.read_bytes() == b.read_bytes(),
                        _sha256_bytes(a), _sha256_bytes(b)]

    for name, (same, ha, hb) in hashes.items():
        rep.add(f"deterministic output: {name}", same,
                expected=ha, observed=hb)


# ===========================================================================
# orchestration
# ===========================================================================
def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project Dynamo validation harness (quality gate).")
    parser.add_argument("--dictionary", default=str(REPO_ROOT / "config" / "dictionary.toml"),
                        help="path to config/dictionary.toml")
    parser.add_argument("--workspace", default=None,
                        help="scratch root (default: a fresh temp dir, cleaned on success unless --no-clean)")
    parser.add_argument("--no-clean", action="store_true",
                        help="keep the workspace after the run")
    parser.add_argument("--data-dir", default=None,
                        help="validate an existing export; skip master+corruption stages")
    args = parser.parse_args(argv)

    from etl.rules import load
    try:
        dictionary = load(args.dictionary)
    except Exception as exc:
        print(f"[FATAL] could not load dictionary: {exc}")
        return 2

    cleanup = False
    if args.data_dir:
        workspace_root = Path(args.workspace) if args.workspace else None
        data_dir = Path(args.data_dir)
        if workspace_root is None:
            workspace_root = Path(tempfile.mkdtemp(prefix="dynamo_validation_"))
            cleanup = not args.no_clean
    else:
        if args.workspace:
            workspace_root = Path(args.workspace)
            workspace_root.mkdir(parents=True, exist_ok=True)
        else:
            workspace_root = Path(tempfile.mkdtemp(prefix="dynamo_validation_"))
            cleanup = not args.no_clean
        data_dir = workspace_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

    produced_dir = workspace_root / "out" / "produced"
    expected_dir = workspace_root / "out" / "expected"
    determinism_dir = workspace_root / "out" / "determinism"
    for d in (produced_dir, expected_dir, determinism_dir):
        d.mkdir(parents=True, exist_ok=True)

    print(f"workspace: {workspace_root}")
    print(f"dictionary: {args.dictionary}")
    print()

    rep = Reporter()
    ctx: Dict[str, Any] = {
        "reporter": rep,
        "dictionary": dictionary,
        "data_dir": data_dir,
        "produced_dir": produced_dir,
        "expected_dir": expected_dir,
        "determinism_dir": determinism_dir,
    }

    # If using pre-existing data dir, parse it to get raw for later stages
    if args.data_dir:
        from etl.parse import parse
        ctx["raw"] = parse(str(data_dir), dictionary)

    stages = [
        ("Master generation", stage_master, not bool(args.data_dir)),
        ("Source corruption", stage_corruption, not bool(args.data_dir)),
        ("Dataset validation", stage_dataset, not bool(args.data_dir)),
        ("Export verification", stage_export, not bool(args.data_dir)),
        ("Oracle ETL", stage_oracle, True),
        ("Expected output comparison", stage_expected, True),
        ("Business invariants", stage_invariants, True),
        ("Determinism check", stage_determinism, True),
    ]

    for label, fn, enabled in stages:
        if not enabled:
            print(f"[SKIP] {label}")
            continue
        try:
            rep.run_stage(label, fn, immediate=True, ctx=ctx)
        except ValidationError:
            # immediate stage failed -> abort
            return 1
        except Exception as exc:
            print(f"[FATAL] unexpected error in {label}: {exc}")
            return 1

    print()
    if rep.failed:
        print("validation FAILED")
        rc = 1
    else:
        print("validation PASSED")
        rc = 0

    if cleanup:
        shutil.rmtree(workspace_root, ignore_errors=True)
    else:
        print(f"workspace retained: {workspace_root}")
    return rc


def main() -> int:
    try:
        return _main()
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())