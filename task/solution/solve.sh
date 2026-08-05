#!/bin/bash
#
# Oracle (reference) solution — runs the deterministic ETL pipeline against the
# public benchmark inputs and writes the five canonical outputs to /app/output/.
# Harbor mounts solution/ at /solution/ and executes this script.
set -euo pipefail

python3 /solution/solve.py