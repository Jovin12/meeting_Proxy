# Meeting Proxy

Meeting Proxy is a local meeting-analysis service. It keeps a live transcript
as a sequence of timestamped events and updates the status of each meeting
note after every event. The backend also retains the original file-based
`/analyze` endpoint for batch analysis.

The current streaming workflow is:

1. Start a meeting with `POST /meeting/start`.
2. Send `TranscriptEvent` objects through `ws://127.0.0.1:8000/meeting/{meeting_id}/ws`.
3. After each event, rebuild the transcript, re-index it, and classify every
   note against the latest retrieved evidence.
4. Render the returned `MeetingState` or fetch it with a state endpoint.
5. End the meeting with `POST /meeting/{meeting_id}/end`.

The React app currently exercises the older one-click `/analyze` flow. The
interactive streaming smoke-test page is available at
`http://127.0.0.1:8000/static/test.html` after the backend starts.

## How Event-to-Event Updates Work

`POST /meeting/start` loads bullet notes from `backend/data/notes.md`, assigns
each note an `open` status, creates a UUID meeting ID, and stores a
`MeetingStateEngine` in the in-memory `meeting_engines` dictionary.

Each event has this shape:

```json
{
  "timestamp": "00:14",
  "speaker": "Alice",
  "text": "Agreed, let's use PostgreSQL."
}
```

`MeetingStateEngine.add_event()` appends the event, formats all events into a
transcript, clears and rebuilds the Chroma collection, retrieves the three
most relevant transcript windows for every note, and calls Ollama once per
note. The note's `status`, `evidence`, `confidence`, and supporting `timestamp`
are replaced with the newest analysis. This is a full re-analysis on every
event, not an incremental update of only the most recent note.

## API

### `GET /`

Returns `{"message": "Meeting Proxy API"}`.

### `POST /meeting/start`

Takes no body. Returns the initial `MeetingState`:

```json
{
  "meeting_id": "generated-uuid",
  "active": true,
  "transcript": [],
  "notes": [
    {
      "id": 0,
      "text": "Decide on the database",
      "status": "open",
      "evidence": null,
      "confidence": null,
      "timestamp": null
    }
  ]
}
```

### `POST /meeting/{meeting_id}/event`

Accepts a JSON `TranscriptEvent` and returns the updated `MeetingState`.
`POST /meeting/event` is a convenience route that uses the most recently
created meeting. Events are rejected after the meeting ends.

### `GET /meeting/{meeting_id}/state`

Returns the current `MeetingState`. `GET /meeting/state` reads the state for
the most recently created meeting.

### `POST /meeting/{meeting_id}/end`

Sets `active` to `false` and returns the final state. The flat equivalent is
`POST /meeting/end`.

### `DELETE /meeting/{meeting_id}`

Removes the meeting from the in-memory store and returns its deleted ID.

### `WS /meeting/{meeting_id}/ws`

The WebSocket immediately sends the current `MeetingState`. The client can
then send event messages:

```json
{
  "type": "event",
  "timestamp": "00:14",
  "speaker": "Alice",
  "text": "Agreed, let's use PostgreSQL."
}
```

The server sends the updated `MeetingState` after processing the event. It
also accepts `{"type": "ping"}` and responds with `{"type": "pong"}`.
Unknown message types receive an error object. The WebSocket loop runs the
embedding and Ollama work in a thread pool so the async receive loop is not
blocked by the synchronous analysis code.

### `POST /analyze` (legacy batch flow)

Takes no body, reads `data/notes.md` and `data/transcript.txt`, indexes the
complete transcript once, and returns one analysis per note:

```json
{
  "notes": [
    {
      "id": 0,
      "text": "Decide on the database",
      "status": "completed",
      "evidence": "The participants agreed to use PostgreSQL.",
      "confidence": 0.95,
      "timestamp": "00:14"
    }
  ]
}
```

Paths in this endpoint are relative to the backend working directory. Missing
files raise a normal file error. The API has no authentication or persistence;
meeting state and Chroma data are lost when the process exits.

## Analysis Pipeline

For both workflows, `TranscriptRetriever` uses Sentence Transformers with
`all-MiniLM-L6-v2` and an in-memory ChromaDB collection named
`meeting_transcript`. It removes blank lines and creates overlapping windows
of three transcript lines. Search returns up to three `{text, distance}`
dictionaries for a note.

