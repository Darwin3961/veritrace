from __future__ import annotations

import difflib
import platform
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from coding_agent.events import Event
from coding_agent.git_utils import GitSummary
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
    COMMAND_OUTPUT_LINES = 12
    COMMAND_OUTPUT_EDGE_LINES = 5
    FINAL_ANSWER_LINES = 6
    COMPACT_DIFF_LINES = 20
    MEDIUM_DIFF_LINES = 60

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

    def _supports_unicode(self) -> bool:
        encoding = getattr(self.console, "encoding", None) or "utf-8"

        try:
            "✓●›".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            return False

        return True

    def _symbol(self, name: str) -> str:
        unicode_values = {
            "bullet": "●",
            "task": "›",
            "success": "✓",
            "failure": "✗",
            "warning": "!",
            "separator": "─",
        }
        ascii_values = {
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

    def _render_header(self) -> None:
        branch = self._read_git_branch()
        header = Text("coding-agent", style="bold cyan")

        if branch:
            header.append("  on  ", style="dim")
            header.append(branch, style="dim cyan")

        header.append("  via  ", style="dim")
        header.append(
            f"Python {platform.python_version()}",
            style="dim",
        )

        if self.model_name:
            header.append("\n")
            model = Text("model      ", style="dim")
            model.append(self._inline(self.model_name), style="dim")
            header.append_text(model)

        if self.workspace_root is not None:
            header.append("\n")
            workspace = Text("workspace  ", style="dim")
            workspace.append(
                sanitize_display_path(self.workspace_root),
                style="dim",
            )
            header.append_text(workspace)

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

        line = Text(f"{self._symbol('bullet')} ", style="cyan")
        line.append(label, style="bold cyan")
        self.console.print(line)

        if command:
            command_line = Text("  $ ", style="dim")
            command_line.append(command)
            self.console.print(command_line)

    def _print_indented(self, value: object, *, style: str = "") -> None:
        text = self._truncate(value)

        for line in text.splitlines():
            rendered = Text("  ", style="dim")
            rendered.append(line, style=style)
            self.console.print(rendered)

    def _print_command_text(self, value: object) -> None:
        lines = str(value).splitlines()

        if len(lines) > self.COMMAND_OUTPUT_LINES:
            omitted = len(lines) - (self.COMMAND_OUTPUT_EDGE_LINES * 2)
            visible: list[str | None] = (
                lines[: self.COMMAND_OUTPUT_EDGE_LINES]
                + [None]
                + lines[-self.COMMAND_OUTPUT_EDGE_LINES :]
            )
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
                )
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

        label, _command = self._tool_presentations.pop(
            event.source_seq or -1,
            self._tool_presentation(name, {}),
        )

        if (
            name == "run_command"
            and isinstance(call_id, str)
            and call_id in self._pending_test_commands
        ):
            self._pending_test_commands.discard(call_id)
            self._test_results.append(
                ok
                and metadata.get("exit_code") == 0
                and not timed_out
                and not policy_blocked
            )

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
                self._print_command_text("\n".join(display_parts))

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
                completed = "Command completed"
            elif name == "write_file":
                completed = "written"
            else:
                completed = label

            self.console.print(
                Text(
                    f"{self._symbol('success')} {completed}",
                    style="bold green",
                )
            )
            return

        if name == "run_command":
            exit_code = metadata.get("exit_code")
            status = (
                f"Command exited with code {exit_code}"
                if isinstance(exit_code, int)
                else "Command failed"
            )
            self.console.print(
                Text(
                    f"{self._symbol('failure')} {status}",
                    style="bold red",
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
        if event.type == "session_start":
            self._tool_presentations.clear()
            self._pending_edits.clear()
            self._pending_passive_tools.clear()
            self._pending_test_commands.clear()
            self._test_results.clear()
            self._render_header()
            return

        if event.type == "user_task":
            self.console.print()
            task = Text(f"{self._symbol('task')} ", style="bold cyan")
            task.append(str(event.data.get("task", "")))
            self.console.print(task)
            self.console.print()
            return

        if event.type == "step_start":
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
            self.console.print(Text("Workspace clean", style="green"))
            return

        self.console.print(Text("Changes", style="bold"))

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

        if summary.diff_text:
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
        lines = result.splitlines()

        if len(lines) <= self.FINAL_ANSWER_LINES:
            visible = lines
        else:
            visible = lines[: self.FINAL_ANSWER_LINES]
            visible.append(
                "… additional response lines omitted in presentation …"
                if self.unicode_symbols
                else "... additional response lines omitted in "
                "presentation ..."
            )

        self.console.print(Text("\n".join(visible)))

    def _panel_row(
        self,
        body: Text,
        label: str,
        value: str,
        *,
        style: str = "",
    ) -> None:
        body.append(f"{label:<18}", style="dim")
        body.append(value, style=style)
        body.append("\n")

    def _render_final_panel(
        self,
        *,
        metrics: dict,
        stop_reason: str | None,
        verification: VerificationSummary,
    ) -> None:
        completed = stop_reason == "completed"
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
        elif not verification.tests_likely_ran:
            self._panel_row(body, "Tests", "not run", style="dim")

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
        self._panel_row(
            body,
            "Tool failures",
            str(verification.tool_failures),
            style="yellow" if verification.tool_failures else "",
        )
        self._panel_row(
            body,
            "Policy blocks",
            str(verification.policy_blocks),
            style="yellow" if verification.policy_blocks else "",
        )

        metric_text = self._format_metrics(metrics)

        if metric_text:
            body.append("\n")
            body.append(metric_text, style="dim")

        if completed:
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
        self.console.print()

        if result:
            self._render_final_answer(result)

        self.console.print()
        self._render_final_panel(
            metrics=metrics,
            stop_reason=stop_reason,
            verification=verification,
        )

        if git_summary is not None:
            self.console.print()
            self.render_git_summary(git_summary)

        if trace_path is not None:
            self.console.print()
            trace = Text("trace  ", style="dim")
            trace.append(sanitize_display_path(trace_path), style="dim")
            self.console.print(trace)
