from __future__ import annotations

from coding_agent.context import ConversationContext
from coding_agent.model import (
    ModelAdapter,
    ModelResponseError,
)
from coding_agent.prompts import SYSTEM_PROMPT
from coding_agent.registry import ToolRegistry


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
    ):
        if max_steps <= 0:
            raise ValueError(
                "max_steps must be greater than 0"
            )

        if max_consecutive_model_errors < 0:
            raise ValueError(
                "max_consecutive_model_errors must be >= 0"
            )

        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_consecutive_model_errors = (
            max_consecutive_model_errors
        )

    def run(self, task: str) -> str:
        if not isinstance(task, str) or not task.strip():
            raise ValueError(
                "task must be a non-empty string"
            )

        context = ConversationContext(
            self.system_prompt
        )
        context.add_user(task)

        consecutive_model_errors = 0

        for _step in range(
            1,
            self.max_steps + 1,
        ):
            try:
                response = self.model.complete(
                    context.messages,
                    self.tools.schemas,
                )

                consecutive_model_errors = 0

            except ModelResponseError as exc:
                consecutive_model_errors += 1

                if (
                    consecutive_model_errors
                    > self.max_consecutive_model_errors
                ):
                    return (
                        "Agent stopped because the model "
                        "repeatedly returned invalid structured "
                        f"responses: {exc}"
                    )

                context.add_protocol_feedback(
                    "Your previous response could not be "
                    "parsed as a valid tool call. "
                    "Return valid tool arguments as a JSON "
                    "object, or provide a final answer if the "
                    "task is complete."
                )

                continue

            context.add_assistant(response)

            if response.has_tool_calls:
                for call in response.tool_calls:
                    result = self.tools.execute(call)
                    context.add_tool_result(result)

                continue

            final_answer = (
                response.content or ""
            ).strip()

            if final_answer:
                return final_answer

            context.add_protocol_feedback(
                "Your previous response contained neither "
                "a tool call nor a final answer. "
                "Use an available tool if more work is needed, "
                "or provide a concise final answer."
            )

        return (
            "Agent stopped after reaching the maximum "
            f"step limit ({self.max_steps})."
        )
