import json

from ollama import chat
from .models import NoteAnalysis


MODEL_NAME = "llama3.2:3b"


def classify_note(note: str, evidence: list[str]) -> NoteAnalysis:
    """
    Classify a meeting note based only on the retrieved transcript evidence.
    """

    evidence_text = "\n".join(
        f"- {item}" for item in evidence
    )

    prompt = prompt = f"""
        You are analyzing a meeting transcript against one meeting note.

        MEETING NOTE:
        {note}

        TRANSCRIPT EVIDENCE:
        {evidence_text}

        Determine whether the meeting actually addressed the meeting note.

        IMPORTANT RULES:

        1. Semantic similarity alone is NOT evidence that a topic was discussed.

        2. Mark the note as "open" when the supplied transcript evidence
        does not directly discuss the subject of the note.

        3. Mark the note as "partial" when the transcript directly discusses
        the subject but does not clearly reach the requested outcome.

        4. Mark the note as "completed" only when the transcript contains
        clear evidence that the requested action, decision, or outcome
        was completed.

        5. Do not infer decisions that were not explicitly made.

        6. Do not treat discussion of a related topic as discussion of
        the requested note.

        7. Return the timestamp of the transcript evidence that best
        supports your classification.

        8. If there is no direct supporting evidence, return null for timestamp.

        Return JSON matching this schema:

        {{
            "status": "open | partial | completed",
            "evidence": "A concise explanation based only on the transcript evidence.",
            "confidence": 0.0,
            "timestamp": "timestamp from the supporting transcript evidence or null"
        }}
    """

    response = chat(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        format=NoteAnalysis.model_json_schema(),
    )

    raw = response["message"]["content"]

    try:
        data = json.loads(raw)

        return NoteAnalysis.model_validate(data)

    except Exception as e:
        print("\n========== INVALID OLLAMA RESPONSE ==========")
        print(raw)
        print("==============================================")
        print(f"Validation error: {e}")

        raise ValueError(
            f"Ollama returned an invalid NoteAnalysis response: {e}"
        )