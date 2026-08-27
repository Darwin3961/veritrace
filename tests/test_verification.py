from __future__ import annotations

import pytest

from coding_agent.events import Event
from coding_agent.verification import (
    VerificationSummary,
    summarize_verification,
)


def tool_call(
    seq: int,
    name: str,
    arguments: dict,
) -> Event:
    return Event.create(
        seq=seq,
        event_type="tool_call",
        data={
            "call_id": f"call-{seq}",
            "name": name,
            "arguments": arguments,
        },
    )


def tool_result(
    seq: int,
    source_seq: int,
    name: str,
    *,
    ok: bool,
    metadata: dict | None = None,
) -> Event:
    return Event.create(
        seq=seq,
        event_type="tool_result",
        source_seq=source_seq,
        data={
            "call_id": f"call-{source_seq}",
            "tool_name": name,
            "ok": ok,
            "output": "",
            "error": None if ok else "failed",
            "metadata": metadata or {},
        },
    )


def command_events(
    command: str,
    *,
    ok: bool,
    exit_code: int | None,
    timeout: bool = False,
) -> list[Event]:
    return [
        tool_call(1, "run_command", {"command": command}),
        tool_result(
            2,
            1,
            "run_command",
            ok=ok,
            metadata={
                "exit_code": exit_code,
                "timeout": timeout,
            },
        ),
    ]


def test_empty_events_return_empty_summary():
    assert summarize_verification([]) == VerificationSummary()


def test_successful_command_and_last_exit_code():
    summary = summarize_verification(
        command_events("python hello.py", ok=True, exit_code=0)
    )

    assert summary.successful_commands == 1
    assert summary.failed_commands == 0
    assert summary.last_command_exit_code == 0
    assert summary.verification_commands == ["python hello.py"]
    assert summary.tests_likely_ran is False


def test_failed_command_and_last_exit_code():
    summary = summarize_verification(
        command_events("python broken.py", ok=False, exit_code=3)
    )

    assert summary.successful_commands == 0
    assert summary.failed_commands == 1
    assert summary.tool_failures == 1
    assert summary.last_command_exit_code == 3


def test_timed_out_command_is_failed_and_counted():
    summary = summarize_verification(
        command_events(
            "python slow.py",
            ok=False,
            exit_code=None,
            timeout=True,
        )
    )

    assert summary.failed_commands == 1
    assert summary.timed_out_commands == 1
    assert summary.last_command_exit_code is None


def test_successful_write_and_edit_count_as_file_changes():
    events = [
        tool_call(1, "write_file", {"path": "a.py"}),
        tool_result(2, 1, "write_file", ok=True),
        tool_call(3, "edit_file", {"path": "a.py"}),
        tool_result(4, 3, "edit_file", ok=True),
    ]

    summary = summarize_verification(events)

    assert summary.successful_file_changes == 2
    assert summary.tool_failures == 0


def test_failed_file_change_is_not_counted_as_success():
    events = [
        tool_call(1, "edit_file", {"path": "a.py"}),
        tool_result(2, 1, "edit_file", ok=False),
    ]

    summary = summarize_verification(events)

    assert summary.successful_file_changes == 0
    assert summary.tool_failures == 1


def test_policy_block_is_counted_from_result_metadata():
    events = [
        tool_call(1, "read_file", {"path": ".env"}),
        tool_result(
            2,
            1,
            "read_file",
            ok=False,
            metadata={"policy_blocked": True},
        ),
    ]

    summary = summarize_verification(events)

    assert summary.policy_blocks == 1
    assert summary.tool_failures == 1


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m pytest -v",
        '"C:\\Python\\python.exe" -m pytest',
        "python -m unittest",
        "unittest",
        "npm test",
        "npm run test",
        "pnpm test",
        "yarn test",
        "cargo test",
        "go test ./...",
        "mvn test",
        "gradle test",
        "./gradlew test",
        ".\\gradlew test",
        "dotnet test",
    ],
)
def test_supported_test_commands_are_detected(command):
    summary = summarize_verification(
        command_events(command, ok=True, exit_code=0)
    )

    assert summary.tests_likely_ran is True
    assert summary.successful_test_commands == 1
    assert summary.failed_test_commands == 0


def test_failed_pytest_is_reported_as_failed_not_passed():
    summary = summarize_verification(
        command_events("pytest", ok=False, exit_code=1)
    )

    assert summary.tests_likely_ran is True
    assert summary.successful_test_commands == 0
    assert summary.failed_test_commands == 1


def test_ordinary_python_script_is_not_a_test_command():
    summary = summarize_verification(
        command_events("python hello.py", ok=True, exit_code=0)
    )

    assert summary.successful_commands == 1
    assert summary.tests_likely_ran is False
    assert summary.successful_test_commands == 0


def test_unpaired_tool_result_does_not_create_command_fact():
    events = [
        tool_result(
            2,
            99,
            "run_command",
            ok=True,
            metadata={"exit_code": 0},
        )
    ]

    summary = summarize_verification(events)

    assert summary.successful_commands == 0
    assert summary.verification_commands == []


def test_multiple_commands_are_aggregated_in_event_order():
    events = [
        tool_call(1, "run_command", {"command": "python hello.py"}),
        tool_result(
            2,
            1,
            "run_command",
            ok=True,
            metadata={"exit_code": 0, "timeout": False},
        ),
        tool_call(3, "run_command", {"command": "pytest"}),
        tool_result(
            4,
            3,
            "run_command",
            ok=False,
            metadata={"exit_code": 1, "timeout": False},
        ),
    ]

    summary = summarize_verification(events)

    assert summary.successful_commands == 1
    assert summary.failed_commands == 1
    assert summary.successful_test_commands == 0
    assert summary.failed_test_commands == 1
    assert summary.last_command_exit_code == 1
    assert summary.verification_commands == [
        "python hello.py",
        "pytest",
    ]


def test_model_claim_alone_never_counts_as_test_success():
    events = [
        Event.create(
            seq=1,
            event_type="assistant_response",
            data={"content": "All tests passed."},
        )
    ]

    summary = summarize_verification(events)

    assert summary.tests_likely_ran is False
    assert summary.successful_test_commands == 0
