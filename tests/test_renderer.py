from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from coding_agent.events import Event
from coding_agent.git_utils import GitSummary
from coding_agent.renderer import RichRenderer
from coding_agent.session import SessionTrace
from coding_agent.verification import VerificationSummary


def make_renderer(**kwargs):
    stream = io.StringIO()
    console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        width=200,
    )
    return RichRenderer(console=console, **kwargs), stream


def make_event(
    event_type: str,
    *,
    step: int | None = None,
    data: dict | None = None,
) -> Event:
    return Event.create(
        seq=1,
        event_type=event_type,
        step=step,
        data=data,
    )


def test_session_header_does_not_expose_event_data():
    renderer, stream = make_renderer()

    renderer.handle_event(
        make_event(
            "session_start",
            data={
                "api_key": "must-not-render",
                "system_prompt": "private prompt",
                "environment": "private environment",
            },
        )
    )

    output = stream.getvalue()
    assert "Coding Agent Session" in output
    assert "must-not-render" not in output
    assert "private prompt" not in output
    assert "private environment" not in output


def test_task_and_step_are_rendered():
    renderer, stream = make_renderer()

    renderer.handle_event(
        make_event("user_task", data={"task": "修复 Unicode 问题"})
    )
    renderer.handle_event(make_event("step_start", step=3))

    output = stream.getvalue()
    assert "Task" in output
    assert "修复 Unicode 问题" in output
    assert "Step 3" in output


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("read_file", {"path": "src/main.py"}, "path=src/main.py"),
        ("list_files", {"path": "src"}, "path=src"),
        (
            "search_code",
            {"query": "needle", "path": "src"},
            "query='needle' path=src",
        ),
        (
            "run_command",
            {"command": "python -m pytest"},
            "command=python -m pytest",
        ),
    ],
)
def test_tool_call_shows_only_relevant_arguments(
    name,
    arguments,
    expected,
):
    renderer, stream = make_renderer()

    renderer.handle_event(
        make_event(
            "tool_call",
            data={"name": name, "arguments": arguments},
        )
    )

    output = stream.getvalue()
    assert name in output
    assert expected in output


@pytest.mark.parametrize("name", ["write_file", "edit_file"])
def test_write_and_edit_calls_do_not_dump_content(name):
    renderer, stream = make_renderer()
    hidden = "HIDDEN_" + ("CONTENT" * 100)

    renderer.handle_event(
        make_event(
            "tool_call",
            data={
                "name": name,
                "arguments": {
                    "path": "src/main.py",
                    "content": hidden,
                    "old_text": hidden,
                    "new_text": hidden,
                },
            },
        )
    )

    output = stream.getvalue()
    assert "path=src/main.py" in output
    assert hidden not in output
    assert "old_text" not in output
    assert "new_text" not in output


def test_long_command_is_truncated():
    renderer, stream = make_renderer(max_output_chars=100)

    renderer.handle_event(
        make_event(
            "tool_call",
            data={
                "name": "run_command",
                "arguments": {"command": "A" * 500},
            },
        )
    )

    output = stream.getvalue()
    assert "characters omitted" in output
    assert "A" * 200 not in output


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {
                "tool_name": "write_file",
                "ok": True,
                "metadata": {},
            },
            "✓ write_file",
        ),
        (
            {
                "tool_name": "read_file",
                "ok": False,
                "error": "file not found",
                "metadata": {},
            },
            "✗ read_file — file not found",
        ),
        (
            {
                "tool_name": "run_command",
                "ok": False,
                "error": "blocked by policy",
                "metadata": {"policy_blocked": True},
            },
            "⊘ run_command — blocked",
        ),
        (
            {
                "tool_name": "run_command",
                "ok": False,
                "error": "timed out",
                "metadata": {"timeout": True},
            },
            "! run_command — timeout",
        ),
    ],
)
def test_tool_result_statuses(data, expected):
    renderer, stream = make_renderer(show_tool_output=False)

    renderer.handle_event(make_event("tool_result", data=data))

    assert expected in stream.getvalue()


def test_command_output_is_limited():
    renderer, stream = make_renderer(max_output_chars=100)

    renderer.handle_event(
        make_event(
            "tool_result",
            data={
                "tool_name": "run_command",
                "ok": True,
                "output": "HEAD" + ("x" * 500) + "TAIL",
                "metadata": {},
            },
        )
    )

    output = stream.getvalue()
    assert "Command output" in output
    assert "characters omitted" in output
    assert "HEAD" in output
    assert "TAIL" in output


