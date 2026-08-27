import json

from coding_agent.events import Event
from coding_agent.types import AgentResponse, ToolCall, ToolResult


def test_tool_call_to_dict():
    call = ToolCall(
        id="call-1",
        name="read_file",
        arguments={"path": "main.py"},
    )

    assert call.to_dict() == {
        "id": "call-1",
        "name": "read_file",
        "arguments": {"path": "main.py"},
    }


def test_successful_tool_result():
    result = ToolResult(
        call_id="call-1",
        tool_name="read_file",
        ok=True,
        output="print('hello')",
        metadata={"path": "main.py"},
    )

    payload = json.loads(result.to_model_content())

    assert payload["ok"] is True
    assert payload["output"] == "print('hello')"
    assert payload["error"] is None


def test_failed_tool_result():
    result = ToolResult(
        call_id="call-2",
        tool_name="read_file",
        ok=False,
        error="file not found",
    )

    payload = json.loads(result.to_model_content())

    assert payload["ok"] is False
    assert payload["error"] == "file not found"


def test_agent_response_with_tool_call():
    call = ToolCall(
        id="call-3",
        name="run_command",
        arguments={"command": "pytest"},
    )

    response = AgentResponse(
        tool_calls=[call],
        finish_reason="tool_calls",
    )

    assert response.has_tool_calls is True
    assert response.tool_calls[0].name == "run_command"


def test_agent_response_without_tool_call():
    response = AgentResponse(
        content="Task completed.",
        finish_reason="stop",
    )

    assert response.has_tool_calls is False


def test_event_creation():
    event = Event.create(
        seq=7,
        event_type="tool_result",
        step=3,
        source_seq=6,
        data={
            "tool": "pytest",
            "ok": False,
            "exit_code": 1,
        },
    )

    assert event.seq == 7
    assert event.type == "tool_result"
    assert event.step == 3
    assert event.source_seq == 6
    assert event.data["exit_code"] == 1
    assert event.timestamp
