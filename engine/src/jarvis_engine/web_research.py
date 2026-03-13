"""Backward-compatibility shim — real implementation moved to jarvis_engine.web.research.

This shim proxies attribute access to the real module so that
``monkeypatch.setattr(web_research, "X", ...)`` affects the canonical module.
"""
import importlib as _importlib
import sys as _sys

_real = _importlib.import_module("jarvis_engine.web.research")

# Re-export everything into this module's namespace
_all_names = [n for n in dir(_real) if not n.startswith("__")]
globals().update({n: getattr(_real, n) for n in _all_names})


def __getattr__(name: str):  # noqa: N807
    return getattr(_real, name)


_this = _sys.modules[__name__]
_original_setattr = type(_this).__setattr__


class _ProxyModule(type(_this)):  # type: ignore[misc]
    """Module subclass that forwards setattr to the real module."""

    def __setattr__(self, name: str, value: object) -> None:
        if not name.startswith("_") or name in ("_search_web", "_fetch_page_text", "_extract_snippet", "_query_keywords"):
            setattr(_real, name, value)
        _original_setattr(self, name, value)

    def __delattr__(self, name: str) -> None:
        if hasattr(_real, name):
            delattr(_real, name)
        super().__delattr__(name)


_this.__class__ = _ProxyModule  # type: ignore[assignment]
