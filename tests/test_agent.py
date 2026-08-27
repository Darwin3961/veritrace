import json
import os
import shlex
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from coding_agent.agent import AgentLoop
from coding_agent.model import ModelResponseError
from coding_agent.registry import ToolRegistry
from coding_agent.types import AgentResponse, ToolCall


def python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]

    if os.name == "nt":
        return subprocess.list2cmdline(parts)

    return shlex.join(parts)


class ScriptedModel:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
            }
        )
        response = next(self._responses)

        if isinstance(response, Exception):
            raise response

        return response


class RepeatingToolModel:
    def __init__(self):
        self.call_count = 0

    def complete(self, messages, tools):
        self.call_count += 1
        return AgentResponse(
            tool_calls=[
                ToolCall(
                    id=f"call-{self.call_count}",
                    name="list_files",
                    arguments={},
                )
            ]
        )


def test_final_only_task(tmp_path: Path):
    model = ScriptedModel(
        [AgentResponse(content="Task completed.", finish_reason="stop")]
    )
    agent = AgentLoop(model, ToolRegistry(tmp_path))

    result = agent.run("Complete the task")

    assert result == "Task completed."


def test_write_then_final_creates_file(tmp_path: Path):
    model = ScriptedModel(
        [
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "call-write",
                        "write_file",
                        {"path": "created.txt", "content": "created"},
                    )
                ]
            ),
            AgentResponse(content="File created.", finish_reason="stop"),
        ]
    )
    agent = AgentLoop(model, ToolRegistry(tmp_path))

    result = agent.run("Create a file")

    assert result == "File created."
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created"


def test_write_run_then_final_completes_local_loop(tmp_path: Path):
    model = ScriptedModel(
        [
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "call-write",
                        "write_file",
                        {
                            "path": "sample.py",
                            "content": "print('LOOP_WORKS')\n",
                        },
                    )
                ]
            ),
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "call-run",
                        "run_command",
                        {"command": python_command("sample.py")},
                    )
                ]
            ),
            AgentResponse(content="Implemented and verified."),
        ]
    )
    agent = AgentLoop(model, ToolRegistry(tmp_path))

    result = agent.run("Create and run sample.py")

    assert result == "Implemented and verified."
    assert (tmp_path / "sample.py").exists()
    run_history = model.calls[2]["messages"][-1]
    assert run_history["role"] == "tool"
    assert run_history["tool_call_id"] == "call-run"
    assert json.loads(run_history["content"])["output"].strip() == "LOOP_WORKS"


def test_failed_tool_is_observation_and_agent_recovers(tmp_path: Path):
    model = ScriptedModel(
        [
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "call-read",
                        "read_file",
                        {"path": "nonexistent.py"},
                    )
                ]
            ),
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "call-write",
                        "write_file",
                        {"path": "nonexistent.py", "content": "value = 1\n"},
                    )
                ]
            ),
            AgentResponse(content="Recovered."),
        ]
    )
    agent = AgentLoop(model, ToolRegistry(tmp_path))

    result = agent.run("Read or create nonexistent.py")

    assert result == "Recovered."
    assert (tmp_path / "nonexistent.py").exists()
    failed_result = json.loads(model.calls[1]["messages"][-1]["content"])
    assert failed_result["ok"] is False
    assert "file not found" in failed_result["error"]


def test_multiple_tool_calls_in_one_response_are_all_executed(tmp_path: Path):
    model = ScriptedModel(
        [
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "call-a",
                        "write_file",
                        {"path": "a.txt", "content": "A"},
                    ),
                    ToolCall(
                        "call-b",
                        "write_file",
                        {"path": "b.txt", "content": "B"},
                    ),
                ]
            ),
            AgentResponse(content="Both files created."),
        ]
    )
    agent = AgentLoop(model, ToolRegistry(tmp_path))

    result = agent.run("Create two files")

    assert result == "Both files created."
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B"
    messages = model.calls[1]["messages"]
    assert [message["tool_call_id"] for message in messages[-2:]] == [
        "call-a",
        "call-b",
    ]


