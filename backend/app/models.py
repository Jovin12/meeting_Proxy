from enum import Enum

from pydantic import BaseModel, Field


class NoteStatus(str, Enum):
    OPEN = "open"
    PARTIAL = "partial"
    COMPLETED = "completed"


class NoteAnalysis(BaseModel):
    status: NoteStatus
    evidence: str
    confidence: float
    timestamp: str | None = None


class Note(BaseModel):
    id: int
    text: str
    status: NoteStatus

    evidence: str | None = None
    confidence: float | None = None

    # NEW
    timestamp: str | None = None


class MeetingAnalysis(BaseModel):
    notes: list[Note]


class TranscriptEvent(BaseModel):
    timestamp: str
    speaker: str | None = None
    text: str


class MeetingStartRequest(BaseModel):
    notes: list[str] | None = None


class NoteCreateRequest(BaseModel):
    text: str


class NotesUpdateRequest(BaseModel):
    notes: list[str]


class MeetingState(BaseModel):
    meeting_id: str
    active: bool = True
    transcript: list[TranscriptEvent] = []
    notes: list[Note] = []