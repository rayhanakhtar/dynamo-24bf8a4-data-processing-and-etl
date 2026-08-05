#!/bin/bash
#
# Runs inside the SHARED environment image (environment/Dockerfile) — canonical TB2
# has no separate verifier image. pytest and pytest-json-ctrf are baked into
# environment/Dockerfile (pinned), so nothing is installed here at verify time.
#
# Harbor overlays tests/ at /tests only at verify time. The expected fixtures live
# in /tests/expected/ (never baked into the environment image, so the agent cannot
# read them). The agent's outputs are read from /app/output/ as declared in
# task.toml [artifacts].
#
# --ctrf writes a standard JSON report; always write 1/0 to /logs/verifier/reward.txt,
# then exit with the pytest status so a failing run still records reward 0.
set -uo pipefail

pytest --ctrf /logs/verifier/ctrf.json -p no:cacheprovider /tests -q
rc=$?

if [ $rc -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit $rc