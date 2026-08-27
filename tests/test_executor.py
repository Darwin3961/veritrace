import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from coding_agent.executor import CommandExecutor


def python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]

    if os.name == "nt":
        return subprocess.list2cmdline(parts)

    return shlex.join(parts)


def test_successful_command_captures_stdout(tmp_path: Path):
    executor = CommandExecutor(tmp_path)

    result = executor.run_command(
        "call-success",
        python_command("-c", "print('hello')"),
    )

    assert result.ok is True
    assert result.metadata["exit_code"] == 0
    assert "hello" in result.output
    assert result.metadata["timeout"] is False


def test_command_runs_in_workspace(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("workspace marker", encoding="utf-8")
    executor = CommandExecutor(tmp_path)
    code = "from pathlib import Path; print(Path('marker.txt').read_text())"

    result = executor.run_command(
        "call-cwd",
        python_command("-c", code),
    )

    assert result.ok is True
    assert result.output.strip() == "workspace marker"


def test_nonzero_exit_returns_failed_tool_result(tmp_path: Path):
    executor = CommandExecutor(tmp_path)
    code = "import sys; print('bad', file=sys.stderr); sys.exit(3)"

    result = executor.run_command(
        "call-nonzero",
        python_command("-c", code),
    )

    assert result.ok is False
    assert result.metadata["exit_code"] == 3
    assert "bad" in result.error
    assert result.metadata["timeout"] is False


def test_timeout_terminates_process_tree(tmp_path: Path):
    child = tmp_path / "child.py"
    child.write_text(
        "from pathlib import Path\n"
        "import time\n"
        "time.sleep(2.5)\n"
        "Path('survivor.txt').write_text('alive', encoding='utf-8')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "print('start', flush=True)\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    executor = CommandExecutor(tmp_path)
    started = time.monotonic()

    result = executor.run_command(
        "call-timeout",
        python_command("parent.py"),
        timeout=1,
    )
    elapsed = time.monotonic() - started
    time.sleep(2.5)

    assert result.ok is False
    assert result.metadata["timeout"] is True
    assert "timed out" in result.error
    assert "start" in result.output
    assert elapsed < 7
    assert not (tmp_path / "survivor.txt").exists()


def test_stdout_is_truncated_with_head_and_tail(tmp_path: Path):
    executor = CommandExecutor(tmp_path, max_output_chars=1000)
    code = "print('HEAD' + 'A' * 20000 + 'TAIL')"

    result = executor.run_command(
        "call-long-stdout",
        python_command("-c", code),
    )

    assert result.ok is True
    assert result.metadata["stdout_truncated"] is True
    assert result.metadata["stdout_omitted_chars"] > 0
    assert "characters omitted" in result.output
    assert result.output.startswith("HEAD")
    assert result.output.rstrip().endswith("TAIL")
    assert len(result.output) <= 1000


def test_stderr_is_truncated_with_head_and_tail(tmp_path: Path):
    executor = CommandExecutor(tmp_path, max_output_chars=1000)
    code = (
        "import sys; "
        "print('HEAD' + 'B' * 20000 + 'TAIL', file=sys.stderr); "
        "sys.exit(1)"
    )

    result = executor.run_command(
        "call-long-stderr",
        python_command("-c", code),
    )

    assert result.ok is False
    assert result.metadata["stderr_truncated"] is True
    assert result.metadata["stderr_omitted_chars"] > 0
    assert "characters omitted" in result.error
    assert result.error.startswith("HEAD")
    assert result.error.rstrip().endswith("TAIL")
    assert len(result.error) <= 1000


@pytest.mark.parametrize("command", ["", "   "])
def test_empty_command_is_rejected(tmp_path: Path, command: str):
    executor = CommandExecutor(tmp_path)

    result = executor.run_command("call-empty", command)

    assert result.ok is False
    assert result.error == "command must be a non-empty string"


def test_nonpositive_timeout_is_rejected(tmp_path: Path):
    executor = CommandExecutor(tmp_path)

    result = executor.run_command(
        "call-timeout-value",
        python_command("-c", "print('never runs')"),
        timeout=0,
    )

    assert result.ok is False
    assert result.error == "timeout must be greater than 0"


def test_missing_workspace_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="Workspace does not exist"):
        CommandExecutor(tmp_path / "missing")


def test_workspace_file_is_rejected(tmp_path: Path):
    file_path = tmp_path / "workspace.txt"
    file_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="Workspace is not a directory"):
        CommandExecutor(file_path)


def test_short_output_is_not_truncated(tmp_path: Path):
    executor = CommandExecutor(tmp_path, max_output_chars=1000)

    result = executor.run_command(
        "call-short",
        python_command("-c", "print('short')"),
    )

    assert result.ok is True
    assert result.metadata["stdout_truncated"] is False
    assert result.metadata["stderr_truncated"] is False
    assert result.metadata["stdout_omitted_chars"] == 0
    assert result.metadata["stderr_omitted_chars"] == 0


def test_nonzero_exit_without_stderr_has_fallback_error(tmp_path: Path):
    executor = CommandExecutor(tmp_path)

    result = executor.run_command(
        "call-no-stderr",
        python_command("-c", "import sys; sys.exit(7)"),
    )

    assert result.ok is False
    assert result.metadata["exit_code"] == 7
    assert "command exited with code 7" in result.error


def test_workspace_python_file_can_be_executed(tmp_path: Path):
    (tmp_path / "sample.py").write_text(
        'print("sample works")\n',
        encoding="utf-8",
    )
    executor = CommandExecutor(tmp_path)

    result = executor.run_command(
        "call-sample",
        python_command("sample.py"),
    )

    assert result.ok is True
    assert result.output.strip() == "sample works"
