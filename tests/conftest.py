"""Shared fixtures and helpers for the Project Dynamo output verifier.

Every test module imports its helpers from here so each module can stay
focused on ONE artifact (customers / orders / payments / history / report).

The verifier never inspects implementation code — it validates the committed
fixtures under ``tests/expected/`` (ground truth) and, when ``DYNAMO_OUTPUT_DIR``
is set, additionally asserts those artifacts are byte-identical to the produced
outputs found there.

Python 3.11 standard library only. No pandas. No external dependencies.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from etl.rules import load as load_rules  # noqa: E402

import pytest  # noqa: E402

EXPECTED_DIR = TESTS_DIR / "expected"

ARTIFACTS = {
    "customers": "customers.csv",
    "orders": "orders.csv",
    "payments": "payments.csv",
    "history": "customer_history.csv",
    "report": "data_quality_report.json",
}

_RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_E164 = re.compile(r"^\+\d{7,15}$")
_RE_MONEY = re.compile(r"^-?\d+$")
_RE_INT = re.compile(r"^-?\d+$")
_RE_IDSTRING = re.compile(r"^[A-Za-z0-9_.:\-]+$")
_RE_BOOL = re.compile(r"^(true|false)$")


# ---------------------------------------------------------------------------
# reading artifacts
# ---------------------------------------------------------------------------
def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Return (header, list-of-row-dicts) for a canonical output CSV.

    Reads the raw bytes through RFC4180 CSV exactly as written by the oracle.
    """
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    data = [dict(zip(header, row)) for row in rows[1:]]
    return header, data


def read_report(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def artifact_path(dir_: Path, artifact: str) -> Path:
    return dir_ / ARTIFACTS[artifact]


def artifact_bytes(dir_: Path, artifact: str) -> bytes:
    return artifact_path(dir_, artifact).read_bytes()


# ---------------------------------------------------------------------------
# cell format checking (independent of the oracle's own writer)
# ---------------------------------------------------------------------------
def check_cell(value: str, meta: Dict[str, Any], nullable: bool) -> Optional[str]:
    """Return an error description for an invalid cell, else None."""
    if value == "":
        return None if nullable else "empty value for non-nullable column"
    fmt = meta.get("format", "value")
    if fmt == "int":
        return None if _RE_INT.fullmatch(value) else f"not an int: {value!r}"
    if fmt == "money":
        return None if _RE_MONEY.fullmatch(value) else f"malformed money: {value!r}"
    if fmt == "bool":
        return None if _RE_BOOL.fullmatch(value) else f"malformed bool: {value!r}"
    if fmt == "iso":
        return None if _RE_ISO.fullmatch(value) else f"not ISO8601_Z: {value!r}"
    if fmt == "date_only":
        return None if (_RE_DATE.fullmatch(value) or _RE_ISO.fullmatch(value)) \
            else f"not a date or ISO: {value!r}"
    if fmt == "e164":
        return None if _RE_E164.fullmatch(value) else f"not E.164: {value!r}"
    if fmt == "lowercase_email":
        if "@" not in value or value != value.lower():
            return f"email not lowercase/canonical: {value!r}"
        return None
    if fmt == "uppercase_postal":
        return None if value == value.upper() else f"not uppercase: {value!r}"
    if fmt == "idstring":
        return None if _RE_IDSTRING.fullmatch(value) else f"bad idstring: {value!r}"
    return None


def validate_cells(header: List[str], rows: List[Dict[str, str]],
                   columns: Dict[str, Dict[str, Any]]) -> List[str]:
    """Run check_cell over every cell; return a list of violations."""
    nullable_cols = {c for c, m in columns.items() if m.get("nullable") is True}
    errors: List[str] = []
    for r_idx, row in enumerate(rows):
        for col in header:
            err = check_cell(row.get(col, ""), columns.get(col, {}), col in nullable_cols)
            if err:
                errors.append(f"row {r_idx} {col}: {err}")
    return errors


def sort_key(row: Dict[str, str], col: str, meta: Dict[str, Any]) -> Any:
    """Type-aware ascending sort key for a canonical column."""
    fmt = meta.get("format", "value")
    value = row.get(col, "")
    if fmt in ("int", "money"):
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)
    return (1, value)


def is_sorted(rows: List[Dict[str, str]], sort_cols: List[str],
              columns: Dict[str, Dict[str, Any]]) -> Optional[Tuple[int, Any, Any]]:
    """Return the first (index, previous_key, current_key) misorder, or None."""
    prev: Optional[Tuple] = None
    for i, row in enumerate(rows):
        key = tuple(sort_key(row, c, columns[c]) for c in sort_cols)
        if prev is not None and key < prev:
            return (i, prev, key)
        prev = key
    return None


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------
def _env_dir(var: str, default: Path) -> Path:
    raw = os.environ.get(var)
    return Path(raw) if raw else default


def _dictionary():
    return load_rules(str(REPO_ROOT / "config" / "dictionary.toml"))


def _output_dir() -> Path:
    return _env_dir("DYNAMO_OUTPUT_DIR", EXPECTED_DIR)


@pytest.fixture(scope="session")
def dictionary():
    """Loaded config/dictionary.toml (the public rules)."""
    return _dictionary()


@pytest.fixture(scope="session")
def expected_dir() -> Path:
    """Committed fixture directory — the verifier's ground truth."""
    return EXPECTED_DIR


@pytest.fixture(scope="session")
def output_dir() -> Path:
    """Directory holding the outputs under test (default: the fixtures).

    Set DYNAMO_OUTPUT_DIR to point at a freshly produced oracle run to make
    every byte-equality assertion a real comparison.
    """
    return _output_dir()


@pytest.fixture(scope="session")
def customers(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "customers"))
    assert header == [c for c, _ in dictionary.output_columns("customers")]
    return header, rows


@pytest.fixture(scope="session")
def orders(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "orders"))
    assert header == [c for c, _ in dictionary.output_columns("orders")]
    return header, rows


@pytest.fixture(scope="session")
def payments(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "payments"))
    assert header == [c for c, _ in dictionary.output_columns("payments")]
    return header, rows


@pytest.fixture(scope="session")
def history(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "history"))
    assert header == [c for c, _ in dictionary.output_columns("customer_history")]
    return header, rows


@pytest.fixture(scope="session")
def report(output_dir) -> Dict[str, Any]:
    return read_report(artifact_path(output_dir, "report"))
