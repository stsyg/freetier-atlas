"""Shared runtime helpers for the long-running worker and scheduler loops.

Provides graceful shutdown on SIGTERM/SIGINT and an interruptible sleep so a
container stops promptly instead of waiting out its poll interval.
"""

from __future__ import annotations

import logging
import signal
import threading

logger = logging.getLogger("freetier_atlas.worker")


class ShutdownSignal:
    """Cooperative shutdown flag wired to SIGTERM and SIGINT.

    Falls back gracefully on platforms or threads where signal handlers cannot
    be installed (for example, non-main threads during tests).
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle)
            except (ValueError, OSError, RuntimeError):
                # Not on the main thread or unsupported signal; skip quietly.
                pass

    def _handle(self, signum: int, _frame: object) -> None:
        logger.info("Received signal %s; shutting down gracefully.", signum)
        self._event.set()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds`` or until shutdown is requested.

        Returns ``True`` when shutdown was requested during the wait.
        """

        return self._event.wait(timeout=seconds)
