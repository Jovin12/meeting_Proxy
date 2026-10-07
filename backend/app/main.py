import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from uuid import uuid4

from .matcher import load_notes, load_transcript
from .retriever import TranscriptRetriever
from .llm import classify_note, suggest_questions
from .models import (
    Note,
    NoteStatus,
    MeetingAnalysis,
    NoteCreateRequest,
    NotesUpdateRequest,
    MeetingStartRequest,
    TranscriptEvent,
    MeetingState,
    SuggestedQuestions,
)
from .state_engine import MeetingStateEngine
from .duplex_llm import ConversationalBot

from .basictts import robotic_tts

app = FastAPI(title="Meeting Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_origin_regex=r"^(chrome-extension|moz-extension|opera-extension)://[^/]+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

retriever = TranscriptRetriever()

meeting_engines: dict[str, MeetingStateEngine] = {}
conversational_engines: dict[str, ConversationalBot] = {}
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HISTORY_PATH = DATA_DIR / "meeting_history.json"


def _load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as history_file:
            value = json.load(history_file)
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


meeting_history = _load_history()


def _persist_history() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = HISTORY_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as history_file:
        json.dump(meeting_history, history_file, indent=2)
    temporary_path.replace(HISTORY_PATH)


def _save_meeting_state(state: MeetingState, ended_at: str | None = None) -> None:
    for record in meeting_history:
        if record["meeting_id"] == state.meeting_id:
            record["state"] = state.model_dump(mode="json")
            if ended_at is not None:
                record["ended_at"] = ended_at
            _persist_history()
            return


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


async def _interrupt_conversation(meeting_id: str) -> None:
    conversational_engine = conversational_engines.get(meeting_id)
    if conversational_engine is not None:
        await conversational_engine.interrupt()


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
def start_meeting(request: MeetingStartRequest | None = None):
    raw_notes = (
        request.notes
        if request is not None and request.notes is not None
        else load_notes(str(DATA_DIR / "notes.md"))
    )
    raw_notes = [note.strip() for note in raw_notes if note.strip()]

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
    conversational_engines[meeting_id] = ConversationalBot()

    state = meeting_engines[meeting_id].get_state()
    meeting_history.append({
        "meeting_id": meeting_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "state": state.model_dump(mode="json"),
    })
    _persist_history()
    return state


@app.get("/notes/default")
def get_default_notes():
    return {"notes": load_notes(str(DATA_DIR / "notes.md"))}


@app.put("/notes/default")
def update_default_notes(request: NotesUpdateRequest):
    notes = [note.strip() for note in request.notes if note.strip()]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / "notes.md").open("w", encoding="utf-8") as notes_file:
        notes_file.write("\n".join(f"- {note}" for note in notes))
        if notes:
            notes_file.write("\n")
    return {"notes": notes}


@app.get("/meetings")
def list_meetings():
    return [
        {
            "meeting_id": record["meeting_id"],
            "started_at": record["started_at"],
            "ended_at": record["ended_at"],
            "active": record["state"].get("active", False),
            "note_count": len(record["state"].get("notes", [])),
            "transcript_count": len(record["state"].get("transcript", [])),
        }
        for record in reversed(meeting_history)
    ]


@app.get("/meetings/{meeting_id}")
def get_saved_meeting(meeting_id: str):
    for record in meeting_history:
        if record["meeting_id"] == meeting_id:
            return record
    raise HTTPException(404, f"No saved meeting with id {meeting_id}.")


# --------------------------------------------------
# FLAT ROUTES (single-meeting convenience)
# --------------------------------------------------

@app.post("/meeting/event")
def add_meeting_event_flat(event: TranscriptEvent):
    engine = _latest_engine()
    if not engine.state.active:
        raise HTTPException(400, "Meeting has already ended.")
    state = engine.add_event(event)
    conversational_engines[state.meeting_id].update_transcript(state.transcript)
    return state


@app.get("/meeting/state")
def get_meeting_state_flat():
    return _latest_engine().get_state()


@app.post("/meeting/end")
async def end_meeting_flat():
    engine = _latest_engine()
    await _interrupt_conversation(engine.state.meeting_id)
    state = engine.end_meeting()
    _save_meeting_state(state, datetime.now(timezone.utc).isoformat())
    return state


