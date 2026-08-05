"""Canonical loader + validator for `config/dictionary.toml`.

This is the ONLY parser of the rules file. It is shared by the dataset
generator, the oracle ETL pipeline, and the verifier, so every consumer reads
the same public rules from one place. It carries NO business logic — it only
loads, validates structure, and exposes typed accessors. The rules themselves
live exclusively in `config/dictionary.toml`.

Python 3.11 stdlib only (`tomllib`).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_VALID_COLUMN_TYPES = {"string", "int", "money", "bool", "timestamp", "float", "nested"}
_VALID_ROLES = {"pk", "fk", "data", "meta"}
_VALID_FORMATS = {"csv", "ndjson", "csv_months", "json"}


class DictionaryError(ValueError):
    """Raised when the rules file is structurally invalid or inconsistent."""


class RuleLoadError(DictionaryError):
    """Raised when the rules file cannot be located or parsed."""


class Dictionary:
    """Validated, typed view over the rules file."""

    def __init__(self, data: Dict[str, Any], source_path: str) -> None:
        self._data = data
        self.source_path = source_path
        self._validate()

    # ------------------------------------------------------------------ raw ---
    def raw(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------- structure ---
    def _validate(self) -> None:
        d = self._data
        if d.get("version") is None:
            raise DictionaryError("missing top-level `version`")
        self._validate_currency(d)
        self._validate_sources(d)
        self._validate_outputs(d)
        self._validate_metrics(d)

    def _validate_currency(self, d: Dict[str, Any]) -> None:
        currency = d.get("currency")
        if currency is None:
            raise DictionaryError("missing [currency]")
        if currency.get("base") not in currency.get("rates", {}):
            raise DictionaryError("currency.base must be present in currency.rates")
        if currency.get("rounding") not in {"half_up", "half_even", "floor", "ceil"}:
            raise DictionaryError("unsupported currency.rounding")

    def _validate_sources(self, d: Dict[str, Any]) -> None:
        priority = d.get("source_priority")
        if not isinstance(priority, dict) or not priority:
            raise DictionaryError("missing [source_priority]")
        ranks = list(priority.values())
        if len(set(ranks)) != len(ranks) or any(not isinstance(r, int) for r in ranks):
            raise DictionaryError("source_priority ranks must be unique integers")

        seen = set()
        for src in d.get("sources", []):
            code = src.get("code")
            if not code:
                raise DictionaryError("each [[sources]] must declare a `code`")
            if code in seen:
                raise DictionaryError(f"duplicate source code {code!r}")
            seen.add(code)
            if code not in priority:
                raise DictionaryError(f"source {code!r} missing from [source_priority]")
            if src.get("priority") != priority[code]:
                raise DictionaryError(
                    f"source {code!r} priority {src.get('priority')} != source_priority {priority[code]}"
                )
            for field in ("path", "format"):
                if src.get(field) is None:
                    raise DictionaryError(f"source {code!r} missing `{field}`")
            if src.get("format") not in _VALID_FORMATS:
                raise DictionaryError(f"source {code!r} invalid format {src.get('format')!r}")
            if "id_generation" not in src:
                raise DictionaryError(f"source {code!r} missing [id_generation]")
            for tbl, cols in self._source_tables(src).items():
                for col, meta in cols.items():
                    ctype = meta.get("type")
                    role = meta.get("role")
                    if ctype not in _VALID_COLUMN_TYPES:
                        raise DictionaryError(f"{code}.{tbl}.{col}: bad type {ctype!r}")
                    if role not in _VALID_ROLES:
                        raise DictionaryError(f"{code}.{tbl}.{col}: bad role {role!r}")

    def source_tables(self, code: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Return {table_name: {column: meta}} for a source.

        Most sources have a single `schema` table; CRM/ERP/MRK have one table
        per emitted file (accounts/contacts/opportunities, invoices/shipments,
        subscribers/sends).
        """
        return self._source_tables(self._get_source(code))

    def _source_tables(self, src: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        if "schema" in src:
            return {"schema": src["schema"]}
        return {
            k: v
            for k, v in src.items()
            if k != "id_generation" and isinstance(v, dict)
            and "type" in next(iter(v.values()), {})
        }

    def _get_source(self, code: str) -> Dict[str, Any]:
        for src in self._data.get("sources", []):
            if src.get("code") == code:
                return src
        raise DictionaryError(f"no source with code {code!r}")

    def _validate_outputs(self, d: Dict[str, Any]) -> None:
        outputs = d.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            raise DictionaryError("missing [outputs]")
        for name, out in outputs.items():
            if out.get("format") not in _VALID_FORMATS:
                raise DictionaryError(f"output {name}: bad format {out.get('format')!r}")
            cols = out.get("columns")
            if out.get("format") != "json":
                if not isinstance(cols, dict) or not cols:
                    raise DictionaryError(f"output {name}: missing columns")
                for col in out.get("primary_key", []):
                    if col not in cols:
                        raise DictionaryError(f"output {name}: pk {col!r} not a column")
                for col in out.get("sort", []):
                    if col not in cols:
                        raise DictionaryError(f"output {name}: sort {col!r} not a column")

    def _validate_metrics(self, d: Dict[str, Any]) -> None:
        metrics = d.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            raise DictionaryError("missing [metrics] (report requires every metric defined)")
        for name, formula in metrics.items():
            if not isinstance(formula, str) or not formula.strip():
                raise DictionaryError(f"metric {name!r} must be a non-empty string formula")

    # ------------------------------------------------------------ currency ---
    def currency_base(self) -> str:
        return self._data["currency"]["base"]

    def currency_rate(self, code: str) -> float:
        rates = self._data["currency"]["rates"]
        if code not in rates:
            raise DictionaryError(f"no FX rate for currency {code!r}")
        return float(rates[code])

    def rounding(self) -> str:
        return self._data["currency"]["rounding"]

    # ------------------------------------------------------------- datetime ---
    def parse_order(self) -> List[str]:
        return list(self._data["datetime"].get("parse_order", []))

    def datetime_format(self, name: str) -> str:
        fmt = self._data["datetime"]["formats"]
        if name not in fmt:
            raise DictionaryError(f"unknown datetime format {name!r}")
        return fmt[name]

    def output_timestamp_format(self) -> str:
        return self._data["datetime"]["formats"]["iso8601_z"]

    # ------------------------------------------------------------- sources ---
    def sources(self) -> List[Dict[str, Any]]:
        return list(self._data["sources"])

    def source(self, code: str) -> Dict[str, Any]:
        return self._get_source(code)

    def priority(self, code: str) -> int:
        return int(self._data["source_priority"][code])

    def source_order(self) -> List[Tuple[str, int]]:
        """All source codes ordered by ascending priority (most trustworthy first)."""
        return sorted(self._data["source_priority"].items(), key=lambda kv: kv[1])

    def source_priority_map(self) -> Dict[str, int]:
        return dict(self._data["source_priority"])

    # -------------------------------------------------------------- outputs ---
    def output(self, name: str) -> Dict[str, Any]:
        out = self._data["outputs"]
        if name not in out:
            raise DictionaryError(f"unknown output {name!r}")
        return out[name]

    def output_path(self, name: str) -> str:
        return str(self.output(name)["path"])

    def output_columns(self, name: str) -> List[Tuple[str, Dict[str, Any]]]:
        return list(self.output(name)["columns"].items())

    def primary_key(self, name: str) -> List[str]:
        return list(self.output(name).get("primary_key", []))

    def sort_columns(self, name: str) -> List[str]:
        return list(self.output(name).get("sort", []))

    def nullable_columns(self, name: str) -> List[str]:
        return list(self.output(name).get("nullable_columns", []))

    # ----------------------------------------------------- metrics/invariants ---
    def metric_defs(self) -> Dict[str, str]:
        return dict(self._data["metrics"])

    def invariant(self, name: str) -> str:
        inv = self._data.get("invariants", {})
        if name not in inv:
            raise DictionaryError(f"unknown invariant {name!r}")
        return inv[name]


def load(path: Optional[str] = None) -> Dictionary:
    """Locate and parse the rules file, returning a validated Dictionary."""
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise RuleLoadError(f"rules file not found: {p}")
        return _parse(p)

    for candidate in _default_paths():
        if candidate.exists():
            return _parse(candidate)

    tried = "; ".join(str(c) for c in _default_paths())
    raise RuleLoadError(f"dictionary.toml not found. Tried: {tried}")


def _parse(p: Path) -> Dictionary:
    with open(p, "rb") as fh:
        data = tomllib.load(fh)
    return Dictionary(data, str(p))


def _default_paths() -> List[Path]:
    env = os.environ.get("DYNAMO_DICTIONARY")
    cwd = Path.cwd()
    paths: List[Path] = []
    if env:
        paths.append(Path(env))

    paths += [
        cwd / "config" / "dictionary.toml",
        cwd / "dictionary.toml",
        Path("/app") / "dictionary.toml",
        Path("/app") / "config" / "dictionary.toml",
    ]

    # search upward for config/dictionary.toml relative to cwd
    parent = cwd
    for _ in range(6):
        if parent.parent == parent:
            break
        paths.append(parent / "config" / "dictionary.toml")
        parent = parent.parent
    return paths