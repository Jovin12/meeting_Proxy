from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from uuid import uuid4

from .matcher import load_notes, load_transcript
from .retriever import TranscriptRetriever
from .llm import classify_note
from .models import (
    Note,
    NoteStatus,
    MeetingAnalysis,
    TranscriptEvent,
    MeetingState,
)
from .state_engine import MeetingStateEngine

app = FastAPI(title="Meeting Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

retriever = TranscriptRetriever()

meeting_engines: dict[str, MeetingStateEngine] = {}


def _get_engine(meeting_id: str) -> MeetingStateEngine:
    engine = meeting_engines.get(meeting_id)
    if engine is None:
        raise HTTPException(
            status_code=404,
            detail=f"No meeting found with id {meeting_id}.",
        )
    return engine


def _latest_engine() -> MeetingStateEngine:
    if not meeting_engines:
        raise HTTPException(
            status_code=400,
            detail="No active meeting. Start a meeting first.",
        )
    meeting_id = next(reversed(meeting_engines))
    return meeting_engines[meeting_id]


# --------------------------------------------------
# BASIC / FILE-BASED ENDPOINTS
# --------------------------------------------------

@app.get("/")
def root():
    return {"message": "Meeting Proxy API"}


@app.post("/analyze")
def analyze_meeting():
    notes = load_notes("data/notes.md")
    transcript = load_transcript("data/transcript.txt")

    retriever.index_transcript(transcript)

    results = []
    for index, note_text in enumerate(notes):
        evidence = retriever.search(note_text, top_k=3)
        analysis = classify_note(note_text, evidence)
        results.append({
            "id": index,
            "text": note_text,
            "status": analysis.status.value,
            "evidence": analysis.evidence,
            "confidence": analysis.confidence,
            "timestamp": analysis.timestamp,
        })

    return {"notes": results}


# --------------------------------------------------
# STREAMING MEETING STATE
# --------------------------------------------------

@app.post("/meeting/start")
def start_meeting():
    raw_notes = load_notes("data/notes.md")

    notes = [
        Note(id=i, text=text, status=NoteStatus.OPEN)
        for i, text in enumerate(raw_notes)
    ]

    meeting_id = str(uuid4())

    meeting_engines[meeting_id] = MeetingStateEngine(
        meeting_id=meeting_id,
        notes=notes,
        retriever=TranscriptRetriever(),
    )

    return meeting_engines[meeting_id].get_state()


# --------------------------------------------------
# FLAT ROUTES (single-meeting convenience)
# --------------------------------------------------

@app.post("/meeting/event")
def add_meeting_event_flat(event: TranscriptEvent):
    engine = _latest_engine()
    if not engine.state.active:
        raise HTTPException(400, "Meeting has already ended.")
    return engine.add_event(event)


@app.get("/meeting/state")
def get_meeting_state_flat():
    return _latest_engine().get_state()


@app.post("/meeting/end")
def end_meeting_flat():
    return _latest_engine().end_meeting()


# --------------------------------------------------
# PER-MEETING ROUTES
# --------------------------------------------------

@app.post("/meeting/{meeting_id}/event")
def add_meeting_event(meeting_id: str, event: TranscriptEvent):
    engine = _get_engine(meeting_id)
    if not engine.state.active:
        raise HTTPException(400, "Meeting has already ended.")
    return engine.add_event(event)


@app.get("/meeting/{meeting_id}/state")
def get_meeting_state(meeting_id: str):
    return _get_engine(meeting_id).get_state()


@app.post("/meeting/{meeting_id}/end")
def end_meeting(meeting_id: str):
    return _get_engine(meeting_id).end_meeting()


@app.delete("/meeting/{meeting_id}")
def delete_meeting(meeting_id: str):
    if meeting_id not in meeting_engines:
        raise HTTPException(404, f"No meeting found with id {meeting_id}.")
    del meeting_engines[meeting_id]
    return {"deleted": meeting_id}


# --------------------------------------------------
# WEBSOCKET — live streaming
# --------------------------------------------------

@app.websocket("/meeting/{meeting_id}/ws")
async def meeting_ws(websocket: WebSocket, meeting_id: str):
    await websocket.accept()

    engine = meeting_engines.get(meeting_id)
    if engine is None:
        await websocket.send_json({"error": f"No meeting {meeting_id}"})
        await websocket.close()
        return

    # 1. Push current state immediately so the client renders without a round trip.
    await websocket.send_json(engine.get_state().model_dump(mode="json"))

    try:
        while True:
            payload = await websocket.receive_json()

            # 2. Client sends a TranscriptEvent-shaped dict.
            #    `type` field lets us add control messages later without breaking.
            msg_type = payload.get("type", "event")

            if msg_type == "event":
                event = TranscriptEvent(
                    timestamp=payload["timestamp"],
                    speaker=payload.get("speaker"),
                    text=payload["text"],
                )

                # add_event does embeddings + LLM call — keep the loop unblocked.
                state = await run_in_threadpool(engine.add_event, event)

                await websocket.send_json(state.model_dump(mode="json"))

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({
                    "type": "error",
                    "detail": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        return


# --------------------------------------------------
# STATIC TEST PAGE
# --------------------------------------------------

import os
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")