from .models import (
    MeetingState,
    TranscriptEvent,
    Note,
)
from .retriever import TranscriptRetriever
from .llm import classify_note


class MeetingStateEngine:

    def __init__(
        self,
        meeting_id: str,
        notes: list[Note],
        retriever: TranscriptRetriever,
    ):
        self.state = MeetingState(
            meeting_id=meeting_id,
            active=True,
            transcript=[],
            notes=notes,
        )

        self.retriever = retriever

    def add_event(self, event: TranscriptEvent) -> MeetingState:
        """
        Add a transcript event and re-analyze the meeting notes.
        """

        if not self.state.active:
            raise ValueError("Meeting is no longer active.")

        # Add the new transcript event
        self.state.transcript.append(event)

        # Build transcript from all events
        transcript = "\n".join(
            f"[{item.timestamp}] {item.speaker}: {item.text}"
            for item in self.state.transcript
        )

        # Re-index the transcript
        self.retriever.index_transcript(transcript)

        # Analyze each note
        for note in self.state.notes:

            evidence = self.retriever.search(
                note.text,
                top_k=3,
            )

            analysis = classify_note(
                note.text,
                evidence,
            )

            # Update note with LLM analysis
            note.status = analysis.status
            note.evidence = analysis.evidence
            note.confidence = analysis.confidence
            note.timestamp = analysis.timestamp

        return self.state

    def end_meeting(self) -> MeetingState:
        """
        End the current meeting.
        """

        self.state.active = False

        return self.state

    def get_state(self) -> MeetingState:
        """
        Return the current meeting state.
        """

        return self.state