`classify_note()` sends only the retrieved evidence and the note to Ollama
using `llama3.2:3b`. The model must return JSON matching `NoteAnalysis`:

- `status`: `open`, `partial`, or `completed`
- `evidence`: concise evidence-based explanation
- `confidence`: a number from `0.0` to `1.0`
- `timestamp`: supporting transcript timestamp, or `null`

Pydantic validates the response. Invalid JSON, statuses, or confidence values
raise `ValueError`.

## Running Locally

Prerequisites:

- Windows PowerShell
- Python 3.14, or a version compatible with the installed environment
- Node.js and npm for the React frontend
- Ollama with the configured model
- GPU/CUDA is recommended but CPU execution may work where supported

The repository includes a virtual environment in `meet_proxy/`. From the
repository root, start the backend in one terminal:

```powershell
.\meet_proxy\Scripts\Activate.ps1
cd .\backend
python -m uvicorn app.main:app --reload
```

The backend is available at `http://127.0.0.1:8000`. Open the stream test at
`http://127.0.0.1:8000/static/test.html` to start a meeting and send events.
The page can also poll the REST state route and includes a canned sequence
matching the topics in `backend/data/notes.md`.

To run the React frontend, use a second terminal:

```powershell
cd .\frontend
npm install
npm run dev
```

Open the Vite URL, usually `http://localhost:5173`. The React button sends a
bodyless `POST` to `http://127.0.0.1:8000/analyze`; it does not currently start
a streaming meeting or open a WebSocket.

Useful frontend checks:

```powershell
npm run lint
npm run build
npm run preview
```

Install Ollama's model before analysis:

```powershell
ollama pull llama3.2:3b
ollama list
```

The first request can be slow while the embedding model downloads and Ollama
loads the model. `backend/requirements.txt` is currently empty, so the local
virtual environment must already contain the backend packages used by the
source, including FastAPI, Uvicorn, ChromaDB, Sentence Transformers, Ollama,
and Pydantic.

## Project Structure

```text
meeting_proxy/
|-- readme.md                 Project documentation
|-- backend/
|   |-- app/
|   |   |-- __init__.py       Python package marker
|   |   |-- main.py           FastAPI routes, meeting registry, WebSocket
|   |   |-- matcher.py        Notes and transcript file readers
|   |   |-- models.py         Pydantic request and state models
|   |   |-- retriever.py      Transcript chunking, embeddings, Chroma search
|   |   |-- llm.py            Ollama note classification
|   |   |-- state_engine.py   Event-to-event meeting state updates
|   |-- data/
|   |   |-- notes.md          Bullet-list notes loaded at meeting start
|   |   |-- transcript.txt    Transcript used by legacy /analyze
|   |-- static/
|       |-- test.html        Browser smoke test for REST and WebSocket flow
|-- frontend/
|   |-- index.html            Browser document and React mount point
|   |-- package.json          Vite scripts and React dependencies
|   |-- vite.config.js        Vite React plugin configuration
|   |-- eslint.config.js      ESLint configuration
|   |-- src/
|       |-- main.jsx          React root mount
|       |-- App.jsx           Legacy analyze button and result cards
|       |-- App.css           Meeting result styles
|       |-- index.css         Global/template styles
|-- examples/
|   |-- recordvoice.py        Tkinter microphone recorder
|   |-- run_clone.py          Pocket TTS clone script
|   |-- voice_clone.py        Pocket TTS cloning UI
|   |-- basictts.py           Pocket TTS basic generation
|-- meet_proxy/               Python virtual environment
```

## Backend File Details

### `backend/app/main.py`

Creates the FastAPI app, configures CORS for `http://localhost:5173`, creates
the legacy module-level retriever, and owns the in-memory
`meeting_engines: dict[str, MeetingStateEngine]` registry.

- `_get_engine(meeting_id)` returns a meeting engine or raises HTTP 404.
- `_latest_engine()` returns the newest meeting or raises HTTP 400.
- `root()` implements `GET /`.
- `analyze_meeting()` implements the legacy file-based `/analyze` pipeline.
- `start_meeting()` loads notes, creates `Note` objects, generates a UUID, and
  stores a new engine with its own retriever.