def test_unknown_tool_error_is_observation(tmp_path: Path):
    model = ScriptedModel(
        [
            AgentResponse(
                tool_calls=[
                    ToolCall("call-unknown", "does_not_exist", {})
                ]
            ),
            AgentResponse(content="Handled unknown tool."),
        ]
    )
    agent = AgentLoop(model, ToolRegistry(tmp_path))

    result = agent.run("Try an unknown tool")

    assert result == "Handled unknown tool."
    payload = json.loads(model.calls[1]["messages"][-1]["content"])
    assert payload["ok"] is False
    assert payload["error"] == "unknown tool: does_not_exist"


def test_max_steps_stops_repeating_tool_calls(tmp_path: Path):
    model = RepeatingToolModel()
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        max_steps=3,
        max_repeated_actions=0,
    )

    result = agent.run("Keep listing")

    assert "maximum step limit (3)" in result
    assert model.call_count == 3


def test_malformed_model_response_can_recover(tmp_path: Path):
    model = ScriptedModel(
        [
            ModelResponseError("invalid arguments"),
            AgentResponse(content="Recovered from protocol error."),
        ]
    )
    agent = AgentLoop(model, ToolRegistry(tmp_path))

    result = agent.run("Recover")

    assert result == "Recovered from protocol error."
    feedback = model.calls[1]["messages"][-1]
    assert feedback["role"] == "user"
    assert "valid tool arguments" in feedback["content"]


def test_repeated_malformed_model_responses_stop_safely(tmp_path: Path):
    model = ScriptedModel(
        [
            ModelResponseError("first"),
            ModelResponseError("second"),
            ModelResponseError("third"),
        ]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        max_consecutive_model_errors=2,
    )

    result = agent.run("Stop after repeated protocol failures")

    assert "repeatedly returned invalid structured responses" in result
    assert "third" in result
    assert len(model.calls) == 3


@pytest.mark.parametrize("task", ["", "   "])
def test_empty_task_is_rejected(tmp_path: Path, task: str):
    agent = AgentLoop(
        ScriptedModel([]),
        ToolRegistry(tmp_path),
    )

    with pytest.raises(ValueError, match="task must be a non-empty string"):
        agent.run(task)


def test_invalid_max_steps_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="max_steps must be greater than 0"):
        AgentLoop(
            ScriptedModel([]),
            ToolRegistry(tmp_path),
            max_steps=0,
        )


def test_invalid_model_error_limit_is_rejected(tmp_path: Path):
    with pytest.raises(
        ValueError,
        match="max_consecutive_model_errors must be >= 0",
    ):
        AgentLoop(
            ScriptedModel([]),
            ToolRegistry(tmp_path),
            max_consecutive_model_errors=-1,
        )


def test_invalid_repeated_action_limit_is_rejected(tmp_path: Path):
    with pytest.raises(
        ValueError,
        match="max_repeated_actions must be >= 0",
    ):
        AgentLoop(
            ScriptedModel([]),
            ToolRegistry(tmp_path),
            max_repeated_actions=-1,
        )


def test_conversation_and_tool_schemas_are_sent_to_model(tmp_path: Path):
    model = ScriptedModel(
        [
            AgentResponse(
                tool_calls=[
                    ToolCall(
                        "call-missing",
                        "read_file",
                        {"path": "missing.py"},
                    )
                ]
            ),
            AgentResponse(content="Observed failure."),
        ]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        system_prompt="Test system prompt",
    )

    agent.run("Inspect missing.py")

    messages = model.calls[1]["messages"]
    assert messages[0] == {
        "role": "system",
        "content": "Test system prompt",
    }
    assert messages[1] == {
        "role": "user",
        "content": "Inspect missing.py",
    }
    assistant = messages[2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call-missing"
    tool_message = messages[3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call-missing"
    assert json.loads(tool_message["content"])["ok"] is False

    schema_names = {
        schema["function"]["name"]
        for schema in model.calls[0]["tools"]
    }
    assert schema_names == {
        "list_files",
        "search_code",
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
    }
