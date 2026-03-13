"""Backward-compatibility shim — real implementation moved to jarvis_engine.web.fetch.

This shim proxies attribute access to the real module so that
``patch("jarvis_engine.web_fetch.X")`` affects the canonical module.
"""
import importlib as _importlib
import sys as _sys

_real = _importlib.import_module("jarvis_engine.web.fetch")

# Re-export everything into this module's namespace so direct attribute
# access and ``from jarvis_engine.web_fetch import X`` both work.
_all_names = [n for n in dir(_real) if not n.startswith("__")]
globals().update({n: getattr(_real, n) for n in _all_names})

# Proxy any remaining attribute lookups (and, crucially, attribute *writes*
# from unittest.mock.patch) through to the real module.
def __getattr__(name: str):  # noqa: N807
    return getattr(_real, name)


# Make ``patch("jarvis_engine.web_fetch.X", ...)`` write into the real
# module so the patched value is seen by code that imported from there.
_this = _sys.modules[__name__]
_original_setattr = type(_this).__setattr__


class _ProxyModule(type(_this)):  # type: ignore[misc]
    """Module subclass that forwards setattr to the real module."""

    def __setattr__(self, name: str, value: object) -> None:
        if not name.startswith("_"):
            setattr(_real, name, value)
        _original_setattr(self, name, value)

    def __delattr__(self, name: str) -> None:
        if not name.startswith("_") and hasattr(_real, name):
            delattr(_real, name)
        super().__delattr__(name)


_this.__class__ = _ProxyModule  # type: ignore[assignment]
