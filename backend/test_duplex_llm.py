import asyncio
import unittest
from types import SimpleNamespace

from app.duplex_llm import ConversationalBot
from app.models import TranscriptEvent


class FakeStreamingClient:
    def __init__(self, responses: list[list[str]]):
        self.responses = responses
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses[len(self.calls) - 1]

        async def chunks():
            for content in response:
                yield SimpleNamespace(message=SimpleNamespace(content=content))

        return chunks()


class InterruptibleFakeClient:
    def __init__(self):
        self.calls: list[dict] = []
        self.first_chunk = asyncio.Event()

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        call_number = len(self.calls)

        async def chunks():
            if call_number == 1:
                yield SimpleNamespace(message=SimpleNamespace(content="obsolete partial"))
                self.first_chunk.set()
                await asyncio.Event().wait()
            else:
                yield SimpleNamespace(message=SimpleNamespace(content="Updated response."))

        return chunks()


class ConversationalBotTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, client):
        bot = ConversationalBot(client=client)
        bot.observation_debounce_seconds = 0
        return bot

    async def test_observes_transcript_and_proposes_without_synthesizing(self):
        client = FakeStreamingClient([["The review is", " due Wednesday."]])
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(
                timestamp="00:12",
                speaker="Alex",
                text="The security review is due Wednesday.",
            ),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertIn("The security review is due Wednesday.", client.calls[0]["messages"][1]["content"])
        self.assertEqual(client.calls[0]["messages"][0]["role"], "system")
        self.assertEqual(client.calls[0]["messages"][-1]["role"], "user")
        self.assertEqual(bot.state.current_topic, "The security review is due Wednesday.")
        self.assertEqual(bot.state.last_bot_response, "The review is due Wednesday.")
        self.assertEqual(
            [event["type"] for event in events],
            ["conversation_started", "conversation_complete"],
        )
        self.assertFalse(any(event["type"] == "conversation_audio" for event in events))
        self.assertEqual(
            bot.state.recent_messages,
            [{"role": "assistant", "content": "The review is due Wednesday."}],
        )

    async def test_model_can_decide_no_response_is_needed(self):
        client = FakeStreamingClient([["NO_", "RESPONSE"]])
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="Let's move on."),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertEqual(
            [event["type"] for event in events],
            ["conversation_started", "conversation_no_response"],
        )
        self.assertEqual(bot.state.recent_messages, [])
        self.assertEqual(bot.state.last_bot_response, "")

    async def test_new_transcript_cancels_and_discards_obsolete_response(self):
        client = InterruptibleFakeClient()
        bot = self.make_bot(client)
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="First question."),
        ])
        first_request_id = await bot.observe_transcript(callback)
        await asyncio.wait_for(client.first_chunk.wait(), timeout=1)

        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="First question."),
            TranscriptEvent(timestamp="00:13", speaker="Blair", text="Updated question."),
        ])
        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertTrue(any(
            event["type"] == "conversation_interrupted" and event["request_id"] == first_request_id
            for event in events
        ))
        self.assertEqual(bot.state.last_bot_response, "Updated response.")
        self.assertEqual(
            bot.state.recent_messages,
            [{"role": "assistant", "content": "Updated response."}],
        )
        self.assertFalse(any(event["type"] == "conversation_delta" for event in events))

    async def test_explicit_interrupt_cancels_generation(self):
        client = InterruptibleFakeClient()
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="Start response."),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        request_id = await bot.observe_transcript(callback)
        await asyncio.wait_for(client.first_chunk.wait(), timeout=1)
        await bot.interrupt(callback)

        self.assertFalse(bot.is_generating)
        self.assertTrue(any(
            event["type"] == "conversation_interrupted" and event["request_id"] == request_id
            for event in events
        ))


if __name__ == "__main__":
    unittest.main()
