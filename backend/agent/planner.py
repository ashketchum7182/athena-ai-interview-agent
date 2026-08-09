"""
Interview planner.

Turns a candidate's mission history into a *prioritized, curriculum-grounded*
question plan before the interview ever starts. This is the deterministic
backbone that guarantees the hard requirements from the technical spec
(>= 8 questions, >= 4 distinct curriculum days) regardless of what the LLM
does at runtime -- the LLM only controls *how* each topic is explored
(follow-ups, tone, difficulty), never *whether* the minimums are met.

Design of the scoring heuristic
--------------------------------
A real interviewer doesn't pick topics at random -- they probe where the
signal is most informative:

  * FAILED missions        -> did the gap get closed since then?      (score +5)
  * SKIPPED missions       -> do they understand it despite skipping? (score +4)
  * Passed, many attempts  -> is the understanding solid now, or did
                               they just get lucky / memorize the fix? (+2 to +3)
  * Passed, first try      -> baseline; good candidate for going deeper (+1)

On top of that, some curriculum days are just more interview-worthy than
others -- pure environment setup ("install VS Code") is a checkbox, not a
concept. We bias strongly toward AI_CORE / SHIP_IT / CAPSTONE days and away
from SETUP days, unless the candidate genuinely doesn't have enough other
material.

We then greedily select across distinct *modules* (not just distinct days)
so the interview naturally samples breadth across the 8-module curriculum,
and we always order the final plan chronologically with any CAPSTONE-type
day pinned to the very end -- mirroring how a real interview builds from
fundamentals up to an integrative, system-level closing question.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MIN_TOPICS = 8
MAX_TOPICS = 10

# Bonus/penalty applied by curriculum day "type". Pure checkbox/setup days
# are deprioritized; integrative/ship-it days are the most interview-worthy.
TYPE_WEIGHT = {
    "SETUP": -3,
    "LEARN": 0,
    "BUILD": 0,
    "OPTIMIZE": 1,
    "AI_CORE": 2,
    "SHIP_IT": 2,
    "CAPSTONE": 3,
}

MAX_PER_MODULE = 3  # cap how many topics can come from a single module


@dataclass
class PlannedTopic:
    day: int
    title: str
    type: str
    module: int
    tools: List[str]
    objectives: List[str]
    reason: str          # why this topic was chosen -- fed to the LLM
    difficulty_hint: str # "foundational" | "standard" | "advanced"
    score: float = 0.0

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "day": self.day,
            "title": self.title,
            "type": self.type,
            "tools": self.tools,
            "objectives": self.objectives,
            "reason_selected": self.reason,
            "difficulty_hint": self.difficulty_hint,
        }


def _day_to_module(curriculum: Dict[str, Any]) -> Dict[int, int]:
    mapping: Dict[int, int] = {}
    for mod in curriculum["modules"]:
        lo, hi = mod["days"]
        for d in range(lo, hi + 1):
            mapping[d] = mod["n"]
    return mapping


def _curriculum_day_index(curriculum: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {d["day"]: d for d in curriculum["days"]}


def _seniority_hint(candidate_member: Dict[str, Any]) -> str:
    """Rough calibration signal for question depth, from years of experience
    and job title. This is a *hint* passed to the LLM, not a hard rule --
    the model should keep adapting based on how the candidate is actually
    performing in real time."""
    years = candidate_member.get("yearsExperience", 0) or 0
    role = (candidate_member.get("jobRole") or "").lower()
    senior_titles = ("senior", "staff", "principal", "lead", "architect", "manager")
    if years >= 8 or any(t in role for t in senior_titles):
        return "advanced"
    if years <= 1:
        return "foundational"
    return "standard"


def _mission_score_and_reason(mission: Dict[str, Any]) -> (float, str):
    if mission.get("skipped"):
        return 4.0, "Skipped this mission during the cohort -- probe baseline awareness of the concept."
    passed = mission.get("passed")
    attempts = mission.get("attempts", 1) or 1
    if passed is False:
        return 5.0, "Did not pass this mission -- check whether the gap has closed since then."
    if attempts >= 4:
        return 3.0, f"Needed {attempts} attempts to pass -- confirm the understanding is solid now, not just trial-and-error."
    if attempts in (2, 3):
        return 2.0, f"Passed after {attempts} attempts -- worth checking depth beyond the mission itself."
    return 1.0, "Passed on the first attempt -- good candidate for going a level deeper than the mission required."


def build_plan(candidate: Dict[str, Any], curriculum: Dict[str, Any]) -> List[PlannedTopic]:
    member = candidate.get("member", {})
    missions = candidate.get("missions", [])
    day_index = _curriculum_day_index(curriculum)
    day_to_module = _day_to_module(curriculum)
    base_difficulty = _seniority_hint(member)

    candidates: List[PlannedTopic] = []
    for m in missions:
        day = m.get("day")
        curr_day = day_index.get(day)
        if curr_day is None:
            continue  # mission references a day outside the provided curriculum
        mission_score, reason = _mission_score_and_reason(m)
        type_bonus = TYPE_WEIGHT.get(curr_day["type"], 0)
        score = mission_score + type_bonus

        # difficulty: struggled/skipped topics start a notch easier than the
        # candidate's baseline; comfortably-passed SHIP_IT/CAPSTONE topics a
        # notch harder, to mimic how a real interviewer calibrates per-topic.
        difficulty = base_difficulty
        if m.get("skipped") or m.get("passed") is False:
            difficulty = "foundational" if base_difficulty != "foundational" else base_difficulty
        elif curr_day["type"] in ("SHIP_IT", "CAPSTONE") and (m.get("attempts", 1) or 1) == 1:
            difficulty = "advanced" if base_difficulty != "foundational" else "standard"

        candidates.append(
            PlannedTopic(
                day=day,
                title=curr_day["title"],
                type=curr_day["type"],
                module=day_to_module.get(day, 0),
                tools=curr_day.get("tools", []),
                objectives=curr_day.get("objectives", []),
                reason=reason,
                difficulty_hint=difficulty,
                score=score,
            )
        )

    if not candidates:
        return []

    target = max(MIN_TOPICS, min(MAX_TOPICS, len(candidates)))
    # If there's enough non-setup material to comfortably clear the spec
    # minimum on its own, drop SETUP days entirely -- they're checkboxes,
    # not concepts worth interviewing on. Only fall back to including them
    # when a candidate genuinely doesn't have enough other material.
    non_setup = [c for c in candidates if c.type != "SETUP"]
    pool = non_setup if len(non_setup) >= MIN_TOPICS else candidates

    pool.sort(key=lambda c: (-c.score, c.day))

    selected: List[PlannedTopic] = []
    per_module_count: Dict[int, int] = {}

    # Pass 1: round-robin across modules to maximize breadth early.
    remaining = list(pool)
    while remaining and len(selected) < target:
        progressed = False
        for c in list(remaining):
            if per_module_count.get(c.module, 0) >= MAX_PER_MODULE:
                continue
            selected.append(c)
            per_module_count[c.module] = per_module_count.get(c.module, 0) + 1
            remaining.remove(c)
            progressed = True
            if len(selected) >= target:
                break
        if not progressed:
            break

    # Pass 2: fill any remaining slots ignoring the per-module cap if we're
    # still short (small candidate profiles).
    if len(selected) < target:
        for c in remaining:
            if len(selected) >= target:
                break
            selected.append(c)

    # Safety net: guarantee >= 4 distinct days no matter what (should already
    # be true for any realistic profile, but never trust it silently).
    distinct_days = {c.day for c in selected}
    if len(distinct_days) < 4:
        for c in sorted(candidates, key=lambda c: (-c.score, c.day)):
            if c.day not in distinct_days:
                selected.append(c)
                distinct_days.add(c.day)
            if len(distinct_days) >= 4:
                break

    # Final narrative order: chronological through the cohort, but push any
    # CAPSTONE-type day to the very end as the closing, integrative question.
    capstones = [c for c in selected if c.type == "CAPSTONE"]
    rest = [c for c in selected if c.type != "CAPSTONE"]
    rest.sort(key=lambda c: c.day)
    ordered = rest + capstones

    return ordered
