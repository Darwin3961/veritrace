from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from demo.create_demo_workspace import main
from demo.scenarios import (
    PROJECT_ROOT,
    SCENARIOS,
    create_scenario_workspace,
    get_scenario,
)


def test_three_complete_unique_scenarios():
    assert {scenario.name for scenario in SCENARIOS} == {
        "bugfix",
        "implement",
        "multi_file",
    }
    assert len({scenario.name for scenario in SCENARIOS}) == len(SCENARIOS)

    for scenario in SCENARIOS:
        assert scenario.description
        assert scenario.task
        assert scenario.files
        assert scenario.verification_command[0]
        assert scenario.verification_command[1:] == ["-m", "pytest", "-q"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_create_each_workspace(tmp_path: Path, scenario):
    workspace = create_scenario_workspace(scenario, tmp_path / scenario.name)

    assert workspace.is_dir()
    for relative_name, content in scenario.files.items():
        assert (workspace / relative_name).read_text(encoding="utf-8") == content


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError, match="unknown scenario"):
        get_scenario("missing")


def test_nonempty_output_requires_force(tmp_path: Path):
    output = tmp_path / "demo"
    output.mkdir()
    marker = output / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_scenario_workspace(get_scenario("bugfix"), output)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_force_replaces_valid_target(tmp_path: Path):
    output = tmp_path / "demo"
    output.mkdir()
    (output / "old.txt").write_text("old", encoding="utf-8")

    create_scenario_workspace(get_scenario("bugfix"), output, force=True)

    assert not (output / "old.txt").exists()
    assert (output / "calc.py").exists()


@pytest.mark.parametrize(
    "target",
    [Path.home(), PROJECT_ROOT, Path(PROJECT_ROOT.anchor)],
)
def test_force_rejects_protected_targets(target: Path):
    with pytest.raises(ValueError, match="refusing"):
        create_scenario_workspace(get_scenario("bugfix"), target, force=True)


def test_scenario_path_escape_is_rejected(tmp_path: Path):
    scenario = replace(
        get_scenario("bugfix"),
        files={"../outside.py": "bad"},
    )

    with pytest.raises(ValueError, match="escapes"):
        create_scenario_workspace(scenario, tmp_path / "workspace")

    assert not (tmp_path / "outside.py").exists()


def test_absolute_scenario_path_is_rejected(tmp_path: Path):
    scenario = replace(
        get_scenario("bugfix"),
        files={str((tmp_path / "outside.py").resolve()): "bad"},
    )

    with pytest.raises(ValueError, match="relative"):
        create_scenario_workspace(scenario, tmp_path / "workspace")


def test_cli_prints_scenario_workspace_and_task(tmp_path: Path, capsys):
    output = tmp_path / "demo"

    assert main(["--scenario", "bugfix", "--output", str(output)]) == 0

    stdout = capsys.readouterr().out
    assert "Scenario: bugfix" in stdout
    assert f"Workspace: {output.resolve()}" in stdout
    assert "Task:" in stdout
