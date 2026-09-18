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

    prompt = f"""
        You are analyzing whether a specific meeting note was actually addressed
        during a meeting.

        Meeting note:
        {note}

        Relevant conversation:
        {evidence_text}

        Classify the meeting note using exactly one of these statuses:

        - completed:
        The conversation contains explicit evidence that the requested topic
        was resolved, decided, assigned, or otherwise completed.

        - partial:
        The conversation explicitly discusses or mentions the topic, but the
        requested outcome is not fully resolved.

        - open:
        There is no sufficient evidence that the topic was actually discussed
        or completed.

        IMPORTANT RULES:

        1. Judge ONLY from the relevant conversation provided above.

        2. Do NOT assume that something happened merely because the meeting note
        says that it should happen.

        3. A note describing an action does NOT prove that the action occurred.

        4. A decision is "completed" only when the participants explicitly make
        or confirm the decision.

        5. An assignment is "completed" only when a person or responsibility is
        explicitly assigned.

        6. A topic being mentioned does not automatically mean the note is
        completed.

        7. Do not use evidence from other notes or unrelated topics.

        8. If the evidence is ambiguous, prefer "partial" or "open" rather than
        "completed".

        9. The evidence field must explain exactly what in the conversation
        supports the classification.

        10. Do not invent people, decisions, actions, dates, or other information
            that is not present in the conversation.

        11. The evidence field MUST refer to information from the conversation,
            NOT information from the meeting note itself.

        12. Keep the evidence explanation short and factual.

        Return ONLY valid JSON matching this exact structure:

        {{
            "status": "completed",
            "evidence": "The participants explicitly agreed to use PostgreSQL.",
            "confidence": 0.95
        }}

        Additional output requirements:

        - "status" must be exactly one of:
        "completed", "partial", "open"

        - "evidence" must be a string.

        - "confidence" must be a number between 0 and 1.

        - Do not add additional fields.

        - Do not include markdown.

        - Do not include explanations outside the JSON.
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