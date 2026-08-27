from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from coding_agent.types import AgentResponse, ToolCall


class ModelResponseError(RuntimeError):
    """Raised when a model response cannot be normalized safely."""


class ModelAdapter:
    """
    Provider-isolated adapter for OpenAI-compatible chat completion APIs.

    The rest of the agent works only with our own AgentResponse and ToolCall
    types rather than provider SDK response objects.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str | None = None,
        client: Any | None = None,
        disable_thinking: bool = True,
    ):
        self.base_url = (
            base_url
            or os.environ.get("MODEL_BASE_URL")
            or "https://api.deepseek.com"
        )

        self.model_name = (
            model_name
            or os.environ.get("MODEL_NAME")
            or "deepseek-v4-flash"
        )

        self.disable_thinking = disable_thinking

        if client is not None:
            self.client = client
            return

        resolved_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
        )

        if not resolved_key:
            raise ValueError(
                "Missing API key. Set DEEPSEEK_API_KEY "
                "or provide api_key explicitly."
            )

        self.client = OpenAI(
            api_key=resolved_key,
            base_url=self.base_url,
        )

    @classmethod
    def from_env(cls) -> "ModelAdapter":
        return cls()

    def _usage_to_dict(self, usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}

        if hasattr(usage, "model_dump"):
            return usage.model_dump()

        if isinstance(usage, dict):
            return dict(usage)

        result: dict[str, Any] = {}

        for name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ):
            value = getattr(usage, name, None)

            if value is not None:
                result[name] = value

        return result

    def _parse_tool_calls(
        self,
        raw_tool_calls: Any,
    ) -> list[ToolCall]:
        normalized: list[ToolCall] = []

        for raw_call in raw_tool_calls or []:
            call_id = getattr(raw_call, "id", None)
            function = getattr(raw_call, "function", None)

            if not call_id or function is None:
                raise ModelResponseError(
                    "Model returned an invalid tool call."
                )

            name = getattr(function, "name", None)
            raw_arguments = getattr(
                function,
                "arguments",
                None,
            )

            if not name:
                raise ModelResponseError(
                    "Model returned a tool call without a name."
                )

            if raw_arguments is None:
                raw_arguments = "{}"

            if isinstance(raw_arguments, dict):
                arguments = raw_arguments
            else:
                try:
                    arguments = json.loads(raw_arguments)
                except (
                    json.JSONDecodeError,
                    TypeError,
                ) as exc:
                    raise ModelResponseError(
                        f"Invalid JSON arguments for tool "
                        f"{name}: {raw_arguments!r}"
                    ) from exc

            if not isinstance(arguments, dict):
                raise ModelResponseError(
                    f"Tool arguments for {name} must decode "
                    f"to a JSON object."
                )

            normalized.append(
                ToolCall(
                    id=str(call_id),
                    name=str(name),
                    arguments=arguments,
                )
            )

        return normalized

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        request: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
        }

        # DeepSeek thinking mode requires additional history handling.
        # The first stable agent version intentionally disables it.
        if (
            self.disable_thinking
            and "deepseek.com" in self.base_url.lower()
        ):
            request["extra_body"] = {
                "thinking": {
                    "type": "disabled",
                }
            }

        response = self.client.chat.completions.create(
            **request
        )

        choices = getattr(response, "choices", None)

        if not choices:
            raise ModelResponseError(
                "Model response contains no choices."
            )

        choice = choices[0]
        message = getattr(choice, "message", None)

        if message is None:
            raise ModelResponseError(
                "Model response contains no assistant message."
            )

        content = getattr(message, "content", None)

        tool_calls = self._parse_tool_calls(
            getattr(message, "tool_calls", None)
        )

        finish_reason = getattr(
            choice,
            "finish_reason",
            None,
        )

        return AgentResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self._usage_to_dict(
                getattr(response, "usage", None)
            ),
        )
