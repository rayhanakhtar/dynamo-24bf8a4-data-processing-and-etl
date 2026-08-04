"""Shared infrastructure for the eight source-system corruptors.

Each corrupt_<code>.py exposes `generate(master) -> SourceBundle`. A bundle is a
list of SourceFile (relative path + list of units) plus statistics used by the
validation stage to check that corruption budgets match the specification.

A unit is one of:
  * dict  — a structured row (serialized as NDJSON object or CSV row/header)
  * list  — a raw CSV row written verbatim (e.g. a reject line)
  * str   — a raw line written verbatim (e.g. NDJSON reject line or comment)

Everything here is deterministic: units are emitted in a fixed order and all
corruption choices come from per-record PRNGs.

PRIVATE: ships with generator/ (→ tests/), never to environment/.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

Unit = Union[Dict[str, Any], List[str], str]


@dataclass
class SourceFile:
    path: str
    units: List[Unit]


@dataclass
class SourceBundle:
    code: str
    files: List[SourceFile]
    stats: Dict[str, Any] = field(default_factory=dict)


def us_phone(e164: Optional[str]) -> Optional[str]:
    """+1 503 555 0140 -> (503) 555-0140"""
    if not e164:
        return None
    d = "".join(ch for ch in e164 if ch.isdigit())
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10:
        return e164
    return f"({d[0:3]}) {d[3:6]}-{d[6:10]}"


def noise_email(email: str, rng) -> str:
    """Upper-case + leading/trailing whitespace (typical export hygiene)."""
    out = email
    if rng.random() < 0.6:
        out = out.upper()
    if rng.random() < 0.35:
        out = " " + out + " "
    elif rng.random() < 0.5:
        out = out + " "
    return out


def iso_to_mdy(iso: str) -> str:
    return dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").strftime("%m/%d/%Y")


def render_file(f: SourceFile, fmt: str) -> str:
    """Serialize one SourceFile into its textual export form.

    CSV files get a header row derived from the first structured dict's keys;
    raw list/str units are written verbatim (used for reject lines and comment
    headers). NDJSON writes one JSON object per dict unit, raw str verbatim.
    """
    if fmt in ("ndjson", "json"):
        lines: List[str] = []
        for unit in f.units:
            if isinstance(unit, dict):
                lines.append(json.dumps(unit, ensure_ascii=False, separators=(",", ":")))
            else:
                lines.append(str(unit).rstrip("\n"))
        return "\n".join(lines) + "\n"

    if fmt in ("csv", "csv_months"):
        first_dict = next((u for u in f.units if isinstance(u, dict)), None)
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        if first_dict is not None:
            writer.writerow(list(first_dict.keys()))
        for unit in f.units:
            if isinstance(unit, dict):
                writer.writerow([_csv_value(v) for v in unit.values()])
            elif isinstance(unit, list):
                writer.writerow(unit)
            else:
                buf.write(str(unit).rstrip("\n") + "\n")
        return buf.getvalue()

    raise ValueError(f"unknown format {fmt!r}")


def _csv_value(v: Any) -> str:
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def partition_units(bundle: SourceBundle) -> List[Unit]:
    """Flatten all units of a bundle in order (validation helper)."""
    out: List[Unit] = []
    for f in bundle.files:
        out.extend(f.units)
    return out


def structured_units(bundle: SourceBundle) -> List[Dict[str, Any]]:
    return [u for u in partition_units(bundle) if isinstance(u, dict)]
