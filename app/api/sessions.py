"""One object per student session, so two students never share the course being discussed. Memory is
bounded: idle sessions expire, and when the store is full the least recently used one goes.

Not thread safe by itself: the API only uses it while holding its single chat lock."""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Callable, Tuple


class SessionStore:
    def __init__(self, create: Callable[[], Any], max_sessions: int, ttl_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.create = create
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._sessions: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()    # oldest use first

    def __len__(self) -> int:
        return len(self._sessions)

    def get(self, session_id: str) -> Any:
        now = self.clock()
        while self._sessions and now - next(iter(self._sessions.values()))[0] > self.ttl_seconds:
            self._sessions.popitem(last=False)
        entry = self._sessions.pop(session_id, None)
        session = entry[1] if entry else self.create()
        self._sessions[session_id] = (now, session)
        if len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
        return session
