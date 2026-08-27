from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from coding_agent.context import ConversationContext
from coding_agent.events import Event
from coding_agent.model import (
    ModelAdapter,
    ModelResponseError,
)
from coding_agent.prompts import SYSTEM_PROMPT
from coding_agent.registry import ToolRegistry
from coding_agent.session import SessionTrace
from coding_agent.types import AgentResponse


class AgentLoop:
    """Single-agent observe-act loop for local coding tasks."""

    def __init__(
        self,
        model: ModelAdapter,
        tools: ToolRegistry,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_steps: int = 20,
        max_consecutive_model_errors: int = 2,
        trace_dir: str | Path | None = None,
        trace_enabled: bool = True,
        max_repeated_actions: int = 3,
        event_sink: Callable[[Event], None] | None = None,
    ):
        if max_steps <= 0:
            raise ValueError(
                "max_steps must be greater than 0"
            )

        if max_consecutive_model_errors < 0:
            raise ValueError(
                "max_consecutive_model_errors must be >= 0"
            )

        if max_repeated_actions < 0:
            raise ValueError(
                "max_repeated_actions must be >= 0"
            )

        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_consecutive_model_errors = (
            max_consecutive_model_errors
        )
        self.trace_dir = trace_dir
        self.trace_enabled = trace_enabled
        self.max_repeated_actions = max_repeated_actions
        self.event_sink = event_sink

        self.last_trace_path: Path | None = None
        self.last_metrics: dict[str, Any] | None = None
        self.last_stop_reason: str | None = None

    def _tool_fingerprint(
        self,
        response: AgentResponse,
    ) -> str:
        payload = [
            {
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in response.tool_calls
        ]

        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _finish(
        self,
        session: SessionTrace,
        *,
        stop_reason: str,
        final_answer: str,
    ) -> str:
        metrics = session.metrics()

        session.emit(
            "session_end",
            data={
                "stop_reason": stop_reason,
                "final_answer": final_answer,
                "metrics": metrics,
            },
        )

        self.last_metrics = metrics
        self.last_stop_reason = stop_reason
        self.last_trace_path = session.path

        return final_answer

    def run(self, task: str) -> str:
        if not isinstance(task, str) or not task.strip():
            raise ValueError(
                "task must be a non-empty string"
            )

        session = SessionTrace(
            trace_dir=self.trace_dir,
            enabled=self.trace_enabled,
            event_sink=self.event_sink,
        )

        self.last_trace_path = None
        self.last_metrics = None
        self.last_stop_reason = None

        session.emit(
            "session_start",
            data={
                "session_id": session.session_id,
                "max_steps": self.max_steps,
                "max_repeated_actions": self.max_repeated_actions,
            },
        )
        session.emit(
            "user_task",
            data={"task": task},
        )

        context = ConversationContext(
            self.system_prompt
        )
        context.add_user(task)

        consecutive_model_errors = 0
        previous_tool_fingerprint: str | None = None
        repeated_count = 0

        for step in range(
            1,
            self.max_steps + 1,
        ):
            session.record_step()
            session.emit("step_start", step=step)

            try:
                response = self.model.complete(
                    context.messages,
                    self.tools.schemas,
                )

                consecutive_model_errors = 0

            except ModelResponseError as exc:
                consecutive_model_errors += 1
                previous_tool_fingerprint = None
                repeated_count = 0

                session.record_model_error()
                session.emit(
                    "model_error",
                    step=step,
                    data={
                        "error": str(exc),
                        "consecutive_errors": consecutive_model_errors,
                    },
                )

                if (
                    consecutive_model_errors
                    > self.max_consecutive_model_errors
                ):
                    final_answer = (
                        "Agent stopped because the model "
                        "repeatedly returned invalid structured "
                        f"responses: {exc}"
                    )
                    return self._finish(
                        session,
                        stop_reason="model_error_limit",
                        final_answer=final_answer,
                    )

                context.add_protocol_feedback(
                    "Your previous response could not be "
                    "parsed as a valid tool call. "
                    "Return valid tool arguments as a JSON "
                    "object, or provide a final answer if the "
                    "task is complete."
                )

                continue

            except Exception:
                self._finish(
                    session,
                    stop_reason="exception",
                    final_answer="",
                )
                raise

            session.record_model_call(response.usage)
            session.emit(
                "assistant_response",
                step=step,
                data={
                    "content": response.content,
                    "finish_reason": response.finish_reason,
                    "tool_call_count": len(response.tool_calls),
                    "usage": response.usage,
                },
            )

            context.add_assistant(response)

            if response.has_tool_calls:
                fingerprint = self._tool_fingerprint(response)

                if fingerprint == previous_tool_fingerprint:
                    repeated_count += 1
                else:
                    previous_tool_fingerprint = fingerprint
                    repeated_count = 1

                if (
                    self.max_repeated_actions > 0
                    and repeated_count >= self.max_repeated_actions
                ):
                    session.emit(
                        "no_progress",
                        step=step,
                        data={
                            "fingerprint": fingerprint,
                            "repeated_count": repeated_count,
                        },
                    )
                    final_answer = (
                        "Agent stopped because the same tool action "
                        f"repeated {repeated_count} consecutive times "
                        "without progress."
                    )
                    return self._finish(
                        session,
                        stop_reason="no_progress",
                        final_answer=final_answer,
                    )

                for call in response.tool_calls:
                    call_event = session.emit(
                        "tool_call",
                        step=step,
                        data={
                            "call_id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    )

                    result = self.tools.execute(call)
                    session.record_tool_result(
                        ok=result.ok,
                        policy_blocked=bool(
                            result.metadata.get(
                                "policy_blocked",
                                False,
                            )
                        ),
                    )
                    session.emit(
                        "tool_result",
                        step=step,
                        source_seq=call_event.seq,
                        data={
                            "call_id": result.call_id,
                            "tool_name": result.tool_name,
                            "ok": result.ok,
                            "output": result.output,
                            "error": result.error,
                            "metadata": result.metadata,
                        },
                    )
                    context.add_tool_result(result)

                continue

            previous_tool_fingerprint = None
            repeated_count = 0

            final_answer = (
                response.content or ""
            ).strip()

            if final_answer:
                return self._finish(
                    session,
                    stop_reason="completed",
                    final_answer=final_answer,
                )

            context.add_protocol_feedback(
                "Your previous response contained neither "
                "a tool call nor a final answer. "
                "Use an available tool if more work is needed, "
                "or provide a concise final answer."
            )

        final_answer = (
            "Agent stopped after reaching the maximum "
            f"step limit ({self.max_steps})."
        )
        return self._finish(
            session,
            stop_reason="max_steps",
            final_answer=final_answer,
        )
