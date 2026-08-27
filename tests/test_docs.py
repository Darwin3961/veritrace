from __future__ import annotations

import re
from pathlib import Path

from main import build_parser


ROOT = Path(__file__).resolve().parents[1]


def test_primary_readme_files_exist_and_are_nonempty():
    for relative_name in ("README.md", "README.txt"):
        content = (ROOT / relative_name).read_text(encoding="utf-8")
        assert content.strip()


def test_submission_readme_is_utf8_bounded_and_has_repository_url():
    raw = (ROOT / "README.txt").read_bytes()
    content = raw.decode("utf-8")

    assert len(content) <= 1000
    assert "https://github.com/Darwin3961/coding-agent" in content


def test_documentation_contains_no_obvious_secret_value():
    combined = "\n".join(
        (ROOT / relative_name).read_text(encoding="utf-8")
        for relative_name in (
            "README.md",
            "README.txt",
            "docs/ARCHITECTURE.md",
            "docs/DEMO.md",
        )
    )

    assert not re.search(r"\bsk-[A-Za-z0-9_-]{8,}\b", combined)
    assert not re.search(
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b",
        combined,
        re.IGNORECASE,
    )


def test_readme_cli_options_match_parser_core_options():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    parser_options = {
        option
        for action in build_parser()._actions
        for option in action.option_strings
    }
    required = {
        "--workspace",
        "--max-steps",
        "--trace-dir",
        "--no-trace",
        "--max-repeated-actions",
        "--plain",
        "--no-diff",
    }

    assert required <= parser_options
    assert all(option in readme for option in required)


def test_readme_states_best_effort_policy_is_not_a_sandbox():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()

    assert "deterministic best-effort guard" in readme
    assert "not an os sandbox" in readme


def test_readme_marks_unimplemented_features_as_limitations():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    limitation = (
        "does not implement multi-agent orchestration, rag, mcp integration"
    )

    assert limitation in readme


def test_architecture_and_demo_documents_exist():
    assert (ROOT / "docs/ARCHITECTURE.md").is_file()
    assert (ROOT / "docs/DEMO.md").is_file()
    assert (ROOT / "docs/VIDEO_SCRIPT.md").is_file()
    assert (ROOT / "docs/INTERVIEW_NOTES.md").is_file()
