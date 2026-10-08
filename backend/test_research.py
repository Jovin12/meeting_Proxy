import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import research


class ResearchTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
