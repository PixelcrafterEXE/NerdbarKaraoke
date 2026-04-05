"""In-memory ring-buffer log handler for the admin dashboard.

Captures log records in a fixed-size deque and optionally emits them
in real-time via SocketIO.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any


class LogBufferHandler(logging.Handler):
    """A logging handler that stores records in a bounded deque.

    New records are appended to the right; when the buffer is full the
    oldest record is silently discarded.

    Attributes:
        buffer: Bounded deque of serialised log dicts.
        socketio: Optional SocketIO instance for real-time emission.
    """

    def __init__(self, capacity: int = 500, socketio: Any | None = None) -> None:
        super().__init__()
        self.buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.socketio = socketio

    def emit(self, record: logging.LogRecord) -> None:
        entry = self._serialise(record)
        self.buffer.append(entry)
        if self.socketio:
            self.socketio.emit("log_entry", entry, namespace="/")

    def get_entries(self, level: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """Return buffered entries, optionally filtered by minimum level.

        Args:
            level: Minimum log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            limit: Maximum number of entries to return (newest first when truncated).

        Returns:
            List of serialised log dicts, oldest first.
        """
        entries = list(self.buffer)
        if level:
            min_level = getattr(logging, level.upper(), logging.DEBUG)
            entries = [e for e in entries if e["levelno"] >= min_level]
        if limit and len(entries) > limit:
            entries = entries[-limit:]
        return entries

    @staticmethod
    def _serialise(record: logging.LogRecord) -> dict[str, Any]:
        return {
            "timestamp": record.created,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "levelno": record.levelno,
            "name": record.name,
            "message": record.getMessage(),
        }
