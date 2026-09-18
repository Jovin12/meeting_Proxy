# pasing the notes.md file
# returns a structured list of strings on what the notes is
import re 
def load_notes(path:str) -> list[str]:
    with open(path, "r", encoding = "utf-8") as f: 
        text = f.read()
    notes = re.findall(r"^- (.+)$", text, re.MULTILINE)
    return notes

def load_transcript(path:str) -> str: 
    with open(path, "r", encoding = "utf-8") as f: 
        return f.read()