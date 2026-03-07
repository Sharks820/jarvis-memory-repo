"""Shared helpers for CLI command modules.

Centralises the ``_dispatch()`` boilerplate that was previously
duplicated across ``main.py``, ``cli_ops.py``, and ``cli_knowledge.py``.
"""

from __future__ import annotations

__all__ = ["cli_dispatch"]

import json
from typing import Any

from jarvis_engine._bus import get_bus as _get_bus


def cli_dispatch(
    command: Any,
    *,
    as_json: bool = False,
    json_field: str = "",
) -> tuple[Any, int]:
    """Dispatch *command* via the bus with common boilerplate.

    Returns ``(result, return_code)``.

    * If *as_json* is ``True`` and *json_field* names a dict/list attribute on
      the result, that value is pretty-printed as JSON and ``return_code`` is
      ``0``.
    * Otherwise ``return_code`` is ``0`` and the caller is responsible for
      printing any remaining key=value output.
    """
    result = _get_bus().dispatch(command)

    # JSON output path -- used by the --json flag on many sub-commands.
    if as_json and json_field:
        data = getattr(result, json_field, None)
        if isinstance(data, (dict, list)):
            print(json.dumps(data, ensure_ascii=True, indent=2, default=str))
            return result, 0

    return result, 0
