from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Event:
    """One structured fact produced during an agent session."""

    seq: int
    type: str
    timestamp: str
    step: int | None = None
    source_seq: int | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        seq: int,
        event_type: str,
        *,
        step: int | None = None,
        source_seq: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> "Event":
        return cls(
            seq=seq,
            type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            step=step,
            source_seq=source_seq,
            data=data or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
