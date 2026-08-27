from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from coding_agent.types import ToolResult


class WorkspaceTools:
    """
    Local file tools restricted to a single workspace.

    All paths supplied by the model are interpreted relative to workspace_root.
    File tools never intentionally access files outside that workspace.
    """

    IGNORED_DIRS = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        "dist",
        "build",
    }

    MAX_SEARCH_FILE_SIZE = 1_000_000

    def __init__(self, workspace_root: str | Path):
        root = Path(workspace_root).expanduser().resolve()

        if not root.exists():
            raise ValueError(f"Workspace does not exist: {root}")

        if not root.is_dir():
            raise ValueError(f"Workspace is not a directory: {root}")

        self.workspace_root = root

    def _error(
        self,
        call_id: str,
        tool_name: str,
        message: str,
        **metadata,
    ) -> ToolResult:
        return ToolResult(
            call_id=call_id,
            tool_name=tool_name,
            ok=False,
            error=message,
            metadata=metadata,
        )

    def _resolve_path(
        self,
        relative_path: str,
        *,
        allow_missing: bool = False,
    ) -> Path:
        if not isinstance(relative_path, str):
            raise ValueError("path must be a string")

        if "\x00" in relative_path:
            raise ValueError("path contains a null byte")

        supplied = Path(relative_path)

        if supplied.is_absolute():
            raise ValueError("absolute paths are not allowed")

        candidate = (self.workspace_root / supplied).resolve(strict=False)

        try:
            relative = candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("path escapes the workspace") from exc

        if ".git" in relative.parts:
            raise ValueError("access to .git is not allowed")

        if not allow_missing and not candidate.exists():
            raise FileNotFoundError(relative_path)

        return candidate

    def _relative_display(self, path: Path) -> str:
        relative = path.relative_to(self.workspace_root)

        if str(relative) == ".":
            return "."

        return relative.as_posix()

    def _iter_files(self, base: Path) -> Iterator[Path]:
        for directory, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in self.IGNORED_DIRS
            )

            for filename in sorted(filenames):
                path = Path(directory) / filename

                try:
                    relative = path.relative_to(self.workspace_root)
                    resolved = self._resolve_path(relative.as_posix())
                except (ValueError, FileNotFoundError):
                    continue

                if resolved.is_file():
                    yield resolved

    def list_files(
        self,
        call_id: str,
        path: str = ".",
        max_entries: int = 200,
    ) -> ToolResult:
        tool_name = "list_files"

        try:
            if max_entries <= 0:
                raise ValueError("max_entries must be greater than 0")

            base = self._resolve_path(path)

            if not base.is_dir():
                return self._error(
                    call_id,
                    tool_name,
                    f"not a directory: {path}",
                    path=path,
                )

            entries: list[str] = []
            truncated = False

            for directory, dirnames, filenames in os.walk(
                base,
                followlinks=False,
            ):
                dirnames[:] = sorted(
                    name
                    for name in dirnames
                    if name not in self.IGNORED_DIRS
                )

                current = Path(directory)

                for dirname in dirnames:
                    candidate = current / dirname

                    try:
                        relative = candidate.relative_to(self.workspace_root)
                        self._resolve_path(relative.as_posix())
                    except (ValueError, FileNotFoundError):
                        continue

                    entries.append(relative.as_posix() + "/")

                    if len(entries) >= max_entries:
                        truncated = True
                        break

                if truncated:
                    break

                for filename in sorted(filenames):
                    candidate = current / filename

                    try:
                        relative = candidate.relative_to(self.workspace_root)
                        self._resolve_path(relative.as_posix())
                    except (ValueError, FileNotFoundError):
                        continue

                    entries.append(relative.as_posix())

                    if len(entries) >= max_entries:
                        truncated = True
                        break

                if truncated:
                    break

            output = "\n".join(entries)

            if not output:
                output = "(empty directory)"

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=True,
                output=output,
                metadata={
                    "path": self._relative_display(base),
                    "entries": len(entries),
                    "truncated": truncated,
                },
            )

        except FileNotFoundError:
            return self._error(
                call_id,
                tool_name,
                f"path not found: {path}",
                path=path,
            )
        except (ValueError, OSError) as exc:
            return self._error(
                call_id,
                tool_name,
                str(exc),
                path=path,
            )

    def search_code(
        self,
        call_id: str,
        query: str,
        path: str = ".",
        *,
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> ToolResult:
        tool_name = "search_code"

        try:
            if not query:
                raise ValueError("query must not be empty")

            if max_results <= 0:
                raise ValueError("max_results must be greater than 0")

            base = self._resolve_path(path)

            if not base.is_dir():
                return self._error(
                    call_id,
                    tool_name,
                    f"not a directory: {path}",
                    path=path,
                )

            needle = query if case_sensitive else query.lower()

            matches: list[str] = []
            files_scanned = 0
            truncated = False

            for file_path in self._iter_files(base):
                try:
                    if file_path.stat().st_size > self.MAX_SEARCH_FILE_SIZE:
                        continue

                    text = file_path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue

                files_scanned += 1

                for line_number, line in enumerate(
                    text.splitlines(),
                    start=1,
                ):
                    haystack = line if case_sensitive else line.lower()

                    if needle not in haystack:
                        continue

                    relative = self._relative_display(file_path)

                    matches.append(
                        f"{relative}:{line_number}: {line.rstrip()}"
                    )

                    if len(matches) >= max_results:
                        truncated = True
                        break

                if truncated:
                    break

            output = (
                "\n".join(matches)
                if matches
                else "No matches found."
            )

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=True,
                output=output,
                metadata={
                    "query": query,
                    "path": self._relative_display(base),
                    "matches": len(matches),
                    "files_scanned": files_scanned,
                    "truncated": truncated,
                },
            )

        except FileNotFoundError:
            return self._error(
                call_id,
                tool_name,
                f"path not found: {path}",
                path=path,
            )
        except (ValueError, OSError) as exc:
            return self._error(
                call_id,
                tool_name,
                str(exc),
                path=path,
            )

    def read_file(
        self,
        call_id: str,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ToolResult:
        tool_name = "read_file"

        try:
            if start_line < 1:
                raise ValueError("start_line must be >= 1")

            if end_line is not None and end_line < start_line:
                raise ValueError(
                    "end_line must be greater than or equal to start_line"
                )

            file_path = self._resolve_path(path)

            if not file_path.is_file():
                return self._error(
                    call_id,
                    tool_name,
                    f"not a file: {path}",
                    path=path,
                )

            text = file_path.read_text(encoding="utf-8")
            lines = text.splitlines(keepends=True)
            total_lines = len(lines)

            if start_line > total_lines and total_lines != 0:
                return self._error(
                    call_id,
                    tool_name,
                    (
                        f"start_line {start_line} exceeds "
                        f"file length {total_lines}"
                    ),
                    path=path,
                    total_lines=total_lines,
                )

            start_index = start_line - 1

            if end_line is None:
                selected = lines[start_index:]
                actual_end = total_lines
            else:
                selected = lines[start_index:end_line]
                actual_end = min(end_line, total_lines)

            output = "".join(selected)

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=True,
                output=output,
                metadata={
                    "path": self._relative_display(file_path),
                    "start_line": start_line,
                    "end_line": actual_end,
                    "total_lines": total_lines,
                },
            )

        except FileNotFoundError:
            return self._error(
                call_id,
                tool_name,
                f"file not found: {path}",
                path=path,
            )
        except UnicodeDecodeError:
            return self._error(
                call_id,
                tool_name,
                f"file is not valid UTF-8 text: {path}",
                path=path,
            )
        except (ValueError, OSError) as exc:
            return self._error(
                call_id,
                tool_name,
                str(exc),
                path=path,
            )

    def write_file(
        self,
        call_id: str,
        path: str,
        content: str,
    ) -> ToolResult:
        tool_name = "write_file"

        try:
            file_path = self._resolve_path(
                path,
                allow_missing=True,
            )

            if file_path.exists() and file_path.is_dir():
                return self._error(
                    call_id,
                    tool_name,
                    f"cannot write to a directory: {path}",
                    path=path,
                )

            created = not file_path.exists()

            file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            file_path.write_text(
                content,
                encoding="utf-8",
            )

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=True,
                output=f"Wrote {len(content)} characters to {path}.",
                metadata={
                    "path": self._relative_display(file_path),
                    "created": created,
                    "characters": len(content),
                },
            )

        except (ValueError, OSError) as exc:
            return self._error(
                call_id,
                tool_name,
                str(exc),
                path=path,
            )

    def edit_file(
        self,
        call_id: str,
        path: str,
        old_text: str,
        new_text: str,
    ) -> ToolResult:
        tool_name = "edit_file"

        try:
            if not old_text:
                raise ValueError("old_text must not be empty")

            file_path = self._resolve_path(path)

            if not file_path.is_file():
                return self._error(
                    call_id,
                    tool_name,
                    f"not a file: {path}",
                    path=path,
                )

            content = file_path.read_text(encoding="utf-8")
            match_count = content.count(old_text)

            if match_count == 0:
                return self._error(
                    call_id,
                    tool_name,
                    (
                        "search text was not found; "
                        "read the relevant file content and retry "
                        "with an exact match"
                    ),
                    path=path,
                    matches=0,
                )

            if match_count > 1:
                return self._error(
                    call_id,
                    tool_name,
                    (
                        f"search text matched {match_count} locations; "
                        "provide more surrounding context so the edit "
                        "matches exactly once"
                    ),
                    path=path,
                    matches=match_count,
                )

            updated = content.replace(
                old_text,
                new_text,
                1,
            )

            file_path.write_text(
                updated,
                encoding="utf-8",
            )

            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                ok=True,
                output=f"Updated {path}.",
                metadata={
                    "path": self._relative_display(file_path),
                    "matches": 1,
                    "old_characters": len(content),
                    "new_characters": len(updated),
                },
            )

        except FileNotFoundError:
            return self._error(
                call_id,
                tool_name,
                f"file not found: {path}",
                path=path,
            )
        except UnicodeDecodeError:
            return self._error(
                call_id,
                tool_name,
                f"file is not valid UTF-8 text: {path}",
                path=path,
            )
        except (ValueError, OSError) as exc:
            return self._error(
                call_id,
                tool_name,
                str(exc),
                path=path,
            )
