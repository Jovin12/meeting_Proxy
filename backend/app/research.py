import hashlib
import json
import logging
import os
import re
import threading
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import chromadb
from pydantic import BaseModel, Field

from .models import UserProfile

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "about", "after", "again", "against", "also", "among", "and", "are", "but",
    "can", "could", "does", "for", "from", "have", "how", "into", "its", "not",
    "our", "that", "the", "their", "them", "there", "these", "they", "this",
    "through", "was", "what", "when", "where", "which", "while", "with",
    "would", "you", "your",
}
_SKIP_DIRECTORIES = {
    ".git", ".venv", "__pycache__", "dist", "meet_proxy", "node_modules",
    "venv",
}
_MAX_LOCAL_FILE_BYTES = 100_000
_MAX_LOCAL_DOCUMENTS = 200
_CACHE_MIN_SIMILARITY = 0.38
_WEB_SEARCH_COOLDOWN = timedelta(minutes=1)
_WEB_SEARCH_DAILY_LIMIT = 5


class ResearchPlan(BaseModel):
    query: str = Field(max_length=400)


class ResearchCache:
    """Persistent local vector cache for profile data and prior web research."""

    COLLECTION_NAME = "meeting_research_cache"

    def __init__(
        self,
        path: Path,
        embedding_model,
        client=None,
    ):
        self.path = path
        self.embedding_model = embedding_model
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = client or chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._budget_path = path.parent / "web_search_budget.json"
        self._budget_lock = threading.Lock()

    def _embedding(self, document: str) -> list[float]:
        return self.embedding_model.encode([document]).tolist()[0]

    def update_profile(self, profile: UserProfile) -> None:
        document = profile.model_dump_json(indent=2)
        searchable_document = f"User profile and tasks:\n{document}"
        self.collection.upsert(
            ids=["user-profile"],
            documents=[searchable_document],
            metadatas=[{
                "kind": "profile",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }],
            embeddings=[self._embedding(searchable_document)],
        )

    def get_profile(self) -> str:
        result = self.collection.get(
            ids=["user-profile"],
            include=["documents"],
        )
        documents = result.get("documents") or []
        return documents[0] if documents else ""

    def search(
        self,
        query: str,
        max_results: int = 5,
        min_similarity: float = _CACHE_MIN_SIMILARITY,
    ) -> list[dict]:
        if self.collection.count() == 0:
            return []
        result = self.collection.query(
            query_embeddings=[self._embedding(query)],
            n_results=min(max_results + 1, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        matches = []
        for document, metadata, distance in zip(
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            similarity = 1.0 - distance
            if metadata.get("kind") != "research" or similarity < min_similarity:
                continue
            matches.append({
                "query": metadata.get("query", ""),
                "content": document,
                "similarity": similarity,
            })
        return matches[:max_results]

    def cache_research(self, query: str, results: list[str]) -> None:
        normalized_query = " ".join(query.casefold().split())
        cache_id = "research-" + hashlib.sha256(
            normalized_query.encode("utf-8")
        ).hexdigest()
        result_text = "\n".join(results) if results else "(No results returned.)"
        document = f"Technical web research query: {query}\nResults:\n{result_text}"
        self.collection.upsert(
            ids=[cache_id],
            documents=[document],
            metadatas=[{
                "kind": "research",
                "query": query[:400],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }],
            embeddings=[self._embedding(document)],
        )

    def acquire_web_search_slot(self) -> tuple[bool, str]:
        """Persistently enforce a cooldown and daily cap across app restarts."""
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        with self._budget_lock:
            try:
                budget = json.loads(self._budget_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                budget = {"date": today, "count": 0, "last_search_at": None}
            except (OSError, json.JSONDecodeError) as error:
                logger.error("Could not read web search budget: %s", error)
                return False, "The persistent web-search budget is unavailable."

            if budget.get("date") != today:
                budget = {"date": today, "count": 0, "last_search_at": None}
            if budget.get("count", 0) >= _WEB_SEARCH_DAILY_LIMIT:
                return False, "The daily limit for external searches has been reached."
            last_search_at = budget.get("last_search_at")
            if last_search_at:
                try:
                    previous_search = datetime.fromisoformat(last_search_at)
                except ValueError:
                    logger.error("Invalid timestamp in persistent web search budget")
                    return False, "The persistent web-search budget is invalid."
                wait_remaining = _WEB_SEARCH_COOLDOWN - (now - previous_search)
                if wait_remaining.total_seconds() > 0:
                    return False, "External search is cooling down to limit request rate."

            budget["count"] = budget.get("count", 0) + 1
            budget["last_search_at"] = now.isoformat()
            temporary_path = self._budget_path.with_suffix(".tmp")
            try:
                temporary_path.write_text(
                    json.dumps(budget),
                    encoding="utf-8",
                )
                temporary_path.replace(self._budget_path)
            except OSError as error:
                logger.exception("Could not persist web search budget")
                return False, f"The web-search budget could not be saved: {error}"
        return True, ""


class _SearchResultsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.titles: list[str] = []
        self.urls: list[str] = []
        self.snippets: list[str] = []
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._capture = "title"
            self._parts = []
            self.urls.append(attributes.get("href") or "")
        elif tag in {"a", "div"} and "result__snippet" in classes:
            self._capture = "snippet"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture == "title" and tag == "a":
            self.titles.append(" ".join("".join(self._parts).split()))
            self._capture = None
        elif self._capture == "snippet" and tag in {"a", "div"}:
            snippet = " ".join("".join(self._parts).split())
            if snippet:
                self.snippets.append(snippet)
            self._capture = None


def query_terms(query: str) -> set[str]:
    return {
        term.casefold()
        for term in re.findall(r"[A-Za-z0-9+#.-]{3,}", query)
        if term.casefold() not in _STOP_WORDS
    }


def sanitize_web_query(query: str, private_values: list[str]) -> str:
    """Remove identifiable profile and workspace strings before external search."""
    sanitized = query
    for value in private_values:
        value = value.strip()
        if value:
            sanitized = re.sub(re.escape(value), " ", sanitized, flags=re.IGNORECASE)
    return " ".join(sanitized.split())[:400]


def find_local_references(
    query: str,
    workspace_root: Path | None = None,
    max_results: int = 5,
) -> list[str]:
    """Find small, relevant excerpts from local Markdown and text documentation."""
    root = workspace_root or Path(__file__).resolve().parents[2]
    terms = query_terms(query)
    if not terms:
        return []

    candidates: list[tuple[int, str]] = []
    document_count = 0
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in _SKIP_DIRECTORIES
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix.casefold() not in {".md", ".txt"}:
                continue
            try:
                if path.stat().st_size > _MAX_LOCAL_FILE_BYTES:
                    continue
                document = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                logger.warning("Could not read local research file %s: %s", path, error)
                continue

            document_count += 1
            if document_count > _MAX_LOCAL_DOCUMENTS:
                break
            for line_number, line in enumerate(document.splitlines(), start=1):
                line_terms = query_terms(line)
                overlap = len(terms & line_terms)
                if overlap:
                    excerpt = line.strip()
                    if len(excerpt) > 600:
                        excerpt = excerpt[:597] + "..."
                    candidates.append((
                        overlap,
                        f"{path.relative_to(root)}:{line_number}: {excerpt}",
                    ))
        if document_count > _MAX_LOCAL_DOCUMENTS:
            break

    candidates.sort(key=lambda result: result[0], reverse=True)
    return [excerpt for _, excerpt in candidates[:max_results]]


def search_web(query: str, timeout: float = 6.0) -> list[str]:
    """Search DuckDuckGo's HTML endpoint without sending profile or transcript data."""
    request = Request(
        f"https://html.duckduckgo.com/html/?{urlencode({'q': query})}",
        headers={"User-Agent": "MeetingProxy/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        html = response.read(1_000_000).decode("utf-8", errors="replace")

    parser = _SearchResultsParser()
    parser.feed(html)
    results = []
    for index, title in enumerate(parser.titles[:5]):
        snippet = parser.snippets[index] if index < len(parser.snippets) else ""
        url = parser.urls[index] if index < len(parser.urls) else ""
        if title or snippet:
            results.append(" | ".join(part for part in (title, snippet, url) if part))
    return results


__all__ = [
    "ResearchPlan",
    "ResearchCache",
    "find_local_references",
    "query_terms",
    "sanitize_web_query",
    "search_web",
]
