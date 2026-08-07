"""Field normalization per `config/dictionary.toml` [normalization] and the
canonical `format` enums declared on [outputs.*.columns].  Pure functions only —
no pipeline state, no rule logic beyond what the rules file declares.

Python 3.11 stdlib only.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional, Tuple

from etl.rules import Dictionary

_EMAIL_KEEP = re.compile(r"[^a-z0-9.@+_-]")
_PHONE_KEEP = re.compile(r"\d")
_NAME_WS = re.compile(r"\s+")


class Normalizer:
    """Binds a rules `Dictionary` and exposes normalized field transforms."""

    def __init__(self, rules: Dictionary) -> None:
        self._rules = rules
        self._parse_order = rules.parse_order()
        self._fmt = {
            name: (rules.datetime_format(name) if name != "recover_broken_iso" else None)
            for name in self._parse_order
        }

    # ------------------------------------------------------------ datetime ---
    def parse_ts(self, raw: Optional[str], profile: Optional[str] = None) -> Tuple[Optional[str], str]:
        """Parse a raw timestamp cell into a canonical UTC ISO string.

        Returns (utc_iso_or_None, provenance) where provenance is one of:
          "canonical"  — parsed by the format native to `profile` (or iso8601_z)
          "corrected"  — parsed only via a secondary/recovery format
          "null"       — matched nothing → NULL (never guessed)
        """
        if raw is None:
            return None, "null"
        text = (str(raw)).strip()
        if not text:
            return None, "null"
        for i, name in enumerate(self._parse_order):
            parsed = self._try(text, name)
            if parsed is not None:
                iso = self._iso(parsed)
                if name == "recover_broken_iso":
                    return iso, "corrected"
                if name == profile or name == self._parse_order[0]:
                    return iso, "canonical"
                return iso, "corrected"
        return None, "null"

    def _try(self, text: str, name: str) -> Optional[dt.datetime]:
        if name == "recover_broken_iso":
            return self._recover(text)
        fmt = self._fmt.get(name)
        if fmt is None:
            return None
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            return None
        self._ensure_utc(parsed, text, name)
        return parsed

    def _recover(self, text: str) -> Optional[dt.datetime]:
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(.*)$", text)
        if not m:
            return None
        try:
            parsed = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
        self._ensure_utc(parsed, text, "recover_broken_iso")
        return parsed

    def _ensure_utc(self, parsed: dt.datetime, text: str, name: str) -> None:
        if name == "recover_broken_iso" and "Z" in text:
            return  # trailing jitter only; still assume UTC Z
        if parsed.tzinfo is not None:
            return

    @staticmethod
    def _iso(d: dt.datetime) -> str:
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        else:
            d = d.astimezone(dt.timezone.utc)
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")

    # -------------------------------------------------------------- strings ---
    def email(self, raw: Optional[str]) -> Optional[str]:
        """lowercase, trim both, drop control; returns None when unusable."""
        if raw is None:
            return None
        v = str(raw).strip()
        if not v:
            return None
        v = v.lower()
        v = _EMAIL_KEEP.sub("", v)
        return v or None

    def phone(self, raw: Optional[str]) -> Optional[str]:
        """E.164: keep digits, prepend default country code, force leading +."""
        if raw is None:
            return None
        digits = _PHONE_KEEP.findall(str(raw))
        if not digits:
            return None
        num = "".join(digits)
        default_cc = self._rules.raw()["normalization"]["phone"]["default_country_code"]
        return "+" + default_cc.lstrip("+") + num

    def name(self, raw: Optional[str]) -> Optional[str]:
        """Title case, trim, strip trailing . and , after normalizing spaces."""
        if raw is None:
            return None
        v = str(raw).strip().strip(".,")
        if not v:
            return None
        v = _NAME_WS.sub(" ", v)
        return v.title()

    def name_block(self, first: Optional[str], last: Optional[str]) -> Optional[str]:
        fn = self.name(first)
        ln = self.name(last)
        if fn and ln:
            return f"{fn} {ln}"
        return fn or ln or None

    def postal(self, raw: Optional[str]) -> Optional[str]:
        """uppercase, strip spaces."""
        if raw is None:
            return None
        v = str(raw).strip()
        if not v:
            return None
        return v.upper().replace(" ", "")

    def currency(self, raw: Optional[str]) -> Optional[str]:
        if raw is None:
            return None
        v = str(raw).strip().upper()
        return v or None

    def money(self, raw: Optional[str], signed: bool = False) -> Optional[int]:
        if raw is None:
            return None
        v = str(raw).strip()
        if not v:
            return None
        try:
            i = int(v)
        except ValueError:
            return None
        if not signed and i < 0:
            return None
        return i

    def bool(self, raw: Optional[str]) -> Optional[bool]:
        if raw is None:
            return None
        v = str(raw).strip().lower()
        if v in {"true", "1", "yes"}:
            return True
        if v in {"false", "0", "no"}:
            return False
        return None

    def int(self, raw: Optional[str]) -> Optional[int]:
        if raw is None:
            return None
        v = str(raw).strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None