# FastAPi backend server
from fastapi import FastAPI

from .matcher import load_notes, load_transcript
from .retriever import TranscriptRetriever
from .llm import classify_note

app = FastAPI(title = "Meeting Proxy")
retriever = TranscriptRetriever()

@app.get("/")
def root():
    return {"message":"Meeting Proxy API"}

@app.post("/analyze")
def analyze_meeting():
    notes = load_notes("data/notes.md")
    transcript = load_transcript("data/transcript.txt")
    retriever.index_transcript(transcript)

    results = []

    for index, note in enumerate(notes):
        evidence = retriever.search(note, top_k = 3)
        analysis = classify_note(note, evidence)

        results.append({
            "id": index,
            "text": note,
            "status": analysis.status.value,
            "evidence": analysis.evidence,
            "confidence": analysis.confidence,
        })

    return {
        "notes": results
    }