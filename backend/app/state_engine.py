from .models import (
    MeetingState,
    TranscriptEvent,
    Note,
    NoteStatus,
)
from .retriever import TranscriptRetriever
from .llm import classify_note


# Statuses never regress. A note that reached a higher rank stays there.
_STATUS_RANK = {
    NoteStatus.OPEN: 0,
    NoteStatus.PARTIAL: 1,
    NoteStatus.COMPLETED: 2,
}


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

        Classification is monotonic: a note can move open -> partial ->
        completed, but never backwards. Once a note reaches completed,
        it is frozen — no further LLM calls touch it. This both fixes
        the "note resets to open when the topic changes" bug and cuts
        the number of LLM calls as the meeting progresses.
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

            # Completed notes are done — don't re-classify.
            if note.status == NoteStatus.COMPLETED:
                continue

            evidence = self.retriever.search(
                note.text,
                top_k=3,
            )

            analysis = classify_note(
                note.text,
                evidence,
            )

            # Never regress to a weaker status.
            if _STATUS_RANK[analysis.status] < _STATUS_RANK[note.status]:
                continue

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