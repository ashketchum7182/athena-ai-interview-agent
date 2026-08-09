\# AI Usage Log — Athena AI Interview Agent



This project was built with AI assistance from Claude (Anthropic, claude.ai) throughout development, debugging, and deployment. This log documents specifically where and how the AI was used, for transparency.



\## Summary



Claude was used as a pair-debugging partner throughout: diagnosing real runtime bugs, walking through Windows/PowerShell terminal issues, guiding GitHub setup, debugging two separate cloud deployments (Render + GitHub Pages), catching a potential secret leak before it went public, and implementing an alternative LLM provider integration. The project's core application logic (planner.py, orchestrator.py, llm.py architecture) was already built going into this session; this log covers the environment setup, debugging, deployment, and extension work done with Claude.



\## Detailed entries



\### 1. Local environment setup (2026-08-09)

\- Diagnosed a broken Windows Python install (Microsoft Store shortcut alias intercepting the real python.exe) and walked through a proper Python 3.11 install with PATH configuration

\- Set up a virtual environment and installed dependencies from requirements.txt

\- Tool: Claude (Anthropic)



\### 2. Runtime bug fixes (2026-08-09)

\- \*\*Bug 1:\*\* `main.py` never called `load\_dotenv()`, so `.env` config (including `LLM\_PROVIDER=mock`) was silently ignored, causing the app to always try real Anthropic API calls and crash with no key present. Fixed by adding the missing import and call.

\- \*\*Bug 2:\*\* `TypeError: Client.\_\_init\_\_() got an unexpected keyword argument 'proxies'` — caused by a version mismatch between anthropic==0.39.0 and a newer, incompatible httpx release. Fixed by pinning httpx==0.27.2.

\- Verified fix via successful `uvicorn` startup and a working `/health` endpoint check.

\- Tool: Claude (Anthropic)



\### 3. Full local stack verification (2026-08-09)

\- Ran the backend (FastAPI/uvicorn on port 8000) and frontend (Python http.server on port 5500) simultaneously

\- Verified a full interview flow end-to-end in mock mode via the browser UI

\- Tool: Claude (Anthropic)



\### 4. GitHub repository setup (2026-08-09)

\- Initialized git in the correct project root, configured git identity

\- Reviewed `git status` output before committing to confirm `.gitignore` was correctly excluding `venv/` and `.env`

\- \*\*Security catch:\*\* identified that `backend/.env.txt` had been committed — a filename `.gitignore`'s `.env` rule didn't match. Verified its contents contained only placeholder text (no real API key), then removed it from git tracking and added `\*.env.txt` to `.gitignore` to prevent recurrence.

\- Pushed the initial commit to a new public repository: https://github.com/ashketchum7182/athena-ai-interview-agent

\- Tool: Claude (Anthropic)



\### 5. Backend deployment to Render (2026-08-09)

\- Configured a new Render Web Service pointing at the GitHub repo (root directory: backend, build command: pip install -r requirements.txt, start command: uvicorn main:app --host 0.0.0.0 --port $PORT)

\- \*\*Bug diagnosed:\*\* build failed with a `pydantic-core` metadata generation error — Render defaulted to Python 3.14, which has no prebuilt wheel for the pinned pydantic-core version, and source compilation failed because Render's build environment blocks writes to the Rust/cargo cache directory it needed.

\- First fix attempt (PYTHON\_VERSION environment variable) didn't take effect due to a value-parsing issue on Render's side (confirmed via a `Failed to resolve Python version 'PYTHON\_VERSION'` log line).

\- Final fix: added a `backend/runtime.txt` file containing `python-3.11.9`, committed and pushed — this correctly pinned the Python version and the build succeeded.

\- Verified live via the deployed `/health` endpoint.

\- Live URL: https://athena-ai-interview-agent.onrender.com

\- Tool: Claude (Anthropic)



\### 6. Frontend deployment via GitHub Pages (2026-08-09)

\- Updated `frontend/index.html`'s hardcoded API URL from `http://127.0.0.1:8000` to the live Render backend URL

\- Configured GitHub Pages (Settings → Pages → Deploy from branch)

\- \*\*Constraint discovered:\*\* GitHub Pages' branch-deploy folder selector only supports `/root` or `/docs`, not arbitrary folder names — renamed `frontend/` to `docs/` to work within this constraint (`git mv frontend docs`)

\- \*\*Bug diagnosed:\*\* first Pages build failed — GitHub Pages runs static sites through Jekyll by default, which attempted to process the plain HTML/JS site as a Jekyll theme and failed looking for a nonexistent `assets/css/style.scss`. Fixed by adding an empty `docs/.nojekyll` file, which tells GitHub Pages to skip Jekyll processing entirely.

\- Also debugged a local/remote sync mismatch (a `git mv` had been performed but not fully verified locally, plus a `Permission denied` file-lock error traced to a still-running local `http.server` process holding the folder open).

\- Live URL: https://ashketchum7182.github.io/athena-ai-interview-agent/

\- Tool: Claude (Anthropic)



\### 7. Render free-tier research (2026-08-09)

\- Researched Render's free-tier limits (750 instance-hours/month, 15-minute inactivity spin-down with 30-60s cold-start on the next request) to plan around potential demo-day latency

\- Tool: Claude (Anthropic), used web search for current platform documentation



\### 8. Alternative LLM provider integration — Gemini (2026-08-09)

\- The original codebase's `agent/llm.py` was built specifically around the Anthropic SDK (tool-use/forced function calling for structured interview control flow)

\- Implemented a new `GeminiLLMClient` class conforming to the same `LLMClient` interface as the existing `AnthropicLLMClient`/`MockLLMClient`, using Google's `google-generativeai` SDK and Gemini's forced function-calling (`mode: "ANY"`) to produce equivalent structured output

\- Added helper functions to convert Anthropic-style tool schemas and message role names (`assistant` → `model`) into Gemini's expected format

\- Wired the new client into `get\_llm\_client()` via a new `LLM\_PROVIDER=gemini` option

\- Tested in an isolated duplicate project copy (`athena-gemini/`) rather than the main submitted project, to avoid any risk to the working, already-deployed version

\- Tool: Claude (Anthropic)



\## Full prompt-by-prompt log

See `PROMPTS.md` in this repository for the complete chronological log of every prompt used across this build/debug/deploy session.



\## Note on authenticity

All work documented here was performed on 2026-08-09, within the hackathon submission window, using the AI tool named above throughout. This log was maintained incrementally alongside the actual work rather than reconstructed afterward.

