# Meeting Proxy

Meeting Proxy is a local meeting-analysis service with a browser extension for
Google Meet. The extension observes finalized captions, sends them to the
backend as timestamped transcript events, and displays meeting-note progress
in a custom panel embedded on the right side of the Google Meet tab. The
backend also retains the original file-based `/analyze` endpoint for batch
analysis.

The current streaming workflow is:

1. Open Google Meet with captions enabled and load the extension.
2. Click the extension action to open the custom panel, optionally edit or
  upload one note per line, and start a meeting. The panel calls
  `POST /meeting/start` with the current notes and opens a WebSocket.
3. The content script waits for a caption row to be stable, then forwards a
  `TranscriptEvent` through the extension service worker.
4. The backend rebuilds the transcript, re-indexes it, and updates eligible
  notes against the latest retrieved evidence.
5. The panel renders each returned `MeetingState`, including the live
  transcript and note matches, and can end the meeting.

Notes can be saved during a meeting without restarting it. The panel's
**Save notes** action updates both the default `backend/data/notes.md` file and
the active meeting's note list. Saved meetings can be selected later from the
panel and reviewed with their transcript and final note matches.

The React app still exercises the older one-click `/analyze` flow. The
extension and the interactive streaming smoke-test page use the event-based
flow. The smoke-test page is available at
`http://127.0.0.1:8000/static/test.html` after the backend starts.

## How Event-to-Event Updates Work

`POST /meeting/start` accepts an optional JSON body. If `notes` is supplied,
those strings become the meeting's notes; an empty list intentionally creates a
meeting with no notes. If the body is omitted or `notes` is `null`, bullet notes
are loaded from `backend/data/notes.md`. Each note receives an `open` status, a
UUID meeting ID is created, and a `MeetingStateEngine` is stored in the
in-memory `meeting_engines` dictionary. A copy of the initial state and UTC
start time is also written to `backend/data/meeting_history.json`.

Each event has this shape:

```json
{
  "timestamp": "00:14",
  "speaker": "Alice",
  "text": "Agreed, let's use PostgreSQL."
}
```

`MeetingStateEngine.add_event()` appends the event, formats all events into a
transcript, clears and rebuilds the Chroma collection, and retrieves the three
most relevant transcript windows for every note that is not completed. It
calls Ollama for each eligible note, then applies a monotonic status policy:
`open -> partial -> completed` is allowed, but a note never moves backwards.
Completed notes are frozen and receive no further LLM calls. Evidence,
confidence, and timestamp are updated only when the new status is not weaker
than the current status.

## API

### `GET /`

Returns `{"message": "Meeting Proxy API"}`.

### `POST /meeting/start`

Accepts an optional body:

```json
{
  "notes": [
    "Decide on the database",
    "Confirm the deployment owner"
  ]
}
```

Returns the initial `MeetingState`:

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

Sets `active` to `false`, persists the final state and UTC end time, and
returns the final state. The flat equivalent is `POST /meeting/end`.

### `GET /notes/default`

Returns the current bullet notes from `backend/data/notes.md`.

### `PUT /notes/default`

Replaces `backend/data/notes.md` with the supplied note list. Empty strings are
discarded and each remaining note is written as a Markdown bullet:

```json
{
  "notes": ["Review the architecture", "Confirm the timeline"]
}
```

### `PUT /meeting/{meeting_id}/notes`

Replaces the notes for an active meeting. Notes with unchanged text retain
their current status, evidence, confidence, and timestamp. New notes start as
`open`; removed notes are removed from the active meeting. The updated state
is persisted and returned.

### `GET /meetings`

Returns saved meeting summaries in newest-first order, including meeting ID,
UTC start/end times, active state, note count, and transcript count.

### `GET /meetings/{meeting_id}`

