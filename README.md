# Athena — AI Technical Interview Agent

An agent that conducts a realistic, multi-turn technical interview grounded in
a candidate's actual progress through the 31-day AI Cohort curriculum, and
produces structured, actionable feedback at the end.

Exposes exactly one endpoint, per the technical spec: **`POST /api/interview`**.

## Contents

```
backend/
  main.py                 FastAPI app — the required HTTP endpoint
  agent/
    planner.py             Turns a candidate's mission history into a
                            prioritized, curriculum-grounded question plan
    orchestrator.py         Turn-by-turn control flow (the "InterviewAgent")
    prompts.py              System prompt construction for each turn / for feedback
    llm.py                  LLM client: real Anthropic tool-use client + offline mock
    session.py               In-memory session state
    schemas.py                Request/response models (match the spec exactly)
  data/curriculum.json      Bundled copy of the curriculum (the client only
                             sends `candidate` in the request; the server
                             needs its own reference for day/module lookups)
frontend/
  index.html                Zero-dependency chat console (talks to the API directly)
tests/
  test_planner.py            Unit tests for the planning logic
  test_end_to_end_mock.py     Full offline interview simulation against all 20
                               real candidates from candidates.json
scripts/
  simulate_interview.py       Drives a full interview against the LIVE server
                               over real HTTP — what a grader's harness would do
candidates.json               Copy of the provided candidate data (used by the
                               frontend dropdown and the test/demo scripts)
```

## Running it

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set ANTHROPIC_API_KEY, or LLM_PROVIDER=mock
uvicorn main:app --reload --port 8000
```

Try it against the real endpoint:

```bash
python3 scripts/simulate_interview.py --candidate CAND-003 --auto
```

Or open the chat console (must be served over HTTP, not `file://`, so the
browser can fetch `candidates.json`):

```bash
cd frontend && python3 -m http.server 5500
# open http://127.0.0.1:5500
```

**No API key handy?** Set `LLM_PROVIDER=mock` in `backend/.env`. The mock
client drives the exact same orchestration code with deterministic, scripted
replies — useful for exercising the full endpoint contract with zero external
calls. This is also what let me validate the system end-to-end inside my own
sandboxed dev environment, which had no network egress and couldn't install
`fastapi`/`anthropic` — see **Verification** below for what that proved and
what it didn't.

## How each requirement is satisfied

| Requirement | Implementation |
|---|---|
| Conversational, multi-turn interview | `orchestrator.py` maintains an `InterviewSession` per `sessionId`, with the full transcript replayed to the model every turn |
| ≥ 8 questions across ≥ 4 curriculum days | **Structural guarantee, not a hope.** `planner.py` deterministically builds an 8–10 topic plan spanning ≥ 4 distinct days *before* the interview starts, from the candidate's actual mission history. The LLM can add follow-ups but cannot skip planned topics or shrink the plan. See "Why this split" below. |
| Follow-ups generated from previous responses | Every turn, the model receives the full transcript and decides `is_followup` vs. advancing, with a per-topic budget (max 2 follow-ups) so it can probe a shallow answer without derailing the interview |
| Context maintained throughout | Full message history is sent to the model every turn (no summarization/truncation needed at this scale — interviews run ~10–16 turns) |
| Structured feedback at the end | A dedicated `deliver_feedback` tool call, fed the full transcript *and* the interviewer's private per-answer notes collected during the interview, forced via tool-use to match the spec's exact schema (`summary`, `strengths[]`, `gaps[]`, `next[]`) |
| Required HTTP endpoint | `POST /api/interview`, request/response shapes match the spec's examples exactly (`schemas.py`) |

### Why the plan/LLM split, specifically

A model that's simply told "ask at least 8 questions across 4 days" in a
system prompt *usually* complies — but "usually" is a bad foundation for a
hard spec requirement, and it also means the model is spending judgment on
bookkeeping instead of on asking good questions. So the two concerns are
split cleanly:

- **Deterministic (code):** *which* topics, in what order, and the hard floor
  on quantity/breadth. `planner.py` scores every mission by how informative
  it would be to probe (failed > skipped > many-attempts-pass > first-try-pass),
  applies a bonus/penalty by curriculum-day type (SETUP days are deprioritized
  — "installed VS Code" isn't a concept to interview on; SHIP_IT/CAPSTONE days
  are prioritized — they're the most integrative), and greedily selects across
  distinct modules for breadth. Validated against **all 20 real candidate
  profiles** — see Verification.
- **Adaptive (LLM):** *how* each topic is explored — question wording, whether
  an answer deserves a follow-up, difficulty calibration against the
  candidate's seniority and how they're actually performing, and the final
  feedback synthesis.

A hard-coded `MAX_TOTAL_QUESTIONS` safety valve (16) also exists in case a
model ever tries to follow up indefinitely — the prompt is patched to signal
"treat this as the final topic" the moment it's hit, so the closing message
the model writes always matches what the code actually does next.

### Adaptivity, concretely

- **Per-candidate plan**: two candidates who took the same cohort get
  different interviews — one who failed the embeddings mission gets probed
  on whether that gap has closed; one who passed it first-try gets pushed
  deeper into tradeoffs instead.
- **Per-answer follow-ups**: a shallow or evasive answer can earn one probing
  follow-up before the interview moves on; a strong, complete answer doesn't
  get follow-up questions for their own sake.
- **Seniority-aware difficulty**: years of experience + job title (e.g.
  "Principal Architect" vs. "Computer Science Intern") set a starting
  difficulty hint per topic, layered under the real-time read on how the
  candidate is actually performing.
- **Narrative shape**: topics run chronologically through the cohort, with
  any CAPSTONE-type day always pinned last — mirroring how a real interview
  builds from fundamentals to an integrative closing question, not a random
  walk through the syllabus.

## Verification

I could not install `fastapi`/`uvicorn`/`anthropic` or run a live server in
my own dev sandbox (no network egress there — that's a constraint of my
environment, not of this codebase). To still verify correctness rather than
just asserting it, I split the system so the orchestration core
(`InterviewAgent`, which `main.py` is a thin wrapper around) has **zero
FastAPI/Anthropic dependency** when running against the offline `MockLLMClient`,
and tested that core directly:

```bash
python3 tests/test_planner.py            # 5/5 pass — planner edge cases
python3 tests/test_end_to_end_mock.py    # 20/20 real candidates pass, full interviews
```

`test_end_to_end_mock.py` runs a complete simulated interview — opening,
follow-ups, topic transitions, closing, feedback generation — for **every one
of the 20 real candidates in `candidates.json`**, and asserts on each:
`questions_asked >= 8`, `distinct_days_covered >= 4`, the interview reaches
`done: true`, and the feedback object has all four required fields.

What this *doesn't* verify: the actual FastAPI HTTP layer (routing, Pydantic
serialization matching the exact JSON shape) and real Claude-generated
question quality, since both need dependencies/network I didn't have here.
`main.py`'s route handler is ~15 lines and calls straight into the
already-tested `InterviewAgent`, so the risk surface there is small, but
please run `scripts/simulate_interview.py` against your own running server
before treating this as fully proven end-to-end.

## Design decisions & known simplifications

- **In-memory session store.** Matches the spec's out-of-scope list (no
  persistent accounts / long-term history required). Swap `session.py`'s
  `SessionStore` for Redis if this needs to run across multiple worker
  processes — nothing else depends on the storage mechanism.
- **Tool-use for structured control, not prompt-parsed JSON.** Both the
  per-turn control fields and the final feedback are obtained via forced
  Claude tool calls, not by asking for JSON in prose and parsing it — more
  reliable, and it's what the fields in `llm.py`'s tool schemas exist for.
- **3-tier difficulty hint** (foundational / standard / advanced) rather than
  a more elaborate tier system, deliberately — it's derived from real signals
  (years experience, job title, mission outcome) rather than being an
  arbitrary finer-grained scale with nothing backing the extra granularity.
  Straightforward to extend if you have more signal to justify it.
- **Frontend scope.** The spec doesn't require a UI at all ("teams are free
  to choose any frontend"). I built a single dependency-free HTML console
  rather than a full Next.js app, since I had no network access to
  `npm install` or test one in my sandbox, and didn't want to hand over a
  frontend I couldn't verify actually runs. `frontend/index.html` is real,
  tested-by-inspection, and works with zero build step — happy to build out
  a fuller Next.js UI as a follow-up if useful.
- **One process, one model provider per run.** `LLM_PROVIDER` picks Anthropic
  or mock at startup; there's no per-request provider switching, which
  wasn't a requirement.
- **Extended thinking is wired up but off by default** (`EXTENDED_THINKING=true`
  in `.env` to enable). It adds latency/cost for every turn, and the base
  prompt already asks for explicit reasoning about follow-up-vs-advance and
  difficulty calibration — thinking is there for teams that want deeper
  multi-hop reasoning on ambiguous answers, not required for correctness.

## Example (mock provider, illustrates control flow only)

```
[interviewer] Hi Sarah Johnson, thanks for joining. Let's start with
              Day 7 — Embeddings Explained. Understand how text is
              converted into vector embeddings
[candidate]   Not sure.
[interviewer] Can you go a bit deeper on that? Specifically, how does
              this connect to embeddings explained?
[candidate]   It depends on the tradeoffs involved and how the system
              is used in practice.
[interviewer] Good. Let's move on -- Day 8, Vector Databases Overview.
              ...
...
[interviewer] That's a great place to stop -- thanks for walking me
              through your thinking today, Sarah Johnson. This wraps
              up the interview; I'll put together your feedback now.
```

With the real Anthropic client, `reply` text is genuinely adaptive prose
grounded in the curriculum objectives and the candidate's actual previous
answer, not templated strings — the mock client exists purely to prove the
control flow (session state, plan progression, minimum enforcement, closing,
feedback shape) works, independent of model quality.