# --------------------------------------------------
# PER-MEETING ROUTES
# --------------------------------------------------

@app.post("/meeting/{meeting_id}/event")
def add_meeting_event(meeting_id: str, event: TranscriptEvent):
    engine = _get_engine(meeting_id)
    if not engine.state.active:
        raise HTTPException(400, "Meeting has already ended.")
    state = engine.add_event(event)
    conversational_engines[meeting_id].update_transcript(state.transcript)
    return state


@app.post("/meeting/{meeting_id}/notes")
def add_meeting_note(meeting_id: str, request: NoteCreateRequest):
    engine = _get_engine(meeting_id)
    if not engine.state.active:
        raise HTTPException(400, "Meeting has already ended.")
    state = engine.add_note(request.text)
    _save_meeting_state(state)
    return state


@app.put("/meeting/{meeting_id}/notes")
def replace_meeting_notes(meeting_id: str, request: NotesUpdateRequest):
    engine = _get_engine(meeting_id)
    if not engine.state.active:
        raise HTTPException(400, "Meeting has already ended.")
    state = engine.replace_notes(request.notes)
    _save_meeting_state(state)
    return state


@app.get("/meeting/{meeting_id}/state")
def get_meeting_state(meeting_id: str):
    return _get_engine(meeting_id).get_state()


@app.post("/meeting/{meeting_id}/questions", response_model=SuggestedQuestions)
async def get_suggested_questions(meeting_id: str):
    engine = _get_engine(meeting_id)
    transcript = engine.state.transcript
    if not transcript:
        raise HTTPException(400, "No transcript is available for question suggestions.")

    transcript_text = "\n".join(
        f"[{event.timestamp}] {event.speaker or 'Unknown'}: {event.text}"
        for event in transcript
    )
    try:
        return await run_in_threadpool(suggest_questions, transcript_text)
    except ValueError as error:
        raise HTTPException(502, str(error)) from error


@app.post("/meeting/{meeting_id}/end")
async def end_meeting(meeting_id: str):
    await _interrupt_conversation(meeting_id)
    state = _get_engine(meeting_id).end_meeting()
    _save_meeting_state(state, datetime.now(timezone.utc).isoformat())
    return state


@app.delete("/meeting/{meeting_id}")
async def delete_meeting(meeting_id: str):
    if meeting_id not in meeting_engines:
        raise HTTPException(404, f"No meeting found with id {meeting_id}.")
    await _interrupt_conversation(meeting_id)
    del meeting_engines[meeting_id]
    conversational_engines.pop(meeting_id, None)
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

    conversational_engine = conversational_engines.get(meeting_id)
    if conversational_engine is None:
        conversational_engine = ConversationalBot()
        conversational_engines[meeting_id] = conversational_engine
    conversational_engine.update_transcript(engine.state.transcript)

    send_lock = asyncio.Lock()

    async def send_payload(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    async def send_conversation_event(payload: dict) -> None:
        try:
            await send_payload(payload)
        except (RuntimeError, WebSocketDisconnect):
            return

    # 1. Push current state immediately so the client renders without a round trip.
    await send_payload(engine.get_state().model_dump(mode="json"))

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
                conversational_engine.update_transcript(state.transcript)
                _save_meeting_state(state)

                await send_payload(state.model_dump(mode="json"))
                await conversational_engine.observe_transcript(send_conversation_event)

            elif msg_type == "conversation_interrupt":
                await conversational_engine.interrupt(send_conversation_event)

            elif msg_type == "ping":
                await send_payload({"type": "pong"})

            else:
                await send_payload({
                    "type": "error",
                    "detail": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        await conversational_engine.interrupt()
        return


# -------------------------------------------------
# GENERATING TTS ENDPOINTS
# -------------------------------------------------

@app.post("/generate-tts")
async def generate_tts(payload: dict):
    text = payload.get('text', "")
    if not text: 
        return Response(content = "No text Provided", status_code = 400)

    audio = await run_in_threadpool(robotic_tts, text)
    return Response(content=audio, media_type="audio/wav")



# --------------------------------------------------
# STATIC TEST PAGE
# --------------------------------------------------

import os
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")