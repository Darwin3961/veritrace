from __future__ import annotations

import argparse
import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo.scenarios import (  # noqa: E402
    SCENARIOS,
    create_scenario_workspace,
    get_scenario,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a deterministic coding-agent demo workspace."
    )
    parser.add_argument(
        "--scenario",
        required=True,
        choices=[scenario.name for scenario in SCENARIOS],
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace a validated existing output directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scenario = get_scenario(args.scenario)

    try:
        workspace = create_scenario_workspace(
            scenario,
            args.output,
            force=args.force,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Scenario: {scenario.name}")
    print(f"Workspace: {workspace}")
    print(f"Task: {scenario.task}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
