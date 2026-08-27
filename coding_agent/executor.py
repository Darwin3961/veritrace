from __future__ import annotations

import locale
import os
import signal
import subprocess
import time
from pathlib import Path

from coding_agent.types import ToolResult


class CommandExecutor:
    """Execute shell commands locally inside one fixed workspace."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        default_timeout: int = 30,
        max_output_chars: int = 10_000,
    ):
        root = Path(workspace_root).expanduser().resolve()

        if not root.exists():
            raise ValueError(f"Workspace does not exist: {root}")

        if not root.is_dir():
            raise ValueError(f"Workspace is not a directory: {root}")

        if default_timeout <= 0:
            raise ValueError("default_timeout must be greater than 0")

        if max_output_chars < 100:
            raise ValueError("max_output_chars must be at least 100")

        self.workspace_root = root
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars

    def _truncate(self, text: str) -> tuple[str, bool, int]:
        """
        Preserve both the beginning and end of long command output.

        Returns:
            (possibly truncated text, truncated?, omitted_character_count)
        """
        if len(text) <= self.max_output_chars:
            return text, False, 0

        marker_reserve = 120
        available = max(self.max_output_chars - marker_reserve, 2)
        head_size = available // 2
        tail_size = available - head_size

        omitted = len(text) - head_size - tail_size

        marker = (
            f"\n\n... <{omitted} characters omitted> ...\n\n"
        )

        result = text[:head_size] + marker + text[-tail_size:]

        if len(result) > self.max_output_chars:
            overflow = len(result) - self.max_output_chars
            tail_size = max(1, tail_size - overflow)
            omitted = len(text) - head_size - tail_size
            marker = (
                f"\n\n... <{omitted} characters omitted> ...\n\n"
            )
            result = text[:head_size] + marker + text[-tail_size:]

        return result, True, omitted

    def _output_encodings(self) -> tuple[str, ...]:
        encodings = ["utf-8"]
        preferred = locale.getpreferredencoding(False)

        if os.name == "nt":
            encodings.append("oem")

        if preferred:
            encodings.append(preferred)

        if os.name == "nt":
            encodings.extend(("mbcs", "gb18030"))

        unique: list[str] = []
        seen: set[str] = set()
        for encoding in encodings:
            normalized = encoding.casefold()
            if normalized not in seen:
                unique.append(encoding)
                seen.add(normalized)

        return tuple(unique)

    def _decode_output(
        self,
        value: bytes | str | None,
    ) -> str:
        if value is None:
            return ""

        if isinstance(value, str):
            return value

        for encoding in self._output_encodings():
            try:
                return value.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue

        return value.decode("utf-8", errors="replace")

    def _popen_kwargs(self) -> dict:
        kwargs = {
            "cwd": str(self.workspace_root),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": True,
        }

        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        return kwargs

    def _terminate_process_tree(
        self,
        process: subprocess.Popen,
    ) -> None:
        if process.poll() is not None:
            return

        if os.name == "nt":
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass
        else:
            try:
                os.killpg(
                    os.getpgid(process.pid),
                    signal.SIGKILL,
                )
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass

    def run_command(
        self,
        call_id: str,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ToolResult:
        tool_name = "run_command"

        if not isinstance(command, str) or not command.strip():
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error="command must be a non-empty string",
            )

        effective_timeout = (
            self.default_timeout
            if timeout is None
            else timeout
        )

        if effective_timeout <= 0:
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error="timeout must be greater than 0",
            )

        started = time.monotonic()

        try:
            process = subprocess.Popen(
                command,
                **self._popen_kwargs(),
            )
        except OSError as exc:
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error=f"failed to start command: {exc}",
                metadata={
                    "command": command,
                    "duration_ms": int(
                        (time.monotonic() - started) * 1000
                    ),
                },
            )

        try:
            stdout, stderr = process.communicate(
                timeout=effective_timeout,
            )

            duration_ms = int(
                (time.monotonic() - started) * 1000
            )

            stdout, stdout_truncated, stdout_omitted = (
                self._truncate(self._decode_output(stdout))
            )
            stderr, stderr_truncated, stderr_omitted = (
                self._truncate(self._decode_output(stderr))
            )

            ok = process.returncode == 0

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=ok,
                output=stdout,
                error=None if ok else stderr or (
                    f"command exited with code {process.returncode}"
                ),
                metadata={
                    "command": command,
                    "exit_code": process.returncode,
                    "duration_ms": duration_ms,
                    "timeout": False,
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "stdout_omitted_chars": stdout_omitted,
                    "stderr_omitted_chars": stderr_omitted,
                },
            )

        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)

            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.SubprocessError:
                stdout = b""
                stderr = b""

            duration_ms = int(
                (time.monotonic() - started) * 1000
            )

            stdout, stdout_truncated, stdout_omitted = (
                self._truncate(self._decode_output(stdout))
            )
            stderr, stderr_truncated, stderr_omitted = (
                self._truncate(self._decode_output(stderr))
            )

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=False,
                output=stdout,
                error=(
                    f"command timed out after "
                    f"{effective_timeout} seconds"
                ),
                metadata={
                    "command": command,
                    "exit_code": process.returncode,
                    "duration_ms": duration_ms,
                    "timeout": True,
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "stdout_omitted_chars": stdout_omitted,
                    "stderr_omitted_chars": stderr_omitted,
                },
            )

        except Exception as exc:
            self._terminate_process_tree(process)

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error=f"command execution failed: {exc}",
                metadata={
                    "command": command,
                    "duration_ms": int(
                        (time.monotonic() - started) * 1000
                    ),
                },
            )
