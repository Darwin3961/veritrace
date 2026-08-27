from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coding_agent.agent import AgentLoop
from coding_agent.git_utils import GitInspector, GitSummary
from coding_agent.model import ModelAdapter
from coding_agent.registry import ToolRegistry
from coding_agent.renderer import RichRenderer
from coding_agent.verification import (
    VerificationSummary,
    summarize_verification,
)


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

    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use plain text output instead of Rich rendering.",
    )

    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="Skip Git workspace status and diff inspection.",
    )

    return parser


def _print_plain_summary(
    *,
    result: str,
    metrics: dict,
    stop_reason: str | None,
    verification: VerificationSummary,
    git_summary: GitSummary | None,
    trace_path: Path | None,
) -> None:
    print(result)

    if trace_path is not None:
        print(f"Trace: {trace_path}")

    if metrics:
        metric_order = (
            "steps",
            "model_calls",
            "model_errors",
            "tool_calls",
            "tool_failures",
            "policy_blocks",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "duration_ms",
        )
        metric_text = " ".join(
            f"{name}={metrics[name]}"
            for name in metric_order
            if name in metrics
        )
        print(f"Metrics: {metric_text}")

    if verification.tests_likely_ran:
        test_status = (
            f"test_commands_succeeded="
            f"{verification.successful_test_commands} "
            f"test_commands_failed="
            f"{verification.failed_test_commands}"
        )
    else:
        test_status = "no explicit test command detected"

    print(
        "Verification: "
        f"commands_succeeded={verification.successful_commands} "
        f"commands_failed={verification.failed_commands} "
        f"commands_timed_out={verification.timed_out_commands} "
        f"file_changes={verification.successful_file_changes} "
        f"policy_blocks={verification.policy_blocks} "
        f"{test_status}"
    )

    if stop_reason:
        print(f"Stop reason: {stop_reason}")

    if git_summary is None:
        return

    if not git_summary.is_repo:
        if git_summary.error:
            print(f"Git unavailable: {git_summary.error}")

        return

    if git_summary.error:
        print(f"Git inspection failed: {git_summary.error}")
        return

    if not git_summary.status_short:
        print("Git: no workspace changes")
        return

    print("Git status:")
    print(git_summary.status_short)

    if git_summary.diff_stat:
        print("Git diff stat:")
        print(git_summary.diff_stat)

    if git_summary.diff_text:
        print("Git diff:")
        print(git_summary.diff_text)


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
        renderer = (
            None
            if args.plain
            else RichRenderer()
        )

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
            event_sink=(
                renderer.handle_event
                if renderer is not None
                else None
            ),
        )

        result = agent.run(task)
        verification = summarize_verification(
            agent.last_events
        )
        git_summary = (
            None
            if args.no_diff
            else GitInspector(workspace).inspect()
        )

        if renderer is not None:
            renderer.render_final(
                result=result,
                metrics=agent.last_metrics or {},
                stop_reason=agent.last_stop_reason,
                verification=verification,
                git_summary=git_summary,
                trace_path=agent.last_trace_path,
            )
        else:
            _print_plain_summary(
                result=result,
                metrics=agent.last_metrics or {},
                stop_reason=agent.last_stop_reason,
                verification=verification,
                git_summary=git_summary,
                trace_path=agent.last_trace_path,
            )

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
