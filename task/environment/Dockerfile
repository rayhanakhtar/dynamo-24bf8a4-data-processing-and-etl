# Single container image used for the task run. Only the agent-visible public
# runtime assets are baked into this image.
#
# The task uses only the Python standard library (tomllib, csv, json,
# dataclasses). pytest tooling is installed at build time for the evaluation
# harness; nothing is installed at task run time.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# Runtime tooling, pinned at BUILD time.
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