"""Output scanner — Wave 10 security hardening.

Scans LLM responses for credential leakage, path disclosure, data
exfiltration markers, instruction injection, and persona violations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class OutputScanResult:
    """Result from scanning an LLM output."""

    safe: bool
    issues: list[str] = field(default_factory=list)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# --- 1. Credential / secret leakage ---

_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Generic API key patterns (key=..., api_key=..., apikey=...)
    ("generic_api_key", re.compile(
        r"""(?:api[_-]?key|apikey|api[_-]?secret|api[_-]?token)\s*[=:]\s*['"]?[A-Za-z0-9_\-]{16,}""",
        re.I,
    )),
    # AWS access key (AKIA...)
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    # AWS secret key
    ("aws_secret_key", re.compile(
        r"""(?:aws[_-]?secret[_-]?access[_-]?key|aws[_-]?secret)\s*[=:]\s*['"]?[A-Za-z0-9/+=]{40}""",
        re.I,
    )),
    # Generic password in assignment
    ("password_assignment", re.compile(
        r"""(?:password|passwd|pwd)\s*[=:]\s*['"]?[^\s'"]{8,}""",
        re.I,
    )),
    # Bearer token
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}")),
    # Private key block
    ("private_key", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----")),
    # GitHub personal access token (ghp_, gho_, ghu_, ghs_, ghr_)
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    # Slack token
    ("slack_token", re.compile(r"xox[bpras]-[0-9A-Za-z\-]{10,}")),
    # Generic secret/token assignment
    ("generic_secret", re.compile(
        r"""(?:secret|token|auth[_-]?token|access[_-]?token)\s*[=:]\s*['"]?[A-Za-z0-9_\-]{20,}""",
        re.I,
    )),
    # Connection string with password
    ("connection_string_password", re.compile(
        r"""(?:mongodb|postgres|mysql|redis|amqp)://[^:]+:[^@]+@""",
        re.I,
    )),
]

# --- 2. Path disclosure ---

_PATH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Unix absolute paths with sensitive directories
    ("unix_sensitive_path", re.compile(r"/(?:etc/(?:passwd|shadow|sudoers)|home/\w+/\.\w+|root/)")),
    # Windows absolute paths (excludes owner's home directory — personal assistant context)
    ("windows_absolute_path", re.compile(
        r"[A-Z]:\\(?:Windows|Program Files)\\", re.I,
    )),
    # Generic absolute paths that look like system disclosure
    ("unix_absolute_path", re.compile(r"/(?:usr|var|opt|srv|tmp)/\w+/\w+")),
    # .env file path
    ("env_file_path", re.compile(r"[/\\]\.env\b")),
    # SSH key paths
    ("ssh_key_path", re.compile(r"[/\\]\.ssh[/\\](?:id_rsa|id_ed25519|authorized_keys)")),
]

# --- 3. Data exfiltration markers ---

_EXFILTRATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "send to" + email
    ("send_to_email", re.compile(
        r"(?:send|forward|email|mail)\s+(?:this\s+)?(?:to|it\s+to)\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}",
        re.I,
    )),
    # "send to" + URL
    ("send_to_url", re.compile(
        r"(?:send|forward|post|upload)\s+(?:this\s+)?(?:to|it\s+to)\s+https?://",
        re.I,
    )),
    # Curl / wget exfiltration
    ("curl_exfiltration", re.compile(
        r"(?:curl|wget)\s+.*(?:-d|--data|--post-data)",
        re.I,
    )),
]

# --- 4. Instruction injection in output ---

_INSTRUCTION_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Dangerous shell commands
    ("dangerous_shell_sudo", re.compile(
        r"(?:^|\n)\s*sudo\s+(?:rm|chmod|chown|kill|shutdown|reboot|dd|mkfs)",
        re.I,
    )),
    ("dangerous_shell_rm", re.compile(
        r"(?:^|\n)\s*rm\s+-[rR]?f",
    )),
    ("dangerous_shell_chmod", re.compile(
        r"(?:^|\n)\s*chmod\s+(?:777|666|[+]?[rwx]+)\s+/",
    )),
    # PowerShell dangerous commands
    ("dangerous_powershell", re.compile(
        r"(?:Remove-Item|Stop-Process|Restart-Computer|Format-Volume)\s+-",
        re.I,
    )),
    # "Run this command" instruction patterns
    ("run_command_instruction", re.compile(
        r"(?:run|execute|type|enter)\s+(?:this|the\s+following)\s+(?:command|in\s+(?:your\s+)?terminal)",
        re.I,
    )),
    # Pipe to shell
    ("pipe_to_shell", re.compile(
        r"(?:curl|wget)\s+.*\|\s*(?:bash|sh|zsh|python)",
        re.I,
    )),
]

# --- 5. Persona violation ---

_PERSONA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "I am [not Jarvis]" — claims to be a different identity
    ("identity_claim", re.compile(
        r"\bI\s+am\s+(?:not\s+Jarvis|(?:actually|really)\s+(?:a|an|the)\s+(?:different|another|separate)\s+)",
        re.I,
    )),
    # "I am [name]" where name is a known AI persona
    ("competing_persona", re.compile(
        r"\bI\s+am\s+(?:ChatGPT|GPT-?4|Bard|Gemini|Copilot|Siri|Alexa|Cortana)\b",
        re.I,
    )),
    # "My name is [not Jarvis]"
    ("name_override", re.compile(
        r"\bmy\s+name\s+is\s+(?!Jarvis\b)\w+",
        re.I,
    )),
    # "I am no longer [Jarvis]"
    ("identity_rejection", re.compile(
        r"\bI\s+am\s+no\s+longer\s+",
        re.I,
    )),
]


# ---------------------------------------------------------------------------
# OutputScanner
# ---------------------------------------------------------------------------

class OutputScanner:
    """Scans LLM responses for security issues.

    Usage::

        scanner = OutputScanner()
        result = scanner.scan_output("Here is the API key: AKIAIOSFODNN7EXAMPLE")
        assert not result.safe
    """

    def scan_output(
        self,
        response: str,
        system_context: dict[str, Any] | None = None,
    ) -> OutputScanResult:
        """Scan an LLM response for security issues.

        Args:
            response: The LLM output text to scan.
            system_context: Optional context dict (currently unused, reserved
                for future per-response policy decisions).

        Returns:
            OutputScanResult with safe=True only if no issues found.
        """
        if not response or not response.strip():
            return OutputScanResult(safe=True, confidence=1.0)

        issues: list[str] = []

        # 1. Credential / secret leakage
        for name, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(response):
                issues.append(f"credential_leak:{name}")

        # 2. Path disclosure
        for name, pattern in _PATH_PATTERNS:
            if pattern.search(response):
                issues.append(f"path_disclosure:{name}")

        # 3. Data exfiltration markers
        for name, pattern in _EXFILTRATION_PATTERNS:
            if pattern.search(response):
                issues.append(f"exfiltration:{name}")

        # 4. Instruction injection in output
        for name, pattern in _INSTRUCTION_INJECTION_PATTERNS:
            if pattern.search(response):
                issues.append(f"instruction_injection:{name}")

        # 5. Persona violation
        for name, pattern in _PERSONA_PATTERNS:
            if pattern.search(response):
                issues.append(f"persona_violation:{name}")

        safe = len(issues) == 0
        # Confidence decreases as more issues are found
        confidence = 1.0 if safe else max(0.1, 1.0 - 0.15 * len(issues))

        return OutputScanResult(safe=safe, issues=issues, confidence=confidence)
