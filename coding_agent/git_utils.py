from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class GitSummary:
    is_repo: bool
    status_short: str = ""
    diff_stat: str = ""
    diff_text: str = ""
    error: str | None = None


class GitInspector:
    """Collect a bounded, read-only summary of one Git workspace."""

    ALLOWED_COMMANDS = {
        ("rev-parse", "--is-inside-work-tree"),
        ("status", "--short"),
        ("diff", "--stat"),
        ("diff", "--no-ext-diff", "--"),
        ("diff", "--cached", "--stat"),
        ("diff", "--cached", "--no-ext-diff", "--"),
    }

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        timeout: int = 10,
        max_diff_chars: int = 12_000,
    ):
        root = Path(workspace_root).expanduser().resolve()

        if not root.exists():
            raise ValueError(f"Workspace does not exist: {root}")

        if not root.is_dir():
            raise ValueError(f"Workspace is not a directory: {root}")

        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")

        if max_diff_chars < 100:
            raise ValueError("max_diff_chars must be at least 100")

        self.workspace_root = root
        self.timeout = timeout
        self.max_diff_chars = max_diff_chars

    def _run(
        self,
        arguments: tuple[str, ...],
    ) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
        if arguments not in self.ALLOWED_COMMANDS:
            return None, "git command is not allowed"

        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=str(self.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                shell=False,
            )
        except FileNotFoundError:
            return None, "git executable is not available"
        except subprocess.TimeoutExpired:
            return None, (
                "git command timed out after "
                f"{self.timeout} seconds"
            )
        except OSError as exc:
            return None, f"git command failed to start: {exc}"

        return completed, None

    def _read(
        self,
        arguments: tuple[str, ...],
    ) -> tuple[str | None, str | None]:
        completed, error = self._run(arguments)

        if error is not None:
            return None, error

        if completed is None:
            return None, "git command returned no result"

        if completed.returncode != 0:
            detail = completed.stderr.strip()

            if not detail:
                detail = f"exit code {completed.returncode}"

            return None, f"git {' '.join(arguments)} failed: {detail}"

        return completed.stdout.rstrip(), None

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_diff_chars:
            return text

        marker_reserve = 100
        available = max(self.max_diff_chars - marker_reserve, 2)
        head = available // 2
        tail = available - head
        omitted = len(text) - head - tail
        marker = f"\n... <{omitted} diff characters omitted> ...\n"
        result = text[:head] + marker + text[-tail:]

        if len(result) > self.max_diff_chars:
            overflow = len(result) - self.max_diff_chars
            tail = max(1, tail - overflow)
            omitted = len(text) - head - tail
            marker = f"\n... <{omitted} diff characters omitted> ...\n"
            result = text[:head] + marker + text[-tail:]

        return result

    def _combine(
        self,
        unstaged: str,
        staged: str,
    ) -> str:
        sections = []

        if unstaged:
            sections.append(f"Unstaged:\n{unstaged}")

        if staged:
            sections.append(f"Staged:\n{staged}")

        return "\n\n".join(sections)

    def inspect(self) -> GitSummary:
        probe, probe_error = self._run(
            ("rev-parse", "--is-inside-work-tree")
        )

        if probe_error is not None:
            return GitSummary(
                is_repo=False,
                error=probe_error,
            )

        if (
            probe is None
            or probe.returncode != 0
            or probe.stdout.strip().lower() != "true"
        ):
            return GitSummary(is_repo=False)

        outputs: dict[str, str] = {}
        commands = {
            "status": ("status", "--short"),
            "unstaged_stat": ("diff", "--stat"),
            "unstaged_diff": ("diff", "--no-ext-diff", "--"),
            "staged_stat": ("diff", "--cached", "--stat"),
            "staged_diff": (
                "diff",
                "--cached",
                "--no-ext-diff",
                "--",
            ),
        }

        for key, arguments in commands.items():
            output, error = self._read(arguments)

            if error is not None:
                return GitSummary(
                    is_repo=True,
                    status_short=outputs.get("status", ""),
                    error=error,
                )

            outputs[key] = output or ""

        diff_stat = self._combine(
            outputs["unstaged_stat"],
            outputs["staged_stat"],
        )
        diff_text = self._combine(
            outputs["unstaged_diff"],
            outputs["staged_diff"],
        )

        return GitSummary(
            is_repo=True,
            status_short=outputs["status"],
            diff_stat=diff_stat,
            diff_text=self._truncate(diff_text),
        )
