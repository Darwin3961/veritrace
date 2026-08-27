from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coding_agent.agent import AgentLoop
from coding_agent.model import ModelAdapter
from coding_agent.registry import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "A lightweight local coding agent."
        )
    )

    parser.add_argument(
        "task",
        nargs="?",
        help="Programming task for the agent.",
    )

    parser.add_argument(
        "--workspace",
        default=".",
        help=(
            "Workspace directory the agent may operate in. "
            "Defaults to the current directory."
        ),
    )

    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum model/tool iterations.",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    task = args.task

    if task is None:
        try:
            task = input("Task: ").strip()
        except EOFError:
            task = ""

    if not task:
        print(
            "Error: task must not be empty.",
            file=sys.stderr,
        )
        return 2

    workspace = Path(
        args.workspace
    ).expanduser().resolve()

    try:
        model = ModelAdapter.from_env()

        tools = ToolRegistry(
            workspace
        )

        agent = AgentLoop(
            model,
            tools,
            max_steps=args.max_steps,
        )

        result = agent.run(task)

    except KeyboardInterrupt:
        print(
            "\nInterrupted by user.",
            file=sys.stderr,
        )
        return 130

    except Exception as exc:
        print(
            f"Error: {exc}",
            file=sys.stderr,
        )
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
