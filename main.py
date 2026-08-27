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

    parser.add_argument(
        "--trace-dir",
        default=str(
            Path(__file__).resolve().parent / "traces"
        ),
        help="Directory for append-only session trace files.",
    )

    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Disable writing the session trace file.",
    )

    parser.add_argument(
        "--max-repeated-actions",
        type=int,
        default=3,
        help=(
            "Stop after this many consecutive identical tool actions; "
            "use 0 to disable the guard."
        ),
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
            trace_dir=args.trace_dir,
            trace_enabled=not args.no_trace,
            max_repeated_actions=args.max_repeated_actions,
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

    if not args.no_trace and agent.last_trace_path:
        print(f"Trace: {agent.last_trace_path}")

    if agent.last_metrics:
        metrics = agent.last_metrics
        print(
            "Metrics: "
            f"steps={metrics['steps']} "
            f"model_calls={metrics['model_calls']} "
            f"tool_calls={metrics['tool_calls']} "
            f"tool_failures={metrics['tool_failures']} "
            f"duration_ms={metrics['duration_ms']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
