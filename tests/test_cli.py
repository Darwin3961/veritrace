import sys
from pathlib import Path

import main as main_module


class FakeModelAdapter:
    @classmethod
    def from_env(cls):
        return "fake-model"


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
    ):
        self.model = model
        self.tools = tools
        self.max_steps = max_steps
        self.trace_dir = trace_dir
        self.trace_enabled = trace_enabled
        self.max_repeated_actions = max_repeated_actions
        self.last_trace_path = None
        self.last_metrics = None
        self.tasks = []
        self.__class__.instances.append(self)

    def run(self, task):
        self.tasks.append(task)
        return "CLI completed."


def install_fakes(monkeypatch):
    FakeToolRegistry.instances.clear()
    FakeAgentLoop.instances.clear()
    monkeypatch.setattr(main_module, "ModelAdapter", FakeModelAdapter)
    monkeypatch.setattr(main_module, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(main_module, "AgentLoop", FakeAgentLoop)


def test_cli_uses_positional_task(monkeypatch, tmp_path: Path, capsys):
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

    assert exit_code == 0
    assert FakeAgentLoop.instances[0].tasks == ["Fix the project"]
    assert FakeAgentLoop.instances[0].max_steps == 7
    assert FakeToolRegistry.instances[0].workspace == tmp_path.resolve()
    assert capsys.readouterr().out.strip() == "CLI completed."


def test_cli_reads_task_from_input(monkeypatch, tmp_path: Path):
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path)],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "Input task")

    exit_code = main_module.main()

    assert exit_code == 0
    assert FakeAgentLoop.instances[0].tasks == ["Input task"]


def test_cli_rejects_empty_task(monkeypatch, capsys):
    install_fakes(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "   ")

    exit_code = main_module.main()

    assert exit_code == 2
    assert "task must not be empty" in capsys.readouterr().err
    assert FakeAgentLoop.instances == []


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

    exit_code = main_module.main()

    assert exit_code == 130
    assert "Interrupted by user" in capsys.readouterr().err


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

    exit_code = main_module.main()

    agent = FakeAgentLoop.instances[0]
    assert exit_code == 0
    assert agent.trace_dir == str(trace_dir)
    assert agent.trace_enabled is False
    assert agent.max_repeated_actions == 5


def test_cli_prints_trace_and_metrics(monkeypatch, tmp_path: Path, capsys):
    install_fakes(monkeypatch)
    trace_path = tmp_path / "traces" / "session.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "Run task"],
    )

    def run_with_details(self, task):
        self.tasks.append(task)
        self.last_trace_path = trace_path
        self.last_metrics = {
            "steps": 2,
            "model_calls": 2,
            "tool_calls": 1,
            "tool_failures": 0,
            "duration_ms": 12,
        }
        return "CLI completed."

    monkeypatch.setattr(FakeAgentLoop, "run", run_with_details)

    exit_code = main_module.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "CLI completed." in output
    assert f"Trace: {trace_path}" in output
    assert "Metrics: steps=2 model_calls=2 tool_calls=1" in output
    assert "tool_failures=0 duration_ms=12" in output


def test_cli_never_prints_api_key(monkeypatch, tmp_path: Path, capsys):
    install_fakes(monkeypatch)
    fake_key = "FAKE_KEY_MUST_NOT_APPEAR"
    monkeypatch.setenv("DEEPSEEK_API_KEY", fake_key)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "Safe task"],
    )

    assert main_module.main() == 0

    captured = capsys.readouterr()
    assert fake_key not in captured.out
    assert fake_key not in captured.err
