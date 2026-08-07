Generate the canonical Northstar Commerce datasets from the raw exports stored under `/app/data/`.

The input consists of eight independent source systems: `store`, `pay`, `crm`, `erp`, `idp`, `support`, `marketing`, and `retail`. Their CSV and NDJSON exports contain intentionally degraded data, including malformed records, duplicate entries, inconsistent schemas, renamed fields, invalid or mixed timestamp formats, broken foreign-key references, obsolete identifiers, and other data-quality issues. Discard any record that cannot be parsed or cannot be resolved into a valid canonical entity.

Treat `/app/config/dictionary.toml` as the complete specification for the task. It defines the source layouts, canonical output schemas, identifier formats, normalization rules, precedence rules, entity-resolution logic, customer-history construction, payment-linkage behavior, currency conversion, serialization requirements, published metrics, and all required invariants. Wherever the dictionary defines behavior, implement it exactly.

Write exactly the following files beneath `/app/output/`:

* `/app/output/customers.csv`
* `/app/output/orders.csv`
* `/app/output/payments.csv`
* `/app/output/customer_history.csv`
* `/app/output/data_quality_report.json`

Each CSV must conform exactly to the corresponding definition in the dictionary's `[outputs]` section. Column sequence, data types, serialization, primary-key requirements, and row ordering must match the specification precisely. Canonical primary keys must be unique, non-empty, contiguous, and numbered consecutively starting from 1.

Apply all normalization rules before producing the outputs. Store monetary values as integer cents, including negative amounts for refunds. Emit timestamps in UTC using ISO-8601 with the `Z` suffix (`YYYY-MM-DDTHH:MM:SSZ`). Convert email addresses to lowercase, phone numbers to E.164 format, and postal codes, regions, and countries to uppercase. If a value cannot be derived from the available data, leave the field empty.

Perform deterministic customer resolution across all source systems. Records representing the same individual must be merged according to the dictionary's matching and precedence rules. Malformed records and records that reference unresolved entities must not be included in the canonical outputs.

Orders and payments must reference their corresponding canonical entities. Every completed order must reconcile exactly with its converted payments. The only allowed exception is the ERP refund sentinel defined by the specification, where `order_key == 0`; these rows follow the documented linkage rules and are excluded from the normal referential-integrity requirement.

Use the exchange rates defined in the dictionary's `[currency]` section. Each rate represents the USD value of one unit of the source currency. Convert every payment independently by multiplying its amount in cents by the applicable rate and applying half-up rounding. Compute each order's USD value as the sum of its converted payments rather than by converting the order total directly.

Write `/app/output/data_quality_report.json` as a JSON object whose structure, serialization, key ordering, integer metrics, and contents satisfy the dictionary. Every published metric must agree with the generated CSV outputs.

The transformation must be deterministic. Given identical inputs, the generated output files must be byte-identical.
