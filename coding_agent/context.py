from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from coding_agent.types import AgentResponse, ToolResult


class ConversationContext:
    """Own the conversation history sent to the model."""

    def __init__(self, system_prompt: str):
        if not system_prompt.strip():
            raise ValueError(
                "system_prompt must not be empty"
            )

        self._messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

    @property
    def messages(self) -> list[dict[str, Any]]:
        return deepcopy(self._messages)

    def add_user(self, content: str) -> None:
        if not content.strip():
            raise ValueError(
                "user message must not be empty"
            )

        self._messages.append(
            {
                "role": "user",
                "content": content,
            }
        )

    def add_assistant(
        self,
        response: AgentResponse,
    ) -> None:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": response.content,
        }

        if response.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                        ),
                    },
                }
                for call in response.tool_calls
            ]

        self._messages.append(message)

    def add_tool_result(
        self,
        result: ToolResult,
    ) -> None:
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": result.call_id,
                "content": result.to_model_content(),
            }
        )

    def add_protocol_feedback(
        self,
        message: str,
    ) -> None:
        """
        Add a concise recovery instruction after an invalid model response.

        This is deliberately represented as a user-style message because
        there is no valid assistant message to append when parsing failed.
        """
        self._messages.append(
            {
                "role": "user",
                "content": message,
            }
        )
