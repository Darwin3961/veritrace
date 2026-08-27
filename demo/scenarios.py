from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DemoScenario:
    """One reproducible coding task and its independent verification."""

    name: str
    description: str
    task: str
    files: dict[str, str]
    verification_command: list[str]


_PYTEST_COMMAND = [sys.executable, "-m", "pytest", "-q"]


SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        name="bugfix",
        description="Repair a small arithmetic regression.",
        task=(
            "Fix the failing tests in this workspace. Inspect the code, "
            "make the smallest correct change, run the tests, and finish "
            "only after verification succeeds."
        ),
        files={
            "calc.py": (
                "def add(a: int, b: int) -> int:\n"
                "    \"\"\"Return the sum of two integers.\"\"\"\n"
                "    return a - b\n"
            ),
            "tests/test_calc.py": (
                "from calc import add\n\n\n"
                "def test_add_positive_numbers():\n"
                "    assert add(2, 3) == 5\n\n\n"
                "def test_add_negative_number():\n"
                "    assert add(7, -2) == 5\n"
            ),
        },
        verification_command=list(_PYTEST_COMMAND),
    ),
    DemoScenario(
        name="implement",
        description="Implement a tested name-normalization helper.",
        task=(
            "Implement normalize_name so that all existing tests pass. "
            "Inspect the repository first and verify the solution with tests."
        ),
        files={
            "strings.py": (
                "def normalize_name(value: str) -> str:\n"
                "    \"\"\"Normalize whitespace and capitalization in a name.\"\"\"\n"
                "    raise NotImplementedError\n"
            ),
            "tests/test_strings.py": (
                "from strings import normalize_name\n\n\n"
                "def test_strips_outer_whitespace():\n"
                "    assert normalize_name(\"  ada lovelace  \") == \"Ada Lovelace\"\n\n\n"
                "def test_collapses_internal_whitespace():\n"
                "    assert normalize_name(\"grace   brewster\\thopper\") == \"Grace Brewster Hopper\"\n\n\n"
                "def test_title_cases_words():\n"
                "    assert normalize_name(\"ALAN turing\") == \"Alan Turing\"\n"
            ),
        },
        verification_command=list(_PYTEST_COMMAND),
    ),
    DemoScenario(
        name="multi_file",
        description="Trace a formatting defect across two application modules.",
        task=(
            "Fix the greeting behavior so that all existing tests pass. "
            "Inspect both application modules, make the smallest correct "
            "change, run the tests, and finish after verification succeeds."
        ),
        files={
            "app.py": (
                "from utils import display_name\n\n\n"
                "def greeting(first: str, last: str) -> str:\n"
                "    return f\"Hello, {display_name(first, last)}!\"\n"
            ),
            "utils.py": (
                "def display_name(first: str, last: str) -> str:\n"
                "    \"\"\"Format a person's display name.\"\"\"\n"
                "    return f\"{last.strip()}, {first.strip()}\"\n"
            ),
            "tests/test_app.py": (
                "from app import greeting\n\n\n"
                "def test_greeting_uses_natural_name_order():\n"
                "    assert greeting(\"Ada\", \"Lovelace\") == \"Hello, Ada Lovelace!\"\n\n\n"
                "def test_greeting_strips_name_whitespace():\n"
                "    assert greeting(\"  Grace\", \"Hopper  \") == \"Hello, Grace Hopper!\"\n"
            ),
        },
        verification_command=list(_PYTEST_COMMAND),
    ),
)


def get_scenario(name: str) -> DemoScenario:
    """Return a named scenario or raise a clear error."""
    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario

    available = ", ".join(item.name for item in SCENARIOS)
    raise ValueError(f"unknown scenario {name!r}; choose from: {available}")


def _is_same_or_parent(candidate: Path, protected: Path) -> bool:
    return candidate == protected or candidate in protected.parents


def _validate_force_target(output: Path) -> None:
    anchor = Path(output.anchor).resolve()
    home = Path.home().resolve()
    project = PROJECT_ROOT.resolve()

    if output == anchor:
        raise ValueError("refusing to replace a filesystem root")

    if _is_same_or_parent(output, home):
        raise ValueError("refusing to replace the home directory or its parent")

    if _is_same_or_parent(output, project):
        raise ValueError("refusing to replace the project directory or its parent")


def _resolve_scenario_file(workspace: Path, relative_name: str) -> Path:
    if not isinstance(relative_name, str) or not relative_name:
        raise ValueError("scenario file paths must be non-empty strings")

    supplied = Path(relative_name)
    if supplied.is_absolute():
        raise ValueError(f"scenario file path must be relative: {relative_name}")

    candidate = (workspace / supplied).resolve(strict=False)
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            f"scenario file path escapes the workspace: {relative_name}"
        ) from exc

    return candidate


def create_scenario_workspace(
    scenario: DemoScenario,
    output: str | Path,
    *,
    force: bool = False,
) -> Path:
    """Create one scenario without permitting unsafe replacement targets."""
    supplied_output = Path(output).expanduser()

    if supplied_output.exists() and supplied_output.is_symlink():
        raise ValueError("refusing to use a symbolic link as the output directory")

    workspace = supplied_output.resolve(strict=False)

    targets = [
        _resolve_scenario_file(workspace, relative_name)
        for relative_name in scenario.files
    ]

    if workspace.exists() and not workspace.is_dir():
        raise ValueError(f"output exists and is not a directory: {workspace}")

    nonempty = workspace.exists() and any(workspace.iterdir())
    if nonempty and not force:
        raise FileExistsError(
            f"output directory is not empty: {workspace}; use --force to replace it"
        )

    if force:
        _validate_force_target(workspace)
        if workspace.exists():
            shutil.rmtree(workspace)

    workspace.mkdir(parents=True, exist_ok=True)

    for target, content in zip(targets, scenario.files.values(), strict=True):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return workspace