Returns a saved meeting record containing its timestamps and complete saved
`MeetingState`.

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
also accepts `{"type": "ping"}` and responds with `{"type": "pong"}`. The
extension sends these pings every 20 seconds while connected.
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
files raise a normal file error. The live meeting registry and Chroma data are
in memory, but completed and in-progress meeting snapshots are also written to
`backend/data/meeting_history.json` for later review. The history file is not a
database and is loaded when the backend starts; active meetings cannot be
resumed after a process restart.

## Analysis Pipeline

For both workflows, `TranscriptRetriever` uses Sentence Transformers with
`all-MiniLM-L6-v2` and an in-memory ChromaDB collection named
`meeting_transcript`. It removes blank lines and creates overlapping windows
of three transcript lines. Search returns up to three `{text, distance}`
dictionaries for a note.

The streaming workflow still re-indexes the complete transcript after each
event, but it avoids reclassifying completed notes. This preserves a completed
decision when later conversation is unrelated and reduces LLM calls as notes
progress.

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
|   |   |-- notes.md          Default bullet-list notes and saved note edits
|   |   |-- meeting_history.json Persisted meeting snapshots and timestamps
|   |   |-- transcript.txt    Transcript used by legacy /analyze
|   |-- static/
|       |-- test.html        Browser smoke test for REST and WebSocket flow
|-- extension/
|   |-- manifest.json        Manifest V3 extension configuration
|   |-- background.js        Backend session, WebSocket, reconnect, and relay
|   |-- content.js           Google Meet caption observer and debounce logic
|   |-- sidepanel.html       Custom panel UI markup and styles
|   |-- sidepanel.js         Panel state rendering and controls
|   |-- settings.js          Stored backend URL settings helpers
|   |-- icons/                Extension icons
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

Creates the FastAPI app, configures CORS for `http://localhost:5173` and
browser extension origins, creates the legacy module-level retriever, and
owns the in-memory `meeting_engines: dict[str, MeetingStateEngine]` registry.
It also loads and persists `backend/data/meeting_history.json`.

- `_get_engine(meeting_id)` returns a meeting engine or raises HTTP 404.
- `_latest_engine()` returns the newest meeting or raises HTTP 400.
- `root()` implements `GET /`.
- `analyze_meeting()` implements the legacy file-based `/analyze` pipeline.
- `start_meeting()` accepts optional custom notes, creates `Note` objects,
  generates a UUID, stores a new engine with its own retriever, and records the
  initial meeting snapshot.
- `get_default_notes()` and `update_default_notes()` read and rewrite
  `backend/data/notes.md`.
- `replace_meeting_notes()` replaces notes on an active meeting and preserves
  analysis fields for unchanged note text.
- `list_meetings()` and `get_saved_meeting()` expose persisted meeting history.
- `add_meeting_event_flat(event)` and `add_meeting_event(meeting_id, event)`
  pass a `TranscriptEvent` to `MeetingStateEngine.add_event()`.
- The state and end routes delegate to `get_state()` and `end_meeting()`;
  end routes persist the final state and end time.
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
- `add_note(text)` appends one open note to the active state.
- `replace_notes(texts)` replaces the note list, preserving analysis fields for
  note text that remains unchanged.
- `add_event(event)` appends one `TranscriptEvent`, rebuilds and indexes the
  complete transcript, then classifies only notes that are not completed.
  `_STATUS_RANK` prevents status regression, and completed notes are frozen.
- `end_meeting()` marks the state inactive.
- `get_state()` returns the current `MeetingState` object.

### `backend/app/models.py`

- `NoteStatus` is the string enum `open`, `partial`, or `completed`.
- `NoteAnalysis` is the validated Ollama response.
- `Note` is a meeting note plus optional analysis fields and timestamp.
- `MeetingAnalysis` wraps a list of notes for the batch response contract.
- `TranscriptEvent` requires `timestamp` and `text`; `speaker` is optional.
- `MeetingStartRequest` accepts optional custom note text for meeting startup.
- `NoteCreateRequest` represents a single note for the legacy add-note route.
- `NotesUpdateRequest` represents a complete replacement note list.
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

