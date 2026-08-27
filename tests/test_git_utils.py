from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from rich.console import Console

from coding_agent.git_utils import GitInspector, GitSummary
from coding_agent.renderer import RichRenderer


def run_git(workspace: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        shell=False,
    )


def init_repo(workspace: Path) -> None:
    run_git(workspace, "init", "--quiet")


def commit_file(
    workspace: Path,
    name: str = "tracked.txt",
    content: str = "original\n",
) -> Path:
    path = workspace / name
    path.write_text(content, encoding="utf-8")
    run_git(workspace, "add", "-f", name)
    run_git(
        workspace,
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "test fixture",
    )
    return path


def make_renderer():
    stream = io.StringIO()
    console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        width=200,
    )
    return RichRenderer(console=console), stream


def test_non_git_workspace_returns_non_repo(tmp_path):
    summary = GitInspector(tmp_path).inspect()

    assert summary == GitSummary(is_repo=False)


def test_empty_repo_has_no_changes(tmp_path):
    init_repo(tmp_path)

    summary = GitInspector(tmp_path).inspect()

    assert summary.is_repo is True
    assert summary.error is None
    assert summary.status_short == ""
    assert summary.diff_stat == ""
    assert summary.diff_text == ""


def test_tracked_unstaged_change_is_reported(tmp_path):
    init_repo(tmp_path)
    path = commit_file(tmp_path)
    path.write_text("modified\n", encoding="utf-8")

    summary = GitInspector(tmp_path).inspect()

    assert summary.is_repo is True
    assert " M tracked.txt" in summary.status_short
    assert "Unstaged:" in summary.diff_stat
    assert "tracked.txt" in summary.diff_stat
    assert "Unstaged:" in summary.diff_text
    assert "+modified" in summary.diff_text


def test_untracked_file_appears_in_status_without_diff(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")

    summary = GitInspector(tmp_path).inspect()

    assert "?? new.txt" in summary.status_short
    assert summary.diff_stat == ""
    assert summary.diff_text == ""


def test_staged_change_is_in_cached_diff(tmp_path):
    init_repo(tmp_path)
    path = commit_file(tmp_path)
    path.write_text("staged\n", encoding="utf-8")
    run_git(tmp_path, "add", "tracked.txt")

    summary = GitInspector(tmp_path).inspect()

    assert "M  tracked.txt" in summary.status_short
    assert "Staged:" in summary.diff_stat
    assert "Staged:" in summary.diff_text
    assert "+staged" in summary.diff_text


def test_staged_and_unstaged_diffs_are_both_reported(tmp_path):
    init_repo(tmp_path)
    path = commit_file(tmp_path)
    path.write_text("staged\n", encoding="utf-8")
    run_git(tmp_path, "add", "tracked.txt")
    path.write_text("unstaged\n", encoding="utf-8")

    summary = GitInspector(tmp_path).inspect()

    assert "MM tracked.txt" in summary.status_short
    assert "Unstaged:" in summary.diff_text
    assert "Staged:" in summary.diff_text
    assert "+unstaged" in summary.diff_text
    assert "+staged" in summary.diff_text


def test_diff_is_truncated_with_head_and_tail(tmp_path):
    init_repo(tmp_path)
    path = commit_file(
        tmp_path,
        content="HEAD_OLD_" + ("A" * 5000) + "_TAIL_OLD\n",
    )
    path.write_text(
        "HEAD_NEW_" + ("B" * 5000) + "_TAIL_NEW\n",
        encoding="utf-8",
    )

    summary = GitInspector(
        tmp_path,
        max_diff_chars=500,
    ).inspect()

    assert "diff characters omitted" in summary.diff_text
    assert "HEAD" in summary.diff_text
    assert "TAIL" in summary.diff_text
    assert len(summary.diff_text) <= 500


@pytest.mark.parametrize(
    "changed_content",
    [
        "API_KEY=fake-value\n",
        "token: fake-value\n",
        "value = 'sk-FAKE_TEST_VALUE'\n",
        "Authorization=Bearer FAKE.TEST.VALUE\n",
    ],
)
def test_diff_secret_values_are_redacted(tmp_path, changed_content):
    init_repo(tmp_path)
    path = commit_file(tmp_path, content="value = 'safe'\n")
    path.write_text(changed_content, encoding="utf-8")

    summary = GitInspector(tmp_path).inspect()

    assert "fake-value" not in summary.diff_text
    assert "sk-FAKE_TEST_VALUE" not in summary.diff_text
    assert "Bearer FAKE.TEST.VALUE" not in summary.diff_text
    assert "[REDACTED]" in summary.diff_text


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        "private.pem",
        "private.key",
    ],
)
def test_sensitive_file_diff_content_is_fully_redacted(tmp_path, name):
    init_repo(tmp_path)
    path = commit_file(
        tmp_path,
        name=name,
        content="OLD_FAKE_PRIVATE_VALUE\n",
    )
    path.write_text("NEW_FAKE_PRIVATE_VALUE\n", encoding="utf-8")

    summary = GitInspector(tmp_path).inspect()

    assert "OLD_FAKE_PRIVATE_VALUE" not in summary.diff_text
    assert "NEW_FAKE_PRIVATE_VALUE" not in summary.diff_text
    assert "sensitive file diff redacted" in summary.diff_text


