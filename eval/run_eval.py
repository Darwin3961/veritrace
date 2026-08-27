from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coding_agent.agent import AgentLoop  # noqa: E402
from coding_agent.model import ModelAdapter  # noqa: E402
from coding_agent.registry import ToolRegistry  # noqa: E402
from demo.scenarios import (  # noqa: E402
    SCENARIOS,
    DemoScenario,
    create_scenario_workspace,
    get_scenario,
)


@dataclass
class EvalResult:
    scenario: str
    agent_stop_reason: str | None
    agent_result: str
    verification_passed: bool
    verification_exit_code: int | None
    steps: int
    model_calls: int
    tool_calls: int
    tool_failures: int
    policy_blocks: int
    total_tokens: int
    duration_ms: int


ModelFactory = Callable[[], Any]


def _verify_scenario(
    scenario: DemoScenario,
    workspace: Path,
) -> tuple[bool, int | None]:
    try:
        completed = subprocess.run(
            scenario.verification_command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None

    return completed.returncode == 0, completed.returncode


def _failure_result(
    scenario: DemoScenario,
    message: str,
    duration_ms: int,
) -> EvalResult:
    return EvalResult(
        scenario=scenario.name,
        agent_stop_reason="exception",
        agent_result=message,
        verification_passed=False,
        verification_exit_code=None,
        steps=0,
        model_calls=0,
        tool_calls=0,
        tool_failures=0,
        policy_blocks=0,
        total_tokens=0,
        duration_ms=duration_ms,
    )


def run_scenario(
    scenario: DemoScenario,
    *,
    max_steps: int = 20,
    keep_workspace: bool = False,
    workspace_parent: str | Path | None = None,
    model_factory: ModelFactory | None = None,
) -> EvalResult:
    """Run one agent scenario and verify it independently afterward."""
    started = time.monotonic()
    parent = (
        Path(workspace_parent).expanduser().resolve()
        if workspace_parent is not None
        else None
    )
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if keep_workspace:
        workspace = Path(
            tempfile.mkdtemp(prefix=f"agent-eval-{scenario.name}-", dir=parent)
        )
    else:
        temporary = tempfile.TemporaryDirectory(
            prefix=f"agent-eval-{scenario.name}-",
            dir=parent,
        )
        workspace = Path(temporary.name)

    try:
        create_scenario_workspace(scenario, workspace)
        resolved_model_factory = model_factory or ModelAdapter.from_env
        model = resolved_model_factory()
        tools = ToolRegistry(workspace)
        agent = AgentLoop(
            model,
            tools,
            max_steps=max_steps,
            trace_enabled=False,
        )
        agent_result = agent.run(scenario.task)
        verification_passed, verification_exit_code = _verify_scenario(
            scenario,
            workspace,
        )
        metrics = agent.last_metrics or {}

        return EvalResult(
            scenario=scenario.name,
            agent_stop_reason=agent.last_stop_reason,
            agent_result=agent_result,
            verification_passed=verification_passed,
            verification_exit_code=verification_exit_code,
            steps=int(metrics.get("steps", 0)),
            model_calls=int(metrics.get("model_calls", 0)),
            tool_calls=int(metrics.get("tool_calls", 0)),
            tool_failures=int(metrics.get("tool_failures", 0)),
            policy_blocks=int(metrics.get("policy_blocks", 0)),
            total_tokens=int(metrics.get("total_tokens", 0)),
            duration_ms=int(metrics.get(
                "duration_ms",
                (time.monotonic() - started) * 1000,
            )),
        )
    except Exception as exc:
        return _failure_result(
            scenario,
            f"evaluation failed: {exc}",
            int((time.monotonic() - started) * 1000),
        )
    finally:
        if temporary is not None:
            temporary.cleanup()


def run_scenarios(
    scenarios: Iterable[DemoScenario],
    *,
    max_steps: int = 20,
    keep_workspaces: bool = False,
    workspace_parent: str | Path | None = None,
    model_factory: ModelFactory | None = None,
) -> list[EvalResult]:
    return [
        run_scenario(
            scenario,
            max_steps=max_steps,
            keep_workspace=keep_workspaces,
            workspace_parent=workspace_parent,
            model_factory=model_factory,
        )
        for scenario in scenarios
    ]


def write_results(path: str | Path, results: list[EvalResult]) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            [asdict(result) for result in results],
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run reproducible coding-agent evaluation scenarios."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--scenario",
        choices=[scenario.name for scenario in SCENARIOS],
    )
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument("--output-json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.max_steps <= 0:
        print("Error: --max-steps must be greater than 0.", file=sys.stderr)
        return 2

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print(
            "Error: DEEPSEEK_API_KEY is required to run live evaluation.",
            file=sys.stderr,
        )
        return 2

    scenarios = list(SCENARIOS) if args.all else [get_scenario(args.scenario)]
    results = run_scenarios(
        scenarios,
        max_steps=args.max_steps,
        keep_workspaces=args.keep_workspaces,
    )

    for result in results:
        label = "PASS" if result.verification_passed else "FAIL"
        print(
            f"[{label}] {result.scenario}: "
            f"stop={result.agent_stop_reason} steps={result.steps} "
            f"tool_calls={result.tool_calls}"
        )

    if args.output_json:
        output = write_results(args.output_json, results)
        print(f"Results: {output}")

    return 0 if all(result.verification_passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