## Browser Extension

The `extension/` directory contains a Manifest V3 extension targeting Google
Meet (`https://meet.google.com/*`). It is the intended live-caption client.

### Install Locally

1. Start the backend from `backend/` as described above.
2. Open `chrome://extensions` or the equivalent extensions page in a
   Chromium-based browser.
3. Enable **Developer mode**, choose **Load unpacked**, and select the
   repository's `extension/` directory.
4. Open Google Meet, enable captions, click the Meeting Proxy extension action
  to open the custom right-side panel, and click **Start meeting**.

The panel is injected into the Google Meet page by `content.js`; it is not a
browser-owned Chrome side panel or Opera sidebar. This avoids relying on
browser-specific sidebar APIs. The embedded panel loads `sidepanel.html` from
the extension and communicates with the service worker through runtime
messages.

The default backend URLs are `http://127.0.0.1:8000` for REST and
`ws://127.0.0.1:8000` for WebSocket traffic. They are stored with
`chrome.storage.sync`; `settings.js` exposes `getSettings()` and
`setSettings(partial)`, although the current panel has no settings form.
The manifest host permissions must cover any backend URL that is configured.

### Extension Message Flow

- `content.js` watches Google Meet caption rows with a `MutationObserver`.
  It waits 800 ms for text to stabilize, suppresses duplicate text per row,
  applies a 500-character safety threshold in the scraper, and sends
  `caption` messages with an elapsed `MM:SS` timestamp. The current code does
  not truncate text when that threshold is exceeded.
- `background.js` handles `start-meeting`, `end-meeting`, `get-status`,
  `get-default-notes`, `update-notes`, `get-meetings`, `get-meeting`, and
  `caption` messages. It starts meetings with the panel's note list, owns the
  WebSocket, forwards caption events, broadcasts state to the panel,
  reconnects with exponential backoff up to 30 seconds, and sends ping
  keepalives.
- `sidepanel.js` manages live and saved-meeting views, starts and ends
  meetings, edits or uploads notes, saves note changes, renders the live
  transcript, renders note status/evidence, and resynchronizes from the
  service worker when reopened. While reviewing a saved meeting, the editor
  is disabled; **Return to live** restores the current session.
- `sidepanel.html` provides the custom panel UI: connection status, meeting
  controls, live transcript, current note matches, collapsible note editor,
  upload/save controls, and saved-meeting history.
- `manifest.json` declares the service worker, Google Meet content script,
  toolbar action, storage/tabs/alarms permissions, local backend hosts, and
  web-accessible panel resources. It does not depend on `side_panel` or
  `sidebar_action` browser APIs.

The extension uses common Manifest V3 APIs and a page-injected custom panel so
the panel behavior is portable across browsers that support the extension
APIs. It still requires the caption DOM selectors in `content.js` to continue
matching the Google Meet UI.

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
  speech-to-text pipeline in the backend. The extension relies on Google
  Meet's caption DOM.
- Every event re-embeds the complete transcript. Completed notes skip Ollama,
  but open and partial notes are still reclassified as the meeting grows.
- Retriever collections and the active meeting registry are in memory. Meeting
  snapshots are persisted in a JSON history file, but active meetings cannot
  be resumed after a backend restart.
- There is no authentication or database-backed concurrency control for the
  JSON history file.
- The React UI still uses the legacy `/analyze` endpoint instead of the
  event/WebSocket flow.
- The extension has no settings UI, so changing stored backend URLs requires
  using the extension storage API or editing the code.
- Google Meet DOM selectors may change and break caption capture.
- The custom panel is only injected into Google Meet tabs where the content
  script is permitted to run; clicking the extension action on another page
  does not open a panel.
- The backend and React URLs are local and hard-coded for development.
- Ollama must be running with the configured model available.