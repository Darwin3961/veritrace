from __future__ import annotations

import json

import pytest

from coding_agent.session import SessionTrace


def read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_disabled_trace_keeps_events_and_metrics_without_file(tmp_path):
    trace = SessionTrace(tmp_path / "traces", enabled=False)

    event = trace.emit("session_start", data={"session_id": trace.session_id})
    trace.record_step()

    assert trace.path is None
    assert event.seq == 1
    assert [item.type for item in trace.events] == ["session_start"]
    assert trace.metrics()["steps"] == 1
    assert not (tmp_path / "traces").exists()


def test_enabled_trace_appends_one_json_line_per_emit(tmp_path):
    trace = SessionTrace(tmp_path / "traces")

    trace.emit("first")
    assert len(trace.path.read_text(encoding="utf-8").splitlines()) == 1

    trace.emit("second")
    assert len(trace.path.read_text(encoding="utf-8").splitlines()) == 2


def test_sequence_starts_at_one_and_strictly_increases(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    events = [trace.emit(f"event-{index}") for index in range(3)]

    assert [event.seq for event in events] == [1, 2, 3]


def test_jsonl_lines_are_valid_and_complete(tmp_path):
    trace = SessionTrace(tmp_path / "traces")

    trace.emit(
        "tool_call",
        step=2,
        data={"call_id": "call-1", "name": "read_file"},
    )

    payload = read_jsonl(trace.path)[0]
    assert payload["seq"] == 1
    assert payload["type"] == "tool_call"
    assert payload["timestamp"]
    assert payload["step"] == 2
    assert payload["source_seq"] is None
    assert payload["data"]["call_id"] == "call-1"


def test_tool_result_can_reference_tool_call_sequence(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    call_event = trace.emit("tool_call", step=1)
    result_event = trace.emit(
        "tool_result",
        step=1,
        source_seq=call_event.seq,
    )

    assert result_event.source_seq == call_event.seq


def test_secret_key_is_redacted(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    event = trace.emit("test", data={"api_key": "fake-value"})

    assert event.data["api_key"] == "[REDACTED]"


def test_nested_secret_key_is_redacted(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    event = trace.emit(
        "test",
        data={"request": {"headers": {"access_token": "fake-value"}}},
    )

    assert event.data["request"]["headers"]["access_token"] == "[REDACTED]"


def test_secret_value_pattern_is_redacted(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)
    fake_value = "sk-FAKE_TEST_VALUE"

    event = trace.emit("test", data={"message": f"value={fake_value}"})

    assert fake_value not in event.data["message"]
    assert "[REDACTED]" in event.data["message"]


def test_bearer_value_is_redacted(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)
    fake_value = "Bearer FAKE.TEST.VALUE"

    event = trace.emit("test", data={"message": fake_value})

    assert fake_value not in event.data["message"]
    assert event.data["message"] == "[REDACTED]"


@pytest.mark.parametrize(
    "value",
    [
        "API_KEY=fake-value",
        "token: fake-value",
        '"password": "fake-value"',
        "Authorization='fake-value'",
    ],
)
def test_secret_assignment_value_is_redacted(tmp_path, value):
    trace = SessionTrace(tmp_path, enabled=False)

    event = trace.emit("test", data={"message": value})

    assert "fake-value" not in event.data["message"]
    assert "[REDACTED]" in event.data["message"]


def test_ordinary_code_string_is_preserved(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)
    code = "def parse_token(value):\n    return value.strip()\n"

    event = trace.emit("code", data={"output": code})

    assert event.data["output"] == code


def test_long_output_is_truncated_for_preview(tmp_path):
    trace = SessionTrace(
        tmp_path,
        enabled=False,
        max_preview_chars=100,
    )
    output = "HEAD" + ("x" * 500) + "TAIL"

    event = trace.emit("tool_result", data={"output": output})
    preview = event.data["output"]

    assert "characters omitted from trace" in preview
    assert preview.startswith("HEAD")
    assert preview.endswith("TAIL")
    assert len(preview) <= 100


def test_unicode_jsonl_round_trip(tmp_path):
    trace = SessionTrace(tmp_path / "traces")

    trace.emit("message", data={"content": "测试成功"})

    assert read_jsonl(trace.path)[0]["data"]["content"] == "测试成功"


def test_events_property_returns_deepcopy(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)
    trace.emit("event", data={"nested": {"value": 1}})

    exported = trace.events
    exported[0].data["nested"]["value"] = 2

    assert trace.events[0].data["nested"]["value"] == 1


def test_record_step(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    trace.record_step()
    trace.record_step()

    assert trace.metrics()["steps"] == 2


def test_record_model_call_uses_standard_token_names(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    trace.record_model_call(
        {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        }
    )

    metrics = trace.metrics()
    assert metrics["model_calls"] == 1
    assert metrics["prompt_tokens"] == 10
    assert metrics["completion_tokens"] == 4
    assert metrics["total_tokens"] == 14


def test_record_model_call_supports_input_output_fallback(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    trace.record_model_call(
        {
            "input_tokens": 6,
            "output_tokens": 3,
        }
    )

    metrics = trace.metrics()
    assert metrics["prompt_tokens"] == 6
    assert metrics["completion_tokens"] == 3
    assert metrics["total_tokens"] == 9


def test_record_model_error(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    trace.record_model_error()

    assert trace.metrics()["model_errors"] == 1


@pytest.mark.parametrize(
    ("ok", "expected_failures"),
    [(True, 0), (False, 1)],
)
def test_record_tool_result_counts_success_and_failure(
    tmp_path,
    ok,
    expected_failures,
):
    trace = SessionTrace(tmp_path, enabled=False)

    trace.record_tool_result(ok=ok)

    metrics = trace.metrics()
    assert metrics["tool_calls"] == 1
    assert metrics["tool_failures"] == expected_failures


def test_record_tool_result_counts_policy_blocks(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    trace.record_tool_result(ok=False, policy_blocked=True)

    metrics = trace.metrics()
    assert metrics["tool_calls"] == 1
    assert metrics["tool_failures"] == 1
    assert metrics["policy_blocks"] == 1


def test_duration_is_nonnegative(tmp_path):
    trace = SessionTrace(tmp_path, enabled=False)

    assert trace.metrics()["duration_ms"] >= 0


def test_invalid_preview_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least 100"):
        SessionTrace(tmp_path, max_preview_chars=99)
