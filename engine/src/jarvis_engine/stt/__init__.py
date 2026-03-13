"""STT subpackage -- speech-to-text with 4-tier fallback chain.

Backward-compatible shim.  All attribute access (get, set, delete) is
proxied to the ``core`` submodule so that:

* ``from jarvis_engine.stt import X`` returns the same object as
  ``from jarvis_engine.stt.core import X``.
* ``mock.patch("jarvis_engine.stt.X")`` patches the attribute on the
  ``core`` module where the actual function code lives, so internal
  calls within ``core.py`` see the mock.
* ``import jarvis_engine.stt.backends`` still works because the
  package's ``__path__`` is preserved.
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys
import types as _types

# --- bootstrap: import core so its names are available -----------------------
from jarvis_engine.stt import core as _core  # noqa: E402

# Submodule names that should be resolved via normal package import
_SUBMODULES = frozenset({"core", "backends", "postprocess", "vad"})


class _SttPackageProxy(_types.ModuleType):
    """Drop-in replacement for the ``jarvis_engine.stt`` package module.

    Every get/set/del is forwarded to ``core`` (except package bookkeeping
    attributes), so ``mock.patch("jarvis_engine.stt.X")`` is equivalent to
    ``mock.patch("jarvis_engine.stt.core.X")``.
    """

    # Attributes that must live on the proxy itself (package machinery)
    _OWN_ATTRS = frozenset({
        "__name__", "__loader__", "__package__", "__spec__",
        "__path__", "__file__", "__builtins__", "__doc__",
        "_core", "__class__", "__dict__",
    })

    def __getattr__(self, name: str):
        # Submodule access (e.g. jarvis_engine.stt.backends)
        if name in _SUBMODULES:
            fqn = f"{self.__name__}.{name}"
            if fqn in _sys.modules:
                return _sys.modules[fqn]
            return _importlib.import_module(fqn)
        # Everything else comes from core
        return getattr(self._core, name)

    def __setattr__(self, name: str, value: object):
        if name in self._OWN_ATTRS:
            super().__setattr__(name, value)
        else:
            # mock.patch sets the attr here — forward to core
            setattr(self._core, name, value)

    def __delattr__(self, name: str):
        if name in self._OWN_ATTRS:
            super().__delattr__(name)
        else:
            # mock.patch cleanup
            delattr(self._core, name)

    def __dir__(self):
        return sorted(set(dir(self._core)) | _SUBMODULES)


# --- install the proxy in sys.modules ----------------------------------------
_this = _sys.modules[__name__]
_proxy = _SttPackageProxy(__name__)
# Copy essential package attributes
_proxy.__path__ = _this.__path__
_proxy.__file__ = _this.__file__
_proxy.__loader__ = _this.__loader__  # type: ignore[attr-defined]
_proxy.__spec__ = _this.__spec__
_proxy.__package__ = _this.__package__
_proxy.__doc__ = __doc__
_proxy._core = _core
_sys.modules[__name__] = _proxy
