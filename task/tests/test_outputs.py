"""
Compatibility module.

Historically the verification workflow referenced ``test_outputs.py`` as its
entry module. The actual verifier has since been split across multiple files,
so this module only loads those files and intentionally contributes no tests
of its own.
"""

import importlib

def _bootstrap():
    modules = (
        "test_customers",
        "test_orders",
        "test_payments",
        "test_history",
        "test_quality_report",
    )
    return tuple(importlib.import_module(m) for m in modules)

_BOOTSTRAPPED = _bootstrap()