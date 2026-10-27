import logging
import math
import os
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

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
_MAX_PASSAGE_CHARACTERS = 1_200
MIN_BM25_SCORE = 1.0
_REQUIREMENT_CUES = re.compile(
    r"\b(latency|throughput|concurren(?:cy|t)|scale|scalable|scalability|users?|"
    r"traffic|volume|filter(?:ing)?|availability|uptime|deployment|"
    r"operational cost|cost|budget|security|consistency|retention|"
    r"require(?:d|ment|s)?|need(?:s|ed)?|must|target|priorit(?:y|ize)|"
    r"goal|prefer(?:s|red)?|vector|database|qdrant|faiss|pinecone|milvus|"
    r"weaviate|index|embedding)\b",
    re.IGNORECASE,
)


def _tokenize(text: str) -> list[str]:
    return [
        term.casefold()
        for term in re.findall(r"[A-Za-z0-9+#.-]{3,}", text)
        if term.casefold() not in _STOP_WORDS
    ]


def query_terms(query: str) -> set[str]:
    return set(_tokenize(query))


def build_research_query(
    question: str,
    conversation_context: Sequence[str],
) -> str:
    """Combine the latest question with relevant requirements from the conversation."""
    requirements: list[str] = []
    seen: set[str] = set()
    for turn in conversation_context:
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", turn):
            sentence = " ".join(sentence.split())
            normalized = sentence.casefold()
            if (
                sentence
                and normalized not in seen
                and _REQUIREMENT_CUES.search(sentence)
            ):
                requirements.append(sentence)
                seen.add(normalized)

    if not requirements:
        return question
    return (
        f"Latest question: {question}\n"
        "Relevant requirements from the conversation:\n"
        + "\n".join(requirements)
    )


def _split_long_passage(text: str) -> list[str]:
    if len(text) <= _MAX_PASSAGE_CHARACTERS:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    passages: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > _MAX_PASSAGE_CHARACTERS:
            if current:
                passages.append(current)
                current = ""
            passages.append(sentence[:_MAX_PASSAGE_CHARACTERS])
            sentence = sentence[_MAX_PASSAGE_CHARACTERS:]
        if current and len(current) + len(sentence) + 1 > _MAX_PASSAGE_CHARACTERS:
            passages.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        passages.append(current)
    return passages


def _document_passages(document: str) -> list[tuple[str, str]]:
    passages: list[tuple[str, str]] = []
    section = ""
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        body = " ".join(" ".join(paragraph_lines).split())
        paragraph_lines.clear()
        if not body:
            return
        for passage in _split_long_passage(body):
            passages.append((section, passage))

    for line in document.splitlines():
        stripped = line.strip()
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", stripped)
        if heading:
            flush_paragraph()
            section = heading.group(1).strip()
        elif stripped:
            paragraph_lines.append(stripped)
        else:
            flush_paragraph()
    flush_paragraph()
    return passages


def _bm25_scores(
    query: str,
    passages: Sequence[str],
) -> list[float]:
    terms = query_terms(query)
    if not terms or not passages:
        return [0.0] * len(passages)

    frequencies = [Counter(_tokenize(passage)) for passage in passages]
    lengths = [sum(counts.values()) for counts in frequencies]
    average_length = sum(lengths) / len(lengths) if lengths else 0
    document_frequencies = Counter(
        term
        for counts in frequencies
        for term in counts
    )

    scores: list[float] = []
    for counts, length in zip(frequencies, lengths):
        score = 0.0
        for term in terms:
            term_frequency = counts[term]
            if not term_frequency:
                continue
            document_frequency = document_frequencies[term]
            inverse_document_frequency = math.log(
                1 + (
                    len(passages) - document_frequency + 0.5
                ) / (document_frequency + 0.5)
            )
            length_normalization = (
                1 - 0.75 + 0.75 * length / average_length
                if average_length
                else 1
            )
            score += inverse_document_frequency * (
                term_frequency * (1.5 + 1)
                / (term_frequency + 1.5 * length_normalization)
            )
        scores.append(score)
    return scores


def find_local_references(
    query: str,
    workspace_root: Path | None = None,
    max_results: int = 5,
) -> list[str]:
    """Return BM25-ranked local documentation passages relevant to a query."""
    root = workspace_root or Path(__file__).resolve().parents[2]
    terms = query_terms(query)
    if not terms:
        return []

    candidates: list[tuple[str, str]] = []
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
            document_passages = _document_passages(document)
            for section, passage in document_passages:
                section_label = f" > {section}" if section else ""
                source = f"{path.relative_to(root)}{section_label}"
                candidates.append((source, passage))
        if document_count > _MAX_LOCAL_DOCUMENTS:
            break

    scores = _bm25_scores(
        query,
        [passage for _, passage in candidates],
    )
    ranked = sorted(
        (
            (score, f"{source}: {passage}")
            for (source, passage), score in zip(candidates, scores)
            if score >= MIN_BM25_SCORE
        ),
        key=lambda result: result[0],
        reverse=True,
    )
    return [excerpt for _, excerpt in ranked[:max_results]]


__all__ = [
    "build_research_query",
    "find_local_references",
    "query_terms",
]
