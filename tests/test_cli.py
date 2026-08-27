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

    def __init__(self, model, tools, *, max_steps):
        self.model = model
        self.tools = tools
        self.max_steps = max_steps
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
