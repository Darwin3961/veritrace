from types import SimpleNamespace

import pytest

from coding_agent.model import ModelAdapter, ModelResponseError


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


def make_tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=arguments,
        ),
    )


def make_response(
    *,
    content=None,
    tool_calls=None,
    finish_reason="stop",
    usage=None,
):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(
        message=message,
        finish_reason=finish_reason,
    )
    return SimpleNamespace(
        choices=[choice],
        usage=usage,
    )


def test_normal_text_response_is_normalized():
    client = FakeClient(
        make_response(content="Task completed.", finish_reason="stop")
    )
    adapter = ModelAdapter(client=client)

    response = adapter.complete([{"role": "user", "content": "task"}], [])

    assert response.content == "Task completed."
    assert response.has_tool_calls is False
    assert response.finish_reason == "stop"


def test_single_tool_call_is_normalized():
    raw_call = make_tool_call(
        "call-1",
        "read_file",
        '{"path":"main.py"}',
    )
    client = FakeClient(
        make_response(tool_calls=[raw_call], finish_reason="tool_calls")
    )
    adapter = ModelAdapter(client=client)

    response = adapter.complete([], [])

    assert response.has_tool_calls is True
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "main.py"}


def test_multiple_tool_calls_preserve_order_and_ids():
    raw_calls = [
        make_tool_call("call-a", "read_file", '{"path":"a.py"}'),
        make_tool_call("call-b", "read_file", '{"path":"b.py"}'),
    ]
    client = FakeClient(make_response(tool_calls=raw_calls))
    adapter = ModelAdapter(client=client)

    response = adapter.complete([], [])

    assert [call.id for call in response.tool_calls] == ["call-a", "call-b"]
    assert [call.arguments["path"] for call in response.tool_calls] == [
        "a.py",
        "b.py",
    ]


def test_malformed_tool_arguments_raise_model_response_error():
    raw_call = make_tool_call("call-1", "read_file", "{bad json")
    adapter = ModelAdapter(
        client=FakeClient(make_response(tool_calls=[raw_call]))
    )

    with pytest.raises(ModelResponseError, match="Invalid JSON arguments"):
        adapter.complete([], [])


def test_non_object_tool_arguments_raise_model_response_error():
    raw_call = make_tool_call("call-1", "read_file", '["main.py"]')
    adapter = ModelAdapter(
        client=FakeClient(make_response(tool_calls=[raw_call]))
    )

    with pytest.raises(ModelResponseError, match="must decode to a JSON object"):
        adapter.complete([], [])


def test_missing_tool_name_raises_model_response_error():
    raw_call = make_tool_call("call-1", None, "{}")
    adapter = ModelAdapter(
        client=FakeClient(make_response(tool_calls=[raw_call]))
    )

    with pytest.raises(ModelResponseError, match="without a name"):
        adapter.complete([], [])


def test_response_without_choices_raises_model_response_error():
    adapter = ModelAdapter(
        client=FakeClient(SimpleNamespace(choices=[], usage=None))
    )

    with pytest.raises(ModelResponseError, match="contains no choices"):
        adapter.complete([], [])


def test_usage_is_converted_to_plain_dict():
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=4,
        total_tokens=14,
    )
    adapter = ModelAdapter(
        client=FakeClient(make_response(content="done", usage=usage))
    )

    response = adapter.complete([], [])

    assert response.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }


def test_deepseek_request_disables_thinking():
    client = FakeClient(make_response(content="done"))
    adapter = ModelAdapter(
        client=client,
        base_url="https://api.deepseek.com",
        disable_thinking=True,
    )

    adapter.complete([], [])

    assert client.completions.requests[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


def test_non_deepseek_request_has_no_thinking_parameter():
    client = FakeClient(make_response(content="done"))
    adapter = ModelAdapter(
        client=client,
        base_url="https://example.com/v1",
        disable_thinking=True,
    )

    adapter.complete([], [])

    assert "extra_body" not in client.completions.requests[0]


def test_injected_client_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client = FakeClient(make_response(content="done"))

    adapter = ModelAdapter(client=client)

    assert adapter.client is client
