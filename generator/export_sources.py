"""Generates all eight source bundles from the master and exports them to an
`environment/data`-style directory.

This is the boundary that produces EXACTLY what the agent will see. Only the
degraded sources exist under `out_dir`; the master is never written here.

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

from generator import (
    corrupt_crm, corrupt_erp, corrupt_idp, corrupt_marketing, corrupt_pay,
    corrupt_retail, corrupt_store, corrupt_support,
)
from generator.generate_master import build_master as _build_master
from generator.master_model import Master
from generator.sources_base import SourceBundle, render_file

CORRUPTORS = {
    "STORE": corrupt_store.generate,
    "PAY": corrupt_pay.generate,
    "CRM": corrupt_crm.generate,
    "ERP": corrupt_erp.generate,
    "IDP": corrupt_idp.generate,
    "SUP": corrupt_support.generate,
    "MRK": corrupt_marketing.generate,
    "CAT": corrupt_retail.generate,
}


def build_all(master: Master) -> Dict[str, SourceBundle]:
    """Return {source_code: SourceBundle} in a deterministic key order."""
    bundles = {}
    for code in sorted(CORRUPTORS):
        bundles[code] = CORRUPTORS[code](master)
    return bundles


def write_all(bundles: Dict[str, SourceBundle], out_dir: str) -> List[str]:
    """Serialize all bundles under `out_dir`, maintaining relative paths."""
    written: List[str] = []
    for code in sorted(bundles):
        bundle = bundles[code]
        fmt = _format_for(code)
        for f in bundle.files:
            target = Path(out_dir) / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_file(f, fmt), encoding="utf-8", newline="")
            written.append(str(target))
    return written


def generate_and_export(out_dir: str, master: Master | None = None) -> Dict[str, SourceBundle]:
    if master is None:
        master = _build_master()
    master.check_invariants()
    bundles = build_all(master)
    write_all(bundles, out_dir)
    return bundles


def write_manifest(bundles: Dict[str, SourceBundle], master: Master, path: str) -> None:
    """Private debug/manifest export (used by the build-time harness only)."""
    manifest = {
        "seed_commit": "MASTER_SEED",
        "counts": {
            "customers": len(master.customers),
            "orders": len(master.orders),
            "payments": len(master.payments),
            "tickets": len(master.tickets),
        },
        "stats": {code: b.stats for code, b in sorted(bundles.items())},
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _format_for(code: str) -> str:
    # formats are defined in dictionary.toml; mirror is avoided by the loader
    from etl.rules import load
    return load().source(code)["format"]