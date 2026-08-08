"""Focused oracle tests for date-only (YYYY-MM-DD) output serialization.

Exercises the oracle pipeline's cell formatter for ``format = "date_only"``
fields (e.g. customers.dob). Lives outside ``tests/`` like the other focused
tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.pipeline import _format, _date_only  # noqa: E402


def _meta(fmt):
    return {"format": fmt}


def test_date_only_iso_timestamp_trimmed_to_date():
    assert _format("1990-11-15T00:00:00Z", _meta("date_only")) == "1990-11-15"


def test_date_only_bare_date_passthrough():
    assert _format("1990-11-15", _meta("date_only")) == "1990-11-15"


def test_date_only_none_renders_empty():
    assert _format(None, _meta("date_only")) == ""


def test_date_only_short_string_passthrough():
    assert _format("1990-11", _meta("date_only")) == "1990-11"


def test_non_date_only_kept_verbatim():
    assert _format("1990-11-15T00:00:00Z", _meta("iso")) == "1990-11-15T00:00:00Z"


def test_date_only_helper_takes_first_ten_chars_of_iso():
    assert _date_only("1999-12-31T23:59:59Z") == "1999-12-31"