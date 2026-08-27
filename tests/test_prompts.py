from __future__ import annotations

import platform
import sys
from pathlib import Path

from coding_agent.prompts import build_runtime_prompt


def test_runtime_prompt_contains_dynamic_platform_and_workspace(tmp_path: Path):
    prompt = build_runtime_prompt("Base instructions.", tmp_path)

    assert f"Current platform: {platform.system()}" in prompt
    assert sys.platform in prompt
    assert f"Workspace root: {tmp_path.resolve()}" in prompt
    assert "/home/smollm2-test" not in prompt


def test_runtime_prompt_explains_fixed_cwd_and_portable_commands(tmp_path: Path):
    prompt = build_runtime_prompt("Base instructions.", tmp_path)

    assert "run_command automatically executes" in prompt
    assert "workspace root as its current working directory" in prompt
    assert "Do not prepend cd or guess another workspace path" in prompt
    assert "Do not assume utilities such as pwd are available" in prompt
    assert "python -m pytest -q" in prompt
