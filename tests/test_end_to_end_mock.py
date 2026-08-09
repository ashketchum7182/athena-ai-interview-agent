"""
End-to-end test of the interview orchestration core, run entirely offline
against the *real* candidate profiles from candidates.json, using
MockLLMClient so no network access or ANTHROPIC_API_KEY is required.

This exercises exactly the same code path `main.py` calls (InterviewAgent.
start_interview / continue_interview) -- it just skips the FastAPI/HTTP
layer, which is a thin wrapper around this class (see main.py).

Run with:
    python3 tests/test_end_to_end_mock.py
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from agent.llm import MockLLMClient
from agent.orchestrator import MIN_DAYS, InterviewAgent
from agent.planner import MIN_TOPICS

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load_candidates():
    with open(os.path.join(ROOT, "candidates.json"), "r", encoding="utf-8") as f:
        return json.load(f)["candidates"]


def run_full_interview(agent: InterviewAgent, candidate: dict, verbose: bool = False):
    session_id = str(uuid.uuid4())
    reply = agent.start_interview(session_id, candidate)
    transcript = [("interviewer", reply)]

    done = False
    feedback = None
    turns = 0
    MAX_TURNS = 40  # test-side runaway guard, independent of the agent's own safety valve

    while not done and turns < MAX_TURNS:
        # Simulate a candidate answer. Alternate short/long answers so the
        # mock client's follow-up heuristic gets exercised both ways.
        fake_answer = "It depends on the tradeoffs involved and how the system is used in practice." if turns % 3 else "Not sure."
        transcript.append(("candidate", fake_answer))
        reply, done, feedback = agent.continue_interview(session_id, fake_answer)
        transcript.append(("interviewer", reply))
        turns += 1

    if verbose:
        for speaker, text in transcript:
            print(f"  [{speaker}] {text}")

    from agent.session import session_store
    session = session_store.get(session_id)

    return {
        "turns": turns,
        "questions_asked": session.questions_asked,
        "distinct_days": len(session.days_covered),
        "days_covered": sorted(session.days_covered),
        "done": done,
        "feedback": feedback,
    }


def main():
    candidates = load_candidates()
    agent = InterviewAgent(llm_client=MockLLMClient())

    print(f"Running {len(candidates)} full mock interviews (spec minimum: "
          f"{MIN_TOPICS} questions across {MIN_DAYS} days)...\n")

    failures = []
    for cand in candidates:
        name = cand["member"]["name"]
        cid = cand["member"]["id"]
        result = run_full_interview(agent, cand)

        checks = {
            "questions_asked >= 8": result["questions_asked"] >= 8,
            "distinct_days >= 4": result["distinct_days"] >= 4,
            "done == True": result["done"] is True,
            "feedback has all 4 fields": result["feedback"] is not None
            and all(k in result["feedback"] for k in ("summary", "strengths", "gaps", "next")),
        }
        ok = all(checks.values())
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures.append((cid, checks))

        print(
            f"{status}  {cid:10s} {name:16s} "
            f"questions={result['questions_asked']:2d}  distinct_days={result['distinct_days']:2d}  "
            f"turns={result['turns']:2d}  days={result['days_covered']}"
        )

    print()
    if failures:
        print(f"{len(failures)} candidate(s) FAILED spec compliance:")
        for cid, checks in failures:
            print(f"  {cid}: {checks}")
        sys.exit(1)
    else:
        print(f"All {len(candidates)} candidates passed spec compliance (>= 8 questions, >= 4 distinct days,")
        print("interview terminates cleanly, structured feedback with all required fields).")

    # Print one full sample transcript + feedback for manual inspection.
    print("\n" + "=" * 70)
    print("SAMPLE FULL TRANSCRIPT (mock LLM -- illustrates control flow only,")
    print("not real question quality; see README for a real-model example)")
    print("=" * 70)
    sample = candidates[0]
    result = run_full_interview(agent, sample, verbose=True)
    print("\nFEEDBACK:")
    print(json.dumps(result["feedback"], indent=2))


if __name__ == "__main__":
    main()
