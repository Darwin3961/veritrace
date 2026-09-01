from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from main import build_parser


ROOT = Path(__file__).resolve().parents[1]


def test_primary_readme_files_exist_and_are_nonempty():
    for relative_name in ("README.md", "README.txt"):
        content = (ROOT / relative_name).read_text(encoding="utf-8")
        assert content.strip()


def test_readme_brand_and_repository_are_veritrace():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    submission = (ROOT / "README.txt").read_text(encoding="utf-8")

    assert "# ✦ VeriTrace" in readme.split("</div>", 1)[0]
    assert submission.startswith("VeriTrace 使用说明\n")
    assert "https://github.com/Darwin3961/veritrace" in submission


def test_readme_has_landing_page_structure_in_order():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    headings = (
        "## Demo",
        "## Why VeriTrace?",
        "## How VeriTrace Works",
        "## Features",
        "## Quick Start",
        "## Evaluation",
        "## Safety and Limitations",
        "## Project Structure",
        "## Documentation",
    )

    positions = [readme.index(heading) for heading in headings]
    assert positions == sorted(positions)
    for anchor in (
        "#demo",
        "#why-veritrace",
        "#how-veritrace-works",
        "#features",
        "#quick-start",
        "#evaluation",
        "#safety-and-limitations",
    ):
        assert f'href="{anchor}"' in readme or f"]({anchor})" in readme


def test_readme_uses_truthful_hero_demo_and_architecture():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    hero = readme.split("</div>", 1)[0]
    badge_row = hero.split("<p>", 1)[1].split("</p>", 1)[0]

    assert "Model claims are not execution facts" in hero
    assert hero.count("img.shields.io") == 4
    assert badge_row.count("<img ") == 4
    assert "376 Tests" in hero
    assert "docs/assets/veritrace-hero.gif" in hero
    assert "veritrace-hero-preview.png" not in readme
    assert "可复现执行流程的浓缩展示" in readme
    assert "```mermaid" not in readme
    assert "docs/assets/veritrace-architecture.svg" in readme
    assert "ToolResult` — normalized execution observation" in readme
    assert "Event` — append-only structured execution fact" in readme
    assert "tests/test_selector.py" not in readme
    assert "docs/VIDEO_SCRIPT.md" not in readme


def test_readme_relative_markdown_links_exist():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    relative_links = re.findall(r"\[[^]]+\]\((?!https?://|#)([^)]+)\)", readme)
    relative_sources = re.findall(r'<img\s+[^>]*src="(?!https?://)([^"]+)"', readme)

    assert relative_links
    assert all(
        (ROOT / link).exists()
        for link in relative_links + relative_sources
    )

    architecture = ROOT / "docs/assets/veritrace-architecture.svg"
    root = ET.parse(architecture).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["viewBox"] == "0 0 1200 440"


def test_readme_avoids_unsupported_product_claims():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    unsupported_claims = (
        "formal verification",
        "guaranteed correctness",
        "production ready",
        "enterprise grade",
        "autonomous software engineer",
        "swe-bench",
        "the model cannot finish until tests pass",
    )

    assert all(claim not in readme for claim in unsupported_claims)


def test_submission_readme_is_utf8_bounded_and_has_repository_url():
    raw = (ROOT / "README.txt").read_bytes()
    content = raw.decode("utf-8")

    assert len(content) <= 1000
    assert "https://github.com/Darwin3961/veritrace" in content


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
    limitation = "multi-agent、rag、mcp 和 long-term memory 等高级编排能力不在当前范围内"

    assert limitation in readme


def test_architecture_and_demo_documents_exist():
    assert (ROOT / "docs/ARCHITECTURE.md").is_file()
    assert (ROOT / "docs/DEMO.md").is_file()
    assert (ROOT / "docs/VIDEO_SCRIPT.md").is_file()
    assert (ROOT / "docs/DESIGN_RATIONALE.md").is_file()
