import tempfile
import unittest
from pathlib import Path

from app import research


class ResearchTests(unittest.TestCase):
    def test_local_references_return_paragraph_context_with_section(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "notes.md").write_text(
                "# Vector search decision\n\n"
                "The team selected FAISS for local vector search. "
                "This keeps embeddings on the application servers.\n\n"
                "The release review is Friday.",
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
        self.assertIn("keeps embeddings on the application servers", excerpts[0])
        self.assertIn("Vector search decision", excerpts[0])

    def test_bm25_ranks_specific_passage_above_repeated_common_terms(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "notes.md").write_text(
                "# General\n\n"
                "Vector database database database database operations.\n\n"
                "Vector database indexing and storage considerations.\n\n"
                "# Latency requirements\n\n"
                "Vector database search must meet a p95 latency target under "
                "20 milliseconds for interactive queries.",
                encoding="utf-8",
            )

            excerpts = research.find_local_references(
                "vector database search latency target",
                workspace_root=root,
            )

        self.assertIn("Latency requirements", excerpts[0])
        self.assertIn("p95 latency target under 20 milliseconds", excerpts[0])

    def test_known_queries_return_expected_passages_and_filter_weak_matches(self):
        passages = {
            "faiss": (
                "The team selected FAISS for local vector search. "
                "This keeps embeddings on application servers."
            ),
            "qdrant": (
                "Qdrant supports filtered vector search with payload indexes "
                "and approximate nearest-neighbor retrieval."
            ),
            "latency": (
                "For a low-latency serving system, benchmark p95 and p99 query "
                "latency under expected concurrency."
            ),
            "weak-match": "Vector systems are useful for storing embeddings.",
            "unrelated": "The release review is Friday.",
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "references.md").write_text(
                "\n\n".join(passages.values()),
                encoding="utf-8",
            )

            expected_passages = {
                "FAISS vector search local embeddings": "selected FAISS for local vector search",
                "Qdrant filtered vector search payload indexes": "Qdrant supports filtered vector search",
                "low latency p95 p99 query latency expected concurrency": "benchmark p95 and p99 query latency",
            }
            for query, expected in expected_passages.items():
                with self.subTest(query=query):
                    results = research.find_local_references(
                        query,
                        workspace_root=root,
                    )
                    self.assertTrue(results)
                    self.assertIn(expected, results[0])

            weak_results = research.find_local_references(
                "vector database scalability limits",
                workspace_root=root,
            )
            unrelated_results = research.find_local_references(
                "banana spaceship telemetry",
                workspace_root=root,
            )

        self.assertNotIn("Vector systems are useful for storing embeddings.", weak_results)
        self.assertEqual(unrelated_results, [])

    def test_research_query_preserves_requirements_from_earlier_turns(self):
        context = [
            "We want low latency for our users.",
            "Let's also consider the API contract.",
            "The database needs metadata filtering.",
            "The user base is 500,000.",
            "Let's compare the options.",
            "We also need high availability.",
        ]

        query = research.build_research_query(
            "Which vector database should we choose?",
            context,
        )

        self.assertIn("Which vector database should we choose?", query)
        self.assertIn("low latency", query)
        self.assertIn("500,000", query)
        self.assertIn("metadata filtering", query)
        self.assertIn("high availability", query)


if __name__ == "__main__":
    unittest.main()
