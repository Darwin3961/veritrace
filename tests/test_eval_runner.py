from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.types import AgentResponse, ToolCall
from demo.scenarios import SCENARIOS, get_scenario
from eval import run_eval


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools):
        return self.responses.pop(0)


def _final_model(text: str = "Done") -> ScriptedModel:
    return ScriptedModel([AgentResponse(content=text, finish_reason="stop")])


def _bugfix_model(final_text: str = "Done") -> ScriptedModel:
    return ScriptedModel([
        AgentResponse(
            tool_calls=[ToolCall(
                id="edit-1",
                name="edit_file",
                arguments={
                    "path": "calc.py",
                    "old_text": "    return a - b\n",
                    "new_text": "    return a + b\n",
                },
            )],
            finish_reason="tool_calls",
        ),
        AgentResponse(content=final_text, finish_reason="stop"),
    ])


def _implement_model() -> ScriptedModel:
    return ScriptedModel([
        AgentResponse(
            tool_calls=[ToolCall(
                id="edit-2",
                name="edit_file",
                arguments={
                    "path": "strings.py",
                    "old_text": "    raise NotImplementedError\n",
                    "new_text": "    return \" \".join(value.split()).title()\n",
                },
            )],
            finish_reason="tool_calls",
        ),
        AgentResponse(content="Implemented", finish_reason="stop"),
    ])


def _multi_file_model() -> ScriptedModel:
    return ScriptedModel([
        AgentResponse(
            tool_calls=[ToolCall(
                id="edit-3",
                name="edit_file",
                arguments={
                    "path": "utils.py",
                    "old_text": "    return f\"{last.strip()}, {first.strip()}\"\n",
                    "new_text": "    return f\"{first.strip()} {last.strip()}\"\n",
                },
            )],
            finish_reason="tool_calls",
        ),
        AgentResponse(content="Fixed", finish_reason="stop"),
    ])


def test_single_scenario_uses_independent_verification(tmp_path: Path):
    result = run_eval.run_scenario(
        get_scenario("bugfix"),
        workspace_parent=tmp_path,
        model_factory=lambda: _bugfix_model("Tests probably pass"),
    )

    assert result.verification_passed is True
    assert result.verification_exit_code == 0
    assert result.agent_result == "Tests probably pass"
    assert result.tool_calls == 1


def test_model_final_claim_does_not_make_failed_verification_pass(tmp_path: Path):
    result = run_eval.run_scenario(
        get_scenario("bugfix"),
        workspace_parent=tmp_path,
        model_factory=lambda: _final_model("All tests pass"),
    )

    assert result.agent_result == "All tests pass"
    assert result.verification_passed is False
    assert result.verification_exit_code != 0


def test_all_scenarios_are_isolated_and_pass(tmp_path: Path):
    factories = {
        "bugfix": _bugfix_model,
        "implement": _implement_model,
        "multi_file": _multi_file_model,
    }
    created_models = iter(factories[scenario.name]() for scenario in SCENARIOS)

    results = run_eval.run_scenarios(
        SCENARIOS,
        keep_workspaces=True,
        workspace_parent=tmp_path,
        model_factory=lambda: next(created_models),
    )

    assert all(result.verification_passed for result in results)
    workspaces = list(tmp_path.iterdir())
    assert len(workspaces) == 3
    assert len({workspace.name for workspace in workspaces}) == 3


def test_one_failure_does_not_stop_remaining_scenarios(tmp_path: Path):
    models = iter([_final_model(), _implement_model(), _multi_file_model()])

    results = run_eval.run_scenarios(
        SCENARIOS,
        workspace_parent=tmp_path,
        model_factory=lambda: next(models),
    )

    assert [result.verification_passed for result in results] == [False, True, True]


def test_model_exception_becomes_failed_result_and_next_scenario_runs(tmp_path: Path):
    def failing_factory():
        raise RuntimeError("model unavailable")

    first = run_eval.run_scenarios(
        [get_scenario("bugfix")],
        workspace_parent=tmp_path,
        model_factory=failing_factory,
    )
    second = run_eval.run_scenarios(
        [get_scenario("implement")],
        workspace_parent=tmp_path,
        model_factory=_implement_model,
    )

    assert first[0].verification_passed is False
    assert "model unavailable" in first[0].agent_result
    assert second[0].verification_passed is True


def test_write_results_is_valid_and_contains_only_result_fields(tmp_path: Path):
    result = run_eval.run_scenario(
        get_scenario("bugfix"),
        workspace_parent=tmp_path,
        model_factory=_bugfix_model,
    )
    output = run_eval.write_results(tmp_path / "results.json", [result])
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload[0]["scenario"] == "bugfix"
    assert payload[0]["verification_passed"] is True
    assert set(payload[0]) == set(run_eval.EvalResult.__dataclass_fields__)
    lowered = output.read_text(encoding="utf-8").lower()
    assert "api_key" not in lowered
    assert "authorization" not in lowered
    assert "reasoning" not in lowered


def test_missing_key_fails_without_constructing_model(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        run_eval.ModelAdapter,
        "from_env",
        lambda: pytest.fail("model must not be constructed"),
    )

    assert run_eval.main(["--scenario", "bugfix"]) == 2
    error = capsys.readouterr().err
    assert "DEEPSEEK_API_KEY is required" in error


def test_cli_single_pass_returns_zero(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-value")
    monkeypatch.setattr(run_eval.ModelAdapter, "from_env", _bugfix_model)
    output = tmp_path / "result.json"

    assert run_eval.main([
        "--scenario",
        "bugfix",
        "--output-json",
        str(output),
    ]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))[0][
        "verification_passed"
    ] is True


def test_cli_all_returns_nonzero_if_any_verification_fails(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-value")
    models = iter([_final_model(), _implement_model(), _multi_file_model()])
    monkeypatch.setattr(run_eval.ModelAdapter, "from_env", lambda: next(models))

    assert run_eval.main(["--all"]) == 1


def test_cli_all_returns_zero_when_every_verification_passes(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-value")
    models = iter([_bugfix_model(), _implement_model(), _multi_file_model()])
    monkeypatch.setattr(run_eval.ModelAdapter, "from_env", lambda: next(models))

    assert run_eval.main(["--all"]) == 0


def test_verification_start_failure_is_safe(monkeypatch, tmp_path: Path):
    def fail_run(*args, **kwargs):
        raise OSError("cannot start")

    monkeypatch.setattr(run_eval.subprocess, "run", fail_run)
    result = run_eval.run_scenario(
        get_scenario("bugfix"),
        workspace_parent=tmp_path,
        model_factory=_bugfix_model,
    )

    assert result.verification_passed is False
    assert result.verification_exit_code is None
