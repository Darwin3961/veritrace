from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from coding_agent.agent import AgentLoop
from coding_agent.git_utils import GitInspector
from coding_agent.registry import ToolRegistry
from coding_agent.renderer import RichRenderer
from coding_agent.types import AgentResponse, ToolCall
from coding_agent.verification import summarize_verification


def python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]

    if os.name == "nt":
        return subprocess.list2cmdline(parts)

    return shlex.join(parts)


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, messages, tools):
        return next(self.responses)


def test_end_to_end_fake_presentation_flow(tmp_path: Path):
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=str(tmp_path),
        check=True,
        shell=False,
    )
    stream = io.StringIO()
    renderer = RichRenderer(
        console=Console(
            file=stream,
            force_terminal=False,
            color_system=None,
            width=200,
        )
    )
    model = ScriptedModel(
        [
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "write-source",
                        "write_file",
                        {
                            "path": "calc.py",
                            "content": (
                                "def add(a, b):\n"
                                "    return a - b\n"
                            ),
                        },
                    )
                ]
            ),
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "fix-source",
                        "edit_file",
                        {
                            "path": "calc.py",
                            "old_text": "    return a - b",
                            "new_text": "    return a + b",
                        },
                    )
                ]
            ),
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "write-test",
                        "write_file",
                        {
                            "path": "tests/test_calc.py",
                            "content": (
                                "from calc import add\n\n\n"
                                "def test_add():\n"
                                "    assert add(2, 3) == 5\n"
                            ),
                        },
                    )
                ]
            ),
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "run-tests",
                        "run_command",
                        {
                            "command": python_command("-m", "pytest", "-q"),
                            "timeout": 20,
                        },
                    )
                ]
            ),
            AgentResponse(
                content="Implemented add(a, b) and verified it.",
                finish_reason="stop",
            ),
        ]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
        event_sink=renderer.handle_event,
    )

    result = agent.run("Implement add(a, b) with a pytest test")
    verification = summarize_verification(agent.last_events)
    git_summary = GitInspector(tmp_path).inspect()
    renderer.render_final(
        result=result,
        metrics=agent.last_metrics,
        stop_reason=agent.last_stop_reason,
        verification=verification,
        git_summary=git_summary,
        trace_path=agent.last_trace_path,
    )

    assert result == "Implemented add(a, b) and verified it."
    assert (tmp_path / "calc.py").read_text(encoding="utf-8").startswith(
        "def add"
    )
    assert (tmp_path / "tests" / "test_calc.py").exists()
    assert verification.successful_file_changes == 3
    assert verification.successful_commands == 1
    assert verification.successful_test_commands == 1
    assert verification.failed_test_commands == 0
    assert git_summary.is_repo is True
    assert "?? calc.py" in git_summary.status_short
    assert "?? tests/" in git_summary.status_short

    trace_lines = agent.last_trace_path.read_text(
        encoding="utf-8"
    ).splitlines()
    trace_events = [json.loads(line) for line in trace_lines]
    assert [event["seq"] for event in trace_events] == list(
        range(1, len(trace_events) + 1)
    )
    assert trace_events[-1]["type"] == "session_end"

    output = stream.getvalue()
    assert "› Implement add(a, b) with a pytest test" in output
    assert "Write  calc.py" in output
    assert "Write  tests/test_calc.py" in output
    assert "Edit  calc.py" in output
    assert "-     return a - b" in output
    assert "+     return a + b" in output
    assert "✓ applied" in output
    assert "$ " in output
    assert "1 passed" in output
    assert "✓ Task completed" in output
    assert "Verification" in output
    assert "✓ 1 succeeded" in output
    assert "5 steps · 5 model calls · 4 tools" in output
    assert "trace  " in output
    assert "Workspace changes" in output
    assert "Coding Agent Session" not in output
    assert "╭" not in output
