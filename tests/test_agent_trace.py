from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from copy import deepcopy

import pytest

from coding_agent.agent import AgentLoop
from coding_agent.model import ModelResponseError
from coding_agent.registry import ToolRegistry
from coding_agent.types import AgentResponse, ToolCall, ToolResult


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


class RepeatingModel:
    def __init__(self, *, usage=None):
        self.call_count = 0
        self.usage = usage or {}

    def complete(self, messages, tools):
        self.call_count += 1
        return AgentResponse(
            tool_calls=[
                ToolCall(
                    id=f"call-{self.call_count}",
                    name="list_files",
                    arguments={"path": "."},
                )
            ],
            usage=self.usage,
        )


class RecordingTools:
    def __init__(self):
        self.executed = []
        self.schemas = []

    def execute(self, call):
        self.executed.append(call)
        return ToolResult(
            call_id=call.id,
            tool_name=call.name,
            ok=True,
            output="ok",
        )


def tool_response(
    call_id: str,
    name: str,
    arguments: dict,
    *,
    usage: dict | None = None,
) -> AgentResponse:
    return AgentResponse(
        tool_calls=[ToolCall(call_id, name, arguments)],
        finish_reason="tool_calls",
        usage=usage or {},
    )


def load_events(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_trace_disabled_keeps_metrics_without_creating_file(tmp_path):
    trace_dir = tmp_path / "traces"
    agent = AgentLoop(
        ScriptedModel([AgentResponse(content="Done.")]),
        ToolRegistry(tmp_path),
        trace_dir=trace_dir,
        trace_enabled=False,
    )

    assert agent.run("Finish") == "Done."
    assert agent.last_trace_path is None
    assert agent.last_metrics["steps"] == 1
    assert agent.last_stop_reason == "completed"
    assert not trace_dir.exists()


def test_final_only_trace_has_complete_event_flow(tmp_path):
    agent = AgentLoop(
        ScriptedModel(
            [
                AgentResponse(
                    content="Done.",
                    finish_reason="stop",
                    usage={
                        "prompt_tokens": 8,
                        "completion_tokens": 2,
                        "total_tokens": 10,
                    },
                )
            ]
        ),
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
    )

    agent.run("Finish")
    events = load_events(agent.last_trace_path)

    assert [event["type"] for event in events] == [
        "session_start",
        "user_task",
        "step_start",
        "assistant_response",
        "session_end",
    ]
    assert [event["seq"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert events[-1]["data"]["stop_reason"] == "completed"
    assert agent.last_metrics["model_calls"] == 1
    assert agent.last_metrics["prompt_tokens"] == 8
    assert agent.last_metrics["completion_tokens"] == 2
    assert agent.last_metrics["total_tokens"] == 10


def test_tool_events_pair_and_success_metrics_are_recorded(tmp_path):
    model = ScriptedModel(
        [
            tool_response(
                "write-1",
                "write_file",
                {"path": "result.txt", "content": "ok"},
            ),
            AgentResponse(content="Written."),
        ]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
    )

    assert agent.run("Write a result") == "Written."
    events = load_events(agent.last_trace_path)
    call_event = next(event for event in events if event["type"] == "tool_call")
    result_event = next(
        event for event in events if event["type"] == "tool_result"
    )

    assert result_event["source_seq"] == call_event["seq"]
    assert result_event["data"]["call_id"] == "write-1"
    assert result_event["data"]["ok"] is True
    assert agent.last_metrics["tool_calls"] == 1
    assert agent.last_metrics["tool_failures"] == 0


def test_failed_tool_and_policy_block_update_metrics(tmp_path):
    model = ScriptedModel(
        [
            tool_response(
                "missing-1",
                "read_file",
                {"path": "missing.py"},
            ),
            tool_response(
                "blocked-1",
                "read_file",
                {"path": ".env"},
            ),
            AgentResponse(content="Handled failures."),
        ]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
    )

    agent.run("Handle failures")
    events = load_events(agent.last_trace_path)
    results = [
        event for event in events if event["type"] == "tool_result"
    ]

    assert [event["data"]["ok"] for event in results] == [False, False]
    assert results[1]["data"]["metadata"]["policy_blocked"] is True
    assert agent.last_metrics["tool_calls"] == 2
    assert agent.last_metrics["tool_failures"] == 2
    assert agent.last_metrics["policy_blocks"] == 1


def test_model_error_recovery_is_traced_and_counted(tmp_path):
    model = ScriptedModel(
        [
            ModelResponseError("malformed"),
            AgentResponse(content="Recovered."),
        ]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
    )

    assert agent.run("Recover") == "Recovered."
    events = load_events(agent.last_trace_path)

    assert any(event["type"] == "model_error" for event in events)
    assert agent.last_metrics["model_errors"] == 1
    assert agent.last_metrics["model_calls"] == 1
    assert agent.last_stop_reason == "completed"


def test_model_error_limit_and_max_steps_have_stop_reasons(tmp_path):
    malformed = AgentLoop(
        ScriptedModel(
            [ModelResponseError("one"), ModelResponseError("two")]
        ),
        ToolRegistry(tmp_path),
        max_consecutive_model_errors=1,
        trace_enabled=False,
    )
    repeating = RepeatingModel()
    limited = AgentLoop(
        repeating,
        ToolRegistry(tmp_path),
        max_steps=2,
        max_repeated_actions=0,
        trace_enabled=False,
    )

    malformed.run("Malformed")
    limited.run("Repeat")

    assert malformed.last_stop_reason == "model_error_limit"
    assert malformed.last_metrics["model_errors"] == 2
    assert limited.last_stop_reason == "max_steps"
    assert limited.last_metrics["steps"] == 2


def test_third_identical_action_stops_before_execution(tmp_path):
    model = RepeatingModel()
    tools = RecordingTools()
    agent = AgentLoop(
        model,
        tools,
        max_steps=5,
        max_repeated_actions=3,
        trace_dir=tmp_path / "traces",
    )

    result = agent.run("Repeat")
    events = load_events(agent.last_trace_path)
    no_progress = next(
        event for event in events if event["type"] == "no_progress"
    )

    assert "repeated 3 consecutive times" in result
    assert model.call_count == 3
    assert len(tools.executed) == 2
    assert no_progress["data"]["repeated_count"] == 3
    assert agent.last_stop_reason == "no_progress"


def test_different_actions_reset_repeated_count(tmp_path):
    responses = [
        tool_response("a-1", "list_files", {"path": "."}),
        tool_response("a-2", "list_files", {"path": "."}),
        tool_response("b-1", "list_files", {"path": "src"}),
        tool_response("a-3", "list_files", {"path": "."}),
        tool_response("a-4", "list_files", {"path": "."}),
        AgentResponse(content="Completed."),
    ]
    tools = RecordingTools()
    agent = AgentLoop(
        ScriptedModel(responses),
        tools,
        max_repeated_actions=3,
        trace_enabled=False,
    )

    assert agent.run("Vary actions") == "Completed."
    assert len(tools.executed) == 5
    assert agent.last_stop_reason == "completed"


def test_test_edit_test_sequence_does_not_trigger_no_progress(tmp_path):
    responses = [
        tool_response("test-1", "run_command", {"command": "pytest"}),
        tool_response(
            "edit-1",
            "edit_file",
            {"path": "a.py", "old_text": "a", "new_text": "b"},
        ),
        tool_response("test-2", "run_command", {"command": "pytest"}),
        AgentResponse(content="Verified."),
    ]
    tools = RecordingTools()
    agent = AgentLoop(
        ScriptedModel(responses),
        tools,
        max_repeated_actions=2,
        trace_enabled=False,
    )

    assert agent.run("Test edit test") == "Verified."
    assert len(tools.executed) == 3
    assert agent.last_stop_reason == "completed"


def test_zero_repeated_action_limit_disables_detection(tmp_path):
    model = RepeatingModel()
    tools = RecordingTools()
    agent = AgentLoop(
        model,
        tools,
        max_steps=3,
        max_repeated_actions=0,
        trace_enabled=False,
    )

    result = agent.run("Repeat without guard")

    assert "maximum step limit" in result
    assert len(tools.executed) == 3
    assert agent.last_stop_reason == "max_steps"


def test_two_runs_create_independent_trace_files_and_sequences(tmp_path):
    model = ScriptedModel(
        [AgentResponse(content="First."), AgentResponse(content="Second.")]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
    )

    assert agent.run("First run") == "First."
    first_path = agent.last_trace_path
    first_events = load_events(first_path)

    assert agent.run("Second run") == "Second."
    second_path = agent.last_trace_path
    second_events = load_events(second_path)

    assert first_path != second_path
    assert first_path.exists() and second_path.exists()
    assert first_events[0]["seq"] == 1
    assert second_events[0]["seq"] == 1
    assert (
        first_events[0]["data"]["session_id"]
        != second_events[0]["data"]["session_id"]
    )


def test_unexpected_model_exception_is_traced_then_reraised(tmp_path):
    agent = AgentLoop(
        ScriptedModel([RuntimeError("provider unavailable")]),
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        agent.run("Fail")

    assert agent.last_stop_reason == "exception"
    events = load_events(agent.last_trace_path)
    assert events[-1]["type"] == "session_end"
    assert events[-1]["data"]["stop_reason"] == "exception"


def test_write_run_final_jsonl_integration_redacts_and_truncates(tmp_path):
    fake_value = "sk-FAKE_TEST_SECRET_1234"
    long_comment = "# " + ("x" * 5000)
    content = (
        f"# {fake_value}\n"
        f"{long_comment}\n"
        "print('TRACE_INTEGRATION_OK')\n"
    )
    model = ScriptedModel(
        [
            tool_response(
                "write-1",
                "write_file",
                {"path": "sample.py", "content": content},
            ),
            tool_response(
                "run-1",
                "run_command",
                {"command": python_command("sample.py")},
            ),
            AgentResponse(content="Implemented and verified."),
        ]
    )
    agent = AgentLoop(
        model,
        ToolRegistry(tmp_path),
        trace_dir=tmp_path / "traces",
    )

    assert agent.run("Create and verify sample.py") == "Implemented and verified."
    raw_trace = agent.last_trace_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw_trace.splitlines()]

    assert fake_value not in raw_trace
    assert "[REDACTED]" in raw_trace
    assert "characters omitted from trace" in raw_trace
    assert [event["seq"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert events[-1]["type"] == "session_end"
    assert events[-1]["data"]["stop_reason"] == "completed"
    assert events[-1]["data"]["metrics"]["tool_calls"] == 2

    calls = {
        event["seq"]: event
        for event in events
        if event["type"] == "tool_call"
    }
    results = [
        event for event in events if event["type"] == "tool_result"
    ]
    assert len(calls) == len(results) == 2
    assert all(event["source_seq"] in calls for event in results)
    assert "TRACE_INTEGRATION_OK" in results[-1]["data"]["output"]
