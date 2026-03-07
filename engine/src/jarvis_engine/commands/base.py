"""Base dataclass for command results with shared return_code and message fields."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResultBase:
    """Common fields shared by all command result dataclasses.

    Subclasses inherit ``return_code`` and ``message`` without repeating
    the field declarations.  Because both fields have defaults, child
    dataclasses may freely add their own default-valued fields.
    """

    return_code: int = 0
    message: str = ""
