from __future__ import annotations

import io
import json
import platform
from pathlib import Path

import pytest
from rich.console import Console

from coding_agent.events import Event
from coding_agent.git_utils import GitSummary
from coding_agent.renderer import RichRenderer, sanitize_display_path
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
    seq: int = 1,
    step: int | None = None,
    source_seq: int | None = None,
    data: dict | None = None,
) -> Event:
    return Event.create(
        seq=seq,
        event_type=event_type,
        step=step,
        source_seq=source_seq,
        data=data,
    )


def edit_call(
    seq: int,
    call_id: str,
    *,
    path: str = "calc.py",
    old_text: str = "return a - b",
    new_text: str = "return a + b",
) -> Event:
    return make_event(
        "tool_call",
        seq=seq,
        data={
            "call_id": call_id,
            "name": "edit_file",
            "arguments": {
                "path": path,
                "old_text": old_text,
                "new_text": new_text,
            },
        },
    )


def edit_result(
    seq: int,
    call_id: str,
    *,
    source_seq: int,
    ok: bool,
    error: str | None = None,
    policy_blocked: bool = False,
) -> Event:
    return make_event(
        "tool_result",
        seq=seq,
        source_seq=source_seq,
        data={
            "call_id": call_id,
            "tool_name": "edit_file",
            "ok": ok,
            "error": error,
            "metadata": {"policy_blocked": policy_blocked},
        },
    )


def test_session_header_is_compact_and_ignores_private_event_data(tmp_path):
    renderer, stream = make_renderer(
        workspace_root=tmp_path,
        model_name="deepseek-v4-flash",
    )

    renderer.handle_event(
        make_event(
            "session_start",
            data={
                "api_key": "must-not-render",
                "system_prompt": "private prompt",
                "remote_url": "https://example.invalid/private.git",
            },
        )
    )

    output = stream.getvalue()
    assert "coding-agent" in output
    assert f"Python {platform.python_version()}" in output
    assert "model      deepseek-v4-flash" in output
    assert "workspace" in output
    assert "Coding Agent Session" not in output
    assert "must-not-render" not in output
    assert "private prompt" not in output
    assert "example.invalid" not in output
    assert "╭" not in output


def test_session_header_includes_read_only_branch_when_available(monkeypatch):
    renderer, stream = make_renderer()
    monkeypatch.setattr(renderer, "_read_git_branch", lambda: "main")

    renderer.handle_event(make_event("session_start"))

    output = stream.getvalue()
    assert "coding-agent  on  main  via  Python" in output
    assert "http" not in output


def test_task_uses_terminal_marker_and_step_is_silent():
    renderer, stream = make_renderer()

    renderer.handle_event(
        make_event("user_task", data={"task": "修复 Unicode 问题"})
    )
    renderer.handle_event(make_event("step_start", step=3))

    output = stream.getvalue()
    assert "› 修复 Unicode 问题" in output
    assert "Step 3" not in output
    assert "Task" not in output


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("read_file", {"path": "src/main.py"}, "● Read  src/main.py"),
        ("list_files", {"path": "src"}, "● List files  src"),
        (
            "search_code",
            {"query": "needle", "path": "src"},
            '● Search  "needle"  in src',
        ),
        (
            "run_command",
            {"command": "python -m pytest"},
            "● Run\n  $ python -m pytest",
        ),
        ("custom_tool", {}, "● Tool  custom_tool"),
    ],
)
def test_tool_calls_use_friendly_terminal_names(name, arguments, expected):
    renderer, stream = make_renderer()

    renderer.handle_event(
        make_event(
            "tool_call",
            data={"name": name, "arguments": arguments},
        )
    )

    output = stream.getvalue()
    assert expected in output

    if name in RichRenderer.TOOL_ACTIONS:
        assert name not in output


@pytest.mark.parametrize("name", ["write_file", "edit_file"])
def test_write_and_edit_calls_hide_file_content_and_replacement(name):
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
    assert "src/main.py" in output
    assert hidden not in output
    assert "old_text" not in output
    assert "new_text" not in output


def test_edit_call_waits_for_success_before_showing_patch():
    renderer, stream = make_renderer()

    renderer.handle_event(edit_call(1, "edit-1"))

    output = stream.getvalue()
    assert "● Edit  calc.py" in output
    assert "return a - b" not in output
    assert "return a + b" not in output
    assert "applied" not in output
    assert "edit-1" in renderer._pending_edits


def test_successful_edit_renders_small_patch_and_clears_cache():
    renderer, stream = make_renderer()
    renderer.handle_event(edit_call(1, "edit-1"))
    renderer.handle_event(
        edit_result(2, "edit-1", source_seq=1, ok=True)
    )

    output = stream.getvalue()
    assert "  - return a - b" in output
    assert "  + return a + b" in output
    assert "✓ applied" in output
    assert renderer._pending_edits == {}


