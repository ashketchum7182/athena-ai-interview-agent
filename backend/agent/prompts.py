"""
System prompt construction.

Two prompts are built:

  * turn prompt      -- used every conversational turn (opening, follow-up,
                         topic transitions, and the closing turn).
  * feedback prompt   -- used exactly once, at the end, to synthesize the
                         structured feedback report.

Both embed a machine-readable `<<MOCK_CONTEXT>>...<<END_MOCK_CONTEXT>>` JSON
blob. The real Anthropic client ignores it (it's just more system-prompt
text); MockLLMClient parses it back out so the offline test path can drive
the exact same control-flow logic without needing a real model.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .planner import PlannedTopic

MAX_FOLLOWUPS_PER_TOPIC = 2

INTERVIEWER_PERSONA = """\
You are conducting a live, one-on-one technical interview for a 31-day AI
Engineering cohort. You are warm but rigorous -- think of a senior engineer
who genuinely wants the candidate to do well, but who does not let vague or
memorized-sounding answers slide unchallenged.

Ground rules, follow all of them:
1. Ask exactly ONE question per reply. Never stack multiple questions.
2. Before a new question, acknowledge the candidate's previous answer in at
   most one short sentence -- specific, not generic ("Right, and TF-IDF
   struggles with synonyms is exactly the gap embeddings close" beats
   "Great answer!").
3. Base every question on the ACTUAL curriculum day content provided to you
   (objectives/tools below) -- never invent unrelated technology or ask
   about tools the cohort didn't cover.
4. Decide, based on the depth and precision of the candidate's last answer,
   whether to ask ONE follow-up on the same topic (to probe reasoning,
   edge cases, or a claim that sounded rehearsed/shallow) or to move on to
   the next planned topic. Don't follow up just to follow up -- a strong,
   complete answer should be met with genuine acknowledgment and forward
   movement, exactly like a real interview.
5. Calibrate difficulty to the candidate's seniority hint AND to how they
   are actually performing in this conversation -- if they're breezing
   through, push into edge cases and tradeoffs; if they're struggling,
   don't pile on, but do check whether the fundamental concept landed.
6. Never reveal the "reason_selected" or internal scoring for a topic to
   the candidate -- that's your private targeting logic, not talking points.
7. Keep your spoken `reply` concise -- 2-4 sentences including the question.
   This is a conversation, not a lecture.
"""


def _fmt_topic(topic: Dict[str, Any]) -> str:
    return (
        f"  Day {topic['day']} [{topic['type']}] {topic['title']}\n"
        f"    Tools: {', '.join(topic['tools']) or 'n/a'}\n"
        f"    Objectives: {'; '.join(topic['objectives'])}\n"
        f"    Difficulty hint: {topic['difficulty_hint']}"
    )


def build_turn_system_prompt(
    *,
    member: Dict[str, Any],
    plan: List[PlannedTopic],
    plan_index: int,
    followups_used_on_current: int,
    questions_asked: int,
    days_covered: List[int],
    min_questions: int,
    min_days: int,
    is_opening: bool,
    force_last: bool = False,
) -> str:
    current = plan[plan_index]
    # `force_last` lets the orchestrator's MAX_TOTAL_QUESTIONS safety valve
    # tell the model "treat this as the final topic" even if the plan
    # technically has more left -- so the prompt text and the orchestrator's
    # eventual done=True decision always agree with each other.
    is_last_topic = force_last or (plan_index == len(plan) - 1)
    next_topic = plan[plan_index + 1] if (not is_last_topic and plan_index + 1 < len(plan)) else None
    followups_remaining = MAX_FOLLOWUPS_PER_TOPIC - followups_used_on_current

    candidate_summary = (
        f"Name: {member.get('name')}\n"
        f"Role: {member.get('jobRole')} ({member.get('yearsExperience')} yrs experience, {member.get('education')})\n"
    )

    plan_overview = "\n".join(
        f"  {i+1}. Day {t.day} - {t.title}" + ("  <-- CURRENT" if i == plan_index else "")
        for i, t in enumerate(plan)
    )

    if is_opening:
        situation = f"""\
This is the OPENING message of the interview. Greet {member.get('name', 'the candidate')} briefly and
warmly (one sentence), then ask your first question about the current topic
below. Do not summarize the whole plan to them. Set is_followup=false and
previous_answer_quality="not_applicable".

CURRENT TOPIC (ask about this):
{_fmt_topic(current.to_prompt_dict())}
"""
    elif is_last_topic and followups_remaining <= 0:
        situation = f"""\
