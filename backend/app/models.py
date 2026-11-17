import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator


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


class SuggestedQuestions(BaseModel):
    questions: list[str] = Field(min_length=3, max_length=3)


class SuggestedResponse(BaseModel):
    responses: list[str] = Field(min_length=3, max_length=3)

    @field_validator("responses")
    @classmethod
    def validate_response_sentences(cls, responses: list[str]) -> list[str]:
        for response in responses:
            sentences = [
                sentence
                for sentence in re.split(r"(?<=[.!?])\s+", response.strip())
                if sentence
            ]
            if not 1 <= len(sentences) <= 3:
                raise ValueError("Each suggested response must contain 1 to 3 sentences.")
        return responses


class UserProfileTask(BaseModel):
    title: str = Field(min_length=1)
    status: str = Field(min_length=1)


class UserProfile(BaseModel):
    name: str = Field(min_length=1)
    background: str
    tasks: list[UserProfileTask] = Field(default_factory=list)


class MeetingState(BaseModel):
    meeting_id: str
    active: bool = True
    transcript: list[TranscriptEvent] = []
    notes: list[Note] = []