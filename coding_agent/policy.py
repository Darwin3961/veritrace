from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from coding_agent.types import ToolCall


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None
    risk: str = "low"


class SafetyPolicy:
    """
    Deterministic best-effort safety policy for local tools.

    This policy reduces obvious risks but is NOT an OS-level sandbox.
    """

    SENSITIVE_BASENAMES = {
        ".env",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "credentials.yml",
        "credentials.yaml",
        "id_rsa",
        "id_ed25519",
    }

    SENSITIVE_SUFFIXES = {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
    }

    SECRET_NAME_PATTERN = re.compile(
        r"(api[_-]?key|token|secret|password|authorization)",
        re.IGNORECASE,
    )

    PARENT_TRAVERSAL_PATTERN = re.compile(
        r"(^|[\\/\s'\"=])\.\.([\\/]|$)"
    )

    DANGEROUS_COMMAND_PATTERNS = [
        (
            "privilege escalation",
            re.compile(
                r"(^|[;&|]\s*|\s)(sudo|runas)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "destructive rm",
            re.compile(
                r"\brm\b[^\r\n]*\s-(?:[A-Za-z]*r[A-Za-z]*f"
                r"|[A-Za-z]*f[A-Za-z]*r)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "destructive Windows delete",
            re.compile(
                r"\b(del|erase|rmdir|rd)\b[^\r\n]*(/s|/q)",
                re.IGNORECASE,
            ),
        ),
        (
            "destructive PowerShell delete",
            re.compile(
                r"\b(Remove-Item|ri)\b"
                r"[^\r\n]*(-Recurse|-Force)",
                re.IGNORECASE,
            ),
        ),
        (
            "destructive git reset",
            re.compile(
                r"\bgit\s+reset\b[^\r\n]*--hard\b",
                re.IGNORECASE,
            ),
        ),
        (
            "destructive git clean",
            re.compile(
                r"\bgit\s+clean\b[^\r\n]*-[A-Za-z]*f",
                re.IGNORECASE,
            ),
        ),
        (
            "destructive git checkout",
            re.compile(
                r"\bgit\s+checkout\s+--\s+\.",
                re.IGNORECASE,
            ),
        ),
        (
            "destructive git restore",
            re.compile(
                r"\bgit\s+restore\b"
                r"[^\r\n]*(--worktree|--staged|\s\.)",
                re.IGNORECASE,
            ),
        ),
        (
            "pipe network content to shell",
            re.compile(
                r"\b(curl|wget|iwr|Invoke-WebRequest)\b"
                r"[^\r\n]*\|[^\r\n]*"
                r"(sh|bash|zsh|powershell|pwsh|cmd)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "encoded PowerShell execution",
            re.compile(
                r"\b(powershell|pwsh)\b"
                r"[^\r\n]*-(EncodedCommand|enc)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "system shutdown",
            re.compile(
                r"\b(shutdown|reboot|poweroff)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "disk administration",
            re.compile(
                r"\b(diskpart|format)\b",
                re.IGNORECASE,
            ),
        ),
    ]

    ENV_DUMP_PATTERNS = [
        re.compile(r"^\s*(env|printenv)\s*$", re.IGNORECASE),
        re.compile(r"^\s*set\s*$", re.IGNORECASE),
        re.compile(
            r"\bGet-ChildItem\s+Env:",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bGet-Item\s+Env:",
            re.IGNORECASE,
        ),
    ]

    SENSITIVE_FILE_COMMAND_PATTERN = re.compile(
        r"\b(cat|type|more|Get-Content|gc)\b"
        r"[^\r\n]*"
        r"(\.env(?:\.[^\s]+)?|"
        r"id_rsa|id_ed25519|"
        r"credentials\.(?:json|ya?ml)|"
        r"[^\s]+\.(?:pem|key|p12|pfx))",
        re.IGNORECASE,
    )

    def __init__(
        self,
        workspace_root: str | Path,
    ):
        root = Path(
            workspace_root
        ).expanduser().resolve()

        if not root.exists():
            raise ValueError(
                f"Workspace does not exist: {root}"
            )

        if not root.is_dir():
            raise ValueError(
                f"Workspace is not a directory: {root}"
            )

        self.workspace_root = root

    def _normalize_tool_path(
        self,
        value: object,
    ) -> str | None:
        if not isinstance(value, str):
            return None

        return value.replace("\\", "/")

    def _is_sensitive_path(
        self,
        value: object,
    ) -> bool:
        normalized = self._normalize_tool_path(value)

        if not normalized:
            return False

        path = Path(normalized)
        basename = path.name.lower()

        if basename == ".env.example":
            return False

        if basename in self.SENSITIVE_BASENAMES:
            return True

        if basename.startswith(".env."):
            return True

        if path.suffix.lower() in self.SENSITIVE_SUFFIXES:
            return True

        return False

    def _check_file_tool(
        self,
        call: ToolCall,
    ) -> PolicyDecision:
        path = call.arguments.get("path")

        if self._is_sensitive_path(path):
            return PolicyDecision(
                allowed=False,
                reason=(
                    "access to sensitive credential files "
                    "is not allowed"
                ),
                risk="high",
            )

        return PolicyDecision(allowed=True)

    def _check_command(
        self,
        command: object,
    ) -> PolicyDecision:
        if not isinstance(command, str):
            return PolicyDecision(
                allowed=False,
                reason="command must be a string",
                risk="medium",
            )

        stripped = command.strip()

        if not stripped:
            return PolicyDecision(
                allowed=False,
                reason="command must not be empty",
                risk="medium",
            )

        if self.PARENT_TRAVERSAL_PATTERN.search(stripped):
            return PolicyDecision(
                allowed=False,
                reason=(
                    "parent-directory traversal in commands "
                    "is not allowed"
                ),
                risk="high",
            )

        for pattern in self.ENV_DUMP_PATTERNS:
            if pattern.search(stripped):
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        "dumping process environment variables "
                        "is not allowed"
                    ),
                    risk="high",
                )

        if self.SECRET_NAME_PATTERN.search(stripped):
            return PolicyDecision(
                allowed=False,
                reason=(
                    "command appears to reference secret "
                    "or credential values"
                ),
                risk="high",
            )

        if self.SENSITIVE_FILE_COMMAND_PATTERN.search(stripped):
            return PolicyDecision(
                allowed=False,
                reason=(
                    "command attempts to read a sensitive "
                    "credential file"
                ),
                risk="high",
            )

        for label, pattern in self.DANGEROUS_COMMAND_PATTERNS:
            if pattern.search(stripped):
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f"blocked potentially dangerous command: {label}"
                    ),
                    risk="high",
                )

        return PolicyDecision(allowed=True)

    def check(
        self,
        call: ToolCall,
    ) -> PolicyDecision:
        if call.name in {
            "read_file",
            "write_file",
            "edit_file",
        }:
            return self._check_file_tool(call)

        if call.name == "run_command":
            return self._check_command(
                call.arguments.get("command")
            )

        return PolicyDecision(allowed=True)
