"""In-memory log ring-buffer for the /admin/logs endpoint."""
import logging
import time
from collections import deque
from contextvars import ContextVar
from typing import Optional

# Context variable: current request-ID (set by RequestIDMiddleware)
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_LEVEL_NO: dict[str, int] = {
    "DEBUG":    logging.DEBUG,
    "INFO":     logging.INFO,
    "WARNING":  logging.WARNING,
    "ERROR":    logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# Shared ring-buffer (maxlen caps memory usage)
_buffer: deque = deque(maxlen=2000)


class InMemoryLogHandler(logging.Handler):
    """Logging handler that appends records to the shared ring-buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append({
                "ts":         record.created,
                "level":      record.levelname,
                "logger":     record.name,
                "msg":        self.format(record),
                "request_id": request_id_var.get("-"),
            })
        except Exception:
            self.handleError(record)


def query_logs(
    minutes: float = 5.0,
    min_level: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (page, total) from the ring-buffer.

    Args:
        minutes:   How many minutes back to look (0.1–1440).
        min_level: Minimum log level name (DEBUG/INFO/WARNING/ERROR/CRITICAL).
        limit:     Maximum entries to return (1–1000).
        offset:    Skip this many entries before returning.

    Returns:
        A 2-tuple of (entries_page, total_matching_count).
    """
    cutoff    = time.time() - minutes * 60
    min_no    = _LEVEL_NO.get(min_level, 0) if min_level else 0

    filtered = [
        entry for entry in _buffer
        if entry["ts"] >= cutoff
        and _LEVEL_NO.get(entry["level"], 0) >= min_no
    ]

    total = len(filtered)
    page  = filtered[offset: offset + limit]
    return page, total
