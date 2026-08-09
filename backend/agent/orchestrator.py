"""
InterviewAgent -- the orchestration layer between the FastAPI route and
everything else (planner, session store, LLM client, prompts).

Control-flow philosophy
------------------------
The LLM is trusted to be *creative* (question wording, follow-up judgment,
difficulty calibration, feedback synthesis) but never trusted to be the sole
enforcer of the *hard requirements* from the technical spec (>= 8 questions,
>= 4 distinct days). Those are guaranteed structurally:

  * `planner.build_plan()` deterministically selects >= 8 topics across
    >= 4 distinct curriculum days before the interview starts.
  * The orchestrator walks that plan turn by turn. The LLM may insert up to
    `MAX_FOLLOWUPS_PER_TOPIC` follow-ups per topic, but cannot skip a
    planned topic or end the interview before the last planned topic is
    reached.
  * A hard `MAX_TOTAL_QUESTIONS` safety valve forces a close even if the
    model tries to keep following up indefinitely.

This means the *minimums are met by construction*, and the interesting,
non-deterministic part of the system -- realistic, adaptive, context-aware
questioning -- is exactly where the LLM's judgment is actually used.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .llm import LLMClient, get_llm_client
from .planner import MIN_TOPICS, build_plan
from .prompts import MAX_FOLLOWUPS_PER_TOPIC, build_feedback_system_prompt, build_turn_system_prompt
from .session import InterviewSession, session_store

MIN_DAYS = 4
MAX_TOTAL_QUESTIONS = 16  # hard safety valve; the plan alone should rarely need it

_CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "data" / "curriculum.json"
with open(_CURRICULUM_PATH, "r", encoding="utf-8") as f:
    CURRICULUM: Dict[str, Any] = json.load(f)


class InterviewError(ValueError):
    """Raised for malformed requests -- caught by the route and turned into HTTP 400."""


class InterviewAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or get_llm_client()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start_interview(self, session_id: str, candidate: Dict[str, Any]) -> str:
        if not isinstance(candidate, dict) or "member" not in candidate or "missions" not in candidate:
            raise InterviewError("candidate must include 'member' and 'missions'.")

        existing = session_store.get(session_id)
        if existing is not None:
            # Idempotent restart guard: if the client resends the start
            # payload for a session that's already running, just replay the
            # opening reply rather than silently starting a second plan.
            if existing.transcript:
                return existing.transcript[0]["content"]

        plan = build_plan(candidate, CURRICULUM)
        if len(plan) < MIN_DAYS:
            raise InterviewError(
                "Could not build a sufficient interview plan from this candidate's mission "
                "history against the provided curriculum (need coverage of at least "
                f"{MIN_DAYS} curriculum days)."
            )

        session = session_store.create(session_id, candidate, plan)
        session.days_covered.add(plan[0].day)

        system_prompt = build_turn_system_prompt(
            member=session.member,
            plan=plan,
            plan_index=0,
            followups_used_on_current=0,
            questions_asked=0,
            days_covered=[],
            min_questions=MIN_TOPICS,
            min_days=MIN_DAYS,
            is_opening=True,
        )
        turn = self.llm.run_turn(system_prompt, messages=[])

        session.transcript.append({"role": "assistant", "content": turn.reply})
        session.questions_asked = 1
        return turn.reply

    def continue_interview(self, session_id: str, message: str) -> Tuple[str, bool, Optional[Dict[str, Any]]]:
        session = session_store.get(session_id)
        if session is None:
            raise InterviewError(f"No interview session found for sessionId={session_id!r}.")
        if session.status == "concluded":
            raise InterviewError("This interview has already concluded.")

        session.transcript.append({"role": "user", "content": message})

        naturally_last = session.plan_index == len(session.plan) - 1
        force_last = (not naturally_last) and session.questions_asked >= MAX_TOTAL_QUESTIONS - 1
        treat_as_last = naturally_last or force_last
        at_followup_cap = session.followups_used_on_current >= MAX_FOLLOWUPS_PER_TOPIC

        system_prompt = build_turn_system_prompt(
            member=session.member,
            plan=session.plan,
            plan_index=session.plan_index,
            followups_used_on_current=session.followups_used_on_current,
            questions_asked=session.questions_asked,
            days_covered=list(session.days_covered),
            min_questions=MIN_TOPICS,
            min_days=MIN_DAYS,
            is_opening=False,
            force_last=force_last,
        )
        turn = self.llm.run_turn(system_prompt, session.transcript)

        session.assessment_log.append(
            {
                "day": session.current_topic.day,
                "quality": turn.previous_answer_quality,
                "note": turn.previous_answer_note,
            }
        )
        session.transcript.append({"role": "assistant", "content": turn.reply})

        # Defensive overrides: never trust the model alone to respect the
        # follow-up cap or the "must close on the last topic" rule.
        is_followup = turn.is_followup and not at_followup_cap
        concluding = treat_as_last and not is_followup

        if concluding:
            session.status = "concluded"
            feedback = self._generate_feedback(session)
            return turn.reply, True, feedback

        if is_followup:
            session.followups_used_on_current += 1
        else:
            session.plan_index += 1
            session.followups_used_on_current = 0
            session.days_covered.add(session.current_topic.day)

        session.questions_asked += 1
        return turn.reply, False, None

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _generate_feedback(self, session: InterviewSession) -> Dict[str, Any]:
        system_prompt = build_feedback_system_prompt(
            member=session.member,
            plan=session.plan,
            assessment_log=session.assessment_log,
        )
        result = self.llm.run_feedback(system_prompt, session.transcript)
        return {
            "summary": result.summary,
            "strengths": result.strengths,
            "gaps": result.gaps,
            "next": result.next,
        }
