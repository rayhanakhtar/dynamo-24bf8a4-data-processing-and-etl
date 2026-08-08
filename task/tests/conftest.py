"""Shared fixtures/helpers for the Harbor output verifier (self-contained).

The verifier runs inside the environment image with tests/ overlaid at /tests.
It reads:
    /app/output/              the agent's five canonical output files (declared
                              in task.toml [artifacts])
    /tests/expected/          committed oracle fixtures (ground truth)
    /app/config/dictionary.toml   the public normative rules

It is fully self-contained (Python 3.11+ stdlib, tomllib) — it must NOT import
any private implementation code, because etl/ ships only with the solution and is
not present at verify time. All checks operate on observable outputs only.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

TESTS_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.environ.get("DYNAMO_OUTPUT_DIR", "/app/output"))
EXPECTED_DIR = Path(os.environ.get("DYNAMO_EXPECTED_DIR", "/tests/expected"))
DICTIONARY_PATH = Path(os.environ.get("DYNAMO_DICTIONARY", "/app/config/dictionary.toml"))

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
_RE_IDSTRING = re.compile(r"^[A-Za-z0-9_.:\-]+$")
_RE_BOOL = re.compile(r"^(true|false)$")


# ---------------------------------------------------------------------------
# minimal dictionary view (tomllib) — public schema + sort + FX rules
# ---------------------------------------------------------------------------
class Dictionary:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def output_columns(self, name: str) -> List[Tuple[str, Dict[str, Any]]]:
        return list(self._data["outputs"][name]["columns"].items())

    def primary_key(self, name: str) -> List[str]:
        return list(self._data["outputs"][name].get("primary_key", []))

    def sort_columns(self, name: str) -> List[str]:
        return list(self._data["outputs"][name].get("sort", []))

    def currency_rate(self, code: str) -> float:
        return float(self._data["currency"]["rates"][code])


def load_dictionary(path: Path) -> Dictionary:
    with open(path, "rb") as fh:
        return Dictionary(tomllib.load(fh))


# ---------------------------------------------------------------------------
# reading artifacts
# ---------------------------------------------------------------------------
def artifact_path(dir_: Path, artifact: str) -> Path:
    return dir_ / ARTIFACTS[artifact]


def _validate_artifact_path(path: Path, base_dir: Path) -> None:
    """Validate that an artifact path is safe to read.
    
    Rejects:
    - Symlinked files
    - Paths whose realpath escapes the base directory
    - Symlinked base directory
    """
    try:
        real_path = path.resolve(strict=True)
        real_base = base_dir.resolve(strict=True)
    except OSError as e:
        raise RuntimeError(f"Path resolution failed for {path}: {e}")
    
    # Check if the path is a symlink
    if path.is_symlink():
        raise RuntimeError(f"Artifact path is a symlink: {path}")
    
    # Check if the resolved path escapes the base directory
    try:
        real_path.relative_to(real_base)
    except ValueError:
        raise RuntimeError(f"Artifact path escapes base directory: {path} -> {real_path}")


def read_csv(path: Path, base_dir: Optional[Path] = None) -> Tuple[List[str], List[Dict[str, str]]]:
    if base_dir is not None:
        _validate_artifact_path(path, base_dir)
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    return header, [dict(zip(header, row)) for row in rows[1:]]


def read_report(path: Path, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    if base_dir is not None:
        _validate_artifact_path(path, base_dir)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_artifact_bytes(path: Path, base_dir: Path) -> bytes:
    """Read an artifact (or fixture) as raw bytes after path validation."""
    _validate_artifact_path(path, base_dir)
    with open(path, "rb") as fh:
        return fh.read()


def assert_distinct_artifacts(a: Path, b: Path) -> None:
    """Reject produced == expected by resolving real paths.

    Closes the acceptance-boundary gap where the verifier could compare an
    artifact to itself and trivially pass; produced and expected must resolve
    to two different files.
    """
    ra = a.resolve()
    rb = b.resolve()
    assert ra != rb, (
        f"produced and expected resolve to the same artifact: {ra}; "
        f"byte-equality is vacuous and cannot be enforced"
    )


# ---------------------------------------------------------------------------
# cell format / sort checking (independent of any implementation)
# ---------------------------------------------------------------------------
def check_cell(value: str, meta: Dict[str, Any], nullable: bool) -> Optional[str]:
    if value == "":
        return None if nullable else "empty value for non-nullable column"
    fmt = meta.get("format", "value")
    if fmt == "int":
        return None if re.fullmatch(r"-?\d+", value) else f"not an int: {value!r}"
    if fmt == "money":
        return None if _RE_MONEY.fullmatch(value) else f"malformed money: {value!r}"
    if fmt == "bool":
        return None if _RE_BOOL.fullmatch(value) else f"malformed bool: {value!r}"
    if fmt == "iso":
        return None if _RE_ISO.fullmatch(value) else f"not ISO8601_Z: {value!r}"
    if fmt == "date_only":
        return None if _RE_DATE.fullmatch(value) else f"not a date (YYYY-MM-DD): {value!r}"
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
    nullable_cols = {c for c, m in columns.items() if m.get("nullable") is True}
    errors: List[str] = []
    for r_idx, row in enumerate(rows):
        for col in header:
            err = check_cell(row.get(col, ""), columns.get(col, {}), col in nullable_cols)
            if err:
                errors.append(f"row {r_idx} {col}: {err}")
    return errors


def sort_key(row: Dict[str, str], col: str, meta: Dict[str, Any]) -> Any:
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
@pytest.fixture(scope="session")
def dictionary() -> Dictionary:
    return load_dictionary(DICTIONARY_PATH)


@pytest.fixture(scope="session")
def output_dir() -> Path:
    return OUTPUT_DIR


@pytest.fixture(scope="session")
def expected_dir() -> Path:
    return EXPECTED_DIR


@pytest.fixture(scope="session")
def customers(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "customers"), output_dir)
    assert header == [c for c, _ in dictionary.output_columns("customers")]
    return header, rows


@pytest.fixture(scope="session")
def orders(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "orders"), output_dir)
    assert header == [c for c, _ in dictionary.output_columns("orders")]
    return header, rows


@pytest.fixture(scope="session")
def payments(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "payments"), output_dir)
    assert header == [c for c, _ in dictionary.output_columns("payments")]
    return header, rows


@pytest.fixture(scope="session")
def history(dictionary, output_dir) -> Tuple[List[str], List[Dict[str, str]]]:
    header, rows = read_csv(artifact_path(output_dir, "history"), output_dir)
    assert header == [c for c, _ in dictionary.output_columns("customer_history")]
    return header, rows


@pytest.fixture(scope="session")
def report(output_dir) -> Dict[str, Any]:
    return read_report(artifact_path(output_dir, "report"), output_dir)
