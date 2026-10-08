import logging
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

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


class ResearchPlan(BaseModel):
    query: str = Field(max_length=400)


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
    "find_local_references",
    "query_terms",
    "sanitize_web_query",
    "search_web",
]