- `add_meeting_event_flat(event)` and `add_meeting_event(meeting_id, event)`
  pass a `TranscriptEvent` to `MeetingStateEngine.add_event()`.
- The state and end routes delegate to `get_state()` and `end_meeting()`.
- `delete_meeting(meeting_id)` removes a meeting from the registry.
- `meeting_ws(websocket, meeting_id)` sends initial state, converts event
  payloads to `TranscriptEvent`, runs `add_event()` in a thread pool, and
  sends updated state JSON.

The module mounts `backend/static` at `/static` and creates that directory if
needed.

### `backend/app/state_engine.py`

`MeetingStateEngine` owns one active meeting.

- `__init__(meeting_id, notes, retriever)` creates a `MeetingState` and stores
  the meeting's `TranscriptRetriever`.
- `add_event(event)` appends one `TranscriptEvent`, rebuilds and indexes the
  complete transcript, then updates every note using `classify_note()`.
- `end_meeting()` marks the state inactive.
- `get_state()` returns the current `MeetingState` object.

### `backend/app/models.py`

- `NoteStatus` is the string enum `open`, `partial`, or `completed`.
- `NoteAnalysis` is the validated Ollama response.
- `Note` is a meeting note plus optional analysis fields and timestamp.
- `MeetingAnalysis` wraps a list of notes for the batch response contract.
- `TranscriptEvent` requires `timestamp` and `text`; `speaker` is optional.
- `MeetingState` contains `meeting_id`, `active`, all transcript events, and
  the current notes.

### `backend/app/matcher.py`

`load_notes(path)` reads UTF-8 Markdown and returns text from lines matching
`- note text`. `load_transcript(path)` returns the complete UTF-8 file. Both
take a string path and allow normal file errors to propagate.

### `backend/app/retriever.py`

`TranscriptRetriever.__init__()` loads the embedding model and creates the
in-memory Chroma collection. `chunk_transcript(transcript, window_size=3)`
returns overlapping newline-joined windows. `clear_collection()` recreates
the collection. `index_transcript(transcript)` clears old chunks, embeds new
chunks, and upserts them. `search(note, top_k=3)` returns matching text and
distance dictionaries, or an empty list for blank notes or empty collections.

### `backend/app/llm.py`

`classify_note(note, evidence)` builds the evidence-based prompt, calls
Ollama's `chat()` with the `NoteAnalysis` JSON schema, parses the response,
and returns a validated `NoteAnalysis`. `MODEL_NAME` is `llama3.2:3b`.

## Frontend and Test Page

`frontend/src/App.jsx` owns `results`, `loading`, and `error` state. Its
`analyzeMeeting()` sends the bodyless legacy request and renders each returned
note's text, status, evidence, and confidence. `main.jsx` mounts `App` under
React `StrictMode`; `App.css` and `index.css` only provide styling.

`backend/static/test.html` is the current streaming client. It starts and
ends meetings with REST, opens the meeting WebSocket, sends manual or canned
events, renders note updates, and displays raw state. It is served by FastAPI,
so it uses the backend origin and does not require a separate frontend build.

## Voice Examples

The scripts in `examples/` are independent Pocket TTS utilities and are not
part of meeting analysis. `recordvoice.py` records
`my_recorded_voice.wav`; `voice_clone.py` provides a Tkinter recording and
cloning UI; `run_clone.py` generates `clone_output.wav` from a recorded voice;
and `basictts.py` generates `output.wav` with the built-in `alba` voice.

Run an example from its directory, for example:

```powershell
cd .\examples
python .\recordvoice.py
```

The voice scripts select CUDA when `torch.cuda.is_available()` is true and
otherwise use CPU. They require microphone/audio-device access where
recording is involved.

## Current Limitations

- Transcript events are supplied by the caller; there is no microphone or
  speech-to-text pipeline in the meeting UI.
- Every event re-embeds the complete transcript and re-runs Ollama for every
  note, which becomes expensive as meetings grow.
- Meetings, retriever collections, and the meeting registry are in memory.
- There is no authentication, database persistence, or meeting recovery.
- The React UI still uses the legacy `/analyze` endpoint instead of the
  event/WebSocket flow.
- The backend and React URLs are local and hard-coded for development.
- Ollama must be running with the configured model available.