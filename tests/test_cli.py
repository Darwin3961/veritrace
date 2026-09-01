from __future__ import annotations

import sys
from pathlib import Path

import main as main_module

from coding_agent.events import Event
from coding_agent.git_utils import GitSummary


class FakeModelAdapter:
    model_name = "fake-model"

    @classmethod
    def from_env(cls):
        return cls()


class FakeToolRegistry:
    instances = []

    def __init__(self, workspace):
        self.workspace = workspace
        self.__class__.instances.append(self)


class FakeAgentLoop:
    instances = []

    def __init__(
        self,
        model,
        tools,
        *,
        max_steps,
        trace_dir,
        trace_enabled,
        max_repeated_actions,
        event_sink,
    ):
        self.model = model
        self.tools = tools
        self.max_steps = max_steps
        self.trace_dir = trace_dir
        self.trace_enabled = trace_enabled
        self.max_repeated_actions = max_repeated_actions
        self.event_sink = event_sink
        self.last_trace_path = None
        self.last_metrics = None
        self.last_stop_reason = None
        self.last_events = []
        self.tasks = []
        self.__class__.instances.append(self)

    def run(self, task):
        self.tasks.append(task)

        if self.event_sink is not None:
            self.event_sink(
                Event.create(
                    seq=1,
                    event_type="step_start",
                    step=1,
                )
            )

        self.last_trace_path = (
            Path(self.trace_dir) / "session.jsonl"
            if self.trace_enabled
            else None
        )
        self.last_metrics = {
            "steps": 2,
            "model_calls": 2,
            "model_errors": 0,
            "tool_calls": 1,
            "tool_failures": 0,
            "policy_blocks": 0,
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "duration_ms": 12,
        }
        self.last_stop_reason = "completed"
        self.last_events = [
            Event.create(
                seq=1,
                event_type="tool_call",
                data={
                    "name": "run_command",
                    "arguments": {"command": "pytest -q"},
                },
            ),
            Event.create(
                seq=2,
                event_type="tool_result",
                source_seq=1,
                data={
                    "tool_name": "run_command",
                    "ok": True,
                    "metadata": {
                        "exit_code": 0,
                        "timeout": False,
                    },
                },
            ),
        ]
        return "CLI completed."


class FakeRenderer:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.events = []
        self.final_calls = []
        self.__class__.instances.append(self)

    def handle_event(self, event):
        self.events.append(event)

    def render_final(self, **kwargs):
        self.final_calls.append(kwargs)


class FakeGitInspector:
    instances = []
    summary = GitSummary(
        is_repo=True,
        status_short=" M app.py",
        diff_stat="app.py | 1 +",
        diff_text="+change",
    )

    def __init__(self, workspace):
        self.workspace = workspace
        self.__class__.instances.append(self)

    def inspect(self):
        return self.summary


class FakeInteractiveCLI:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def run(self):
        return 0


def install_fakes(monkeypatch):
    FakeToolRegistry.instances.clear()
    FakeAgentLoop.instances.clear()
    FakeRenderer.instances.clear()
    FakeGitInspector.instances.clear()
    FakeInteractiveCLI.instances.clear()
    monkeypatch.setattr(main_module, "ModelAdapter", FakeModelAdapter)
    monkeypatch.setattr(main_module, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(main_module, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(main_module, "RichRenderer", FakeRenderer)
    monkeypatch.setattr(main_module, "GitInspector", FakeGitInspector)
    monkeypatch.setattr(main_module, "InteractiveCLI", FakeInteractiveCLI)


def test_cli_default_uses_rich_event_sink_and_final_render(
    monkeypatch,
    tmp_path: Path,
):
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--workspace",
            str(tmp_path),
            "--max-steps",
            "7",
            "Fix the project",
        ],
    )

    exit_code = main_module.main()

    agent = FakeAgentLoop.instances[0]
    renderer = FakeRenderer.instances[0]
    final = renderer.final_calls[0]
    assert exit_code == 0
    assert agent.tasks == ["Fix the project"]
    assert agent.max_steps == 7
    assert agent.event_sink.__self__ is renderer
    assert [event.type for event in renderer.events] == ["step_start"]
    assert FakeToolRegistry.instances[0].workspace == tmp_path.resolve()
    assert FakeGitInspector.instances[0].workspace == tmp_path.resolve()
    assert final["result"] == "CLI completed."
    assert final["metrics"]["steps"] == 2
    assert final["stop_reason"] == "completed"
    assert final["verification"].successful_test_commands == 1
    assert final["git_summary"] is FakeGitInspector.summary
    assert final["trace_path"].name == "session.jsonl"
    assert renderer.kwargs["workspace_root"] == tmp_path.resolve()
    assert renderer.kwargs["model_name"] == "fake-model"


