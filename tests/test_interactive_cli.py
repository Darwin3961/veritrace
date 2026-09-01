from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.document import Document

from coding_agent.events import Event
from coding_agent.interactive_cli import InteractiveCLI, SlashCommandCompleter


class ScriptedPromptSession:
    def __init__(self, values):
        self.values = iter(values)
        self.prompts = []

    def prompt(self, prompt):
        self.prompts.append(prompt)
        value = next(self.values)

        if isinstance(value, BaseException):
            raise value

        return value


class FakeAgent:
    def __init__(self):
        self.tasks = []
        self.last_stop_reason = None
        self.last_metrics = None
        self.last_trace_path = None
        self.last_events = []

    def run(self, task):
        self.tasks.append(task)
        self.last_stop_reason = "completed"
        self.last_metrics = {"tool_calls": 1, "steps": 2}
        self.last_trace_path = Path("traces/session.jsonl")
        self.last_events = [
            Event.create(
                1,
                "tool_call",
                data={
                    "call_id": "test-call",
                    "name": "run_command",
                    "arguments": {"command": "python -m pytest -q"},
                },
            ),
            Event.create(
                2,
                "tool_result",
                source_seq=1,
                data={
                    "call_id": "test-call",
                    "tool_name": "run_command",
                    "ok": True,
                    "metadata": {"exit_code": 0, "timeout": False},
                },
            ),
        ]
        return "Task result."


class FakeRenderer:
    task_symbol = "›"

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [("/st", "/status"), ("/v", "/verify"), ("/tr", "/trace")],
)
def test_slash_completer_expands_known_prefixes(prefix, expected):
    completions = list(
        SlashCommandCompleter().get_completions(Document(prefix), None)
    )

    assert [completion.text for completion in completions] == [expected]


def make_cli(values, tmp_path):
    agent = FakeAgent()
    renderer = FakeRenderer()
    cli = InteractiveCLI(
        agent=agent,
        renderer=renderer,
        workspace=tmp_path,
        inspect_git=False,
        prompt_session=ScriptedPromptSession(values),
    )
    return cli, agent, renderer


def call_names(renderer):
    return [name for name, _args, _kwargs in renderer.calls]


def test_help_and_status_do_not_call_agent(tmp_path):
    cli, agent, renderer = make_cli(["/help", "/status", "/exit"], tmp_path)

    assert cli.run() == 0
    assert agent.tasks == []
    assert "render_help" in call_names(renderer)
    assert "render_status" in call_names(renderer)


def test_verify_and_trace_without_history_are_safe(tmp_path):
    cli, agent, renderer = make_cli(["/verify", "/trace", "/exit"], tmp_path)

    assert cli.run() == 0
    assert agent.tasks == []
    notices = [
        args[0]
        for name, args, _kwargs in renderer.calls
        if name == "render_notice"
    ]
    assert notices == [
        "No verification evidence yet.",
        "No execution trace yet.",
    ]


def test_normal_task_runs_once_and_saves_last_run(tmp_path):
    cli, agent, renderer = make_cli(["Run the tests", "/exit"], tmp_path)

    assert cli.run() == 0
    assert agent.tasks == ["Run the tests"]
    assert cli.last_run is not None
    assert cli.last_run.task == "Run the tests"
    assert cli.last_run.stop_reason == "completed"
    assert cli.last_run.verification.successful_test_commands == 1
    assert "render_final" in call_names(renderer)


def test_verify_and_trace_project_the_last_run(tmp_path):
    cli, agent, renderer = make_cli(
        ["Run the tests", "/verify", "/trace", "/exit"],
        tmp_path,
    )

    assert cli.run() == 0
    assert agent.tasks == ["Run the tests"]
    assert "render_verification" in call_names(renderer)
    assert "render_trace" in call_names(renderer)


def test_repl_runs_each_task_as_an_independent_agent_call(tmp_path):
    cli, agent, _renderer = make_cli(
        ["First task", "Second task", "/exit"],
        tmp_path,
    )

    assert cli.run() == 0
    assert agent.tasks == ["First task", "Second task"]
    assert cli.last_run is not None
    assert cli.last_run.task == "Second task"


def test_clear_does_not_call_agent_and_renders_header_again(tmp_path):
    cli, agent, renderer = make_cli(["/clear", "/exit"], tmp_path)

    assert cli.run() == 0
    assert agent.tasks == []
    assert call_names(renderer).count("render_header") == 2
    assert "clear" in call_names(renderer)


def test_exit_and_eof_end_cleanly(tmp_path):
    cli, agent, _renderer = make_cli(["/exit"], tmp_path)
    assert cli.run() == 0
    assert agent.tasks == []

    cli, agent, _renderer = make_cli([EOFError()], tmp_path)
    assert cli.run() == 0
    assert agent.tasks == []


def test_keyboard_interrupt_at_prompt_returns_to_input(tmp_path):
    cli, agent, renderer = make_cli([KeyboardInterrupt(), "/exit"], tmp_path)

    assert cli.run() == 0
    assert agent.tasks == []
    assert "stop_thinking" in call_names(renderer)
