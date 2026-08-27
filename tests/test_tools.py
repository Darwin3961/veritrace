from pathlib import Path

import pytest

from coding_agent.tools import WorkspaceTools


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, WorkspaceTools]:
    root = tmp_path / "workspace"
    root.mkdir()
    return root, WorkspaceTools(root)


def test_list_files_includes_normal_entries_and_ignores_noise(workspace):
    root, tools = workspace
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("private", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "secret.py").write_text("hidden", encoding="utf-8")

    result = tools.list_files("call-list")

    assert result.ok is True
    assert "src/" in result.output
    assert "src/main.py" in result.output
    assert ".git" not in result.output
    assert ".venv" not in result.output


def test_read_file_reads_complete_utf8_file(workspace):
    root, tools = workspace
    content = "第一行\nsecond line\n"
    (root / "notes.txt").write_text(content, encoding="utf-8")

    result = tools.read_file("call-read", "notes.txt")

    assert result.ok is True
    assert result.output == content
    assert result.metadata["total_lines"] == 2


def test_read_file_reads_requested_line_range(workspace):
    root, tools = workspace
    (root / "lines.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = tools.read_file(
        "call-range",
        "lines.txt",
        start_line=2,
        end_line=3,
    )

    assert result.ok is True
    assert result.output == "two\nthree\n"
    assert result.metadata["start_line"] == 2
    assert result.metadata["end_line"] == 3


def test_read_file_returns_error_for_missing_file(workspace):
    _, tools = workspace

    result = tools.read_file("call-missing", "missing.txt")

    assert result.ok is False
    assert result.error == "file not found: missing.txt"


def test_read_file_safely_rejects_non_utf8_file(workspace):
    root, tools = workspace
    (root / "binary.bin").write_bytes(b"\xff\xfe\xfd")

    result = tools.read_file("call-binary", "binary.bin")

    assert result.ok is False
    assert result.error == "file is not valid UTF-8 text: binary.bin"


def test_search_code_is_case_insensitive_and_reports_location(workspace):
    root, tools = workspace
    (root / "module.py").write_text(
        "first line\nImportantValue = 3\n",
        encoding="utf-8",
    )

    result = tools.search_code("call-search", "importantvalue")

    assert result.ok is True
    assert result.output == "module.py:2: ImportantValue = 3"
    assert result.metadata["matches"] == 1


def test_search_code_returns_success_when_no_matches_exist(workspace):
    root, tools = workspace
    (root / "module.py").write_text("value = 3\n", encoding="utf-8")

    result = tools.search_code("call-no-match", "missing_symbol")

    assert result.ok is True
    assert result.output == "No matches found."
    assert result.metadata["matches"] == 0


def test_search_code_does_not_scan_ignored_directories(workspace):
    root, tools = workspace
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("hidden_needle", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "module.py").write_text("hidden_needle", encoding="utf-8")
    (root / "visible.py").write_text("ordinary content\n", encoding="utf-8")

    result = tools.search_code("call-ignored", "hidden_needle")

    assert result.ok is True
    assert result.output == "No matches found."
    assert result.metadata["files_scanned"] == 1


def test_write_file_creates_file_and_parent_directories(workspace):
    root, tools = workspace

    result = tools.write_file("call-write", "nested/output.txt", "created")

    assert result.ok is True
    assert (root / "nested" / "output.txt").read_text(encoding="utf-8") == "created"
    assert result.metadata["created"] is True


def test_write_file_overwrites_existing_file(workspace):
    root, tools = workspace
    target = root / "existing.txt"
    target.write_text("old", encoding="utf-8")

    result = tools.write_file("call-overwrite", "existing.txt", "new")

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "new"
    assert result.metadata["created"] is False


def test_edit_file_replaces_one_exact_match(workspace):
    root, tools = workspace
    target = root / "module.py"
    target.write_text("before\ntarget = 1\nafter\n", encoding="utf-8")

    result = tools.edit_file("call-edit", "module.py", "target = 1", "target = 2")

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "before\ntarget = 2\nafter\n"
    assert result.metadata["matches"] == 1


def test_edit_file_zero_matches_fails_without_modifying_file(workspace):
    root, tools = workspace
    target = root / "module.py"
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")

    result = tools.edit_file("call-zero", "module.py", "missing = 1", "value = 2")

    assert result.ok is False
    assert result.metadata["matches"] == 0
    assert target.read_text(encoding="utf-8") == original


def test_edit_file_multiple_matches_fails_without_modifying_file(workspace):
    root, tools = workspace
    target = root / "module.py"
    original = "repeat\nrepeat\n"
    target.write_text(original, encoding="utf-8")

    result = tools.edit_file("call-multiple", "module.py", "repeat", "changed")

    assert result.ok is False
    assert result.metadata["matches"] == 2
    assert target.read_text(encoding="utf-8") == original


def test_edit_file_rejects_empty_search_text(workspace):
    root, tools = workspace
    target = root / "module.py"
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")

    result = tools.edit_file("call-empty", "module.py", "", "changed")

    assert result.ok is False
    assert result.error == "old_text must not be empty"
    assert target.read_text(encoding="utf-8") == original


def test_parent_traversal_is_rejected(workspace, tmp_path):
    _, tools = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    result = tools.read_file("call-parent", "../outside.txt")

    assert result.ok is False
    assert result.error == "path escapes the workspace"


def test_absolute_path_is_rejected(workspace, tmp_path):
    _, tools = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    result = tools.read_file("call-absolute", str(outside.resolve()))

    assert result.ok is False
    assert result.error == "absolute paths are not allowed"


def test_git_internal_path_is_rejected(workspace):
    root, tools = workspace
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("config", encoding="utf-8")

    result = tools.read_file("call-git", ".git/config")

    assert result.ok is False
    assert result.error == "access to .git is not allowed"


def test_external_symlink_cannot_be_read_or_written(workspace, tmp_path):
    root, tools = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "external.txt"

    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    read_result = tools.read_file("call-link-read", "external.txt")
    write_result = tools.write_file("call-link-write", "external.txt", "changed")

    assert read_result.ok is False
    assert write_result.ok is False
    assert outside.read_text(encoding="utf-8") == "outside"
