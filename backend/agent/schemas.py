"""
Request/response models for POST /api/interview.

These mirror the technical specification exactly:

Start:    {"sessionId": "...", "candidate": {...}}      -> {"reply": "...", "done": false}
Turn:     {"sessionId": "...", "message": "..."}         -> {"reply": "...", "done": false}
End:      (same as turn)                                 -> {"reply": "...", "done": true, "feedback": {...}}
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class InterviewRequest(BaseModel):
    sessionId: str
    candidate: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class Feedback(BaseModel):
    summary: str
    strengths: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    next: List[str] = Field(default_factory=list)


class InterviewResponse(BaseModel):
    reply: str
    done: bool
    feedback: Optional[Feedback] = None
    # Note: the route uses response_model_exclude_none=True so that
    # "feedback" is simply absent (not "feedback": null) on non-final turns,
    # matching the spec's examples exactly.
