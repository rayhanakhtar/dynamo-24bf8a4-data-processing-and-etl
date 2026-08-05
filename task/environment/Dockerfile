# Single container image — built for BOTH the agent run and the verifier (canonical
# TB2: no separate tests/Dockerfile). Baked in here: the task's runtime deps AND the
# verifier's deps (pytest), so tests/test.sh installs nothing at verify time.
#
# We do NOT COPY solution/ or tests/ into this image — the oracle solution and the
# expected fixtures are only overlaid/mounted by Harbor, so ground truth stays out of
# the agent's reach.
#
# Base: pre-approved, digest-pinned python image (>= 3.11). The task uses only the
# Python standard library (tomllib, csv, json, dataclasses), so no runtime apt/pip deps
# are needed beyond pytest for the verifier.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# Verifier tooling, baked at BUILD time (pinned) — the verifier installs nothing at
# verify time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
    && pip install --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

# Public benchmark inputs the agent is allowed to see:
#   /app/data/              the eight degraded source exports (STORE, PAY, CRM, ERP,
#                           IDP, SUP, MRK, CAT)
#   /app/config/dictionary.toml   the normative public rules (schemas, sorting, ids,
#                           normalization) that define the canonical outputs
COPY data /app/data
COPY config /app/config

# The agent writes its consolidated outputs here.
RUN mkdir -p /app/output

WORKDIR /app