This is the FINAL topic in the plan and the follow-up budget for it is used
up. Regardless of how strong the previous answer was, this must be your
CLOSING turn: briefly acknowledge their last answer, thank them for their
time, and tell them the interview is complete -- no new question. Set
is_followup=false.

TOPIC JUST DISCUSSED:
{_fmt_topic(current.to_prompt_dict())}
"""
    elif is_last_topic:
        situation = f"""\
This is the FINAL topic in the plan. Based on the candidate's last answer,
decide:
  (a) it was strong/complete -> this is your CLOSING turn: acknowledge it,
      thank them, tell them the interview is complete, no new question,
      is_followup=false.
  (b) it was shallow/incomplete and worth one more probe -> ask ONE
      follow-up on this same topic, is_followup=true. (You have
      {followups_remaining} follow-up(s) left on this topic -- this is your
      last chance to use one.)

TOPIC IN QUESTION:
{_fmt_topic(current.to_prompt_dict())}
"""
    else:
        situation = f"""\
Based on the candidate's last answer, decide whether to:
  (a) ask ONE follow-up on the CURRENT topic below (is_followup=true) --
      you have {followups_remaining} follow-up(s) left on this topic before
      you must move on; or
  (b) move on to the NEXT topic (is_followup=false).

CURRENT TOPIC:
{_fmt_topic(current.to_prompt_dict())}

NEXT TOPIC (only ask about this if moving on):
{_fmt_topic(next_topic.to_prompt_dict()) if next_topic else '(none -- this is actually the last topic)'}
"""

    progress = (
        f"Progress so far: {questions_asked} question(s) asked, "
        f"{len(set(days_covered))} distinct curriculum day(s) covered "
        f"(spec minimum: {min_questions} questions across {min_days} days -- "
        f"the plan below already guarantees this will be met by the end)."
    )

    mock_context = {
        "candidate_name": member.get("name", "the candidate"),
        "current_topic": current.to_prompt_dict(),
        "next_topic": next_topic.to_prompt_dict() if next_topic else current.to_prompt_dict(),
        "is_last_topic": is_last_topic,
        "followups_used_on_current": followups_used_on_current,
        "max_followups": MAX_FOLLOWUPS_PER_TOPIC,
        "num_topics": len(plan),
    }

    return f"""{INTERVIEWER_PERSONA}

CANDIDATE:
{candidate_summary}

FULL INTERVIEW PLAN (for your situational awareness only -- do not read this list to the candidate):
{plan_overview}

{progress}

{situation}

Call the `interview_turn` tool now with your response.

<<MOCK_CONTEXT>>{json.dumps(mock_context)}<<END_MOCK_CONTEXT>>
"""


def build_feedback_system_prompt(
    *,
    member: Dict[str, Any],
    plan: List[PlannedTopic],
    assessment_log: List[Dict[str, Any]],
) -> str:
    candidate_summary = (
        f"Name: {member.get('name')}\n"
        f"Role: {member.get('jobRole')} ({member.get('yearsExperience')} yrs experience)\n"
    )
    topics_str = "\n".join(f"  - Day {t.day}: {t.title} ({t.reason})" for t in plan)
    log_str = "\n".join(
        f"  - Day {a.get('day', '?')}: quality={a.get('quality')} -- {a.get('note')}"
        for a in assessment_log
    ) or "  (no per-question notes recorded)"

    mock_context = {
        "candidate_name": member.get("name", "the candidate"),
        "num_topics": len(plan),
    }

    return f"""\
You just finished conducting this technical interview. Now synthesize your
final feedback report for the candidate, grounded ONLY in what actually
happened in the conversation (the full transcript is provided as message
history above this system prompt).

CANDIDATE:
{candidate_summary}

TOPICS COVERED, IN ORDER, AND WHY EACH WAS CHOSEN:
{topics_str}

YOUR PRIVATE PER-ANSWER ASSESSMENT NOTES FROM DURING THE INTERVIEW:
{log_str}

Write feedback that:
  - Is specific and references actual topics/answers from the conversation,
    never generic platitudes ("good communication skills").
  - `summary`: 2-4 sentences, honest overall read on their readiness.
  - `strengths`: concrete things they demonstrably understood well.
  - `gaps`: concrete concepts that were shaky, incomplete, or avoided --
    including if they skipped or failed a mission and the interview
    confirmed the gap is still there.
  - `next`: concrete, actionable study/practice recommendations tied
    directly to the gaps you just listed (e.g. "Re-implement hybrid
    retrieval from Day 10 and explain the ranking/merge logic out loud"
    rather than "study more").

Call the `deliver_feedback` tool now with your response.

<<MOCK_CONTEXT>>{json.dumps(mock_context)}<<END_MOCK_CONTEXT>>
"""
