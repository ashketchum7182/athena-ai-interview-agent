# Project Report — Athena AI Interview Agent

## What it does

An AI agent that conducts a realistic, multi-turn technical interview grounded in a candidate's actual progress through a 31-day AI curriculum, then produces structured, actionable feedback at the end. Exposes a single endpoint: `POST /api/interview`.

## Tech stack

| Layer | Tech |
|---|---|
| Backend | Python, FastAPI, Pydantic |
| LLM | Anthropic Claude (tool-use/forced function calling) — with an offline mock client and an alternative Gemini client also implemented |
| Frontend | Vanilla HTML/JS, zero dependencies |
| Hosting | Backend on Render, frontend on GitHub Pages |
| Session state | In-memory (per the spec's out-of-scope list — no persistent accounts required) |

**Live demo:** https://ashketchum7182.github.io/athena-ai-interview-agent/
**Backend health check:** https://athena-ai-interview-agent.onrender.com/health
**Repo:** https://github.com/ashketchum7182/athena-ai-interview-agent

## Architecture — the core design decision

The interview logic is deliberately split into two concerns:

- **Deterministic (code) — `planner.py`:** decides *which* topics get asked, in what order, and enforces the hard requirement floor. It scores every mission in a candidate's history by how informative it would be to probe (failed > skipped > many-attempts-pass > first-try-pass), weights by curriculum-day type, and greedily selects across distinct modules to guarantee ≥8 questions spanning ≥4 distinct days — as a structural guarantee, not a prompted hope.
- **Adaptive (LLM) — `orchestrator.py` + `llm.py`:** decides *how* each topic is explored — question wording, whether an answer earns a follow-up (max 2 per topic), difficulty calibration against the candidate's seniority and real-time performance, and the final feedback synthesis.

This split means the spec's hard requirements (question count, day coverage) can't be violated by the model having an off turn, while the actual interview quality still comes from genuine LLM judgment.

## What's implemented

| Requirement | Status |
|---|---|
| Conversational, multi-turn interview with full context | ✅ Done — full transcript replayed each turn |
| ≥8 questions across ≥4 curriculum days | ✅ Done — structurally enforced by planner.py |
| Follow-ups generated from previous answers | ✅ Done — per-topic follow-up budget, quality-gated |
| Structured feedback at the end | ✅ Done — forced tool-call matching exact schema |
| Required HTTP endpoint | ✅ Done — `POST /api/interview` |
| Offline/mock mode for zero-dependency testing | ✅ Done — 20/20 real candidates pass full simulated interviews |
| Public deployment | ✅ Done — backend (Render) + frontend (GitHub Pages) |
| Alternative LLM provider (Gemini) | ✅ Implemented, tested in an isolated branch/copy |

## Verification

- `tests/test_planner.py` — 5/5 planning edge cases pass
- `tests/test_end_to_end_mock.py` — full simulated interviews pass for all 20 real candidates in `candidates.json`, asserting question count, day coverage, completion, and feedback shape
- `scripts/simulate_interview.py` — drives a full interview against the live HTTP server, mirroring what a grader's harness would do

## Known limitations / next steps

- **Session storage is in-memory** — fine at hackathon scale, would need Redis or similar to run across multiple worker processes in production
- **Render free tier cold-starts** after 15 minutes of inactivity (30-60s wake-up on first request) — a known tradeoff of free hosting, not a code issue
- **Gemini integration** is implemented and structurally sound but has had less live-traffic testing than the Anthropic path, since it was added late in the build
- **Frontend is intentionally minimal** — a dependency-free HTML console, since the spec explicitly allows any frontend approach; a fuller UI (e.g. Next.js) is a natural next step
- **Extended thinking mode** is wired up but off by default — available for teams wanting deeper multi-hop reasoning on ambiguous follow-up decisions, at added latency/cost

## Development process note

This project was built and debugged with AI assistance (Claude, Anthropic) throughout — see `AI_USAGE_LOG.md` and `PROMPTS.md` in this repo for a full transparent record of that process, including real bugs hit and fixed (a missing `load_dotenv()` call, a dependency version conflict, two separate cloud deployment failures) rather than a idealized retelling.
