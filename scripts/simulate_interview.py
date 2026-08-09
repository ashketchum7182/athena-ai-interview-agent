"""
Drives one full interview against the LIVE running server via real HTTP
requests -- exactly how a grader's test harness would exercise the
technical spec's endpoint.

Usage:
    # 1. In one terminal:
    cd backend && uvicorn main:app --reload --port 8000
    # (set LLM_PROVIDER=mock in backend/.env first if you don't have an
    #  ANTHROPIC_API_KEY handy -- see backend/.env.example)

    # 2. In another terminal:
    python3 scripts/simulate_interview.py --candidate CAND-003
    python3 scripts/simulate_interview.py --candidate CAND-003 --auto   # scripted answers, no typing
"""
import argparse
import json
import os
import sys
import uuid

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_candidate(candidate_id: str) -> dict:
    with open(os.path.join(ROOT, "candidates.json"), "r", encoding="utf-8") as f:
        candidates = json.load(f)["candidates"]
    for c in candidates:
        if c["member"]["id"] == candidate_id:
            return c
    raise SystemExit(f"No candidate with id={candidate_id!r} in candidates.json")


AUTO_ANSWERS = [
    "Sure -- the core idea is you embed text into a dense vector space so semantically "
    "similar content ends up close together, then use cosine similarity or a similar "
    "metric to retrieve the nearest neighbors at query time.",
    "Honestly I'd have to think about the edge cases more carefully -- I remember the "
    "general shape but not the specific tradeoffs off the top of my head.",
    "We'd want to validate the schema with Pydantic before executing anything, log every "
    "tool call with its arguments and result for auditability, and fail closed if the "
    "model requests a tool that isn't in the allow-list.",
    "I structured it as a router that decides between SQL for structured lookups and "
    "vector search for semantic questions, then merged and deduplicated results before "
    "handing them to the LLM.",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="CAND-003", help="candidate id from candidates.json")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/interview")
    parser.add_argument("--auto", action="store_true", help="use scripted answers instead of stdin")
    args = parser.parse_args()

    candidate = load_candidate(args.candidate)
    session_id = str(uuid.uuid4())

    print(f"Starting interview for {candidate['member']['name']} ({args.candidate}) "
          f"session={session_id}\n")

    resp = requests.post(args.url, json={"sessionId": session_id, "candidate": candidate})
    resp.raise_for_status()
    data = resp.json()
    print(f"[interviewer] {data['reply']}\n")
    assert data["done"] is False, "first response must have done=false"

    turn = 0
    while not data["done"]:
        if args.auto:
            message = AUTO_ANSWERS[turn % len(AUTO_ANSWERS)]
            print(f"[candidate]    {message}\n")
        else:
            message = input("[candidate]    ")

        resp = requests.post(args.url, json={"sessionId": session_id, "message": message})
        resp.raise_for_status()
        data = resp.json()
        print(f"\n[interviewer] {data['reply']}\n")
        turn += 1

        if turn > 40:
            print("Runaway guard tripped in the test script itself -- stopping.")
            break

    assert data["done"] is True
    assert "feedback" in data, "final response must include structured feedback"
    fb = data["feedback"]
    for field in ("summary", "strengths", "gaps", "next"):
        assert field in fb, f"feedback missing required field: {field}"

    print("=" * 60)
    print("FEEDBACK")
    print("=" * 60)
    print(json.dumps(fb, indent=2))
    print(f"\n{turn} candidate turns total. Contract verified: done=true + all 4 feedback fields present.")


if __name__ == "__main__":
    main()
