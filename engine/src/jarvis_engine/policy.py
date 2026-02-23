from __future__ import annotations


ALLOWED_COMMANDS = {
    "git",
    "ollama",
    "python",
    "pip",
    "node",
    "npm",
    "pytest",
    "jarvis",
}


class PolicyEngine:
    """Very small allowlist gate for high-level commands."""

    def __init__(self) -> None:
        self._allowed_commands = ALLOWED_COMMANDS

    def is_allowed(self, command: str) -> bool:
        tokens = command.strip().split()
        if not tokens:
            return False
        return tokens[0].lower() in self._allowed_commands

