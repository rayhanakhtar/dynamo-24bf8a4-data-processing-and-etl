Transform the source exports under `/app/data/` into the canonical Northstar Commerce datasets. The input consists of eight source systems (`store`, `pay`, `crm`, `erp`, `idp`, `support`, `marketing`, and `retail`) containing CSV and NDJSON exports. These files intentionally contain malformed records, duplicated data, schema drift, inconsistent field names, invalid timestamps, obsolete or missing references, and other data-quality problems. Ignore records that cannot be parsed or resolved into valid canonical entities.

Use `/app/config/dictionary.toml` as the authoritative specification. It defines every required source schema, canonical output schema, identifier format, normalization rule, precedence rule, entity-resolution rule, history-generation rule, currency-conversion rule, payment-linkage rule, serialization requirement, published metric, and output invariant. Whenever a rule is specified there, follow it exactly.

Produce exactly five files: `/app/output/customers.csv`, `/app/output/orders.csv`, `/app/output/payments.csv`, `/app/output/customer_history.csv`, and `/app/output/data_quality_report.json`.

Each CSV must exactly match the schema declared in `[outputs]` of the dictionary, including column order, field types, primary-key definitions, required sort order, and serialization. Primary keys must be unique, non-empty, contiguous beginning at 1, and contain no gaps.

Normalize values according to the dictionary. Monetary amounts are stored as integer cents, including negative values for refunds. Emit timestamps as UTC ISO-8601 using the format `YYYY-MM-DDTHH:MM:SSZ`. Store email addresses in lowercase, phone numbers in E.164 format, and postal codes, regions, and countries in uppercase. Leave any unresolved field empty instead of inventing a value. Records that cannot be parsed into a valid canonical row must be discarded; for example, an ERP invoice whose `total_cents` is not a parseable integer money value is invalid and must not contribute to any invoice-to-order match.

For name normalization, `case = "title"` means apply the standard Python `str.title()` transformation to the trimmed, suffix-stripped name string. Apply this transformation deterministically to the complete normalized name value.

For IDP records, derive the canonical `is_active` boolean from the normalized IDP `status`: if the normalized status equals `active`, set `is_active` to `true`; otherwise set `is_active` to `false`. This mapping applies to every IDP record that contributes to a canonical customer.

Entity matching must deterministically merge records describing the same customer. Canonical identifiers and attribute precedence must follow the dictionary exactly. Records that cannot be parsed or that reference unresolved entities must not appear in the outputs.

Multiple source records may resolve to the same canonical customer even when their non-key attributes conflict. Retail customer records may share a normalized phone number while differing in email or name; such records must remain one resolved customer rather than being split into separate customers. Resolve conflicting canonical attributes using the source precedence and deterministic tie-breaking rules defined in the dictionary, and never choose a conflicting value arbitrarily. When multiple records have the same source priority, select the valid non-empty value from the record with the lowest source row identifier; numeric row identifiers are compared numerically, while non-numeric identifiers are compared lexicographically. For RETAIL/CAT records specifically, records with the same CAT source priority therefore resolve conflicting attributes by the lowest `cust_no`.

Orders and payments must reference their canonical entities. Completed orders must reconcile exactly with their associated payments after currency conversion. ERP refund sentinel rows (`order_key == 0`) are the only permitted exception and follow the linkage rules defined in the dictionary.

Currency conversion is defined by `[currency]`. Each rate represents the USD value of one unit of the source currency. Convert each payment independently by multiplying its amount in cents by the corresponding rate and applying half-up rounding. An order's USD amount is the sum of its converted payments rather than a converted order total.

Write `/app/output/data_quality_report.json` as a JSON object whose structure, serialization, key ordering, integer metrics, and contents satisfy the dictionary. Every published metric must agree with the generated CSV outputs.

For the `foreign_keys_repaired` metric, inspect SUP support tickets only. A ticket contributes 1 when its non-empty `order_ref`, after uppercasing for the `OR-` prefix check, starts with `OR-`; remove the first three characters from the original reference, lowercase the remainder, prepend `or_`, and count the ticket only if that resulting canonical STORE order identifier exists. Empty, non-`OR-`, and unresolved references contribute 0. Payment `order_ref` values are not included in this metric.

For identical inputs, the pipeline must always generate byte-identical outputs.
