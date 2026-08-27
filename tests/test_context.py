import json

import pytest

from coding_agent.context import ConversationContext
from coding_agent.types import AgentResponse, ToolCall, ToolResult


def test_context_starts_with_only_system_message():
    context = ConversationContext("System instructions")

    assert context.messages == [
        {"role": "system", "content": "System instructions"}
    ]


def test_add_user_message():
    context = ConversationContext("System")

    context.add_user("Fix the tests")

    assert context.messages[-1] == {
        "role": "user",
        "content": "Fix the tests",
    }


def test_messages_returns_deepcopy():
    context = ConversationContext("System")
    messages = context.messages

    messages[0]["content"] = "Changed"
    messages.append({"role": "user", "content": "Injected"})

    assert context.messages == [{"role": "system", "content": "System"}]


def test_add_assistant_text_message():
    context = ConversationContext("System")

    context.add_assistant(AgentResponse(content="Done"))

    assert context.messages[-1] == {
        "role": "assistant",
        "content": "Done",
    }


def test_add_assistant_single_tool_call_serializes_arguments():
    context = ConversationContext("System")
    call = ToolCall(
        id="call-1",
        name="read_file",
        arguments={"path": "main.py"},
    )

    context.add_assistant(AgentResponse(tool_calls=[call]))

    message = context.messages[-1]
    assert message["role"] == "assistant"
    assert message["tool_calls"][0]["id"] == "call-1"
    assert message["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(
        message["tool_calls"][0]["function"]["arguments"]
    ) == {"path": "main.py"}


def test_add_assistant_multiple_tool_calls_preserves_order():
    context = ConversationContext("System")
    calls = [
        ToolCall("call-a", "read_file", {"path": "a.py"}),
        ToolCall("call-b", "read_file", {"path": "b.py"}),
    ]

    context.add_assistant(AgentResponse(tool_calls=calls))

    tool_calls = context.messages[-1]["tool_calls"]
    assert [call["id"] for call in tool_calls] == ["call-a", "call-b"]


def test_unicode_tool_arguments_are_not_damaged():
    context = ConversationContext("System")
    call = ToolCall(
        "call-unicode",
        "write_file",
        {"path": "说明.txt", "content": "你好"},
    )

    context.add_assistant(AgentResponse(tool_calls=[call]))

    raw_arguments = context.messages[-1]["tool_calls"][0]["function"][
        "arguments"
    ]
    assert "说明.txt" in raw_arguments
    assert json.loads(raw_arguments) == call.arguments


@pytest.mark.parametrize(
    "result",
    [
        ToolResult("call-ok", "read_file", True, output="content"),
        ToolResult("call-error", "read_file", False, error="missing"),
    ],
)
def test_tool_results_enter_context_as_json(result):
    context = ConversationContext("System")

    context.add_tool_result(result)

    message = context.messages[-1]
    payload = json.loads(message["content"])
    assert message["role"] == "tool"
    assert message["tool_call_id"] == result.call_id
    assert payload["ok"] is result.ok
    assert payload["error"] == result.error


def test_add_protocol_feedback_uses_user_role():
    context = ConversationContext("System")

    context.add_protocol_feedback("Return valid JSON.")

    assert context.messages[-1] == {
        "role": "user",
        "content": "Return valid JSON.",
    }


@pytest.mark.parametrize("prompt", ["", "   "])
def test_empty_system_prompt_is_rejected(prompt):
    with pytest.raises(ValueError, match="system_prompt must not be empty"):
        ConversationContext(prompt)


@pytest.mark.parametrize("message", ["", "   "])
def test_empty_user_message_is_rejected(message):
    context = ConversationContext("System")

    with pytest.raises(ValueError, match="user message must not be empty"):
        context.add_user(message)
