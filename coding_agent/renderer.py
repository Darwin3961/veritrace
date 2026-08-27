from __future__ import annotations

import difflib
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from coding_agent.events import Event
from coding_agent.git_utils import GitSummary
from coding_agent.verification import VerificationSummary


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
            header.append(branch, style="cyan")

        header.append("  via  ", style="dim")
        header.append(
            f"Python {platform.python_version()}",
            style="dim",
        )
        self.console.print(header)

        if self.model_name:
            model = Text("model      ", style="dim")
            model.append(self._inline(self.model_name), style="dim")
            self.console.print(model)

        if self.workspace_root is not None:
            workspace = Text("workspace  ", style="dim")
            workspace.append(
                sanitize_display_path(self.workspace_root),
                style="dim",
            )
            self.console.print(workspace)

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

        if name == "edit_file":
            call_id = event.data.get("call_id")
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

        if name == "edit_file":
            call_id = data.get("call_id")

            if isinstance(call_id, str):
                pending_edit = self._pending_edits.pop(call_id, None)

        label, _command = self._tool_presentations.pop(
            event.source_seq or -1,
            self._tool_presentation(name, {}),
        )

        if self.show_tool_output and name == "run_command":
            output = data.get("output", "")

            if output:
                self._print_indented(output)

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

            completed = "Command completed" if name == "run_command" else label
            self.console.print(
                Text(
                    f"{self._symbol('success')} {completed}",
                    style="bold green",
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

    def _summary_row(
        self,
        label: str,
        value: str,
        *,
        style: str = "",
    ) -> None:
        line = Text(f"  {label:<18}", style="dim")
        line.append(value, style=style)
        self.console.print(line)

    def _verification_status(
        self,
        successful: int,
        failed: int,
        timed_out: int = 0,
    ) -> tuple[str, str]:
        parts: list[str] = []

        if successful:
            parts.append(f"{successful} succeeded")

        if failed:
            parts.append(f"{failed} failed")

        if timed_out:
            parts.append(f"{timed_out} timed out")

        if not parts:
            return "0 observed", "dim"

        if failed or timed_out:
            return (
                f"{self._symbol('failure')} " + ", ".join(parts),
                "red",
            )

        return (
            f"{self._symbol('success')} " + ", ".join(parts),
            "green",
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

        self.console.print(Text("Workspace changes", style="bold"))

        for line in summary.status_short.splitlines():
            self.console.print(Text(f"  {line}"))

        if summary.diff_stat:
            self.console.print()
            compact_stats = self._compact_diff_stats(summary.diff_stat)

            if compact_stats:
                self.console.print(Text("Tracked diff", style="bold"))

                for compact_stat in compact_stats:
                    self._print_indented(compact_stat, style="dim")
            else:
                self.console.print(Text("Diff summary", style="bold"))
                self._print_indented(summary.diff_stat, style="dim")

        if summary.diff_text:
            self.console.print()
            self.console.print(Text("Diff", style="bold"))
            self.console.print(
                Syntax(
                    summary.diff_text,
                    "diff",
                    word_wrap=True,
                    background_color="default",
                )
            )

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

    def _format_metrics(self, metrics: dict) -> str:
        parts: list[str] = []

        def count(name: str, singular: str, plural: str) -> None:
            if name not in metrics:
                return

            value = int(metrics.get(name, 0) or 0)
            parts.append(f"{value} {singular if value == 1 else plural}")

        count("steps", "step", "steps")
        count("model_calls", "model call", "model calls")
        count("tool_calls", "tool", "tools")

        total_tokens = int(metrics.get("total_tokens", 0) or 0)

        if total_tokens:
            if total_tokens >= 1000:
                parts.append(f"{total_tokens / 1000:.1f}k tokens")
            else:
                parts.append(f"{total_tokens} tokens")

        tool_failures = int(metrics.get("tool_failures", 0) or 0)

        if tool_failures:
            label = "tool failure" if tool_failures == 1 else "tool failures"
            parts.append(f"{tool_failures} {label}")

        policy_blocks = int(metrics.get("policy_blocks", 0) or 0)

        if policy_blocks:
            label = "policy block" if policy_blocks == 1 else "policy blocks"
            parts.append(f"{policy_blocks} {label}")

        duration_ms = metrics.get("duration_ms")

        if duration_ms is not None:
            duration = int(duration_ms or 0)
            parts.append(
                f"{duration / 1000:.1f}s"
                if duration >= 1000
                else f"{duration}ms"
            )

        return " · ".join(parts)

    def _render_verification(self, verification: VerificationSummary) -> None:
        self.console.print(Text("Verification", style="bold"))

        if verification.tests_likely_ran:
            test_status, test_style = self._verification_status(
                verification.successful_test_commands,
                verification.failed_test_commands,
            )
        else:
            test_status = "No explicit test command detected"
            test_style = "dim"

        self._summary_row("Tests", test_status, style=test_style)
        command_status, command_style = self._verification_status(
            verification.successful_commands,
            verification.failed_commands,
            verification.timed_out_commands,
        )
        self._summary_row(
            "Commands",
            command_status,
            style=command_style,
        )
        self._summary_row(
            "File changes",
            str(verification.successful_file_changes),
        )
        self._summary_row(
            "Tool failures",
            str(verification.tool_failures),
            style="red" if verification.tool_failures else "",
        )
        self._summary_row(
            "Policy blocks",
            str(verification.policy_blocks),
            style="yellow" if verification.policy_blocks else "",
        )

        if verification.verification_commands:
            self._summary_row("Commands run", "")

            for command in verification.verification_commands:
                line = Text("    $ ", style="dim")
                line.append(self._truncate(command))
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
        separator = self._symbol("separator") * min(self.console.width, 48)
        self.console.print()
        self.console.print(Text(separator, style="dim"))
        self.console.print()

        if stop_reason == "completed":
            self.console.print(
                Text(
                    f"{self._symbol('success')} Task completed",
                    style="bold green",
                )
            )
        else:
            stop_labels = {
                "max_steps": "maximum step limit",
                "no_progress": "repeated action",
                "model_error_limit": "model response error limit",
                "exception": "unexpected error",
            }
            label = stop_labels.get(stop_reason, stop_reason or "unknown reason")
            self.console.print(
                Text(
                    f"{self._symbol('warning')} Agent stopped: {label}",
                    style="bold yellow",
                )
            )

        if result:
            self.console.print()
            self.console.print(Text(result))

        self.console.print()
        self._render_verification(verification)

        metric_text = self._format_metrics(metrics)

        if metric_text:
            self.console.print()
            self.console.print(Text(metric_text, style="dim"))

        if git_summary is not None:
            self.console.print()
            self.render_git_summary(git_summary)

        if trace_path is not None:
            self.console.print()
            trace = Text("trace  ", style="dim")
            trace.append(sanitize_display_path(trace_path), style="dim")
            self.console.print(trace)
