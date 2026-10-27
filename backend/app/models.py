from enum import Enum

from pydantic import BaseModel, Field


class NoteStatus(str, Enum):
    OPEN = "open"
    PARTIAL = "partial"
    COMPLETED = "completed"


class UserTaskStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class UserTask(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    status: UserTaskStatus = UserTaskStatus.NOT_STARTED


class ActiveTaskUpdates(BaseModel):
    tasks: list[UserTask] = Field(default_factory=list, max_length=8)


class UserProfile(BaseModel):
    name: str = Field(default="", max_length=120)
    background: str = Field(default="", max_length=5000)
    tasks: list[UserTask] = Field(default_factory=list)


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
    questions: list[str] = Field(
        min_length=0,
        max_length=3,
        description="Three suggestions, or an empty list while waiting for other speakers.",
    )


class MeetingState(BaseModel):
    meeting_id: str
    active: bool = True
    transcript: list[TranscriptEvent] = []
    notes: list[Note] = []


class ConversationalState(BaseModel):
    transcript: list[TranscriptEvent] = Field(default_factory=list)
    current_topic: str = ""
    recent_messages: list[dict[str, str]] = Field(default_factory=list)
    last_bot_response: str = ""