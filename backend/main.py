"""
FastAPI entrypoint.
Exposes the single required endpoint:
    POST /api/interview
Run with:
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations
import logging

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from agent.orchestrator import InterviewAgent, InterviewError
from agent.schemas import Feedback, InterviewRequest, InterviewResponse
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("athena")
app = FastAPI(title="Athena AI Interview Agent", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
agent = InterviewAgent()
@app.get("/health")
def health():
    return {"status": "ok"}
@app.post("/api/interview", response_model=InterviewResponse, response_model_exclude_none=True)
def interview(req: InterviewRequest) -> InterviewResponse:
    try:
        if req.candidate is not None:
            reply = agent.start_interview(req.sessionId, req.candidate)
            return InterviewResponse(reply=reply, done=False)
        if req.message is not None:
            reply, done, feedback = agent.continue_interview(req.sessionId, req.message)
            return InterviewResponse(
                reply=reply,
                done=done,
                feedback=Feedback(**feedback) if feedback else None,
            )
        raise InterviewError("Request must include either 'candidate' (to start) or 'message' (to continue).")
    except InterviewError as e:
        logger.warning("Bad interview request for session=%s: %s", req.sessionId, e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Unhandled error for session=%s", req.sessionId)
        raise HTTPException(status_code=500, detail="Internal error while processing the interview turn.")