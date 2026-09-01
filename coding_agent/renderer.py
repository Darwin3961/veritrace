from __future__ import annotations

import difflib
import platform
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from coding_agent.events import Event
from coding_agent.git_utils import GitSummary
from coding_agent.session import SessionTrace
from coding_agent.verification import VerificationSummary, _is_test_command


def sanitize_display_path(
    value: str | Path,
    *,
    max_chars: int = 80,
) -> str:
    """Shorten a path for display without exposing the home directory."""
    path = Path(value).expanduser()

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path

    display = str(resolved)

    try:
        relative = resolved.relative_to(Path.home().resolve())
    except (OSError, ValueError):
        pass
    else:
        display = "~" if str(relative) == "." else str(Path("~") / relative)

    if len(display) <= max_chars:
        return display

    separator = "\\" if "\\" in display else "/"
    parts = [part for part in display.replace("\\", "/").split("/") if part]
    tail = separator.join(parts[-3:])
    shortened = f"...{separator}{tail}"

    if len(shortened) <= max_chars:
        return shortened

    return "..." + shortened[-(max_chars - 3):]


@dataclass(frozen=True, slots=True)
class TestCommandPresentation:
    summary: str
    node_id: str | None = None
    error: str | None = None


class RichRenderer:
    """Render sanitized agent events without affecting agent execution."""

    DIFF_STAT_PATTERN = re.compile(
        r"(?P<files>\d+) files? changed"
        r"(?:, (?P<insertions>\d+) insertions?\(\+\))?"
        r"(?:, (?P<deletions>\d+) deletions?\(-\))?"
    )

    SMALL_EDIT_LINES = 12
    LARGE_EDIT_LINES = 40
    PATCH_PREVIEW_CHARS = 1200
    PATCH_LINE_CHARS = 180
    PASSIVE_TOOLS = {"list_files", "search_code", "read_file"}
    SUCCESS_COMMAND_OUTPUT_LINES = 6
    FAILURE_COMMAND_OUTPUT_LINES = 10
    SUCCESS_COMMAND_HEAD_LINES = 2
    SUCCESS_COMMAND_TAIL_LINES = 3
    FAILURE_COMMAND_EDGE_LINES = 4
    COMMAND_DETAILS_LINES = 40
    COMMAND_DETAILS_EDGE_LINES = 20
    COMPACT_DIFF_LINES = 20
    MEDIUM_DIFF_LINES = 60

    PYTEST_COMMAND_PATTERN = re.compile(
        r"(?:^|[\s;&|])[^\s;&|]*pytest(?:\.exe)?\b",
        re.IGNORECASE,
    )
    PYTEST_SUMMARY_PATTERN = re.compile(
        r"^\s*=*\s*(?P<counts>"
        r"\d+\s+(?:passed|failed|skipped|xfailed|xpassed|"
        r"errors?|warnings?|deselected)"
        r"(?:,\s*\d+\s+(?:passed|failed|skipped|xfailed|xpassed|"
        r"errors?|warnings?|deselected))*"
        r")\s+in\s+(?P<duration>\d+(?:\.\d+)?)s\s*=*\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    PYTEST_NODE_PATTERN = re.compile(
        r"^FAILED\s+(?P<node>\S+)",
        re.MULTILINE,
    )
    ASSERTION_ERROR_PATTERN = re.compile(
        r"AssertionError(?:\s*:\s*[^\r\n]*)?",
    )

    TOOL_ACTIONS = {
        "list_files": "List files",
        "search_code": "Search",
        "read_file": "Read",
        "write_file": "Write",
        "edit_file": "Edit",
        "run_command": "Run",
    }

    def __init__(
        self,
        *,
        console: Console | None = None,
        show_tool_output: bool = True,
        max_output_chars: int = 1200,
        workspace_root: str | Path | None = None,
        model_name: str | None = None,
        unicode_symbols: bool | None = None,
        trace_enabled: bool = True,
    ):
        if max_output_chars < 100:
            raise ValueError(
                "max_output_chars must be at least 100"
            )

        self.console = console or Console()
        self.show_tool_output = show_tool_output
        self.max_output_chars = max_output_chars
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else None
        )
        self.model_name = model_name
        self.trace_enabled = trace_enabled
        self.interactive_mode = False
        self._thinking_status = None
        self.unicode_symbols = (
            self._supports_unicode()
            if unicode_symbols is None
            else unicode_symbols
        )
        self._tool_presentations: dict[int, tuple[str, str | None]] = {}
        self._pending_edits: dict[str, tuple[str, str, str]] = {}
        self._pending_passive_tools: dict[str, str] = {}
        self._pending_test_commands: set[str] = set()
        self._test_results: list[bool] = []
        self._failed_test_tool_results = 0
        self._last_test_exit_code: int | None = None

    def _supports_unicode(self) -> bool:
        encoding = getattr(self.console, "encoding", None) or "utf-8"

        try:
            "✦✓●›".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            return False

        return True

    def _symbol(self, name: str) -> str:
        unicode_values = {
            "signature": "✦",
            "bullet": "●",
            "task": "›",
            "success": "✓",
            "failure": "✗",
            "warning": "!",
            "separator": "─",
        }
        ascii_values = {
            "signature": "*",
            "bullet": "*",
            "task": ">",
            "success": "OK",
            "failure": "X",
            "warning": "!",
            "separator": "-",
        }
        values = unicode_values if self.unicode_symbols else ascii_values
        return values[name]

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

    def _inline(self, value: object, *, max_chars: int = 64) -> str:
        text = " ".join(str(value).splitlines())

        if len(text) <= max_chars:
            return text

        return text[: max_chars - 3] + "..."

    def _read_git_branch(self) -> str | None:
        if self.workspace_root is None:
            return None

        try:
            completed = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(self.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        branch = completed.stdout.strip()

        if completed.returncode != 0 or not branch:
            return None

        return self._inline(branch)

    @property
    def task_symbol(self) -> str:
        return self._symbol("task")

    def set_interactive_mode(self, enabled: bool) -> None:
        self.interactive_mode = enabled

    def render_header(self) -> None:
        branch = self._read_git_branch()
        face = "(•◡•)" if self.unicode_symbols else "(o.o)"
        header = Text(" \\|/   ", style="bright_cyan")
        header.append(
            f"{self._symbol('signature')} VeriTrace",
            style="bold bright_white",
        )
        header.append(f"\n{face}  ", style="cyan")
        header.append("Verifiable Coding Agent", style="bright_cyan")
        header.append("\n\n       ")

        if branch:
            header.append(branch, style="dim")
            header.append(" · ", style="dim")

        header.append(
            f"Python {platform.python_version()}",
            style="dim",
        )

        if self.model_name:
            header.append("\n")
            model = Text("       model       ", style="cyan")
            model.append(self._inline(self.model_name), style="bright_white")
            header.append_text(model)

        if self.workspace_root is not None:
            try:
                is_home = (
                    self.workspace_root
                    == Path.home().expanduser().resolve()
                )
            except OSError:
                is_home = False

            workspace_name = (
                "~"
                if is_home
                else self.workspace_root.name
                or sanitize_display_path(self.workspace_root, max_chars=40)
            )
            header.append("\n")
            workspace = Text("       workspace   ", style="cyan")
            workspace.append(
                workspace_name,
                style="bright_white",
            )
            header.append_text(workspace)

        header.append("\n")
        safety = Text("       safety      ", style="cyan")
        safety.append("controlled", style="bright_yellow")
        header.append_text(safety)
        header.append("\n")
        trace = Text("       trace       ", style="cyan")
        trace.append(
            "enabled" if self.trace_enabled else "disabled",
            style="bright_green" if self.trace_enabled else "bright_yellow",
        )
        header.append_text(trace)

        self.console.print(
            Panel(
                header,
                box=(
                    box.ROUNDED
                    if self.unicode_symbols
                    else box.ASCII
                ),
                border_style="dim cyan",
                padding=(0, 1),
                expand=False,
            )
        )

    def start_thinking(self) -> None:
        if self._thinking_status is not None or not self.console.is_terminal:
            return

        label = Text("VeriTrace  thinking…", style="dim cyan")
        self._thinking_status = self.console.status(label, spinner="dots")
        self._thinking_status.start()

    def stop_thinking(self) -> None:
        status = self._thinking_status
        self._thinking_status = None

        if status is not None:
            status.stop()

    def clear(self) -> None:
        self.stop_thinking()
        self.console.clear()

    def render_notice(self, message: str, *, style: str = "dim") -> None:
        self.console.print(Text(message, style=style))

    def render_help(self, commands: dict[str, str]) -> None:
        heading = Text("Commands ", style="bold cyan")
        heading.append(self._symbol("separator") * 22, style="dim")
        self.console.print(heading)

        for command, description in commands.items():
            line = Text(f"  {command:<10}", style="cyan")
            line.append(description, style="dim")
            self.console.print(line)

    def _tool_presentation(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, str | None]:
        action = self.TOOL_ACTIONS.get(name)

        if action is None:
            return f"Tool  {name}", None

        if name == "list_files":
            return f"{action}  {arguments.get('path', '.')}", None

        if name in {"read_file", "write_file", "edit_file"}:
            return f"{action}  {arguments.get('path', '')}".rstrip(), None

        if name == "search_code":
            query = self._truncate(arguments.get("query", ""))
            path = arguments.get("path", ".")
            return f'{action}  "{query}"  in {path}', None

        command = self._truncate(arguments.get("command", ""))
        return action, command

    def _render_tool_call(self, event: Event) -> None:
        name = str(event.data.get("name", "unknown"))
        arguments = event.data.get("arguments", {})

        if not isinstance(arguments, dict):
            arguments = {}

        label, command = self._tool_presentation(name, arguments)
        self._tool_presentations[event.seq] = (label, command)
        call_id = event.data.get("call_id")

        if (
            name == "run_command"
            and isinstance(call_id, str)
            and isinstance(arguments.get("command"), str)
            and _is_test_command(arguments["command"])
        ):
            self._pending_test_commands.add(call_id)

        if name == "edit_file":
            path = arguments.get("path")
            old_text = arguments.get("old_text")
            new_text = arguments.get("new_text")

            if (
                isinstance(call_id, str)
                and isinstance(path, str)
                and isinstance(old_text, str)
                and isinstance(new_text, str)
            ):
                self._pending_edits[call_id] = (
                    path,
                    old_text,
                    new_text,
                )

        if name in self.PASSIVE_TOOLS and isinstance(call_id, str):
            self._pending_passive_tools[call_id] = label
            return

        action_style = (
            "bold bright_cyan" if name == "run_command" else "bold cyan"
        )
        line = Text(
            f"{self._symbol('bullet')} ",
            style="bright_cyan" if name == "run_command" else "cyan",
        )
        line.append(label, style=action_style)
        self.console.print(line)

        if command:
            command_line = Text("  $ ", style="cyan")
            command_line.append(command, style="bright_white")
            self.console.print(command_line)

    def _print_indented(self, value: object, *, style: str = "") -> None:
        text = self._truncate(value)

        for line in text.splitlines():
            rendered = Text("  ", style="dim")
            rendered.append(line, style=style)
            self.console.print(rendered)

    def _print_command_text(self, value: object, *, ok: bool) -> None:
        lines = str(value).splitlines()

        if ok:
            limit = self.SUCCESS_COMMAND_OUTPUT_LINES
            head = self.SUCCESS_COMMAND_HEAD_LINES
            tail = self.SUCCESS_COMMAND_TAIL_LINES
        else:
            limit = self.FAILURE_COMMAND_OUTPUT_LINES
            head = self.FAILURE_COMMAND_EDGE_LINES
            tail = self.FAILURE_COMMAND_EDGE_LINES

        if len(lines) > limit:
            omitted = len(lines) - head - tail
            visible: list[str | None] = lines[:head] + [None] + lines[-tail:]
        else:
            omitted = 0
            visible = list(lines)

        for line in visible:
            if line is None:
                omission = (
                    f"  … {omitted} lines omitted …"
                    if self.unicode_symbols
                    else f"  ... {omitted} lines omitted ..."
                )
                self.console.print(Text(omission, style="dim"))
                continue

            rendered = Text("  ", style="dim")
            rendered.append(
                self._truncate_patch_line(
                    line,
                    self.PATCH_LINE_CHARS,
                ),
                style="white",
            )
            self.console.print(rendered)

    def _parse_test_presentation(
        self,
        command: str | None,
        output: str,
    ) -> TestCommandPresentation | None:
        if (
            not command
            or not _is_test_command(command)
            or self.PYTEST_COMMAND_PATTERN.search(command) is None
        ):
            return None

        summaries = list(self.PYTEST_SUMMARY_PATTERN.finditer(output))

        if not summaries:
            return None

        summary_match = summaries[-1]
        counts = re.split(r",\s*", summary_match.group("counts"))
        duration = summary_match.group("duration")
        summary = " · ".join([*counts, f"{duration}s"])
        node_matches = list(self.PYTEST_NODE_PATTERN.finditer(output))
        node_id = node_matches[-1].group("node") if node_matches else None
        assertion_matches = list(
            self.ASSERTION_ERROR_PATTERN.finditer(output)
        )
        detailed_assertion = next(
            (
                match.group(0).strip()
                for match in assertion_matches
                if ":" in match.group(0)
            ),
            None,
        )
        error = detailed_assertion or (
            assertion_matches[-1].group(0).strip()
            if assertion_matches
            else None
        )
        return TestCommandPresentation(
            summary=summary,
            node_id=node_id,
            error=error,
        )

    def _render_test_presentation(
        self,
        presentation: TestCommandPresentation,
        *,
        ok: bool,
    ) -> None:
        style = "bold bright_green" if ok else "bold bright_red"
        symbol = self._symbol("success" if ok else "failure")
        self.console.print(
            Text(f"{symbol} {presentation.summary}", style=style)
        )

        if presentation.node_id:
            prefix = "  ↳ " if self.unicode_symbols else "  -> "
            node = Text(prefix, style="cyan")
            node.append(presentation.node_id, style="white")
            self.console.print(node)

        if presentation.error:
            self.console.print(
                Text(f"  {presentation.error}", style="red")
            )

    def _safe_detail_text(self, value: object) -> str:
        text = "" if value is None else str(value)

        for pattern in SessionTrace.SECRET_VALUE_PATTERNS:
            text = pattern.sub("[REDACTED]", text)

        if self.workspace_root is not None:
            workspace = str(self.workspace_root)
            replacement = self.workspace_root.name or "."
            text = text.replace(workspace, replacement)
            text = text.replace(
                self.workspace_root.as_posix(),
                replacement,
            )

        try:
            home = Path.home().resolve()
        except OSError:
            return text

        text = text.replace(str(home), "~")
        return text.replace(home.as_posix(), "~")

    def _print_command_details(self, value: object) -> None:
        lines = self._safe_detail_text(value).splitlines()

        if len(lines) > self.COMMAND_DETAILS_LINES:
            edge = self.COMMAND_DETAILS_EDGE_LINES
            omitted = len(lines) - (edge * 2)
            visible: list[str | None] = lines[:edge] + [None] + lines[-edge:]
        else:
            omitted = 0
            visible = list(lines)

        for line in visible:
            if line is None:
                omission = (
                    f"  … {omitted} lines omitted …"
                    if self.unicode_symbols
                    else f"  ... {omitted} lines omitted ..."
                )
                self.console.print(Text(omission, style="dim"))
                continue

            rendered = Text("  ")
            rendered.append(
                self._truncate_patch_line(
                    line,
                    self.PATCH_LINE_CHARS,
                ),
                style="white",
            )
            self.console.print(rendered)

    def _truncate_patch_line(self, value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value

        marker_reserve = 40
        available = max(max_chars - marker_reserve, 2)
        head = available // 2
        tail = available - head
        omitted = len(value) - head - tail
        marker = f"... <{omitted} chars omitted> ..."
        result = value[:head] + marker + value[-tail:]

        if len(result) > max_chars:
            overflow = len(result) - max_chars
            tail = max(1, tail - overflow)
            omitted = len(value) - head - tail
            marker = f"... <{omitted} chars omitted> ..."
            result = value[:head] + marker + value[-tail:]

        return result

    def _edit_changes(
        self,
        old_text: str,
        new_text: str,
    ) -> list[tuple[str, str]]:
        changes: list[tuple[str, str]] = []
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        matcher = difflib.SequenceMatcher(
            a=old_lines,
            b=new_lines,
            autojunk=False,
        )

        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if tag == "equal":
                continue

            if tag in {"replace", "delete"}:
                changes.extend(
                    ("-", line)
                    for line in old_lines[old_start:old_end]
                )

            if tag in {"replace", "insert"}:
                changes.extend(
                    ("+", line)
                    for line in new_lines[new_start:new_end]
                )

        return changes

    def _render_patch_lines(
        self,
        changes: list[tuple[str, str]],
        *,
        max_line_chars: int,
    ) -> None:
        for prefix, source_line in changes:
            style = "dim red" if prefix == "-" else "dim green"
            line = Text(f"  {prefix} ", style=style)
            line.append(
                self._truncate_patch_line(
                    source_line,
                    max_line_chars,
                ),
                style=style,
            )
            self.console.print(line)

    def _render_edit_applied(
        self,
        pending_edit: tuple[str, str, str] | None,
    ) -> None:
        if pending_edit is None:
            self.console.print(
                Text(
                    f"{self._symbol('success')} applied",
                    style="bold green",
                )
            )
            return

        _path, old_text, new_text = pending_edit
        changes = self._edit_changes(old_text, new_text)
        deleted = sum(prefix == "-" for prefix, _line in changes)
        added = sum(prefix == "+" for prefix, _line in changes)
        changed_lines = len(changes)

        if changed_lines > self.LARGE_EDIT_LINES:
            self.console.print(
                Text(
                    f"{self._symbol('success')} applied · "
                    f"{changed_lines} lines changed (+{added} -{deleted})",
                    style="bold green",
                )
            )
            self.console.print(
                Text(
                    "  preview omitted for a large edit",
                    style="dim",
                )
            )
            return

        if changed_lines <= self.SMALL_EDIT_LINES:
            max_line_chars = self.PATCH_LINE_CHARS
            preview_chars = sum(
                len(
                    self._truncate_patch_line(
                        line,
                        max_line_chars,
                    )
                )
                + 4
                for _prefix, line in changes
            )

            if preview_chars > self.PATCH_PREVIEW_CHARS and changes:
                max_line_chars = max(
                    40,
                    self.PATCH_PREVIEW_CHARS // len(changes) - 4,
                )

            self._render_patch_lines(
                changes,
                max_line_chars=max_line_chars,
            )
            suffix = ""
        else:
            visible = changes[:6] + changes[-6:]
            max_line_chars = max(
                60,
                self.PATCH_PREVIEW_CHARS // len(visible) - 8,
            )
            self._render_patch_lines(
                visible[:6],
                max_line_chars=min(max_line_chars, self.PATCH_LINE_CHARS),
            )
            omitted = changed_lines - len(visible)
            omission = (
                f"  … {omitted} changed lines omitted …"
                if self.unicode_symbols
                else f"  ... {omitted} changed lines omitted ..."
            )
            self.console.print(Text(omission, style="dim"))
            self._render_patch_lines(
                visible[6:],
                max_line_chars=min(max_line_chars, self.PATCH_LINE_CHARS),
            )
            suffix = f" · {changed_lines} lines changed"

        self.console.print(
            Text(
                f"{self._symbol('success')} applied{suffix}",
                style="bold green",
            )
        )

    def _render_tool_result(self, event: Event) -> None:
        data = event.data
        name = str(data.get("tool_name", "tool"))
        ok = bool(data.get("ok", False))
        metadata = data.get("metadata", {})

        if not isinstance(metadata, dict):
            metadata = {}

        policy_blocked = bool(metadata.get("policy_blocked", False))
        timed_out = bool(metadata.get("timeout", False))
        pending_edit = None
        passive_label = None
        call_id = data.get("call_id")

        if name == "edit_file":
            if isinstance(call_id, str):
                pending_edit = self._pending_edits.pop(call_id, None)

        if name in self.PASSIVE_TOOLS and isinstance(call_id, str):
            passive_label = self._pending_passive_tools.pop(call_id, None)

        label, command = self._tool_presentations.pop(
            event.source_seq or -1,
            self._tool_presentation(name, {}),
        )
        test_presentation_rendered = False

        if (
            name == "run_command"
            and isinstance(call_id, str)
            and call_id in self._pending_test_commands
        ):
            self._pending_test_commands.discard(call_id)
            exit_code = metadata.get("exit_code")
            self._last_test_exit_code = (
                exit_code if isinstance(exit_code, int) else None
            )
            self._test_results.append(
                ok
                and metadata.get("exit_code") == 0
                and not timed_out
                and not policy_blocked
            )

            if not ok:
                self._failed_test_tool_results += 1

        if name in self.PASSIVE_TOOLS:
            display = passive_label or label

            if policy_blocked:
                self.console.print(
                    Text(
                        f"{self._symbol('warning')} {display}",
                        style="bold yellow",
                    )
                )
                self._print_indented("Blocked by policy", style="yellow")
            elif ok:
                self.console.print(
                    Text(
                        f"{self._symbol('success')} {display}",
                        style="bold green",
                    )
                )
            else:
                self.console.print(
                    Text(
                        f"{self._symbol('failure')} {display}",
                        style="bold red",
                    )
                )
                self._print_indented(
                    data.get("error") or "tool failed",
                    style="red",
                )

            return

        if self.show_tool_output and name == "run_command":
            output = data.get("output", "")
            error = data.get("error")
            fallback_error = (
                f"command exited with code {metadata.get('exit_code')}"
            )
            display_parts: list[str] = []

            if output:
                display_parts.append(str(output))

            if (
                not ok
                and not policy_blocked
                and not timed_out
                and error
                and str(error).strip().lower() != fallback_error
            ):
                display_parts.append(str(error))

            if display_parts:
                display_text = "\n".join(display_parts)
                presentation = (
                    None
                    if policy_blocked or timed_out
                    else self._parse_test_presentation(command, display_text)
                )

                if presentation is None:
                    self._print_command_text(display_text, ok=ok)
                else:
                    command_succeeded = bool(
                        ok and metadata.get("exit_code") == 0
                    )
                    self._render_test_presentation(
                        presentation,
                        ok=command_succeeded,
                    )
                    test_presentation_rendered = True

        if policy_blocked:
            line = Text(
                f"{self._symbol('warning')} Blocked by policy",
                style="bold yellow",
            )
            self.console.print(line)
            error = data.get("error")

            if error:
                self._print_indented(error, style="yellow")

            return

        if timed_out:
            line = Text(
                f"{self._symbol('warning')} Command timed out",
                style="bold yellow",
            )
            self.console.print(line)
            error = data.get("error")

            if error:
                self._print_indented(error, style="yellow")

            return

        if ok:
            if name == "edit_file":
                self._render_edit_applied(pending_edit)
                return

            if name == "run_command":
                if test_presentation_rendered:
                    return

                exit_code = metadata.get("exit_code")
                completed = (
                    f"exit code {exit_code}"
                    if isinstance(exit_code, int)
                    else "Command completed"
                )
            elif name == "write_file":
                completed = "written"
            else:
                completed = label

            self.console.print(
                Text(
                    f"{self._symbol('success')} {completed}",
                    style="bold bright_green",
                )
            )
            return

        if name == "run_command":
            if test_presentation_rendered:
                return

            exit_code = metadata.get("exit_code")
            status = (
                f"Command exited with code {exit_code}"
                if isinstance(exit_code, int)
                else "Command failed"
            )
            self.console.print(
                Text(
                    f"{self._symbol('failure')} {status}",
                    style="bold bright_red",
                )
            )
            return

        self.console.print(
            Text(
                f"{self._symbol('failure')} {label}",
                style="bold red",
            )
        )
        error = data.get("error") or "tool failed"
        self._print_indented(error, style="red")

    def handle_event(self, event: Event) -> None:
        if event.type in {
            "assistant_response",
            "tool_call",
            "tool_result",
            "model_error",
            "no_progress",
            "session_end",
        }:
            self.stop_thinking()

        if event.type == "session_start":
            self.stop_thinking()
            self._tool_presentations.clear()
            self._pending_edits.clear()
            self._pending_passive_tools.clear()
            self._pending_test_commands.clear()
            self._test_results.clear()
            self._failed_test_tool_results = 0
            self._last_test_exit_code = None

            if not self.interactive_mode:
                self.render_header()
            return

        if event.type == "user_task":
            if self.interactive_mode:
                return

            self.console.print()
            task = Text(f"{self._symbol('task')} ", style="bold cyan")
            task.append(str(event.data.get("task", "")))
            self.console.print(task)
            self.console.print()
            return

        if event.type == "step_start":
            self.start_thinking()
            return

        if event.type == "assistant_response":
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
                    f"{self._symbol('warning')} Model response error; "
                    f"retrying ({count}).",
                    style="yellow",
                )
            )
            return

        if event.type == "no_progress":
            count = event.data.get("repeated_count", "?")
            self.console.print(
                Text(
                    f"{self._symbol('warning')} No progress detected after "
                    f"{count} repeated tool actions.",
                    style="bold yellow",
                )
            )

        if event.type == "session_end":
            self.stop_thinking()

    def render_git_summary(
        self,
        summary: GitSummary,
        *,
        include_diff: bool = True,
        heading: str = "Changes",
    ) -> None:
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
            self.console.print(Text("Workspace clean", style="green"))
            return

        self.console.print(Text(heading, style="bold"))

        for line in summary.status_short.splitlines():
            self.console.print(Text(f"  {line}"))

        if summary.diff_stat:
            self.console.print()
            compact_stats = self._compact_diff_stats(summary.diff_stat)

            if compact_stats:
                for compact_stat in compact_stats:
                    self._print_indented(compact_stat, style="dim")
            else:
                self.console.print(Text("Diff summary", style="bold"))
                self._print_indented(summary.diff_stat, style="dim")

        if include_diff and summary.diff_text:
            self.console.print()
            self._render_compact_diff(summary.diff_text)

    def _compact_diff_stats(self, diff_stat: str) -> list[str]:
        compact: list[tuple[str | None, str]] = []
        section: str | None = None

        for raw_line in diff_stat.splitlines():
            line = raw_line.strip()

            if line in {"Unstaged:", "Staged:"}:
                section = line[:-1]
                continue

            match = self.DIFF_STAT_PATTERN.fullmatch(line)

            if match is None:
                continue

            files = int(match.group("files"))
            insertions = int(match.group("insertions") or 0)
            deletions = int(match.group("deletions") or 0)
            file_label = "file" if files == 1 else "files"
            value = f"{files} {file_label} changed · +{insertions} -{deletions}"
            compact.append((section, value))

        if len(compact) == 1:
            return [compact[0][1]]

        return [
            f"{section}: {value}" if section else value
            for section, value in compact
        ]

    def _compact_diff_entries(
        self,
        diff_text: str,
    ) -> list[tuple[str, str, str]]:
        entries: list[tuple[str, str, str]] = []
        path: str | None = None
        in_hunk = False

        for line in diff_text.splitlines():
            if line in {"Unstaged:", "Staged:"}:
                path = None
                in_hunk = False
                continue

            if line.startswith("diff --git "):
                try:
                    parts = shlex.split(line)
                except ValueError:
                    path = None
                else:
                    path = parts[3] if len(parts) >= 4 else None

                    if path and path.startswith("b/"):
                        path = path[2:]

                in_hunk = False
                continue

            if line.startswith("@@"):
                in_hunk = path is not None
                continue

            if not in_hunk or path is None:
                continue

            if line.startswith("+") and not line.startswith("+++"):
                entries.append((path, "+", line[1:]))
            elif line.startswith("-") and not line.startswith("---"):
                entries.append((path, "-", line[1:]))

        return entries

    def _render_compact_diff(self, diff_text: str) -> None:
        entries = self._compact_diff_entries(diff_text)
        changed_lines = len(entries)
        self.console.print(Text("Diff", style="bold"))

        if not entries:
            self._print_indented(
                "preview unavailable in compact mode; "
                "full diff available in plain mode",
                style="dim",
            )
            return

        if changed_lines > self.MEDIUM_DIFF_LINES:
            self._print_indented(
                f"preview omitted · {changed_lines} changed lines",
                style="dim",
            )
            self._print_indented(
                "full diff available in plain mode",
                style="dim",
            )
            return

        if changed_lines <= self.COMPACT_DIFF_LINES:
            visible: list[tuple[str, str, str] | None] = list(entries)
            omitted = 0
        else:
            visible = entries[:10] + [None] + entries[-10:]
            omitted = changed_lines - 20

        current_path: str | None = None

        for entry in visible:
            if entry is None:
                omission = (
                    f"  … {omitted} changed lines omitted …"
                    if self.unicode_symbols
                    else f"  ... {omitted} changed lines omitted ..."
                )
                self.console.print(Text(omission, style="dim"))
                current_path = None
                continue

            entry_path, prefix, source_line = entry

            if entry_path != current_path:
                if current_path is not None:
                    self.console.print()

                self.console.print(Text(f"  {entry_path}", style="dim cyan"))
                current_path = entry_path

            style = "dim red" if prefix == "-" else "dim green"
            line = Text(f"  {prefix} ", style=style)
            line.append(
                self._truncate_patch_line(
                    source_line,
                    self.PATCH_LINE_CHARS,
                ),
                style=style,
            )
            self.console.print(line)

    def _format_metrics(self, metrics: dict) -> str:
        parts: list[str] = []

        def count(name: str, singular: str, plural: str) -> None:
            if name not in metrics:
                return

            value = int(metrics.get(name, 0) or 0)
            parts.append(f"{value} {singular if value == 1 else plural}")

        count("steps", "step", "steps")
        count("tool_calls", "tool", "tools")

        total_tokens = int(metrics.get("total_tokens", 0) or 0)

        if total_tokens:
            if total_tokens >= 1000:
                parts.append(f"{total_tokens / 1000:.1f}k tokens")
            else:
                parts.append(f"{total_tokens} tokens")

        duration_ms = metrics.get("duration_ms")

        if duration_ms is not None:
            duration = int(duration_ms or 0)
            parts.append(
                f"{duration / 1000:.1f}s"
                if duration >= 1000
                else f"{duration}ms"
            )

        return " · ".join(parts)

    def _render_final_answer(self, result: str) -> None:
        self.console.print(Text("Assistant:", style="bold cyan"))
        self.console.print(Markdown(result))

    def _panel_row(
        self,
        body: Text,
        label: str,
        value: str,
        *,
        style: str = "",
    ) -> None:
        body.append(f"{label:<18}", style="white")
        body.append(value, style=style or "white")
        body.append("\n")

    def _is_verified(
        self,
        *,
        stop_reason: str | None,
        verification: VerificationSummary,
    ) -> bool:
        return bool(
            stop_reason == "completed"
            and verification.tests_likely_ran
            and self._test_results
            and self._test_results[-1]
            and self._last_test_exit_code == 0
        )

    def render_verification(
        self,
        *,
        metrics: dict,
        stop_reason: str | None,
        verification: VerificationSummary,
        trace_path: Path | None,
    ) -> None:
        completed = stop_reason == "completed"
        verified = self._is_verified(
            stop_reason=stop_reason,
            verification=verification,
        )
        informational = bool(
            completed
            and not verification.tests_likely_ran
            and verification.successful_file_changes == 0
            and verification.tool_failures == 0
            and verification.policy_blocks == 0
        )
        body = Text()

        if not completed:
            stop_labels = {
                "max_steps": "Maximum step limit reached",
                "no_progress": "Repeated action detected",
                "model_error_limit": "Model response error limit reached",
                "exception": "Unexpected error",
            }
            self._panel_row(
                body,
                "Stop reason",
                stop_labels.get(
                    stop_reason,
                    stop_reason or "Unknown reason",
                ),
                style="yellow",
            )

        if self._test_results:
            final_passed = self._test_results[-1]
            final_status = (
                f"{self._symbol('success')} passed"
                if final_passed
                else f"{self._symbol('failure')} failed"
            )
            self._panel_row(
                body,
                "Final test run",
                final_status,
                style="green" if final_passed else "red",
            )
            if self._last_test_exit_code is not None:
                self._panel_row(
                    body,
                    "Exit code",
                    str(self._last_test_exit_code),
                )
        elif not verification.tests_likely_ran:
            self._panel_row(body, "Tests", "not run")

        if verification.tests_likely_ran:
            history = (
                f"{verification.successful_test_commands} passed · "
                f"{verification.failed_test_commands} failed"
            )
            self._panel_row(body, "Test history", history)

        self._panel_row(
            body,
            "Files changed",
            str(verification.successful_file_changes),
        )
        presented_tool_failures = verification.tool_failures

        if verified:
            presented_tool_failures = max(
                0,
                presented_tool_failures
                - self._failed_test_tool_results,
            )

        if not verified or presented_tool_failures:
            self._panel_row(
                body,
                "Tool failures",
                str(presented_tool_failures),
                style="yellow" if presented_tool_failures else "",
            )
        self._panel_row(
            body,
            "Policy blocks",
            str(verification.policy_blocks),
            style="yellow" if verification.policy_blocks else "",
        )
        self._panel_row(
            body,
            "Trace",
            "recorded" if trace_path is not None else "disabled",
            style="green" if trace_path is not None else "white",
        )

        if verified:
            body.append("\nSupported by execution evidence.", style="green")
        elif (
            completed
            and not verification.tests_likely_ran
            and not informational
        ):
            body.append(
                "\nNo successful test evidence found.",
                style="yellow",
            )

        metric_text = self._format_metrics(metrics)

        if metric_text:
            body.append("\n")
            body.append(metric_text, style="grey70")

        if verified:
            title = Text(
                f" {self._symbol('success')} VERIFIED ",
                style="bold green",
            )
            border_style = "dim green"
        elif completed:
            title = Text(
                f" {self._symbol('success')} Task completed ",
                style="bold green",
            )
            border_style = "dim cyan"
        else:
            title = Text(
                f" {self._symbol('warning')} Agent stopped ",
                style="bold yellow",
            )
            border_style = "dim yellow"

        self.console.print(
            Panel(
                body,
                title=title,
                title_align="left",
                box=(
                    box.ROUNDED
                    if self.unicode_symbols
                    else box.ASCII
                ),
                border_style=border_style,
                padding=(0, 1),
                expand=False,
            )
        )

    def _trace_actions(
        self,
        events: list[Event],
    ) -> list[tuple[int, str, bool, bool]]:
        calls = {
            event.seq: event
            for event in events
            if event.type == "tool_call"
        }
        actions: list[tuple[int, str, bool, bool]] = []

        for event in events:
            if event.type != "tool_result":
                continue

            call = calls.get(event.source_seq or -1)

            if call is None:
                continue

            name = str(call.data.get("name", "tool"))
            arguments = call.data.get("arguments", {})

            if not isinstance(arguments, dict):
                arguments = {}

            label, _command = self._tool_presentation(name, arguments)
            metadata = event.data.get("metadata", {})

            if not isinstance(metadata, dict):
                metadata = {}

            ok = bool(event.data.get("ok", False))
            warning = bool(
                metadata.get("policy_blocked", False)
                or metadata.get("timeout", False)
            )
            actions.append(
                (
                    len(actions) + 1,
                    self._inline(label, max_chars=40),
                    ok,
                    warning,
                )
            )

        return actions

    def render_command_details(self, events: list[Event]) -> None:
        calls = {
            event.seq: event
            for event in events
            if event.type == "tool_call"
        }

        for result in reversed(events):
            if (
                result.type != "tool_result"
                or result.data.get("tool_name") != "run_command"
            ):
                continue

            call = calls.get(result.source_seq or -1)

            if call is None or call.data.get("name") != "run_command":
                continue

            arguments = call.data.get("arguments", {})

            if not isinstance(arguments, dict):
                arguments = {}

            command = self._safe_detail_text(arguments.get("command", ""))
            heading = Text("Command details ", style="bold cyan")
            heading.append(self._symbol("separator") * 16, style="dim")
            self.console.print(heading)
            command_line = Text("  $ ", style="cyan")
            command_line.append(command, style="bright_white")
            self.console.print(command_line)

            output = result.data.get("output", "")
            error = result.data.get("error")
            metadata = result.data.get("metadata", {})

            if not isinstance(metadata, dict):
                metadata = {}

            fallback_error = (
                f"command exited with code {metadata.get('exit_code')}"
            )
            parts: list[str] = []

            if output:
                parts.append(str(output))

            if (
                error
                and str(error).strip().lower() != fallback_error
            ):
                parts.append(str(error))

            if parts:
                self.console.print()
                self._print_command_details("\n".join(parts))

            timed_out = bool(metadata.get("timeout", False))
            policy_blocked = bool(metadata.get("policy_blocked", False))
            ok = bool(result.data.get("ok", False))
            exit_code = metadata.get("exit_code")

            if timed_out:
                status = Text(
                    f"{self._symbol('warning')} command timed out",
                    style="bold yellow",
                )
            elif policy_blocked:
                status = Text(
                    f"{self._symbol('warning')} blocked by policy",
                    style="bold yellow",
                )
            elif ok:
                detail = (
                    f"exit code {exit_code}"
                    if isinstance(exit_code, int)
                    else "command completed"
                )
                status = Text(
                    f"{self._symbol('success')} {detail}",
                    style="bold bright_green",
                )
            else:
                detail = (
                    f"exit code {exit_code}"
                    if isinstance(exit_code, int)
                    else "command failed"
                )
                status = Text(
                    f"{self._symbol('failure')} {detail}",
                    style="bold bright_red",
                )

            self.console.print(status)
            return

        self.render_notice("No command details available yet.")

    def render_trace(
        self,
        events: list[Event],
        trace_path: Path | None,
    ) -> None:
        actions = self._trace_actions(events)

        if not actions:
            self.render_notice("No execution trace yet.")
            return

        heading = Text("Trace ", style="bold cyan")
        heading.append(self._symbol("separator") * 24, style="dim")
        self.console.print(heading)
        visible = actions[-8:]
        omitted = len(actions) - len(visible)

        if omitted:
            self.console.print(Text(f"  … {omitted} earlier actions …", style="dim"))

        for number, label, ok, warning in visible:
            symbol = (
                self._symbol("warning")
                if warning
                else self._symbol("success") if ok else self._symbol("failure")
            )
            style = "yellow" if warning else "green" if ok else "red"
            line = Text(f"{number:02d}  ", style="dim")
            line.append(f"{label:<42}", style="cyan")
            line.append(symbol, style=style)
            self.console.print(line)

        edits = sum(
            1
            for event in events
            if event.type == "tool_call"
            and event.data.get("name") in {"edit_file", "write_file"}
        )
        commands = sum(
            1
            for event in events
            if event.type == "tool_call"
            and event.data.get("name") == "run_command"
        )
        self.console.print()
        self.console.print(
            Text(
                f"{len(actions)} actions · {edits} edits · {commands} commands",
                style="dim",
            )
        )

        if trace_path is not None:
            trace = Text("trace  ", style="dim")
            trace.append(sanitize_display_path(trace_path), style="dim")
            self.console.print(trace)

    def render_status(
        self,
        *,
        task: str | None,
        stop_reason: str | None,
        metrics: dict,
        verification: VerificationSummary | None,
        git_summary: GitSummary | None,
        trace_path: Path | None,
    ) -> None:
        heading = Text("Status ", style="bold cyan")
        heading.append(self._symbol("separator") * 23, style="dim")
        self.console.print(heading)
        workspace = self.workspace_root.name if self.workspace_root else "-"
        values = [
            ("workspace", workspace),
            ("model", self.model_name or "-"),
            ("last task", stop_reason or ("not run" if task is None else "unknown")),
            ("actions", str(int(metrics.get("tool_calls", 0) or 0))),
        ]

        if git_summary is not None and git_summary.status_short:
            files_changed = len(git_summary.status_short.splitlines())
        elif verification is not None:
            files_changed = verification.successful_file_changes
        else:
            files_changed = 0

        commands = (
            verification.successful_commands + verification.failed_commands
            if verification is not None
            else 0
        )
        verified = bool(
            verification is not None
            and self._is_verified(
                stop_reason=stop_reason,
                verification=verification,
            )
        )
        verification_label = (
            "verified"
            if verified
            else "not verified" if task is not None else "not run"
        )
        values.extend(
            [
                ("files changed", str(files_changed)),
                ("commands", str(commands)),
                ("verification", verification_label),
                ("trace", "saved" if trace_path is not None else "disabled"),
            ]
        )

        for label, value in values:
            line = Text(f"  {label:<15}", style="dim")
            line.append(value)
            self.console.print(line)

    def render_final(
        self,
        *,
        result: str,
        metrics: dict,
        stop_reason: str | None,
        verification: VerificationSummary,
        git_summary: GitSummary | None,
        trace_path: Path | None,
    ) -> None:
        self.stop_thinking()
        self.console.print()

        if result:
            self._render_final_answer(result)

        self.console.print()
        self.render_verification(
            metrics=metrics,
            stop_reason=stop_reason,
            verification=verification,
            trace_path=trace_path,
        )

        if (
            git_summary is not None
            and verification.successful_file_changes > 0
        ):
            self.console.print()
            self.render_git_summary(
                git_summary,
                include_diff=False,
                heading="Workspace changes",
            )

        if trace_path is not None:
            self.console.print()
            trace = Text(
                "Trace  " if self.interactive_mode else "trace  ",
                style="dim",
            )

            if self.interactive_mode:
                trace.append("recorded", style="green")
                trace.append(" · ", style="dim")
                trace.append("/trace", style="cyan")
            else:
                trace.append(sanitize_display_path(trace_path), style="dim")

            self.console.print(trace)
