from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from coding_agent.events import Event
from coding_agent.git_utils import GitSummary


class RichRenderer:
    """Render sanitized agent events without affecting agent execution."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        show_tool_output: bool = True,
        max_output_chars: int = 1200,
    ):
        if max_output_chars < 100:
            raise ValueError(
                "max_output_chars must be at least 100"
            )

        self.console = console or Console()
        self.show_tool_output = show_tool_output
        self.max_output_chars = max_output_chars

    def _truncate(self, value: object) -> str:
        text = "" if value is None else str(value)

        if len(text) <= self.max_output_chars:
            return text

        marker_reserve = 80
        available = max(
            self.max_output_chars - marker_reserve,
            2,
        )
        head = available // 2
        tail = available - head
        omitted = len(text) - head - tail
        marker = f"\n... <{omitted} characters omitted> ...\n"
        result = text[:head] + marker + text[-tail:]

        if len(result) > self.max_output_chars:
            overflow = len(result) - self.max_output_chars
            tail = max(1, tail - overflow)
            omitted = len(text) - head - tail
            marker = f"\n... <{omitted} characters omitted> ...\n"
            result = text[:head] + marker + text[-tail:]

        return result

    def _tool_call_detail(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        if name in {"list_files", "read_file"}:
            return f"path={arguments.get('path', '.')}"

        if name == "search_code":
            query = self._truncate(arguments.get("query", ""))
            path = arguments.get("path", ".")
            return f"query={query!r} path={path}"

        if name in {"write_file", "edit_file"}:
            return f"path={arguments.get('path', '')}"

        if name == "run_command":
            return f"command={self._truncate(arguments.get('command', ''))}"

        return ""

    def _render_tool_call(self, event: Event) -> None:
        name = str(event.data.get("name", "unknown"))
        arguments = event.data.get("arguments", {})

        if not isinstance(arguments, dict):
            arguments = {}

        detail = self._tool_call_detail(name, arguments)
        line = Text("→ ", style="cyan")
        line.append(name, style="bold cyan")

        if detail:
            line.append("  ")
            line.append(detail)

        self.console.print(line)

    def _render_tool_result(self, event: Event) -> None:
        data = event.data
        name = str(data.get("tool_name", "tool"))
        ok = bool(data.get("ok", False))
        metadata = data.get("metadata", {})

        if not isinstance(metadata, dict):
            metadata = {}

        policy_blocked = bool(metadata.get("policy_blocked", False))
        timed_out = bool(metadata.get("timeout", False))

        if policy_blocked:
            prefix = "⊘"
            status = "blocked"
            style = "bold yellow"
        elif timed_out:
            prefix = "!"
            status = "timeout"
            style = "bold yellow"
        elif ok:
            prefix = "✓"
            status = ""
            style = "bold green"
        else:
            prefix = "✗"
            status = self._truncate(data.get("error", "tool failed"))
            style = "bold red"

        line = Text(f"{prefix} {name}", style=style)

        if status:
            line.append(" — ")
            line.append(status)

        self.console.print(line)

        if (
            self.show_tool_output
            and name == "run_command"
        ):
            output = self._truncate(data.get("output", ""))

            if output:
                self.console.print(
                    Panel(
                        Text(output),
                        title="Command output",
                        border_style="dim",
                    )
                )

    def handle_event(self, event: Event) -> None:
        if event.type == "session_start":
            self.console.print(
                Rule(
                    Text("Coding Agent Session", style="bold cyan")
                )
            )
            return

        if event.type == "user_task":
            self.console.print(
                Panel(
                    Text(str(event.data.get("task", ""))),
                    title="Task",
                    border_style="cyan",
                )
            )
            return

        if event.type == "step_start":
            step = event.step
            self.console.print(
                Text(f"Step {step}", style="bold blue")
            )
            return

        if event.type == "tool_call":
            self._render_tool_call(event)
            return

        if event.type == "tool_result":
            self._render_tool_result(event)
            return

        if event.type == "model_error":
            count = event.data.get("consecutive_errors", 1)
            self.console.print(
                Text(
                    f"Model response error; retrying ({count}).",
                    style="yellow",
                )
            )
            return

        if event.type == "no_progress":
            count = event.data.get("repeated_count", "?")
            self.console.print(
                Text(
                    "No progress detected after "
                    f"{count} repeated tool actions.",
                    style="bold yellow",
                )
            )

    def render_git_summary(self, summary: GitSummary) -> None:
        if not summary.is_repo:
            if summary.error:
                self.console.print(
                    Text(f"Git unavailable: {summary.error}", style="dim")
                )

            return

        if summary.error:
            self.console.print(
                Text(f"Git inspection failed: {summary.error}", style="yellow")
            )
            return

        if not summary.status_short:
            self.console.print(
                Text("Git: no workspace changes", style="green")
            )
            return

        self.console.print(
            Panel(
                Text(summary.status_short),
                title="Git status",
                border_style="magenta",
            )
        )

        if summary.diff_stat:
            self.console.print(
                Panel(
                    Text(summary.diff_stat),
                    title="Git diff stat",
                    border_style="magenta",
                )
            )

        if summary.diff_text:
            self.console.print(
                Panel(
                    Syntax(
                        summary.diff_text,
                        "diff",
                        word_wrap=True,
                    ),
                    title="Git diff",
                    border_style="magenta",
                )
            )
