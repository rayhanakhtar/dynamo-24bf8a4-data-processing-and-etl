"""Typed canonical records produced by the oracle pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Customer:
    customer_key: int
    gid: str
    first_name: Optional[str]
    last_name: Optional[str]
    dob: Optional[str]
    gender: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    line1: Optional[str]
    city: Optional[str]
    region: Optional[str]
    postal: Optional[str]
    country: Optional[str]
    is_business: bool
    company: Optional[str]
    household: Optional[str]
    loyalty_tier: Optional[str]
    is_active: bool
    created_ts: Optional[str]
    updated_ts: Optional[str]
    # raw exposure maps for history reconstruction
    exposures: Dict[str, List] = field(default_factory=dict)
    has_idp: bool = False


@dataclass
class Order:
    order_key: int
    customer_key: int
    gid: str
    placed_ts: Optional[str]
    status: Optional[str]
    channel: Optional[str]
    subtotal_cents: Optional[int]
    tax_cents: Optional[int]
    shipping_cents: Optional[int]
    total_cents: Optional[int]
    currency: Optional[str]
    ship_to_name: Optional[str]
    ship_to_line1: Optional[str]
    ship_to_city: Optional[str]
    ship_to_region: Optional[str]
    ship_to_postal: Optional[str]
    ship_to_country: Optional[str]
    amount_usd: int
    # provenance
    order_id: str = ""
    # ERP handshake
    bill_to: Optional[str] = None
    erp_issue: Optional[str] = None


@dataclass
class Payment:
    payment_key: int
    order_key: int
    customer_key: int
    gid: str
    paid_ts: Optional[str]
    amount_cents: Optional[int]
    currency: Optional[str]
    amount_usd: int
    method: Optional[str]
    status: Optional[str]


@dataclass
class History:
    customer_key: int
    history_key: int
    attribute: str
    old_value: Optional[str]
    new_value: Optional[str]
    effective_from: str
    effective_to: str
    source: str