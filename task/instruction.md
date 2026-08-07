# Northstar Commerce — Consolidated Customer/Order/Payment Pipeline

## Objective
Build a deterministic ETL pipeline that merges eight degraded source exports into five canonical output artifacts for the Northstar Commerce platform.

## Inputs
Source datasets reside under `/app/data/` in eight directories:
- `store` — retail transactions
- `pay` — payment records
- `crm` — customer relationship data
- `erp` — enterprise resource planning invoices & shipments
- `idp` — identity provider records
- `support` — support tickets
- `marketing` — marketing campaign data
- `retail` — retail customer data

Each contains CSV or NDJSON exports with deliberate corruptions:
- Malformed rows (JSON/CSV syntax errors, column-count mismatches)
- Unparseable or inconsistent timestamps across multiple formats
- Obsolete or missing foreign-key references
- Duplicate rows
- Renamed or inconsistent column values

The normative rule set is `/app/config/dictionary.toml`. It is the **single source of truth** for:
- Source schemas (fields, types, roles, identifier formats)
- Output schemas (column names, order, types, formats, primary keys, sort order)
- Currency conversion and rounding rules
- Datetime parse order and output format
- Normalization rules
- Entity-matching and precedence rules
- Global identifier precedence (source priority)
- Payment-to-order linkage logic
- Customer-history reconstruction rules
- Published metrics and their invariants

## Required Outputs
Produce exactly five files in `/app/output/`:
1. `/app/output/customers.csv`
2. `/app/output/orders.csv`
3. `/app/output/payments.csv`
4. `/app/output/customer_history.csv`
5. `/app/output/data_quality_report.json`

## Output Specifications

### Schema & Ordering
Column names, order, types, and formats for each CSV are declared in `[outputs]` of `/app/config/dictionary.toml`. Files must match exactly.
Primary keys: unique, non-empty, contiguous from 1.
Row ordering: ascending by declared sort columns.

### Data Formats
- **Money**: integer cents (minor units); negative for refunds
- **Timestamps**: UTC ISO-8601 with `Z` suffix (`YYYY-MM-DDTHH:MM:SSZ`)
- **Email**: lowercase
- **Phone**: E.164 (`+...`)
- **Postal code, region, country**: uppercase
- Unresolvable fields: empty

### Currency Conversion
Rates in `[currency]` give USD per unit (e.g., `CAD=0.75` → 1 CAD = 0.75 USD).
Formula: `amount_usd = round_half_up(amount_cents × rate)`
- Always **multiply** cents by rate (never divide)
- Round half-up (0.5 rounds away from zero)
- Apply per-payment **before** aggregation
- Order `amount_usd` = sum of its payments' `amount_usd` (not a scaled order total)
- Verifier recomputes every USD value with this exact formula and enforces byte-for-byte equality

### Entity Resolution
- Deterministic merge of identical real-world customers across sources
- Canonical ID assigned per global source-priority rule (dictionary)
- Unparseable records and records referencing missing entities excluded from output

### Linkage & Integrity
- Orders and payments must link to their canonical customer/order
- Completed orders must reconcile exactly to payments in USD
- Every payment references an existing canonical order
- **Exception**: ERP sentinel payments (`order_key == 0`) from ERP invoice-match refunds — excluded from referential-integrity and reconciliation checks (see `payment_order_exists` invariant in dictionary)

## Quality Report
`/app/output/data_quality_report.json` — JSON object with:
- `number_type = "int"`
- `key_order = "sorted"`
- `include_details = true`
- `metrics` object containing **every** metric defined in `[metrics]` of the dictionary
- Each metric: integer, consistent with actual CSV row counts (e.g., `final_customer_count` = rows in `customers.csv`)

## Determinism
Same inputs → byte-identical outputs. No invented values; unresolvable fields remain empty.