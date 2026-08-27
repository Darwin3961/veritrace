from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.release_check import REQUIRED_FILES, ReleaseChecker


REMOTE = "https://github.com/Darwin3961/coding-agent.git"


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _write_required_files(root: Path) -> None:
    for relative_name in REQUIRED_FILES:
        path = root / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")

    (root / "README.txt").write_text("release readme\n", encoding="utf-8")
    (root / ".env.example").write_text(
        "DEEPSEEK_API_KEY=\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(
        "traces/\neval/results/\n",
        encoding="utf-8",
    )


def _create_repo(root: Path) -> None:
    _write_required_files(root)
    _git(root, "init")
    _git(root, "config", "user.email", "release@example.invalid")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", REMOTE)


def _runner_with_pytest(returncode: int):
    def runner(command, **kwargs):
        if command[0] == sys.executable and command[1:] == ["-m", "pytest", "-q"]:
            return subprocess.CompletedProcess(
                command,
                returncode,
                stdout=("1 passed\n" if returncode == 0 else "1 failed\n"),
                stderr="",
            )
        return subprocess.run(command, **kwargs)

    return runner


def _results_by_name(checker: ReleaseChecker):
    return {result.name: result for result in checker.run()}


def test_clean_repository_passes(tmp_path: Path):
    _create_repo(tmp_path)
    results = ReleaseChecker(
        tmp_path,
        command_runner=_runner_with_pytest(0),
    ).run()

    assert all(result.passed for result in results), results


def test_dirty_repository_fails(tmp_path: Path):
    _create_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("dirty", encoding="utf-8")

    results = _results_by_name(ReleaseChecker(
        tmp_path,
        command_runner=_runner_with_pytest(0),
    ))

    assert results["working tree clean"].passed is False


def test_readme_too_long_fails(tmp_path: Path):
    _create_repo(tmp_path)
    (tmp_path / "README.txt").write_text("x" * 1001, encoding="utf-8")
    _git(tmp_path, "add", "README.txt")
    _git(tmp_path, "commit", "-m", "long readme")

    results = _results_by_name(ReleaseChecker(
        tmp_path,
        command_runner=_runner_with_pytest(0),
    ))

    assert results["README.txt length"].passed is False


def test_tracked_secret_fails(tmp_path: Path):
    _create_repo(tmp_path)
    (tmp_path / "secret.txt").write_text(
        "DEEPSEEK_API_KEY=" + "real-production-value-123\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "secret.txt")
    _git(tmp_path, "commit", "-m", "add secret fixture")

    results = _results_by_name(ReleaseChecker(
        tmp_path,
        command_runner=_runner_with_pytest(0),
    ))

    assert results["tracked secret scan"].passed is False
    assert "secret.txt" in results["tracked secret scan"].detail


def test_empty_env_example_key_is_allowed(tmp_path: Path):
    _create_repo(tmp_path)
    checker = ReleaseChecker(tmp_path, command_runner=_runner_with_pytest(0))

    assert checker._check_env_example().passed is True


def test_nonempty_env_example_key_is_rejected(tmp_path: Path):
    _create_repo(tmp_path)
    (tmp_path / ".env.example").write_text(
        "DEEPSEEK_API_KEY=" + "real-production-value-123\n",
        encoding="utf-8",
    )
    checker = ReleaseChecker(tmp_path, command_runner=_runner_with_pytest(0))

    assert checker._check_env_example().passed is False


def test_traces_and_eval_results_are_ignored(tmp_path: Path):
    _create_repo(tmp_path)
    results = _results_by_name(ReleaseChecker(
        tmp_path,
        command_runner=_runner_with_pytest(0),
    ))

    assert results["traces ignored"].passed is True
    assert results["eval results ignored"].passed is True


def test_pytest_failure_fails_release(tmp_path: Path):
    _create_repo(tmp_path)
    results = _results_by_name(ReleaseChecker(
        tmp_path,
        command_runner=_runner_with_pytest(1),
    ))

    assert results["pytest"].passed is False
    assert results["pytest"].detail == "1 failed"


def test_binary_tracked_file_is_skipped_by_secret_scan(tmp_path: Path):
    _create_repo(tmp_path)
    (tmp_path / "asset.bin").write_bytes(
        b"\x00\x01" + b"sk-" + b"binary-production-value-123456"
    )
    _git(tmp_path, "add", "asset.bin")
    _git(tmp_path, "commit", "-m", "binary asset")
    checker = ReleaseChecker(tmp_path, command_runner=_runner_with_pytest(0))

    assert checker._check_tracked_secrets().passed is True


def test_non_git_directory_fails_clearly(tmp_path: Path):
    _write_required_files(tmp_path)
    results = _results_by_name(ReleaseChecker(
        tmp_path,
        command_runner=_runner_with_pytest(0),
    ))

    assert results["tracked secret scan"].passed is False
    assert results["working tree clean"].passed is False
    assert results["branch"].passed is False
    assert results["origin URL"].passed is False
