Transform the source exports under `/app/data/` into the canonical Northstar Commerce datasets. The input consists of eight source systems (`store`, `pay`, `crm`, `erp`, `idp`, `support`, `marketing`, and `retail`) containing CSV and NDJSON exports. These files intentionally contain malformed records, duplicated data, schema drift, inconsistent field names, invalid timestamps, obsolete or missing references, and other data-quality problems. Ignore records that cannot be parsed or resolved into valid canonical entities.

Use `/app/config/dictionary.toml` as the authoritative specification. It defines every required source schema, canonical output schema, identifier format, normalization rule, precedence rule, entity-resolution rule, history-generation rule, currency-conversion rule, payment-linkage rule, serialization requirement, published metric, and output invariant. Whenever a rule is specified there, follow it exactly.

Produce exactly five files: `/app/output/customers.csv`, `/app/output/orders.csv`, `/app/output/payments.csv`, `/app/output/customer_history.csv`, and `/app/output/data_quality_report.json`.

Each CSV must exactly match the schema declared in `[outputs]` of the dictionary, including column order, field types, primary-key definitions, required sort order, and serialization. Primary keys must be unique, non-empty, contiguous beginning at 1, and contain no gaps.

Normalize values according to the dictionary. Monetary amounts are stored as integer cents, including negative values for refunds. Emit timestamps as UTC ISO-8601 using the format `YYYY-MM-DDTHH:MM:SSZ`. Store email addresses in lowercase, phone numbers in E.164 format, and postal codes, regions, and countries in uppercase. Leave any unresolved field empty instead of inventing a value.

Entity matching must deterministically merge records describing the same customer. Canonical identifiers and attribute precedence must follow the dictionary exactly. Records that cannot be parsed or that reference unresolved entities must not appear in the outputs.

Orders and payments must reference their canonical entities. Completed orders must reconcile exactly with their associated payments after currency conversion. ERP refund sentinel rows (`order_key == 0`) are the only permitted exception and follow the linkage rules defined in the dictionary.

Currency conversion is defined by `[currency]`. Each rate represents the USD value of one unit of the source currency. Convert each payment independently by multiplying its amount in cents by the corresponding rate and applying half-up rounding. An order's USD amount is the sum of its converted payments rather than a converted order total.

Write `/app/output/data_quality_report.json` as a JSON object whose structure, serialization, key ordering, integer metrics, and contents satisfy the dictionary. Every published metric must agree with the generated CSV outputs.

For identical inputs, the pipeline must always generate byte-identical outputs.

Write `/app/output/data_quality_report.json` as a JSON object whose structure, serialization, key ordering, integer metrics, and contents satisfy the dictionary. Every published metric must agree with the generated CSV outputs.

For identical inputs, the pipeline must always generate byte-identical outputs.
