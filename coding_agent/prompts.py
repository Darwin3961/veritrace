from __future__ import annotations

import platform
import sys
from pathlib import Path


SYSTEM_PROMPT = """You are a local coding agent operating inside one project workspace.

Your job is to complete the user's programming task by inspecting the repository,
using the provided local tools, editing files when necessary, and verifying the
result with appropriate commands or tests.

Rules:

1. Use tools to inspect the workspace instead of guessing file contents.
2. Prefer search_code before reading many unrelated files.
3. Use read_file to obtain exact context before editing.
4. For localized edits, use edit_file with an exact unique old_text match.
5. Use write_file when creating a new file or when full-file replacement is clearly appropriate.
6. Use run_command to compile, run, or test changes when verification is possible.
7. A failed tool call or failed command is an observation. Analyze the failure and continue when recovery is possible.
8. Never claim that a command, test, or edit succeeded unless the corresponding tool result confirms it.
9. Do not attempt to access files outside the workspace.
10. Do not attempt to modify .git.
11. Keep the final answer concise and summarize what was changed and how it was verified.
12. When the task is complete, stop calling tools and provide the final answer.

You have no server-side file system or code execution capability. All actions must be performed through the provided local tools.
"""


def build_runtime_prompt(
    base_prompt: str,
    workspace_root: str | Path,
) -> str:
    """Add local runtime facts without introducing provider-specific details."""
    workspace = Path(workspace_root).expanduser().resolve()
    system_name = platform.system() or "Unknown"
    platform_description = platform.platform() or system_name

    runtime_context = f"""Runtime environment:

- Current platform: {system_name} ({sys.platform}; {platform_description})
- Workspace root: {workspace}
- run_command automatically executes with the workspace root as its current working directory.
- Do not prepend cd or guess another workspace path; pass the intended command directly.
- Use commands compatible with the current platform. Do not assume utilities such as pwd are available.
- Prefer cross-platform verification commands such as python -m pytest -q when appropriate.
"""

    return base_prompt.rstrip() + "\n\n" + runtime_context
