import os
import shlex
import subprocess
import sys
from pathlib import Path

from coding_agent.registry import ToolRegistry
from coding_agent.types import ToolCall


def python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]

    if os.name == "nt":
        return subprocess.list2cmdline(parts)

    return shlex.join(parts)


def test_schemas_contain_exactly_six_expected_tools(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    names = {
        schema["function"]["name"]
        for schema in registry.schemas
    }

    assert names == {
        "list_files",
        "search_code",
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
    }


def test_schemas_are_returned_as_deepcopy(tmp_path: Path):
    registry = ToolRegistry(tmp_path)
    schemas = registry.schemas
    schemas[0]["function"]["name"] = "changed"

    assert registry.schemas[0]["function"]["name"] == "list_files"


def test_dispatch_write_and_read_file(tmp_path: Path):
    registry = ToolRegistry(tmp_path)
    write_call = ToolCall(
        "call-write",
        "write_file",
        {"path": "note.txt", "content": "hello"},
    )

    write_result = registry.execute(write_call)
    read_result = registry.execute(
        ToolCall("call-read", "read_file", {"path": "note.txt"})
    )

    assert write_result.ok is True
    assert write_result.call_id == "call-write"
    assert read_result.ok is True
    assert read_result.output == "hello"
    assert read_result.call_id == "call-read"


def test_dispatch_edit_file(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall(
            "call-edit",
            "edit_file",
            {
                "path": "module.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
            },
        )
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_dispatch_search_code(tmp_path: Path):
    (tmp_path / "module.py").write_text("target_value = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall("call-search", "search_code", {"query": "target_value"})
    )

    assert result.ok is True
    assert "module.py:1: target_value = 1" in result.output


def test_dispatch_list_files(tmp_path: Path):
    (tmp_path / "visible.txt").write_text("content", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall("call-list", "list_files", {})
    )

    assert result.ok is True
    assert "visible.txt" in result.output


def test_dispatch_run_command(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall(
            "call-command",
            "run_command",
            {"command": python_command("-c", "print('registry works')")},
        )
    )

    assert result.ok is True
    assert result.call_id == "call-command"
    assert "registry works" in result.output


def test_unknown_tool_returns_failed_result(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall("call-unknown", "does_not_exist", {})
    )

    assert result.ok is False
    assert result.call_id == "call-unknown"
    assert result.error == "unknown tool: does_not_exist"


def test_missing_required_argument_returns_failed_result(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall("call-invalid", "read_file", {})
    )

    assert result.ok is False
    assert result.call_id == "call-invalid"
    assert "invalid arguments for read_file" in result.error


def test_extra_argument_returns_failed_result(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall(
            "call-extra",
            "read_file",
            {"path": "missing.txt", "unexpected": True},
        )
    )

    assert result.ok is False
    assert result.call_id == "call-extra"
    assert "invalid arguments for read_file" in result.error
