"""Deterministic expected-fixture generator AND fixture drift guard.

Stage 3: builds the canonical oracle outputs from the validated benchmark
dataset and stores them under ``tests/expected/`` so the pytest verifier can
compare against committed ground truth.

Responsibilities
----------------
1. Execute the oracle ETL pipeline against the validated benchmark dataset.
2. Generate the canonical outputs:
       customers.csv, orders.csv, payments.csv,
       customer_history.csv, data_quality_report.json
3. Store them inside ``tests/expected/``.
4. Drift guard: if fixtures already exist, regenerate them, compare
   byte-for-byte with the committed fixtures, and fail immediately if any
   output differs — clearly naming the artifact that drifted.

The dataset is regenerated deterministically from the private master if no
``--data-dir`` is supplied, so the script is usable at any time and always
produces the same bytes (master generation is byte-stable).

Python 3.11 standard library only. No pandas. No external dependencies.

Usage:
    python generator/build_expected.py [--data-dir DATA] [--dictionary config/dictionary.toml]
                                       [--expected-dir tests/expected]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
for _root in (REPO_ROOT,):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from etl.rules import Dictionary, load as load_rules  # noqa: E402

# The five canonical artifacts, in the order they must exist on disk.
ARTIFACTS: List[str] = [
    "customers.csv",
    "orders.csv",
    "payments.csv",
    "customer_history.csv",
    "data_quality_report.json",
]


def _output_filename(rules: Dictionary, name: str) -> str:
    """Map an artifact name to its on-disk basename per the dictionary."""
    if name == "data_quality_report.json":
        base = Path(rules.output_path("data_quality_report")).name
    else:
        stem = name[: -len(".csv")]
        base = Path(rules.output_path(stem)).name
    return base


def _oracle_outputs(data_dir: str, rules: Dictionary) -> Dict[str, bytes]:
    """Run the oracle ETL pipeline and return {artifact_basename: bytes}."""
    from etl.pipeline import pipeline_main

    with tempfile.TemporaryDirectory(prefix="dynamo_expected_") as tmp:
        pipeline_main(data_dir, tmp, rules)
        produced: Dict[str, bytes] = {}
        for artifact in ARTIFACTS:
            fname = _output_filename(rules, artifact)
            produced[fname] = (Path(tmp) / fname).read_bytes()
        return produced


def _build_dataset(dict_path: Path) -> Path:
    """Regenerate the validated benchmark dataset into a temp directory."""
    from generator.export_sources import generate_and_export
    from generator.generate_master import build_master

    tmp = Path(tempfile.mkdtemp(prefix="dynamo_dataset_"))
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    master = build_master()
    master.check_invariants()
    generate_and_export(str(data_dir), master)
    return data_dir


def _expected_snapshot(expected_dir: Path, rules: Dictionary) -> Dict[str, bytes]:
    snapshot: Dict[str, bytes] = {}
    for artifact in ARTIFACTS:
        fname = _output_filename(rules, artifact)
        path = expected_dir / fname
        if not path.exists():
            continue
        snapshot[fname] = path.read_bytes()
    return snapshot


def _write_fixtures(expected_dir: Path, produced: Dict[str, bytes]) -> List[str]:
    written: List[str] = []
    expected_dir.mkdir(parents=True, exist_ok=True)
    for fname, payload in produced.items():
        (expected_dir / fname).write_bytes(payload)
        written.append(fname)
    return written


def _compare_snapshots(
    committed: Dict[str, bytes],
    produced: Dict[str, bytes],
) -> Tuple[List[str], Dict[str, str]]:
    import hashlib

    changed: List[str] = []
    digests: Dict[str, str] = {}
    all_names = sorted(set(committed) | set(produced))
    for fname in all_names:
        digests[fname] = hashlib.sha256(produced[fname]).hexdigest()
        if fname not in committed:
            changed.append(f"{fname} (missing committed fixture)")
        elif committed[fname] != produced[fname]:
            changed.append(fname)
    return changed, digests


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate canonical expected fixtures and guard against drift.")
    parser.add_argument("--data-dir", default=None,
                        help="validated benchmark dataset dir (default: regenerate deterministically)")
    parser.add_argument("--dictionary", default=str(REPO_ROOT / "config" / "dictionary.toml"),
                        help="path to config/dictionary.toml")
    parser.add_argument("--expected-dir", default=str(REPO_ROOT / "tests" / "expected"),
                        help="where committed fixtures live")
    args = parser.parse_args(argv)

    try:
        rules = load_rules(args.dictionary)
    except Exception as exc:
        print(f"[FATAL] could not load dictionary: {exc}")
        return 2

    expected_dir = Path(args.expected_dir)

    if args.data_dir:
        data_dir = args.data_dir
        print(f"data-dir   : {data_dir}")
    else:
        data_dir = _build_dataset(Path(args.dictionary))
        print(f"data-dir   : {data_dir} (deterministically regenerated)")

    print(f"expected-dir: {expected_dir}")

    print("running oracle ETL pipeline ...")
    try:
        produced = _oracle_outputs(data_dir, rules)
    except Exception as exc:
        print(f"[FAIL] oracle pipeline failed: {type(exc).__name__}: {exc}")
        return 1

    existing = _expected_snapshot(expected_dir, rules)
    if not existing:
        written = _write_fixtures(expected_dir, produced)
        print(f"[GEN] no committed fixtures found; wrote {len(written)} artifacts:")
        for fname in written:
            print(f"      - {expected_dir / fname}")
        print("COMMIT these fixtures; they are the verifier's ground truth.")
        return 0

    changed, digests = _compare_snapshots(existing, produced)
    if changed:
        print("[FAIL] fixture drift detected — the oracle outputs changed.")
        for fname in changed:
            print(f"      - {fname}: sha256={digests.get(fname, 'n/a')}")
        print("      Expected fixtures and freshly generated outputs must be byte-identical.")
        return 1

    print("[PASS] all fixtures byte-identical to freshly generated oracle outputs.")
    for fname in sorted(digests):
        print(f"      - {fname}: sha256={digests[fname]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