@pytest.mark.parametrize("tool_name", ["read_file", "search_code"])
def test_read_and_search_results_do_not_dump_content(tool_name):
    renderer, stream = make_renderer(show_tool_output=True)
    hidden = "FULL_FILE_CONTENT_MUST_NOT_RENDER"

    renderer.handle_event(
        make_event(
            "tool_result",
            data={
                "tool_name": tool_name,
                "ok": True,
                "output": hidden,
                "metadata": {},
            },
        )
    )

    output = stream.getvalue()
    assert f"✓ {tool_name}" in output
    assert hidden not in output


def test_model_error_and_no_progress_are_rendered():
    renderer, stream = make_renderer()

    renderer.handle_event(
        make_event(
            "model_error",
            data={"consecutive_errors": 2, "error": "hidden detail"},
        )
    )
    renderer.handle_event(
        make_event(
            "no_progress",
            data={"repeated_count": 3, "fingerprint": "hidden"},
        )
    )

    output = stream.getvalue()
    assert "retrying (2)" in output
    assert "3 repeated tool actions" in output
    assert "hidden detail" not in output
    assert "fingerprint" not in output


def test_unknown_event_and_session_end_are_silent():
    renderer, stream = make_renderer()

    renderer.handle_event(make_event("unknown", data={"value": "hidden"}))
    renderer.handle_event(make_event("session_end", data={"result": "hidden"}))

    assert stream.getvalue() == ""


def test_markup_injection_is_rendered_as_plain_text():
    renderer, stream = make_renderer()
    injected = "[bold red]INJECTED[/bold red]"

    renderer.handle_event(
        make_event("user_task", data={"task": injected})
    )

    assert injected in stream.getvalue()


def test_session_trace_writes_jsonl_before_calling_sink(tmp_path):
    observed = []
    trace = None

    def sink(event):
        lines = trace.path.read_text(encoding="utf-8").splitlines()
        observed.append((event.seq, json.loads(lines[-1])["seq"]))

    trace = SessionTrace(tmp_path / "traces", event_sink=sink)
    trace.emit("step_start", step=1)

    assert observed == [(1, 1)]


def test_renderer_exception_does_not_affect_session_trace(tmp_path):
    def failing_sink(_event):
        raise RuntimeError("renderer failed")

    trace = SessionTrace(
        tmp_path / "traces",
        event_sink=failing_sink,
    )

    event = trace.emit("step_start", step=1)

    assert event.seq == 1
    assert trace.events[0].type == "step_start"
    assert len(trace.path.read_text(encoding="utf-8").splitlines()) == 1


def test_invalid_renderer_output_limit_is_rejected():
    with pytest.raises(ValueError, match="at least 100"):
        RichRenderer(max_output_chars=99)


def test_final_summary_is_markup_safe_and_does_not_invent_tests(tmp_path):
    renderer, stream = make_renderer()
    injected = "[bold red]FINAL[/bold red]"

    renderer.render_final(
        result=injected,
        metrics={
            "steps": 2,
            "model_calls": 2,
            "tool_calls": 1,
            "duration_ms": 5,
        },
        stop_reason="completed",
        verification=VerificationSummary(
            successful_commands=1,
            verification_commands=["python hello.py"],
        ),
        git_summary=GitSummary(is_repo=True),
        trace_path=tmp_path / "trace.jsonl",
    )

    output = stream.getvalue()
    assert injected in output
    assert "Result" in output
    assert "Verification" in output
    assert "No explicit test command detected" in output
    assert "Ordinary command successes" in output
    assert "python hello.py" in output
    assert "Metrics: steps=2 model_calls=2 tool_calls=1 duration_ms=5" in output
    assert "Stop reason: completed" in output
    assert f"Trace: {tmp_path / 'trace.jsonl'}" in output
    assert "Git: no workspace changes" in output
    assert "All tests passed" not in output


@pytest.mark.parametrize(
    ("successful", "failed", "expected"),
    [
        (1, 0, "1 succeeded, 0 failed"),
        (0, 1, "0 succeeded, 1 failed"),
    ],
)
def test_final_summary_reports_observed_test_command_outcomes(
    successful,
    failed,
    expected,
):
    renderer, stream = make_renderer()

    renderer.render_final(
        result="Done.",
        metrics={},
        stop_reason="completed",
        verification=VerificationSummary(
            successful_commands=successful,
            failed_commands=failed,
            tests_likely_ran=True,
            successful_test_commands=successful,
            failed_test_commands=failed,
            verification_commands=["pytest"],
        ),
        git_summary=None,
        trace_path=None,
    )

    assert expected in stream.getvalue()
