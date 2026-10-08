import tempfile
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import chromadb

from app import research
from app.models import UserProfile, UserTask, UserTaskStatus


class FakeEmbeddingModel:
    FEATURES = (
        "qdrant", "faiss", "vector", "scalability", "benchmark",
        "database", "profile", "jovin", "project", "meeting",
    )

    def encode(self, documents):
        vectors = []
        for document in documents:
            lowered = document.casefold()
            vectors.append([
                float(lowered.count(feature))
                for feature in self.FEATURES
            ])

        class Encoded:
            def __init__(self, values):
                self.values = values

            def tolist(self):
                return self.values

        return Encoded(vectors)


class ResearchTests(unittest.TestCase):
    def make_cache(self, directory):
        return research.ResearchCache(
            Path(directory) / "vector-cache",
            FakeEmbeddingModel(),
            client=chromadb.EphemeralClient(),
        )

    def test_local_references_return_relevant_documentation_lines(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "notes.md").write_text(
                "The team selected FAISS for local vector search.\n"
                "The release review is Friday.\n",
                encoding="utf-8",
            )
            (root / "unrelated.txt").write_text(
                "The office is closed for the holiday.\n",
                encoding="utf-8",
            )

            excerpts = research.find_local_references(
                "FAISS vector search",
                workspace_root=root,
            )

        self.assertEqual(len(excerpts), 1)
        self.assertIn("selected FAISS for local vector search", excerpts[0])

    def test_web_search_parses_title_snippet_and_url(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                del args
                return None

            def read(self, limit):
                if limit <= 0:
                    return b""
                return (
                    b'<a class="result__a" href="https://example.com/docs">Qdrant docs</a>'
                    b'<a class="result__snippet">Scaling guidance</a>'
                )

        with patch.object(research, "urlopen", return_value=FakeResponse()) as urlopen:
            results = research.search_web("Qdrant scaling")

        self.assertEqual(
            results,
            ["Qdrant docs | Scaling guidance | https://example.com/docs"],
        )
        request = urlopen.call_args.args[0]
        self.assertNotIn("Jovin", request.full_url)

    def test_private_profile_and_project_values_are_removed_from_web_queries(self):
        query = research.sanitize_web_query(
            "Jovin Meeting Proxy Qdrant scalability limits",
            ["Jovin", "Meeting Proxy"],
        )

        self.assertEqual(query, "Qdrant scalability limits")

    def test_profile_is_upserted_in_cache_when_it_changes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = self.make_cache(temporary_directory)
            cache.update_profile(UserProfile(
                name="Jovin",
                background="Meeting Proxy owner",
                tasks=[UserTask(title="Check vector benchmarks")],
            ))
            cache.update_profile(UserProfile(
                name="Jovin",
                background="Backend engineer",
                tasks=[UserTask(
                    title="Check vector benchmarks",
                    status=UserTaskStatus.IN_PROGRESS,
                )],
            ))

            profile = cache.get_profile()

        self.assertIn("Backend engineer", profile)
        self.assertIn("in_progress", profile)
        self.assertNotIn("Meeting Proxy owner", profile)

    def test_prior_web_research_is_recalled_from_persistent_vector_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = self.make_cache(temporary_directory)
            cache.cache_research(
                "Qdrant FAISS scalability benchmarks",
                ["Qdrant supports distributed deployments; FAISS is an embedded library."],
            )

            matches = cache.search("Qdrant scalability limits compared with FAISS")

        self.assertTrue(matches)
        self.assertIn("Qdrant FAISS scalability benchmarks", matches[0]["query"])
        self.assertIn("distributed deployments", matches[0]["content"])

    def test_web_search_budget_persists_cooldown_across_cache_instances(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = self.make_cache(temporary_directory)
            allowed, message = cache.acquire_web_search_slot()
            self.assertTrue(allowed, message)

            reopened_cache = self.make_cache(temporary_directory)
            allowed, message = reopened_cache.acquire_web_search_slot()

        self.assertFalse(allowed)
        self.assertIn("cooling down", message)

    def test_web_search_budget_enforces_daily_cap(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = self.make_cache(temporary_directory)
            cache._budget_path.write_text(json.dumps({
                "date": datetime.now(timezone.utc).date().isoformat(),
                "count": 5,
                "last_search_at": None,
            }), encoding="utf-8")

            allowed, message = cache.acquire_web_search_slot()

        self.assertFalse(allowed)
        self.assertIn("daily limit", message)


if __name__ == "__main__":
    unittest.main()
