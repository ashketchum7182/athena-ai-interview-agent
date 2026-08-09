"""
In-memory interview session state.

Per the technical spec's "out of scope" list (no persistent accounts, no
long-term history required), a simple in-process dict guarded by a lock is
sufficient. Swap `SessionStore` for a Redis-backed implementation if this
ever needs to run across multiple worker processes -- nothing else in the
codebase depends on the storage mechanism.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .planner import PlannedTopic


@dataclass
class InterviewSession:
    session_id: str
    candidate: Dict[str, Any]
    plan: List[PlannedTopic]
    plan_index: int = 0
    followups_used_on_current: int = 0
    transcript: List[Dict[str, str]] = field(default_factory=list)  # [{"role": "user"/"assistant", "content": str}]
    questions_asked: int = 0
    days_covered: Set[int] = field(default_factory=set)
    assessment_log: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "in_progress"  # in_progress | concluded
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def member(self) -> Dict[str, Any]:
        return self.candidate.get("member", {})

    @property
    def current_topic(self) -> PlannedTopic:
        return self.plan[self.plan_index]


class SessionStore:
    def __init__(self):
        self._sessions: Dict[str, InterviewSession] = {}
        self._lock = threading.Lock()

    def create(self, session_id: str, candidate: Dict[str, Any], plan: List[PlannedTopic]) -> InterviewSession:
        with self._lock:
            session = InterviewSession(session_id=session_id, candidate=candidate, plan=plan)
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Optional[InterviewSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


# Process-wide singleton -- FastAPI runs single-process by default for this
# use case; see README for notes on scaling to multiple workers.
session_store = SessionStore()
