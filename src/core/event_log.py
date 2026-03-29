from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from src.utils.logger import get_logger

T = TypeVar("T")


@dataclass
class Event:
    """Structured event log entry."""

    seq: int
    ts: str  # ISO 8601 UTC
    type: str
    agent_id: str
    payload: dict[str, Any]


class EventLog:
    """
    Append-only event log with in-memory ring buffer and pub/sub.

    Persists events to data/event_log_{agent_id}.jsonl.
    """

    def __init__(
        self,
        agent_id: str,
        data_dir: str | Path = "data",
        ring_buffer_size: int = 1000,
    ) -> None:
        self.agent_id = agent_id
        self.data_dir = Path(data_dir)
        self.ring_buffer_size = ring_buffer_size
        self._ring_buffer: deque[Event] = deque(maxlen=ring_buffer_size)
        self._seq = 0
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, list[Callable[[Event], Coroutine[Any, Any, None]]]] = {}
        self._global_subscribers: list[Callable[[Event], Coroutine[Any, Any, None]]] = []
        self._logger = get_logger(f"event_log.{agent_id}")

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / f"event_log_{agent_id}.jsonl"

        # Recover sequence number from existing log
        self._recover_state()

    def _recover_state(self) -> None:
        """Recover last sequence number and populate ring buffer from disk."""
        if not self.file_path.exists():
            return

        try:
            # Read last N lines for ring buffer and sequence recovery
            # For simplicity in this implementation, we read the whole file if small,
            # or just tail it. Given standard log sizes, reading all lines at startup
            # is acceptable for now, but in production we'd use `seek` from end.

            with open(self.file_path, encoding="utf-8") as f:
                lines = f.readlines()

            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    event = Event(**data)
                    self._ring_buffer.append(event)
                    self._seq = max(self._seq, event.seq)
                except json.JSONDecodeError:
                    self._logger.warning("Failed to decode event log line: %s", line)
                    continue

            self._logger.info(
                f"Recovered event log state. Last seq: {self._seq}, buffer: {len(self._ring_buffer)}"
            )

        except Exception as e:
            self._logger.error(f"Failed to recover event log: {e}")

    async def log(self, event_type: str, payload: dict[str, Any]) -> Event:
        """
        Log an event to disk and notify subscribers.

        Args:
            event_type: Category of event (e.g., 'order_created', 'risk_check')
            payload: Structured data for the event

        Returns:
            The created Event object
        """
        async with self._lock:
            self._seq += 1
            event = Event(
                seq=self._seq,
                ts=datetime.now(UTC).isoformat(),
                type=event_type,
                agent_id=self.agent_id,
                payload=payload,
            )

            # 1. Update ring buffer
            self._ring_buffer.append(event)

            # 2. Append to disk (non-blocking via thread)
            await asyncio.to_thread(self._append_to_disk, event)

            # 3. Notify subscribers (fire and forget / parallel)
            self._notify_subscribers(event)

            return event

    def _append_to_disk(self, event: Event) -> None:
        """Blocking write to disk."""
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event)) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to write event to disk: {e}")

    def _notify_subscribers(self, event: Event) -> None:
        """Dispatch event to subscribers."""
        # Global subscribers
        for callback in self._global_subscribers:
            asyncio.create_task(self._safe_callback(callback, event))

        # Type-specific subscribers
        if event.type in self._subscribers:
            for callback in self._subscribers[event.type]:
                asyncio.create_task(self._safe_callback(callback, event))

    async def _safe_callback(
        self, callback: Callable[[Event], Coroutine[Any, Any, None]], event: Event
    ) -> None:
        """Execute subscriber callback safely."""
        try:
            await callback(event)
        except Exception as e:
            self._logger.error(f"Subscriber failed for event {event.type}: {e}")

    def subscribe(
        self, event_type: str | None, callback: Callable[[Event], Coroutine[Any, Any, None]]
    ) -> None:
        """
        Subscribe to events.

        Args:
            event_type: Specific event type to listen for, or None for all events.
            callback: Async function to call with the event.
        """
        if event_type is None:
            self._global_subscribers.append(callback)
        else:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def get_recent(self, limit: int = 100) -> list[Event]:
        """Get N most recent events from memory."""
        return list(self._ring_buffer)[-limit:]

    def get_recent_by_type(self, event_type: str, limit: int = 100) -> list[Event]:
        """Get N most recent events of a specific type from memory."""
        matching = [e for e in self._ring_buffer if e.type == event_type]
        return matching[-limit:]
