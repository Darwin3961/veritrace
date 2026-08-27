from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coding_agent.events import Event


class SessionTrace:
    """
    Append-only structured trace for one agent run.

    Trace files are local runtime artifacts and must not contain
    known credential values.
    """

    SECRET_KEY_PATTERN = re.compile(
        r"(api[_-]?key|token|secret|password|authorization)",
        re.IGNORECASE,
    )

    SECRET_VALUE_PATTERNS = [
        re.compile(
            r"\bsk-[A-Za-z0-9_-]{8,}\b"
        ),
        re.compile(
            r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b",
            re.IGNORECASE,
        ),
    ]

    def __init__(
        self,
        trace_dir: str | Path | None = None,
        *,
        enabled: bool = True,
        max_preview_chars: int = 4000,
    ):
        if max_preview_chars < 100:
            raise ValueError(
                "max_preview_chars must be at least 100"
            )

        self.enabled = enabled
        self.max_preview_chars = max_preview_chars

        self.session_id = uuid.uuid4().hex
        self._events: list[Event] = []
        self._next_seq = 1
        self._started_monotonic = time.monotonic()

        self._metrics = {
            "steps": 0,
            "model_calls": 0,
            "model_errors": 0,
            "tool_calls": 0,
            "tool_failures": 0,
            "policy_blocks": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        self.path: Path | None = None

        if self.enabled:
            directory = Path(
                trace_dir or "traces"
            ).expanduser().resolve()

            directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            timestamp = datetime.now(
                timezone.utc
            ).strftime("%Y%m%dT%H%M%S.%fZ")

            self.path = (
                directory
                / f"session-{timestamp}-{self.session_id[:8]}.jsonl"
            )

    @property
    def events(self) -> list[Event]:
        return deepcopy(self._events)

    def _preview(
        self,
        value: str,
    ) -> str:
        if len(value) <= self.max_preview_chars:
            return value

        available = self.max_preview_chars - 80
        head = max(1, available // 2)
        tail = max(1, available - head)
        omitted = len(value) - head - tail

        return (
            value[:head]
            + f"\n... <{omitted} characters omitted from trace> ...\n"
            + value[-tail:]
        )

    def _redact_string(
        self,
        value: str,
    ) -> str:
        result = value

        for pattern in self.SECRET_VALUE_PATTERNS:
            result = pattern.sub(
                "[REDACTED]",
                result,
            )

        return self._preview(result)

    def _redact(
        self,
        value: Any,
        *,
        key: str | None = None,
    ) -> Any:
        if (
            key is not None
            and self.SECRET_KEY_PATTERN.search(key)
        ):
            return "[REDACTED]"

        if isinstance(value, dict):
            return {
                str(item_key): self._redact(
                    item_value,
                    key=str(item_key),
                )
                for item_key, item_value in value.items()
            }

        if isinstance(value, list):
            return [
                self._redact(item)
                for item in value
            ]

        if isinstance(value, tuple):
            return [
                self._redact(item)
                for item in value
            ]

        if isinstance(value, str):
            return self._redact_string(value)

        return value

    def emit(
        self,
        event_type: str,
        *,
        step: int | None = None,
        source_seq: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        event = Event.create(
            seq=self._next_seq,
            event_type=event_type,
            step=step,
            source_seq=source_seq,
            data=self._redact(
                data or {}
            ),
        )

        self._next_seq += 1
        self._events.append(event)

        if (
            self.enabled
            and self.path is not None
        ):
            with self.path.open(
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write(
                    json.dumps(
                        event.to_dict(),
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        return event

    def record_step(self) -> None:
        self._metrics["steps"] += 1

    def record_model_call(
        self,
        usage: dict[str, Any] | None,
    ) -> None:
        self._metrics["model_calls"] += 1

        if not usage:
            return

        prompt = int(
            usage.get(
                "prompt_tokens",
                usage.get(
                    "input_tokens",
                    0,
                ),
            )
            or 0
        )

        completion = int(
            usage.get(
                "completion_tokens",
                usage.get(
                    "output_tokens",
                    0,
                ),
            )
            or 0
        )

        total = int(
            usage.get(
                "total_tokens",
                prompt + completion,
            )
            or 0
        )

        self._metrics["prompt_tokens"] += prompt
        self._metrics["completion_tokens"] += completion
        self._metrics["total_tokens"] += total

    def record_model_error(self) -> None:
        self._metrics["model_errors"] += 1

    def record_tool_result(
        self,
        *,
        ok: bool,
        policy_blocked: bool = False,
    ) -> None:
        self._metrics["tool_calls"] += 1

        if not ok:
            self._metrics["tool_failures"] += 1

        if policy_blocked:
            self._metrics["policy_blocks"] += 1

    def metrics(self) -> dict[str, Any]:
        result = dict(self._metrics)

        result["duration_ms"] = int(
            (
                time.monotonic()
                - self._started_monotonic
            )
            * 1000
        )

        return result
