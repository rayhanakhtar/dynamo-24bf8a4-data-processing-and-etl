"""
Pytest compatibility shim.

The grading infrastructure expects a module named ``test_outputs.py`` to exist.
The real verifier is organized into separate artifact-specific modules, so this
file simply imports them during collection. It intentionally defines no test
functions of its own, preventing duplicate collection while preserving the
legacy entrypoint expected by the runner.
"""

from importlib import import_module

_VERIFIER_MODULES = [
    "test_customers",
    "test_orders",
    "test_payments",
    "test_history",
    "test_quality_report",
]


def _load_verifier_modules():
    """Import every verifier module exactly once."""
    loaded = []
    for module_name in _VERIFIER_MODULES:
        loaded.append(import_module(module_name))
    return tuple(loaded)


_LOADED_MODULES = _load_verifier_modules()