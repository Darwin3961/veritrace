from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style

from coding_agent.events import Event
from coding_agent.git_utils import GitInspector, GitSummary
from coding_agent.renderer import RichRenderer
from coding_agent.verification import VerificationSummary, summarize_verification


COMMANDS = {
    "/help": "Show available commands",
    "/status": "Show current session status",
    "/trace": "Show last execution trace",
    "/verify": "Show last verification evidence",
    "/clear": "Clear the screen",
    "/exit": "Exit VeriTrace",
}

PROMPT_STYLE = Style.from_dict(
    {
        "completion-menu": "bg:#111820 #d1d5db",
        "completion-menu.completion.current": "bg:#0a7e8c #ffffff",
        "completion-menu.meta": "bg:#111820 #7d8590",
        "completion-menu.meta.completion.current": "bg:#0a7e8c #ffffff",
    }
)


class SlashCommandCompleter(Completer):
    """Complete the small, fixed set of VeriTrace slash commands."""

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        if not text.startswith("/") or any(char.isspace() for char in text):
            return

        for command, description in COMMANDS.items():
            if command.startswith(text):
                yield Completion(
                    command,
                    start_position=-len(text),
                    display_meta=description,
                )


@dataclass(slots=True)
class LastRunState:
    task: str
    result: str
    stop_reason: str | None
    metrics: dict[str, Any]
    events: list[Event]
    verification: VerificationSummary
    trace_path: Path | None
    git_summary: GitSummary | None


class InteractiveCLI:
    """A presentation-only REPL around independent AgentLoop runs."""

    def __init__(
        self,
        *,
        agent,
        renderer: RichRenderer,
        workspace: Path,
        inspect_git: bool = True,
        prompt_session=None,
    ) -> None:
        self.agent = agent
        self.renderer = renderer
        self.workspace = workspace
        self.inspect_git = inspect_git
        self.last_run: LastRunState | None = None
        self.prompt_session = prompt_session or PromptSession(
            history=InMemoryHistory(),
            completer=SlashCommandCompleter(),
            complete_while_typing=True,
            style=PROMPT_STYLE,
        )

    def _prompt(self) -> str:
        prompt = FormattedText(
            [("bold ansicyan", f"{self.renderer.task_symbol} ")]
        )
        return self.prompt_session.prompt(prompt).strip()

    def _run_task(self, task: str) -> None:
        result = self.agent.run(task)
        events = self.agent.last_events
        verification = summarize_verification(events)
        git_summary = (
            GitInspector(self.workspace).inspect()
            if self.inspect_git
            else None
        )
        state = LastRunState(
            task=task,
            result=result,
            stop_reason=self.agent.last_stop_reason,
            metrics=self.agent.last_metrics or {},
            events=events,
            verification=verification,
            trace_path=self.agent.last_trace_path,
            git_summary=git_summary,
        )
        self.last_run = state
        self.renderer.render_final(
            result=state.result,
            metrics=state.metrics,
            stop_reason=state.stop_reason,
            verification=state.verification,
            git_summary=state.git_summary,
            trace_path=state.trace_path,
        )

    def _dispatch(self, command: str) -> bool:
        if command == "/help":
            self.renderer.render_help(COMMANDS)
            return True

        if command == "/clear":
            self.renderer.clear()
            self.renderer.render_header()
            return True

        if command in {"/exit", "/quit"}:
            return False

        state = self.last_run

        if command == "/status":
            self.renderer.render_status(
                task=state.task if state else None,
                stop_reason=state.stop_reason if state else None,
                metrics=state.metrics if state else {},
                verification=state.verification if state else None,
                git_summary=state.git_summary if state else None,
                trace_path=state.trace_path if state else None,
            )
            return True

        if command == "/verify":
            if state is None:
                self.renderer.render_notice("No verification evidence yet.")
            else:
                self.renderer.render_verification(
                    metrics=state.metrics,
                    stop_reason=state.stop_reason,
                    verification=state.verification,
                    trace_path=state.trace_path,
                )
            return True

        if command == "/trace":
            if state is None:
                self.renderer.render_notice("No execution trace yet.")
            else:
                self.renderer.render_trace(state.events, state.trace_path)
            return True

        self.renderer.render_notice(
            f"Unknown command: {command}. Use /help.",
            style="yellow",
        )
        return True

    def run(self) -> int:
        self.renderer.set_interactive_mode(True)
        self.renderer.render_header()

        while True:
            try:
                value = self._prompt()
            except KeyboardInterrupt:
                self.renderer.stop_thinking()
                continue
            except EOFError:
                self.renderer.stop_thinking()
                return 0

            if not value:
                continue

            if value.startswith("/"):
                if not self._dispatch(value.lower()):
                    self.renderer.stop_thinking()
                    return 0
                continue

            self._run_task(value)
