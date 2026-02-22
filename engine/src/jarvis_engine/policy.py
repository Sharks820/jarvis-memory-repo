from __future__ import annotations


class PolicyEngine:
    """Very small allowlist gate for high-level commands."""

    def __init__(self) -> None:
        self._allowed_prefixes = {
            "git status",
            "git log",
            "ollama list",
            "ollama ps",
        }

    def is_allowed(self, command: str) -> bool:
        normalized = " ".join(command.strip().split()).lower()
        return any(normalized.startswith(prefix) for prefix in self._allowed_prefixes)

