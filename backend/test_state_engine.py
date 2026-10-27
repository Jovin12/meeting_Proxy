import unittest

from app.models import TranscriptEvent
from app.state_engine import MeetingStateEngine


class FakeRetriever:
    def __init__(self):
        self.indexed_transcripts = []

    def index_transcript(self, transcript):
        self.indexed_transcripts.append(transcript)

    def search(self, note, top_k=3):
        return []


class MeetingStateEngineTests(unittest.TestCase):
    def setUp(self):
        self.retriever = FakeRetriever()
        self.engine = MeetingStateEngine("meeting-1", [], self.retriever)

    def test_event_with_existing_utterance_id_replaces_in_place(self):
        self.engine.add_event(
            TranscriptEvent(
                timestamp="00:01",
                speaker="Alex",
                text="We need a fast database.",
                utterance_id="utterance-1",
            )
        )
        self.engine.add_event(
            TranscriptEvent(
                timestamp="00:02",
                speaker="Alex",
                text="We need a fast database for search.",
                utterance_id="utterance-1",
            )
        )

        transcript = self.engine.get_state().transcript
        self.assertEqual(len(transcript), 1)
        self.assertEqual(transcript[0].timestamp, "00:02")
        self.assertEqual(transcript[0].text, "We need a fast database for search.")
        self.assertEqual(
            self.retriever.indexed_transcripts[-1],
            "[00:02] Alex: We need a fast database for search.",
        )

    def test_events_without_utterance_ids_are_appended(self):
        self.engine.add_event(
            TranscriptEvent(timestamp="00:01", speaker="Alex", text="First.")
        )
        self.engine.add_event(
            TranscriptEvent(timestamp="00:02", speaker="Blair", text="Second.")
        )

        transcript = self.engine.get_state().transcript
        self.assertEqual(len(transcript), 2)
        self.assertEqual([event.text for event in transcript], ["First.", "Second."])

    def test_update_replaces_only_matching_utterance(self):
        self.engine.add_event(
            TranscriptEvent(
                timestamp="00:01",
                speaker="Alex",
                text="First.",
                utterance_id="utterance-1",
            )
        )
        self.engine.add_event(
            TranscriptEvent(
                timestamp="00:02",
                speaker="Blair",
                text="Second.",
                utterance_id="utterance-2",
            )
        )
        self.engine.add_event(
            TranscriptEvent(
                timestamp="00:03",
                speaker="Alex",
                text="First, updated.",
                utterance_id="utterance-1",
            )
        )

        transcript = self.engine.get_state().transcript
        self.assertEqual(len(transcript), 2)
        self.assertEqual(
            [event.text for event in transcript],
            ["First, updated.", "Second."],
        )


if __name__ == "__main__":
    unittest.main()