def test_failed_edit_never_renders_patch_and_clears_cache():
    renderer, stream = make_renderer()
    renderer.handle_event(edit_call(1, "edit-failed"))
    renderer.handle_event(
        edit_result(
            2,
            "edit-failed",
            source_seq=1,
            ok=False,
            error="search text was not found",
        )
    )

    output = stream.getvalue()
    assert "✗ Edit  calc.py" in output
    assert "search text was not found" in output
    assert "return a - b" not in output
    assert "return a + b" not in output
    assert "applied" not in output
    assert renderer._pending_edits == {}


def test_policy_blocked_edit_never_renders_patch_and_clears_cache():
    renderer, stream = make_renderer()
    renderer.handle_event(
        edit_call(
            1,
            "edit-blocked",
            path=".env",
            old_text="OLD_FAKE_VALUE",
            new_text="NEW_FAKE_VALUE",
        )
    )
    renderer.handle_event(
        edit_result(
            2,
            "edit-blocked",
            source_seq=1,
            ok=False,
            error="policy blocked tool call: denied",
            policy_blocked=True,
        )
    )

    output = stream.getvalue()
    assert "! Blocked by policy" in output
    assert "OLD_FAKE_VALUE" not in output
    assert "NEW_FAKE_VALUE" not in output
    assert "applied" not in output
    assert renderer._pending_edits == {}


def test_edit_cache_pairs_parallel_calls_by_call_id():
    renderer, stream = make_renderer()
    renderer.handle_event(
        edit_call(1, "edit-a", path="a.py", old_text="old A", new_text="new A")
    )
    renderer.handle_event(
        edit_call(2, "edit-b", path="b.py", old_text="old B", new_text="new B")
    )
    renderer.handle_event(edit_result(3, "edit-b", source_seq=2, ok=True))

    first_result = stream.getvalue()
    assert "- old B" in first_result
    assert "+ new B" in first_result
    assert "- old A" not in first_result
    assert set(renderer._pending_edits) == {"edit-a"}

    renderer.handle_event(edit_result(4, "edit-a", source_seq=1, ok=True))
    output = stream.getvalue()
    assert "- old A" in output
    assert "+ new A" in output
    assert renderer._pending_edits == {}


def test_failed_edit_does_not_pollute_next_edit_preview():
    renderer, stream = make_renderer()
    renderer.handle_event(
        edit_call(1, "failed", old_text="failed old", new_text="failed new")
    )
    renderer.handle_event(
        edit_result(
            2,
            "failed",
            source_seq=1,
            ok=False,
            error="not found",
        )
    )
    renderer.handle_event(
        edit_call(3, "success", old_text="good old", new_text="good new")
    )
    renderer.handle_event(edit_result(4, "success", source_seq=3, ok=True))

    output = stream.getvalue()
    assert "failed old" not in output
    assert "failed new" not in output
    assert "- good old" in output
    assert "+ good new" in output


def test_session_start_clears_pending_edit_cache():
    renderer, _stream = make_renderer()
    renderer.handle_event(edit_call(1, "edit-1"))
    assert renderer._pending_edits

    renderer.handle_event(make_event("session_start", seq=2))

    assert renderer._pending_edits == {}


def test_small_edit_preview_shows_every_changed_line():
    renderer, stream = make_renderer()
    old_lines = [f"old-{index}" for index in range(6)]
    new_lines = [f"new-{index}" for index in range(6)]
    renderer.handle_event(
        edit_call(
            1,
            "small",
            old_text="\n".join(old_lines),
            new_text="\n".join(new_lines),
        )
    )
    renderer.handle_event(edit_result(2, "small", source_seq=1, ok=True))

    output = stream.getvalue()

    for line in old_lines:
        assert f"- {line}" in output

    for line in new_lines:
        assert f"+ {line}" in output

    assert "changed lines omitted" not in output


def test_medium_edit_preview_shows_head_omission_and_tail():
    renderer, stream = make_renderer()
    old_lines = [f"old-{index}" for index in range(10)]
    new_lines = [f"new-{index}" for index in range(10)]
    renderer.handle_event(
        edit_call(
            1,
            "medium",
            old_text="\n".join(old_lines),
            new_text="\n".join(new_lines),
        )
    )
    renderer.handle_event(edit_result(2, "medium", source_seq=1, ok=True))

    output = stream.getvalue()
    assert "- old-0" in output
    assert "- old-5" in output
    assert "old-6" not in output
    assert "new-3" not in output
    assert "+ new-4" in output
    assert "+ new-9" in output
    assert "8 changed lines omitted" in output
    assert "✓ applied · 20 lines changed" in output


