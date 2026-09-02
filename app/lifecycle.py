from __future__ import annotations

import time
from threading import Lock
from typing import Callable


class ClientLifecycle:
    def __init__(self, idle_timeout: float = 10, shutdown_grace: float = 3, clock: Callable[[], float] | None = None):
        self.idle_timeout = idle_timeout
        self.shutdown_grace = shutdown_grace
        self._clock = clock or time.monotonic
        self._clients: dict[str, float] = {}
        self._idle_since: float | None = None
        self._lock = Lock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def heartbeat(self, client_id: str, active: bool = True) -> None:
        now = self._clock()
        with self._lock:
            if active:
                self._clients[client_id] = now
                self._idle_since = None
            else:
                self._clients.pop(client_id, None)
                if not self._clients:
                    self._idle_since = now

    def should_shutdown(self) -> bool:
        now = self._clock()
        with self._lock:
            stale = [client_id for client_id, last_seen in self._clients.items() if now - last_seen >= self.idle_timeout]
            for client_id in stale:
                self._clients.pop(client_id, None)
            if self._clients:
                self._idle_since = None
                return False
            if stale and self._idle_since is None:
                self._idle_since = now
            return self._idle_since is not None and now - self._idle_since >= self.shutdown_grace
