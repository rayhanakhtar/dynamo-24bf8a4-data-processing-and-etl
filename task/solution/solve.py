#!/usr/bin/env python3
"""Oracle (reference) solution for the Northstar Commerce consolidation task.

Runs the deterministic ETL pipeline against the PUBLIC benchmark inputs:
    /app/data/              the eight degraded source exports
    /app/config/dictionary.toml   the normative public rules

and writes the five canonical outputs to /app/output/.

The pipeline is driven entirely from the public inputs — it never touches any
private generator/master material. Python 3.11+ standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

SOLUTION_DIR = Path(__file__).resolve().parent
if str(SOLUTION_DIR) not in sys.path:
    sys.path.insert(0, str(SOLUTION_DIR))

from etl.pipeline import pipeline_main  # noqa: E402
from etl.rules import load as load_rules  # noqa: E402

DATA_DIR = "/app/data"
DICTIONARY_PATH = "/app/config/dictionary.toml"
OUTPUT_DIR = "/app/output"


def main() -> int:
    rules = load_rules(DICTIONARY_PATH)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    res = pipeline_main(DATA_DIR, OUTPUT_DIR, rules)

    expected = {
        "customers": len(res.customers),
        "orders": len(res.orders),
        "payments": len(res.payments),
        "customer_history": len(res.history),
    }
    for name, count in expected.items():
        print(f"wrote /app/output/{name}.csv ({count} rows)")

    report = Path(OUTPUT_DIR) / "data_quality_report.json"
    print(f"wrote {report} ({report.stat().st_size} bytes)")

    if any(v == 0 for v in expected.values()):
        print("ERROR: an output is empty", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
