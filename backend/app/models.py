from enum import Enum
from pydantic import BaseModel, Field


class NoteStatus(str, Enum):
    OPEN = "open"
    PARTIAL = "partial"
    COMPLETED = "completed"


class NoteAnalysis(BaseModel):
    status: NoteStatus
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class Note(BaseModel):
    id: int
    text: str
    status: NoteStatus = NoteStatus.OPEN
    evidence: str | None = None
    confidence: float | None = None


class MeetingAnalysis(BaseModel):
    notes: list[Note]