"""
LLM client abstraction.

Two implementations behind one interface:

  * AnthropicLLMClient -- the real interviewer, using Claude's tool-use
    (forced function calling) to get reliable structured control fields
    back alongside the natural-language reply. This is what runs in
    production.

  * MockLLMClient -- a deterministic, offline stand-in with zero external
    dependencies. It exists so the full FastAPI endpoint (session state,
    plan progression, minimum-question/day enforcement, feedback shape)
    can be exercised and verified end-to-end in environments without
    network access or an API key -- e.g. this sandbox, or CI.

Both return the same small dataclasses so `orchestrator.py` never needs to
know which one it's talking to.
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TurnResult:
    reply: str
    is_followup: bool
    previous_answer_quality: str  # strong | adequate | weak | no_answer | not_applicable
    previous_answer_note: str


@dataclass
class FeedbackResult:
    summary: str
    strengths: List[str]
    gaps: List[str]
    next: List[str]


INTERVIEW_TURN_TOOL = {
    "name": "interview_turn",
    "description": (
        "Deliver the next thing you say to the candidate in this technical "
        "interview, plus your private assessment of their previous answer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": (
                    "Exactly what you will say to the candidate next, in your voice "
                    "as the interviewer. On the opening turn: a brief warm welcome "
                    "plus the first question. On a normal turn: a short (1 sentence "
                    "max) acknowledgment of their previous answer, then either a "
                    "follow-up question or the next topic's question -- never both a "
                    "follow-up AND a new topic in the same reply. On the closing turn: "
                    "a brief, warm wrap-up with no new question."
                ),
            },
            "is_followup": {
                "type": "boolean",
                "description": (
                    "True if `reply` asks a deeper follow-up question on the SAME "
                    "topic as the previous turn. False if `reply` moves on to the "
                    "next planned topic, or if this is the opening/closing turn."
                ),
            },
            "previous_answer_quality": {
                "type": "string",
                "enum": ["strong", "adequate", "weak", "no_answer", "not_applicable"],
                "description": (
                    "Your honest assessment of the candidate's PREVIOUS answer. "
                    "Use 'not_applicable' only on the very first turn, before they've "
                    "answered anything."
                ),
            },
            "previous_answer_note": {
                "type": "string",
                "description": (
                    "1-2 sentence private note on what was right/wrong/missing in "
                    "their previous answer. Not shown to the candidate -- used later "
                    "to synthesize final feedback. Empty string on the opening turn."
                ),
            },
        },
        "required": ["reply", "is_followup", "previous_answer_quality", "previous_answer_note"],
    },
}

DELIVER_FEEDBACK_TOOL = {
    "name": "deliver_feedback",
    "description": "Deliver the final structured feedback report for this interview.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-4 sentence overall assessment of the candidate's performance in this interview.",
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concise, specific, actionable strengths observed -- reference actual topics discussed.",
            },
            "gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concise, specific, actionable gaps or weaknesses observed -- reference actual topics discussed.",
            },
            "next": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concise, concrete recommended next steps for the candidate to close the gaps found.",
            },
        },
        "required": ["summary", "strengths", "gaps", "next"],
    },
}


class LLMClient:
    """Interface implemented by both the real and mock clients."""

    def run_turn(self, system_prompt: str, messages: List[Dict[str, str]]) -> TurnResult:
        raise NotImplementedError

    def run_feedback(self, system_prompt: str, messages: List[Dict[str, str]]) -> FeedbackResult:
        raise NotImplementedError


class AnthropicLLMClient(LLMClient):
    def __init__(self, model: Optional[str] = None, extended_thinking: Optional[bool] = None):
        import anthropic  # imported lazily so the mock path has zero deps

        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        self.extended_thinking = (
            extended_thinking
            if extended_thinking is not None
            else os.environ.get("EXTENDED_THINKING", "false").lower() == "true"
        )
        self.thinking_budget = int(os.environ.get("THINKING_BUDGET_TOKENS", "2000"))

    def _call(self, system_prompt: str, messages: List[Dict[str, str]], tool: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=1500,
            system=system_prompt,
            messages=messages,
            tools=[tool],
        )
        if self.extended_thinking:
            # NOTE: the Anthropic API requires tool_choice="auto" (not a forced
            # tool) whenever extended thinking is enabled, since the model needs
            # room to think before deciding to call the tool. We ask for it via
            # the system prompt instead and fall back to a forced retry if the
            # model doesn't comply.
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
            kwargs["tool_choice"] = {"type": "auto"}
            kwargs["max_tokens"] = max(kwargs["max_tokens"], self.thinking_budget + 1000)
        else:
            kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}

        response = self._client.messages.create(**kwargs)
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)

        if tool_use is None and self.extended_thinking:
            # Fallback: retry once, forcing the tool without thinking.
            kwargs.pop("thinking", None)
            kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}
            kwargs["max_tokens"] = 1500
            response = self._client.messages.create(**kwargs)
            tool_use = next((b for b in response.content if b.type == "tool_use"), None)

        if tool_use is None:
            raise RuntimeError(f"Model did not call the {tool['name']} tool as required.")
        return tool_use.input

    def run_turn(self, system_prompt: str, messages: List[Dict[str, str]]) -> TurnResult:
        data = self._call(system_prompt, messages, INTERVIEW_TURN_TOOL)
        return TurnResult(
            reply=data["reply"],
            is_followup=bool(data.get("is_followup", False)),
            previous_answer_quality=data.get("previous_answer_quality", "not_applicable"),
            previous_answer_note=data.get("previous_answer_note", ""),
        )

    def run_feedback(self, system_prompt: str, messages: List[Dict[str, str]]) -> FeedbackResult:
        data = self._call(system_prompt, messages, DELIVER_FEEDBACK_TOOL)
        return FeedbackResult(
            summary=data["summary"],
            strengths=list(data.get("strengths", [])),
            gaps=list(data.get("gaps", [])),
            next=list(data.get("next", [])),
        )


class MockLLMClient(LLMClient):
    """
    Deterministic, offline stand-in for the real interviewer. Used to
    self-test the FastAPI endpoint's control flow (session state, plan
    progression, minimum enforcement, feedback shape) without any network
    access or API key. Not intended to produce realistic interview
    questions -- see AnthropicLLMClient for that.
    """

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def run_turn(self, system_prompt: str, messages: List[Dict[str, str]]) -> TurnResult:
        ctx = _extract_mock_context(system_prompt)
        opening = len(messages) == 0

        if opening:
            topic = ctx["current_topic"]
            reply = (
                f"Hi {ctx['candidate_name']}, thanks for joining. Let's start with "
                f"Day {topic['day']} — {topic['title']}. {topic['objectives'][0]}"
                if topic["objectives"]
                else f"Hi {ctx['candidate_name']}, let's start with {topic['title']}."
            )
            return TurnResult(reply=reply, is_followup=False,
                               previous_answer_quality="not_applicable", previous_answer_note="")

        last_answer = messages[-1]["content"] if messages else ""
        quality = "weak" if len(last_answer.strip()) < 40 else "adequate"

        if ctx["is_last_topic"] and ctx["followups_used_on_current"] >= 1:
            reply = (
                f"That's a great place to stop -- thanks for walking me through your "
                f"thinking today, {ctx['candidate_name']}. This wraps up the interview; "
                f"I'll put together your feedback now."
            )
            return TurnResult(reply=reply, is_followup=False,
                               previous_answer_quality=quality,
                               previous_answer_note=f"[mock] answer length={len(last_answer)}")

        want_followup = quality == "weak" and ctx["followups_used_on_current"] < ctx["max_followups"]

        if want_followup:
            topic = ctx["current_topic"]
            reply = f"Can you go a bit deeper on that? Specifically, how does this connect to {topic['title'].lower()}?"
            return TurnResult(reply=reply, is_followup=True,
                               previous_answer_quality=quality,
                               previous_answer_note=f"[mock] answer length={len(last_answer)}")

        if ctx["is_last_topic"]:
            reply = (
                f"That's a great place to stop -- thanks for walking me through your "
                f"thinking today, {ctx['candidate_name']}. This wraps up the interview; "
                f"I'll put together your feedback now."
            )
            return TurnResult(reply=reply, is_followup=False,
                               previous_answer_quality=quality,
                               previous_answer_note=f"[mock] answer length={len(last_answer)}")

        topic = ctx["next_topic"]
        reply = (
            f"Good. Let's move on -- Day {topic['day']}, {topic['title']}. "
            f"{topic['objectives'][0] if topic['objectives'] else 'Walk me through how you approached this.'}"
        )
        return TurnResult(reply=reply, is_followup=False,
                           previous_answer_quality=quality,
                           previous_answer_note=f"[mock] answer length={len(last_answer)}")

    def run_feedback(self, system_prompt: str, messages: List[Dict[str, str]]) -> FeedbackResult:
        ctx = _extract_mock_context(system_prompt)
        return FeedbackResult(
            summary=f"[mock] {ctx['candidate_name']} completed a {ctx.get('num_topics', '?')}-topic mock interview.",
            strengths=["[mock] Engaged with every topic presented."],
            gaps=["[mock] Some answers were brief; real client would probe further."],
            next=["[mock] Re-run with a real Anthropic API key for substantive feedback."],
        )


def _extract_mock_context(system_prompt: str) -> Dict[str, Any]:
    """The orchestrator embeds a JSON blob for the mock client to read back
    out of the system prompt, so MockLLMClient never needs orchestrator
    internals imported directly (keeps the dependency direction one-way)."""
    marker = "<<MOCK_CONTEXT>>"
    end_marker = "<<END_MOCK_CONTEXT>>"
    start = system_prompt.index(marker) + len(marker)
    end = system_prompt.index(end_marker)
    return json.loads(system_prompt[start:end])


def get_llm_client() -> LLMClient:
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if provider == "mock":
        return MockLLMClient()
    return AnthropicLLMClient()
