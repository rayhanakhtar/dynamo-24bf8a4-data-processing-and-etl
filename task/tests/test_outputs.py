"""Backward-compatibility entrypoint for the Project Dynamo CI.

The Project Dynamo CI expects a verifier module named `test_outputs.py` to be
present before running the verifier. The verifier suite itself is implemented
as one module per artifact (`test_customers.py`, `test_orders.py`,
`test_payments.py`, `test_history.py`, and `test_quality_report.py`), and the
CI runs pytest against the whole `/tests` directory, so those modules are
collected and executed directly.

This file exists solely to satisfy the CI's legacy entrypoint check. It imports
the per-artifact verifier modules so the same suite is loaded, and intentionally
does NOT re-export their test names: re-exporting would make pytest collect
every test twice, once under this module and once under the artifact module.
"""

import test_customers
import test_history
import test_orders
import test_payments
import test_quality_report
