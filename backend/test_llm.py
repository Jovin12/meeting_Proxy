import unittest
from unittest.mock import patch

from app import llm
from app.models import (
    ActiveTaskUpdates,
    SuggestedQuestions,
    TranscriptEvent,
    UserProfile,
    UserTask,
    UserTaskStatus,
)


class QuestionSuggestionTests(unittest.TestCase):
    def test_question_transcript_excludes_participant_proxy_turns(self):
        transcript = [
            TranscriptEvent(
                timestamp="00:01",
                speaker="Alex",
                text="We still need to confirm the launch date.",
            ),
            TranscriptEvent(
                timestamp="00:02",
                speaker="Jordan Lee",
                text="I'm Jordan, the product lead for this project.",
            ),
            TranscriptEvent(
                timestamp="00:03",
                speaker="Jordan Lee (You)",
                text="Should I send the release notes today?",
            ),
            TranscriptEvent(
                timestamp="00:04",
                speaker="Taylor",
                text="The QA review is waiting on the new build.",
            ),
        ]

        result = llm.transcript_for_question_suggestions(transcript, "Jordan Lee")

        self.assertIn("Alex: We still need to confirm the launch date.", result)
        self.assertIn("Taylor: The QA review is waiting on the new build.", result)
        self.assertNotIn("Jordan Lee:", result)
        self.assertNotIn("release notes", result)

    def test_question_transcript_is_empty_when_only_proxy_turns_exist(self):
        transcript = [
            TranscriptEvent(
                timestamp="00:02",
                speaker="Jordan Lee (You)",
                text="I'm Jordan, the product lead for this project.",
            ),
        ]

        self.assertEqual(
            llm.transcript_for_question_suggestions(transcript, "Jordan Lee"),
            "",
        )

    def test_question_generation_uses_profile_and_other_speaker_context(self):
        profile = UserProfile(
            name="Jordan Lee",
            background="Product lead on the mobile launch.",
            tasks=[
                UserTask(
                    title="Confirm the launch date",
                    status=UserTaskStatus.IN_PROGRESS,
                ),
            ],
        )
        expected = SuggestedQuestions(questions=[
            "What is blocking confirmation of the launch date?",
            "When will QA receive the updated build?",
            "Who owns the next launch readiness review?",
        ])
        with patch.object(
            llm,
            "chat",
            return_value={"message": {"content": expected.model_dump_json()}},
        ) as chat:
            result = llm.suggest_questions(
                "[00:01] Alex: We still need to confirm the launch date.",
                profile,
            )

        self.assertEqual(result, expected)
        prompt = chat.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Jordan Lee", prompt)
        self.assertIn("Product lead on the mobile launch.", prompt)
        self.assertIn("Confirm the launch date", prompt)
        self.assertIn("do not write a question that replies to or follows up on", prompt)


class ActiveTaskMemoryTests(unittest.TestCase):
    def test_does_not_call_model_without_a_profile_name(self):
        profile = UserProfile(name="")
        event = TranscriptEvent(
            timestamp="00:12",
            speaker="Morgan",
            text="Can you check the database benchmarks?",
        )

        with patch.object(llm, "chat") as chat:
            result = llm.extract_active_task_updates(event, profile)

        self.assertEqual(result.tasks, [])
        chat.assert_not_called()

    def test_extracts_only_explicit_assignment_and_includes_existing_state(self):
        profile = UserProfile(
            name="Jovin",
            background="Backend engineer",
            tasks=[UserTask(title="Review API contract", status=UserTaskStatus.IN_PROGRESS)],
        )
        event = TranscriptEvent(
            timestamp="00:12",
            speaker="Morgan",
            text="Jovin, can you check the database benchmarks by tomorrow?",
        )
        expected = ActiveTaskUpdates(tasks=[
            UserTask(title="Check the database benchmarks", status=UserTaskStatus.NOT_STARTED),
        ])
        with patch.object(
            llm,
            "chat",
            return_value={"message": {"content": expected.model_dump_json()}},
        ) as chat:
            result = llm.extract_active_task_updates(event, profile)

        self.assertEqual(result, expected)
        prompt = chat.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Jovin", prompt)
        self.assertIn("Review API contract", prompt)
        self.assertIn("database benchmarks", prompt)
        self.assertIn("general request to the whole group", prompt)
        self.assertIn("even though it is phrased as a question", prompt)
        self.assertIn("Jovin, can you check the database benchmarks by tomorrow?", prompt)

    def test_merge_adds_and_updates_tasks_without_changing_profile_fields(self):
        profile = UserProfile(
            name="Jovin",
            background="Backend engineer",
            tasks=[
                UserTask(title="Review API contract", status=UserTaskStatus.NOT_STARTED),
            ],
        )
        updates = ActiveTaskUpdates(tasks=[
            UserTask(title="review api contract", status=UserTaskStatus.IN_PROGRESS),
            UserTask(title="Check database benchmarks", status=UserTaskStatus.NOT_STARTED),
        ])

        result = llm.merge_active_task_updates(profile, updates)

        self.assertEqual(result.name, "Jovin")
        self.assertEqual(result.background, "Backend engineer")
        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(result.tasks[0].title, "Review API contract")
        self.assertEqual(result.tasks[0].status, UserTaskStatus.IN_PROGRESS)
        self.assertEqual(result.tasks[1].status, UserTaskStatus.NOT_STARTED)
        self.assertEqual(profile.tasks[0].status, UserTaskStatus.NOT_STARTED)

    def test_merge_does_not_regress_a_task_status(self):
        profile = UserProfile(
            name="Jovin",
            tasks=[UserTask(title="Run benchmarks", status=UserTaskStatus.COMPLETED)],
        )
        updates = ActiveTaskUpdates(tasks=[
            UserTask(title="Run benchmarks", status=UserTaskStatus.IN_PROGRESS),
        ])

        result = llm.merge_active_task_updates(profile, updates)

        self.assertEqual(result.tasks[0].status, UserTaskStatus.COMPLETED)

    def test_extracts_explicit_completion_for_an_existing_task(self):
        profile = UserProfile(
            name="Jovin",
            tasks=[UserTask(
                title="Check database benchmarks",
                status=UserTaskStatus.IN_PROGRESS,
            )],
        )
        event = TranscriptEvent(
            timestamp="00:24",
            speaker="Morgan",
            text="Jovin finished checking the database benchmarks.",
        )
        expected = ActiveTaskUpdates(tasks=[
            UserTask(
                title="Check database benchmarks",
                status=UserTaskStatus.COMPLETED,
            ),
        ])
        with patch.object(
            llm,
            "chat",
            return_value={"message": {"content": expected.model_dump_json()}},
        ) as chat:
            result = llm.extract_active_task_updates(event, profile)

        self.assertEqual(result, expected)
        self.assertIn("Return only the changed task", chat.call_args.kwargs["messages"][0]["content"])

    def test_duplicate_task_updates_are_idempotent(self):
        profile = UserProfile(
            name="Jovin",
            tasks=[UserTask(title="Check database benchmarks")],
        )
        updates = ActiveTaskUpdates(tasks=[
            UserTask(title="Check database benchmarks", status=UserTaskStatus.IN_PROGRESS),
        ])

        once = llm.merge_active_task_updates(profile, updates)
        twice = llm.merge_active_task_updates(once, updates)

        self.assertEqual(twice.tasks, once.tasks)
        self.assertEqual(len(twice.tasks), 1)


if __name__ == "__main__":
    unittest.main()
