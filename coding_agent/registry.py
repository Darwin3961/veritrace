from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from coding_agent.executor import CommandExecutor
from coding_agent.policy import SafetyPolicy
from coding_agent.tools import WorkspaceTools
from coding_agent.types import ToolCall, ToolResult


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files and directories recursively inside "
                "the workspace or a workspace subdirectory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Workspace-relative directory path."
                        ),
                        "default": ".",
                    },
                    "max_entries": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 200,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search UTF-8 text files inside the workspace "
                "for a string and return file:line matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                    },
                    "path": {
                        "type": "string",
                        "default": ".",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "default": False,
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 50,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file inside the workspace, "
                "optionally limited to a line range."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                    },
                    "end_line": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or replace a UTF-8 text file inside "
                "the workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    },
                    "content": {
                        "type": "string",
                    },
                },
                "required": [
                    "path",
                    "content",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace one exact unique text block inside a "
                "workspace file. The old_text must match exactly "
                "once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    },
                    "old_text": {
                        "type": "string",
                    },
                    "new_text": {
                        "type": "string",
                    },
                },
                "required": [
                    "path",
                    "old_text",
                    "new_text",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Execute a shell command locally inside the "
                "workspace and return stdout, error information, "
                "exit code and timeout metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                    },
                    "timeout": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolRegistry:
    """Expose tool schemas and dispatch normalized ToolCalls locally."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        command_timeout: int = 30,
        max_command_output: int = 10_000,
        policy: SafetyPolicy | None = None,
    ):
        self.file_tools = WorkspaceTools(
            workspace_root
        )

        self.command_executor = CommandExecutor(
            workspace_root,
            default_timeout=command_timeout,
            max_output_chars=max_command_output,
        )

        self.policy = (
            policy
            if policy is not None
            else SafetyPolicy(workspace_root)
        )

        self._handlers: dict[
            str,
            Callable[..., ToolResult],
        ] = {
            "list_files": self.file_tools.list_files,
            "search_code": self.file_tools.search_code,
            "read_file": self.file_tools.read_file,
            "write_file": self.file_tools.write_file,
            "edit_file": self.file_tools.edit_file,
            "run_command": (
                self.command_executor.run_command
            ),
        }

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return deepcopy(TOOL_SCHEMAS)

    def execute(
        self,
        call: ToolCall,
    ) -> ToolResult:
        handler = self._handlers.get(call.name)

        if handler is None:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=f"unknown tool: {call.name}",
            )

        decision = self.policy.check(call)

        if not decision.allowed:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=(
                    "policy blocked tool call: "
                    f"{decision.reason}"
                ),
                metadata={
                    "policy_blocked": True,
                    "policy_risk": decision.risk,
                },
            )

        try:
            return handler(
                call.id,
                **call.arguments,
            )
        except TypeError as exc:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=(
                    f"invalid arguments for "
                    f"{call.name}: {exc}"
                ),
            )
        except Exception as exc:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=(
                    f"tool execution failed: {exc}"
                ),
            )