def test_cli_plain_reads_task_from_input(monkeypatch, tmp_path: Path):
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--plain", "--workspace", str(tmp_path)],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "Input task")

    assert main_module.main() == 0
    assert FakeAgentLoop.instances[0].tasks == ["Input task"]


def test_cli_rejects_empty_task(monkeypatch, capsys):
    install_fakes(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["main.py", "--plain"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "   ")

    exit_code = main_module.main()

    assert exit_code == 2
    assert "task must not be empty" in capsys.readouterr().err
    assert FakeAgentLoop.instances == []
    assert FakeRenderer.instances == []


def test_cli_rich_without_task_enters_interactive_mode(
    monkeypatch,
    tmp_path: Path,
):
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path)],
    )

    assert main_module.main() == 0
    interactive = FakeInteractiveCLI.instances[0]
    assert interactive.kwargs["agent"] is FakeAgentLoop.instances[0]
    assert interactive.kwargs["renderer"] is FakeRenderer.instances[0]
    assert interactive.kwargs["workspace"] == tmp_path.resolve()
    assert FakeAgentLoop.instances[0].tasks == []


def test_cli_plain_mode_prints_complete_summary(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    install_fakes(monkeypatch)
    original_run = FakeAgentLoop.run

    def run_with_markdown(self, task):
        original_run(self, task)
        return "## Plain result\n\n- **kept** as `raw Markdown`"

    monkeypatch.setattr(FakeAgentLoop, "run", run_with_markdown)
    trace_dir = tmp_path / "custom-traces"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--workspace",
            str(tmp_path),
            "--trace-dir",
            str(trace_dir),
            "--plain",
            "Run task",
        ],
    )

    assert main_module.main() == 0
    output = capsys.readouterr().out
    agent = FakeAgentLoop.instances[0]

    assert FakeRenderer.instances == []
    assert agent.event_sink is None
    assert "## Plain result" in output
    assert "- **kept** as `raw Markdown`" in output
    assert f"Trace: {trace_dir / 'session.jsonl'}" in output
    assert "Metrics: steps=2 model_calls=2" in output
    assert "Verification: commands_succeeded=1" in output
    assert "test_commands_succeeded=1" in output
    assert "Stop reason: completed" in output
    assert "Git status:" in output
    assert "M app.py" in output
    assert "Git diff stat:" in output
    assert "Git diff:" in output
    assert "\x1b[" not in output
    assert "(•◡•)" not in output
    assert "\\|/" not in output


def test_cli_no_diff_skips_git_inspector(monkeypatch, tmp_path: Path):
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--workspace",
            str(tmp_path),
            "--no-diff",
            "Run task",
        ],
    )

    assert main_module.main() == 0

    assert FakeGitInspector.instances == []
    final = FakeRenderer.instances[0].final_calls[0]
    assert final["git_summary"] is None


def test_cli_passes_trace_and_progress_options(monkeypatch, tmp_path: Path):
    install_fakes(monkeypatch)
    trace_dir = tmp_path / "custom-traces"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--workspace",
            str(tmp_path),
            "--trace-dir",
            str(trace_dir),
            "--no-trace",
            "--max-repeated-actions",
            "5",
            "Check options",
        ],
    )

    assert main_module.main() == 0

    agent = FakeAgentLoop.instances[0]
    final = FakeRenderer.instances[0].final_calls[0]
    assert agent.trace_dir == str(trace_dir)
    assert agent.trace_enabled is False
    assert agent.max_repeated_actions == 5
    assert final["trace_path"] is None


def test_cli_handles_keyboard_interrupt(monkeypatch, tmp_path: Path, capsys):
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "Interrupt task"],
    )

    def interrupt(self, _task):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeAgentLoop, "run", interrupt)

    assert main_module.main() == 130
    assert "Interrupted by user" in capsys.readouterr().err


def test_cli_handles_regular_exception(monkeypatch, tmp_path: Path, capsys):
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "Fail task"],
    )

    def fail(self, _task):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(FakeAgentLoop, "run", fail)

    assert main_module.main() == 1
    assert "Error: provider unavailable" in capsys.readouterr().err


def test_cli_never_prints_api_key(monkeypatch, tmp_path: Path, capsys):
    install_fakes(monkeypatch)
    fake_key = "FAKE_KEY_MUST_NOT_APPEAR"
    monkeypatch.setenv("DEEPSEEK_API_KEY", fake_key)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--workspace",
            str(tmp_path),
            "--plain",
            "Safe task",
        ],
    )

    assert main_module.main() == 0

    captured = capsys.readouterr()
    assert fake_key not in captured.out
    assert fake_key not in captured.err