def test_large_edit_preview_only_shows_bounded_summary():
    renderer, stream = make_renderer()
    old_text = "\n".join(f"old-{index}" for index in range(25))
    new_text = "\n".join(f"new-{index}" for index in range(25))
    renderer.handle_event(
        edit_call(1, "large", old_text=old_text, new_text=new_text)
    )
    renderer.handle_event(edit_result(2, "large", source_seq=1, ok=True))

    output = stream.getvalue()
    assert "✓ applied · 50 lines changed (+25 -25)" in output
    assert "preview omitted for a large edit" in output
    assert "old-0" not in output
    assert "new-0" not in output
    assert len(output) < 500


def test_edit_preview_truncates_very_long_lines():
    renderer, stream = make_renderer()
    old_line = "A" * 2000 + "OLD_TAIL"
    new_line = "B" * 2000 + "NEW_TAIL"
    renderer.handle_event(
        edit_call(1, "long", old_text=old_line, new_text=new_line)
    )
    renderer.handle_event(edit_result(2, "long", source_seq=1, ok=True))

    output = stream.getvalue()
    assert "chars omitted" in output
    assert "OLD_TAIL" in output
    assert "NEW_TAIL" in output
    assert old_line not in output
    assert new_line not in output
    assert len(output) < 700


def test_edit_preview_preserves_markup_and_unicode_as_plain_text():
    renderer, stream = make_renderer()
    old_text = "[bold red]旧值[/bold red]"
    new_text = "[green]新值[/green]"
    renderer.handle_event(
        edit_call(1, "unicode", old_text=old_text, new_text=new_text)
    )
    renderer.handle_event(edit_result(2, "unicode", source_seq=1, ok=True))

    output = stream.getvalue()
    assert f"- {old_text}" in output
    assert f"+ {new_text}" in output


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
            "✓ Write",
        ),
        (
            {
                "tool_name": "read_file",
                "ok": False,
                "error": "file not found",
                "metadata": {},
            },
            "✗ Read\n  file not found",
        ),
        (
            {
                "tool_name": "run_command",
                "ok": False,
                "error": "policy blocked tool call: denied",
                "metadata": {"policy_blocked": True},
            },
            "! Blocked by policy\n  policy blocked tool call: denied",
        ),
        (
            {
                "tool_name": "run_command",
                "ok": False,
                "error": "command timed out after 30 seconds",
                "metadata": {"timeout": True},
            },
            "! Command timed out\n  command timed out after 30 seconds",
        ),
    ],
)
def test_tool_results_have_clear_terminal_statuses(data, expected):
    renderer, stream = make_renderer(show_tool_output=False)

    renderer.handle_event(make_event("tool_result", data=data))

    assert expected in stream.getvalue()


def test_command_output_is_borderless_and_limited():
    renderer, stream = make_renderer(max_output_chars=100)
    renderer.handle_event(
        make_event(
            "tool_call",
            data={
                "name": "run_command",
                "arguments": {"command": "python -m pytest -q"},
            },
        )
    )
    renderer.handle_event(
        make_event(
            "tool_result",
            seq=2,
            source_seq=1,
            data={
                "tool_name": "run_command",
                "ok": True,
                "output": "HEAD" + ("x" * 500) + "TAIL",
                "metadata": {},
            },
        )
    )

    output = stream.getvalue()
    assert "$ python -m pytest -q" in output
    assert "characters omitted" in output
    assert "HEAD" in output
    assert "TAIL" in output
    assert "✓ Command completed" in output
    assert "Command output" not in output
    assert "╭" not in output


def test_empty_command_output_is_not_rendered():
    renderer, stream = make_renderer()
    renderer.handle_event(
        make_event(
            "tool_result",
            data={
                "tool_name": "run_command",
                "ok": True,
                "output": "",
                "metadata": {},
            },
        )
    )

    assert stream.getvalue() == "✓ Command completed\n"


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
    assert "✓" in output
    assert hidden not in output


def test_model_error_and_no_progress_are_rendered_without_private_data():
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


def test_ascii_fallback_uses_plain_symbols_without_crashing():
    renderer, stream = make_renderer(unicode_symbols=False)
    renderer.handle_event(make_event("session_start"))
    renderer.handle_event(make_event("user_task", data={"task": "Run"}))
    renderer.handle_event(
        make_event(
            "tool_call",
            data={"name": "read_file", "arguments": {"path": "a.py"}},
        )
    )
    renderer.handle_event(
        make_event(
            "tool_result",
            seq=2,
            source_seq=1,
            data={"tool_name": "read_file", "ok": True},
        )
    )

    output = stream.getvalue()
    assert "> Run" in output
    assert "* Read  a.py" in output
    assert "OK Read  a.py" in output


def test_display_path_hides_home_directory_identity():
    private_name = Path.home().name
    displayed = sanitize_display_path(
        Path.home() / "projects" / "coding-agent" / "demo"
    )

    assert displayed.startswith("~")
    assert private_name not in displayed
    assert displayed.endswith("demo")


