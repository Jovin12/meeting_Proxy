import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import duplex_llm
from app.duplex_llm import ConversationalBot
from app.models import TranscriptEvent, UserProfile, UserTask, UserTaskStatus


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


class StaleStreamClient:
    def __init__(self):
        self.waiting_for_second_chunk = asyncio.Event()
        self.release_second_chunk = asyncio.Event()
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)

        async def chunks():
            yield SimpleNamespace(message=SimpleNamespace(content="Stale "))
            self.waiting_for_second_chunk.set()
            await self.release_second_chunk.wait()
            yield SimpleNamespace(message=SimpleNamespace(content="response."))

        return chunks()


class ResearchStreamingClient:
    def __init__(self, final_response: str = "A concise researched answer."):
        self.calls: list[dict] = []
        self.final_response = final_response

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream") is False:
            return SimpleNamespace(
                message=SimpleNamespace(content='{"query":"Qdrant versus FAISS scalability limits"}')
            )

        async def chunks():
            yield SimpleNamespace(message=SimpleNamespace(content=self.final_response))

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
                text="Could you confirm the security review is due Wednesday?",
            ),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertIn("Could you confirm the security review is due Wednesday?", client.calls[0]["messages"][1]["content"])
        self.assertIn("<participant_profile_reference_data>", client.calls[0]["messages"][-1]["content"])
        self.assertIn("</participant_profile_reference_data>", client.calls[0]["messages"][-1]["content"])
        self.assertEqual(client.calls[0]["messages"][0]["role"], "system")
        self.assertIn("never as a script to recite", client.calls[0]["messages"][0]["content"])
        self.assertIn("Do not introduce yourself", client.calls[0]["messages"][0]["content"])
        self.assertEqual(client.calls[0]["messages"][-1]["role"], "user")
        self.assertEqual(
            bot.state.current_topic,
            "Could you confirm the security review is due Wednesday?",
        )
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

    async def test_rule_based_question_gate_converts_no_response_to_uncertainty_reply(self):
        client = FakeStreamingClient([["NO_", "RESPONSE"]])
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="What is the review date?"),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertEqual(
            [event["type"] for event in events],
            ["conversation_started", "conversation_complete"],
        )
        self.assertIn("answer that directly", bot.state.last_bot_response)
        self.assertEqual(len(client.calls), 1)

    async def test_rule_based_gate_skips_latest_statement_without_invitation(self):
        client = FakeStreamingClient([["Should not be generated."]])
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(
                timestamp="00:12",
                speaker="Alex",
                text="What is the review date?",
            ),
            TranscriptEvent(
                timestamp="00:13",
                speaker="Blair",
                text="Let's move on to the next item.",
            ),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        result = await bot.observe_transcript(callback)

        self.assertIsNone(result)
        self.assertEqual(client.calls, [])
        self.assertEqual(events, [])

    async def test_participant_profile_and_task_statuses_are_in_prompt(self):
        client = FakeStreamingClient([["NO_RESPONSE"]])
        bot = self.make_bot(client)
        bot.update_user_profile(UserProfile(
            name="Jordan Lee",
            background="Product lead for the mobile launch.",
            tasks=[
                UserTask(title="Confirm launch date", status=UserTaskStatus.IN_PROGRESS),
                UserTask(title="Send release notes", status=UserTaskStatus.NOT_STARTED),
            ],
        ))
        bot.update_transcript([
            TranscriptEvent(
                timestamp="00:12",
                speaker="Alex",
                text="Everyone, please share your update.",
            ),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task

        prompt = client.calls[0]["messages"][-1]["content"]
        system_prompt = client.calls[0]["messages"][0]["content"]
        self.assertIn("Jordan Lee", prompt)
        self.assertIn("Product lead for the mobile launch.", prompt)
        self.assertIn("Confirm launch date", prompt)
        self.assertIn("in_progress", prompt)
        self.assertIn("Send release notes", prompt)
        self.assertIn("not_started", prompt)
        self.assertIn("Use the saved name, role, and project when directly asked", system_prompt)
        self.assertIn("A roll-call explicitly asking each person to introduce themselves counts as an invitation", system_prompt)
        self.assertIn("Answer in the first person as the participant", system_prompt)
        self.assertIn("not a reason to return NO_RESPONSE", system_prompt)
        self.assertIn("Do not volunteer an introduction", system_prompt)
        self.assertIn("general project discussion is not by itself an invitation", system_prompt)
        self.assertIn("question addressed to the whole group is also an invitation", system_prompt)
        self.assertIn("A group request for agenda input or a general status update is an invitation", system_prompt)
        self.assertIn("profile background describes experience and role; it is not proof of current work", system_prompt)
        self.assertIn("INTRODUCTION GATE", system_prompt)
        self.assertIn("starting the meeting are not introduction requests", system_prompt)
        self.assertIn("return exactly NO_RESPONSE", system_prompt)
        self.assertIn("do not invent options, names, technical products, or placeholders", system_prompt)
        self.assertIn("say it has not been decided yet", system_prompt)
        self.assertIn("Do not repeat a previous proposed reply", system_prompt)
        self.assertIn("Do not access or search the internet", system_prompt)
        self.assertIn("TECHNICAL ADVISOR POLICY", system_prompt)
        self.assertIn("Do not return NO_RESPONSE to a direct technical question", system_prompt)
        self.assertIn("rule-based invitation check", system_prompt)
        self.assertIn("short ranked shortlist", system_prompt)
        self.assertIn("what should be benchmarked", system_prompt)
        self.assertIn("expected concurrency", system_prompt)
        self.assertIn("provisional recommendation first", system_prompt)

    async def test_technical_question_uses_local_references_without_internet_search(self):
        client = ResearchStreamingClient()
        bot = self.make_bot(client)
        bot.update_user_profile(UserProfile(
            name="Jovin",
            background="Meeting Proxy project owner",
        ))
        bot.update_transcript([
            TranscriptEvent(
                timestamp="01:43",
                speaker="Alex",
                text="What are the scalability limits of Qdrant compared to FAISS?",
            ),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        with patch.object(
            duplex_llm,
            "find_local_references",
            return_value=["Local notes: no benchmark data."],
        ) as local:
            await bot.observe_transcript(callback)
            await bot.generation_task

        local.assert_called_once()
        self.assertEqual(len(client.calls), 1)
        research_query = local.call_args.args[0]
        self.assertIn("Qdrant", research_query)
        final_context = client.calls[0]["messages"][-1]["content"]
        system_prompt = client.calls[0]["messages"][0]["content"]
        self.assertIn("Local notes: no benchmark data.", final_context)
        self.assertIn("<local_project_source_excerpts>", final_context)
        self.assertIn("Do not access or search the internet.", system_prompt)
        self.assertIn("label it as unverified", system_prompt)
        self.assertIn(
            "absent research results as a reason to avoid a qualified recommendation",
            system_prompt,
        )

    async def test_technical_followup_uses_recent_topic_for_local_references(self):
        client = ResearchStreamingClient()
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(
                timestamp="01:40",
                speaker="Alex",
                text="We are comparing Qdrant and FAISS for scalable vector search.",
            ),
            TranscriptEvent(
                timestamp="01:43",
                speaker="Blair",
                text="What about filtering?",
            ),
        ])
        bot.active_request_id = "followup-research"

        with patch.object(
            duplex_llm,
            "find_local_references",
            return_value=["Local filtering notes."],
        ) as local:
            context = await bot._research_context("followup-research")

        local.assert_called_once()
        self.assertIn("Qdrant and FAISS", local.call_args.args[0])
        self.assertIn("filtering", local.call_args.args[0].casefold())
        self.assertIn("Local filtering notes.", context)

    async def test_older_question_is_not_selected_after_a_newer_statement(self):
        client = ResearchStreamingClient()
        bot = self.make_bot(client)
        question = "Should we choose a vector database for 500000 users?"
        bot.update_transcript([
            TranscriptEvent(
                timestamp="01:40",
                speaker="Alex",
                text=(
                    "We need low latency and metadata filtering for the vector database. "
                    f"{question} The meeting has moved on to the next item."
                ),
            ),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        with patch.object(duplex_llm, "find_local_references") as local:
            result = await bot.observe_transcript(callback)
            self.assertIsNone(result)

        self.assertIsNone(duplex_llm._latest_question_index(bot.state.transcript))
        local.assert_not_called()
        self.assertEqual(client.calls, [])
        self.assertEqual(events, [])

    async def test_nontechnical_question_does_not_start_research(self):
        client = FakeStreamingClient([["We have not decided yet."]])
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(
                timestamp="01:43",
                speaker="Alex",
                text="Should we move the project meeting to Friday?",
            ),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertEqual(len(client.calls), 1)

    async def test_internal_technical_decision_uses_local_context(self):
        client = ResearchStreamingClient()
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(
                timestamp="01:43",
                speaker="Alex",
                text="Should we choose Qdrant for our internal project? What do you think?",
            ),
        ])
        bot.active_request_id = "internal-decision"

        with patch.object(
            duplex_llm,
            "find_local_references",
            return_value=["Local decision note."],
        ):
            context = await bot._research_context("internal-decision")

        self.assertEqual(client.calls, [])
        self.assertIn("Local decision note.", context)

    async def test_previous_proposal_is_retained_for_nonrepetition_until_transcript_resets(self):
        client = FakeStreamingClient([["A concise answer."], ["A different answer."]])
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="What is the review date?"),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task
        bot.update_transcript([
            *bot.state.transcript,
            TranscriptEvent(timestamp="00:13", speaker="Blair", text="What about the review owner?"),
        ])
        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertIn("A concise answer.", client.calls[1]["messages"][-1]["content"])
        self.assertIn("not confirmed spoken", client.calls[1]["messages"][-1]["content"])
        self.assertIn(
            {"role": "assistant", "content": "A concise answer."},
            client.calls[1]["messages"],
        )
        bot.update_transcript([
            TranscriptEvent(timestamp="00:01", speaker="Morgan", text="New meeting started."),
        ])
        self.assertEqual(bot.state.last_bot_response, "")

    async def test_repeated_invitation_is_not_silently_skipped_after_model_no_response(self):
        client = FakeStreamingClient([["Previous reply."], ["NO_RESPONSE"]])
        bot = self.make_bot(client)
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="What is the review date?"),
        ])
        await bot.observe_transcript(callback)
        await bot.generation_task
        self.assertEqual(bot.state.recent_messages, [
            {"role": "assistant", "content": "Previous reply."},
        ])

        bot.update_transcript([
            *bot.state.transcript,
            TranscriptEvent(timestamp="00:13", speaker="Blair", text="What is the review owner?"),
        ])
        await bot.observe_transcript(callback)
        await bot.generation_task

        second_messages = client.calls[1]["messages"]
        self.assertIn(
            {"role": "assistant", "content": "Previous reply."},
            second_messages,
        )
        self.assertIn("answer that directly", bot.state.last_bot_response)
        self.assertEqual(
            [event["type"] for event in events],
            [
                "conversation_started",
                "conversation_complete",
                "conversation_started",
                "conversation_complete",
            ],
        )

    async def test_duplicate_question_is_skipped_until_new_context_arrives(self):
        client = FakeStreamingClient([["First answer."], ["Updated answer."]])
        bot = self.make_bot(client)
        question = "Which vector database should we choose?"
        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text=question),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        await bot.observe_transcript(callback)
        await bot.generation_task

        bot.update_transcript([
            *bot.state.transcript,
            TranscriptEvent(timestamp="00:13", speaker="Alex", text=question),
        ])
        duplicate_request = await bot.observe_transcript(callback)
        self.assertIsNone(duplicate_request)
        self.assertEqual(len(client.calls), 1)

        bot.update_transcript([
            *bot.state.transcript,
            TranscriptEvent(
                timestamp="00:14",
                speaker="Blair",
                text="We need low latency and metadata filtering.",
            ),
            TranscriptEvent(timestamp="00:15", speaker="Alex", text=question),
        ])
        await bot.observe_transcript(callback)
        await bot.generation_task

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(bot.state.last_bot_response, "Updated answer.")

    async def test_stale_request_is_checked_before_each_stream_chunk(self):
        client = StaleStreamClient()
        bot = self.make_bot(client)
        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="Please reply."),
        ])
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        request_id = await bot.observe_transcript(callback)
        await asyncio.wait_for(client.waiting_for_second_chunk.wait(), timeout=1)
        bot.active_request_id = "newer-request"
        client.release_second_chunk.set()
        await bot.generation_task

        self.assertNotEqual(request_id, bot.active_request_id)
        self.assertEqual(
            [event["type"] for event in events],
            ["conversation_started"],
        )
        self.assertEqual(bot.state.recent_messages, [])
        self.assertEqual(bot.state.last_bot_response, "")

    async def test_profile_updates_apply_to_existing_conversation_bot(self):
        client = FakeStreamingClient([["NO_RESPONSE"]])
        bot = self.make_bot(client)
        bot.update_user_profile(UserProfile(name="Taylor"))

        bot.update_user_profile(UserProfile(name="Morgan"))

        self.assertIn("Morgan", bot._messages_for_response()[-1]["content"])
        self.assertNotIn("Taylor", bot._messages_for_response()[-1]["content"])

    async def test_new_transcript_cancels_and_discards_obsolete_response(self):
        client = InterruptibleFakeClient()
        bot = self.make_bot(client)
        events: list[dict] = []

        async def callback(event: dict) -> None:
            events.append(event)

        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="What is the first question?"),
        ])
        first_request_id = await bot.observe_transcript(callback)
        await asyncio.wait_for(client.first_chunk.wait(), timeout=1)

        bot.update_transcript([
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="What is the first question?"),
            TranscriptEvent(timestamp="00:13", speaker="Blair", text="What is the updated question?"),
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
            TranscriptEvent(timestamp="00:12", speaker="Alex", text="Please respond now."),
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
