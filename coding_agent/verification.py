from __future__ import annotations

import re
from dataclasses import dataclass, field

from coding_agent.events import Event


@dataclass(slots=True)
class VerificationSummary:
    successful_commands: int = 0
    failed_commands: int = 0
    timed_out_commands: int = 0
    successful_file_changes: int = 0
    tool_failures: int = 0
    policy_blocks: int = 0
    last_command_exit_code: int | None = None
    tests_likely_ran: bool = False
    successful_test_commands: int = 0
    failed_test_commands: int = 0
    verification_commands: list[str] = field(default_factory=list)


TEST_COMMAND_PATTERNS = [
    re.compile(
        r"(?:^|[;&|]\s*)"
        r"(?:[^\s;&|]*pytest(?:\.exe)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[;&|]\s*)"
        r"(?:\"[^\"]*python(?:\.exe)?\"|"
        r"'[^']*python(?:\.exe)?'|"
        r"[^\s;&|]*python(?:\.exe)?)"
        r"\s+-m\s+(?:pytest|unittest)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[;&|]\s*)unittest\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[;&|]\s*)npm\s+(?:run\s+)?test\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[;&|]\s*)(?:pnpm|yarn|cargo|go|mvn)\s+test\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[;&|]\s*)"
        r"(?:gradle|(?:\./|\.\\)?gradlew)\s+test\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[;&|]\s*)dotnet\s+test\b",
        re.IGNORECASE,
    ),
]


def _is_test_command(command: str) -> bool:
    return any(
        pattern.search(command)
        for pattern in TEST_COMMAND_PATTERNS
    )


def summarize_verification(
    events: list[Event],
) -> VerificationSummary:
    """Derive verification facts only from paired tool events."""
    summary = VerificationSummary()
    calls = {
        event.seq: event
        for event in events
        if event.type == "tool_call"
    }

    for event in events:
        if event.type != "tool_result":
            continue

        data = event.data
        ok = bool(data.get("ok", False))
        metadata = data.get("metadata", {})

        if not isinstance(metadata, dict):
            metadata = {}

        if not ok:
            summary.tool_failures += 1

        if metadata.get("policy_blocked", False):
            summary.policy_blocks += 1

        call = calls.get(event.source_seq)

        if call is None:
            continue

        name = str(call.data.get("name", ""))
        arguments = call.data.get("arguments", {})

        if not isinstance(arguments, dict):
            arguments = {}

        if name in {"write_file", "edit_file"}:
            if ok:
                summary.successful_file_changes += 1

            continue

        if name != "run_command":
            continue

        command = arguments.get("command", "")

        if not isinstance(command, str):
            command = str(command)

        summary.verification_commands.append(command)

        exit_code = metadata.get("exit_code")

        if isinstance(exit_code, int):
            summary.last_command_exit_code = exit_code
        else:
            summary.last_command_exit_code = None

        timed_out = bool(metadata.get("timeout", False))
        succeeded = ok and exit_code == 0 and not timed_out

        if succeeded:
            summary.successful_commands += 1
        else:
            summary.failed_commands += 1

        if timed_out:
            summary.timed_out_commands += 1

        if _is_test_command(command):
            summary.tests_likely_ran = True

            if succeeded:
                summary.successful_test_commands += 1
            else:
                summary.failed_test_commands += 1

    return summary