def test_git_missing_returns_safe_error(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(
        "coding_agent.git_utils.subprocess.run",
        missing,
    )

    summary = GitInspector(tmp_path).inspect()

    assert summary.is_repo is False
    assert "not available" in summary.error


def test_git_timeout_returns_safe_error(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(
        "coding_agent.git_utils.subprocess.run",
        timeout,
    )

    summary = GitInspector(tmp_path, timeout=1).inspect()

    assert summary.is_repo is False
    assert "timed out" in summary.error


def test_git_error_after_repo_probe_is_reported(tmp_path, monkeypatch):
    calls = 0

    def fail_after_probe(command, **kwargs):
        nonlocal calls
        calls += 1

        if calls == 1:
            return subprocess.CompletedProcess(command, 0, "true\n", "")

        return subprocess.CompletedProcess(command, 2, "", "test failure")

    monkeypatch.setattr(
        "coding_agent.git_utils.subprocess.run",
        fail_after_probe,
    )

    summary = GitInspector(tmp_path).inspect()

    assert summary.is_repo is True
    assert "test failure" in summary.error


def test_inspection_is_read_only_and_uses_only_allowed_commands(
    tmp_path,
    monkeypatch,
):
    init_repo(tmp_path)
    path = commit_file(tmp_path)
    path.write_text("modified\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")
    status_before = run_git(tmp_path, "status", "--porcelain=v1").stdout

    real_run = subprocess.run
    observed = []

    def recording_run(command, **kwargs):
        observed.append((tuple(command[1:]), dict(kwargs)))
        return real_run(command, **kwargs)

    monkeypatch.setattr(
        "coding_agent.git_utils.subprocess.run",
        recording_run,
    )

    summary = GitInspector(tmp_path).inspect()
    status_after = real_run(
        ["git", "status", "--porcelain=v1"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        shell=False,
    ).stdout

    assert summary.is_repo is True
    assert status_after == status_before
    assert observed

    for arguments, kwargs in observed:
        assert arguments in GitInspector.ALLOWED_COMMANDS
        assert kwargs["shell"] is False
        assert Path(kwargs["cwd"]) == tmp_path.resolve()


def test_invalid_workspace_and_limits_are_rejected(tmp_path):
    missing = tmp_path / "missing"
    file_path = tmp_path / "file.txt"
    file_path.write_text("file", encoding="utf-8")

    with pytest.raises(ValueError, match="does not exist"):
        GitInspector(missing)

    with pytest.raises(ValueError, match="not a directory"):
        GitInspector(file_path)

    with pytest.raises(ValueError, match="timeout"):
        GitInspector(tmp_path, timeout=0)

    with pytest.raises(ValueError, match="at least 100"):
        GitInspector(tmp_path, max_diff_chars=99)


def test_renderer_shows_clean_and_changed_git_summaries():
    renderer, stream = make_renderer()
    renderer.render_git_summary(GitSummary(is_repo=True))
    renderer.render_git_summary(
        GitSummary(
            is_repo=True,
            status_short=" M app.py",
            diff_stat="app.py | 1 +",
            diff_text=(
                "diff --git a/app.py b/app.py\n"
                "--- a/app.py\n"
                "+++ b/app.py\n"
                "@@ -0,0 +1 @@\n"
                "+safe change"
            ),
        )
    )

    output = stream.getvalue()
    assert "Workspace clean" in output
    assert "Changes" in output
    assert "M app.py" in output
    assert "Diff summary" in output
    assert "+ safe change" in output


def test_renderer_git_summary_is_markup_safe():
    renderer, stream = make_renderer()
    injected = "[bold red]INJECTED[/bold red]"

    renderer.render_git_summary(
        GitSummary(
            is_repo=True,
            status_short=f" M {injected}",
            diff_text=f"+{injected}",
        )
    )

    assert injected in stream.getvalue()


def test_renderer_non_repo_is_silent_unless_error():
    renderer, stream = make_renderer()

    renderer.render_git_summary(GitSummary(is_repo=False))
    assert stream.getvalue() == ""

    renderer.render_git_summary(
        GitSummary(is_repo=False, error="git missing")
    )
    assert "Git unavailable: git missing" in stream.getvalue()