def test_display_path_truncation_preserves_basename(tmp_path):
    path = tmp_path / ("long-directory-" * 10) / "session-123.jsonl"
    displayed = sanitize_display_path(path, max_chars=40)

    assert len(displayed) <= 40
    assert displayed.endswith("session-123.jsonl")


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


def test_final_summary_is_compact_safe_and_does_not_invent_tests(tmp_path):
    renderer, stream = make_renderer()
    injected = "[bold red]FINAL[/bold red]"

    renderer.render_final(
        result=injected,
        metrics={
            "steps": 2,
            "model_calls": 2,
            "model_errors": 0,
            "tool_calls": 1,
            "tool_failures": 0,
            "total_tokens": 10700,
            "duration_ms": 4200,
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
    assert "✓ Task completed" in output
    assert injected in output
    assert "Verification" in output
    assert "No explicit test command detected" in output
    assert "$ python hello.py" in output
    assert "2 steps · 2 model calls · 1 tool · 10.7k tokens · 4.2s" in output
    assert "model_errors=0" not in output
    assert "tool_failures=0" not in output
    assert "trace  " in output
    assert "trace.jsonl" in output
    assert Path.home().name not in output
    assert "Workspace clean" in output
    assert "All tests passed" not in output
    assert "Result" not in output
    assert "╭" not in output


@pytest.mark.parametrize(
    ("stop_reason", "expected", "unexpected"),
    [
        ("completed", "✓ Task completed", "Agent stopped"),
        ("max_steps", "! Agent stopped: maximum step limit", "✓ Task completed"),
        ("no_progress", "! Agent stopped: repeated action", "✓ Task completed"),
        (
            "model_error_limit",
            "! Agent stopped: model response error limit",
            "✓ Task completed",
        ),
    ],
)
def test_final_status_respects_stop_reason(stop_reason, expected, unexpected):
    renderer, stream = make_renderer()
    renderer.render_final(
        result="Done.",
        metrics={},
        stop_reason=stop_reason,
        verification=VerificationSummary(),
        git_summary=None,
        trace_path=None,
    )

    output = stream.getvalue()
    assert expected in output
    assert unexpected not in output


@pytest.mark.parametrize(
    ("successful", "failed", "expected"),
    [
        (1, 0, "✓ 1 succeeded"),
        (0, 1, "✗ 1 failed"),
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


def test_git_status_and_diff_are_borderless_and_markup_safe():
    renderer, stream = make_renderer()
    injected = "[bold red]INJECTED[/bold red]"
    renderer.render_git_summary(
        GitSummary(
            is_repo=True,
            status_short=f" M app.py\n?? {injected}",
            diff_stat="app.py | 1 +",
            diff_text=f"diff --git a/app.py b/app.py\n+{injected}",
        )
    )

    output = stream.getvalue()
    assert "Workspace changes" in output
    assert "M app.py" in output
    assert "??" in output
    assert "Diff summary" in output
    assert "Diff" in output
    assert injected in output
    assert "╭" not in output


def test_git_summary_compacts_known_tracked_diff_stat():
    renderer, stream = make_renderer()
    renderer.render_git_summary(
        GitSummary(
            is_repo=True,
            status_short=" M calc.py",
            diff_stat=(
                "Unstaged:\n"
                " calc.py | 2 +-\n"
                " 1 file changed, 1 insertion(+), 1 deletion(-)"
            ),
        )
    )

    output = stream.getvalue()
    assert "Tracked diff" in output
    assert "1 file changed · +1 -1" in output
    assert "calc.py | 2 +-" not in output


def test_git_summary_does_not_invent_stats_without_diff_stat():
    renderer, stream = make_renderer()
    renderer.render_git_summary(
        GitSummary(
            is_repo=True,
            status_short="?? new_file.py",
        )
    )

    output = stream.getvalue()
    assert "?? new_file.py" in output
    assert "Tracked diff" not in output
    assert "+0 -0" not in output


def test_untracked_files_are_not_counted_in_tracked_diff_summary():
    renderer, stream = make_renderer()
    renderer.render_git_summary(
        GitSummary(
            is_repo=True,
            status_short=" M calc.py\n?? new_file.py",
            diff_stat=(
                "Unstaged:\n"
                " calc.py | 2 +-\n"
                " 1 file changed, 1 insertion(+), 1 deletion(-)"
            ),
            diff_text=(
                "Unstaged:\n"
                "diff --git a/calc.py b/calc.py\n"
                "-return a - b\n"
                "+return a + b"
            ),
        )
    )

    output = stream.getvalue()
    assert "?? new_file.py" in output
    assert "1 file changed · +1 -1" in output
    assert "2 files changed" not in output
    assert "Diff" in output
    assert "-return a - b" in output
    assert "+return a + b" in output
