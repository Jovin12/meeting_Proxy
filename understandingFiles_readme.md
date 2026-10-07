# Meeting Proxy

Meeting Proxy is a local meeting-analysis service with a browser extension for
Google Meet. The extension observes finalized captions, sends them to the
backend as timestamped transcript events, and displays meeting-note progress
in a custom panel embedded on the right side of the Google Meet tab. The
panel has a **Minutes of the Meeting** tab for captions, notes, and saved
meetings, and a **User Proxy** tab for transcript-based question suggestions,
transcript-driven reply proposals, and switching Meet's outgoing audio between
the selected microphone and generated speech. The backend also
retains the original file-based `/analyze` endpoint for batch analysis and
exposes Pocket TTS through `POST /generate-tts`.

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
6. In **User Proxy**, the panel requests three questions from the accumulated
  meeting transcript. Selecting a suggestion fills the speech field; **Speak**
  sends it through Pocket TTS and the existing audio bridge.
7. The conversational engine observes each transcript update, debounces
  changing captions, and decides whether a reply is needed. Any proposed text
  appears in the panel; only **Speak response** sends it through Pocket TTS
  and the existing audio bridge. **Interrupt** cancels an active decision or
  stops current proxy playback.

Question generation uses the meeting transcript maintained by caption
analysis, but does not change note classification. `MeetingStateEngine` owns
transcript ingestion and note classification; `ConversationalBot` owns its
separate transcript observation and proposal state. The User Proxy audio
path uses the audio track Google Meet already sends, routes it through a Web
Audio graph, and switches between the selected microphone and generated WAV
audio. This relies on an experimental WebRTC bridge because Google Meet has no
supported extension API for replacing its outgoing audio track; see
[User Proxy Audio](#user-proxy-audio).

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

### `POST /generate-tts`

Accepts JSON text and returns generated speech as binary WAV data:

```http
POST /generate-tts
Content-Type: application/json

{"text":"Hello!"}
```

The response content type is `audio/wav`, not JSON. Empty text returns HTTP
400. Pocket TTS loads its model and the built-in `alba` voice lazily on the
first request, then reuses them. Synthesis runs in a thread pool so it does not
block FastAPI's async request loop.

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

### `POST /meeting/{meeting_id}/questions`

Uses the specified meeting's accumulated transcript to ask Ollama for exactly
three concise, transcript-grounded questions. The response has this shape:

```json
{
  "questions": [
    "What is the target date?",
    "Who owns the rollout?",
    "What risks remain?"
  ]
}
```

The endpoint returns HTTP 400 when the meeting has no transcript and HTTP 502
if the Ollama response cannot be parsed or validated. Model inference runs in
a thread pool. The extension requests suggestions when **User Proxy** opens
and debounces refreshes after transcript updates while the tab remains visible;
the user can also refresh manually.

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

After each transcript event, the same socket updates the conversational
observer without modifying `MeetingState` or note classification. The
observer debounces caption updates and supports this control message:

```json
{"type": "conversation_interrupt"}
```

Responses arrive as `conversation_started` followed by either
`conversation_no_response`, `conversation_complete`, or `conversation_error`.
The completed text is only a proposal. It is not synthesized or sent to Meet
until the user explicitly selects **Speak response** in the panel.

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
- Pocket TTS, PyTorch, and SciPy in the backend Python environment for TTS
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
Pydantic, Pocket TTS, PyTorch, and SciPy.

## Project Structure

```text
meeting_proxy/
|-- readme.md                 Project documentation
|-- understandingFiles_readme.md Architecture, workflows, and file reference
|-- backend/
|   |-- requirements.txt      Currently empty; dependencies are environment-managed
|   |-- app/
|   |   |-- __init__.py       Python package marker
|   |   |-- main.py           FastAPI routes, meeting registries, WebSockets
|   |   |-- matcher.py        Notes and transcript file readers
|   |   |-- models.py         Pydantic request, meeting, and conversation state
|   |   |-- retriever.py      Transcript chunking, embeddings, Chroma search
|   |   |-- llm.py            Ollama note classification and question suggestions
|   |   |-- duplex_llm.py     Transcript-driven conversation decision engine
|   |   |-- state_engine.py   Event-to-event meeting state updates
|   |   |-- basictts.py       Lazy Pocket TTS model and WAV-byte generation
|   |-- data/
|   |   |-- notes.md          Default bullet-list notes and saved note edits
|   |   |-- meeting_history.json Persisted meeting snapshots and timestamps
|   |   |-- transcript.txt    Transcript used by legacy /analyze
|   |-- static/
|       |-- test.html        REST, fake transcript, conversation, and interrupt smoke test
|   |-- test_duplex_llm.py    Fake-client conversation and cancellation tests
|-- extension/
|   |-- manifest.json        Manifest V3 extension configuration
|   |-- background.js        Backend session, WebSocket, reconnect, and relay
|   |-- content.js           Google Meet caption observer and debounce logic
|   |-- meet_audio_bridge.js MAIN-world WebRTC audio-track bridge
|   |-- sidepanel.html       Two-tab panel UI markup and styles
|   |-- sidepanel.js         Tabs, transcript questions, meeting state, and TTS
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
  sends updated state JSON. It mirrors transcript events into the meeting's
  `ConversationalBot`, which asynchronously decides whether a reply is needed;
  interrupt messages cancel its current decision. Note analysis remains in
  `MeetingStateEngine`.
- `conversational_engines` stores one conversational bot per meeting.
  Meeting end and deletion cancel any in-flight response.
- `get_suggested_questions(meeting_id)` formats that meeting's transcript,
  calls the Ollama question generator in a thread pool, and returns exactly
  three validated questions. It returns HTTP 400 for an empty transcript and
  HTTP 502 for invalid model output.
- `generate_tts(payload)` calls the reusable `robotic_tts()` helper in a
  thread pool and returns its WAV bytes with `audio/wav` content type.

The module mounts `backend/static` at `/static` and creates that directory if
needed.

### `backend/app/basictts.py`

`robotic_tts(text)` lazily loads the Pocket TTS model and the built-in `alba`
voice on its first call, then reuses both for later synthesis requests. A lock
serializes model generation. The resulting samples are encoded into an
in-memory WAV file and returned as bytes; the helper does not write a WAV file
to disk.

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
- `SuggestedQuestions` validates the question generator response as exactly
  three strings.
- `MeetingState` contains `meeting_id`, `active`, all transcript events, and
  the current notes.
- `ConversationalState` contains the mirrored transcript, latest transcript
  turn as `current_topic`, a bounded assistant-response history, and the last
  completed proposed response. It is separate from persisted `MeetingState`.

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
and returns a validated `NoteAnalysis`. `suggest_questions(transcript)` uses
the same `llama3.2:3b` model to generate exactly three concise questions based
only on the supplied transcript, then validates the JSON with `SuggestedQuestions`.
Transcript text is treated as untrusted data rather than model instructions.
`MODEL_NAME` is `llama3.2:3b`.

### `backend/app/duplex_llm.py`

`ConversationalBot` mirrors transcript events into its own
`ConversationalState`, keeps the latest transcript turn as the topic cue, and
maintains the last 12 assistant proposals. Each transcript update cancels the
previous observation and schedules a debounced model decision. The model either
receives transcript context as the final user message, then either returns the
exact `NO_RESPONSE` marker or a proposed reply; proposals are sent as text and
are never synthesized automatically. `interrupt()` cancels the active decision
so stale proposals are not delivered.

### `backend/test_duplex_llm.py`

Uses a fake streaming Ollama client to test transcript context, proposals,
no-response decisions, and cancellation of obsolete or interrupted
observations. Run with:

```powershell
.\meet_proxy\Scripts\python.exe -m unittest discover -s backend -p test_duplex_llm.py -v
```

## Frontend and Test Page

`frontend/src/App.jsx` owns `results`, `loading`, and `error` state. Its
`analyzeMeeting()` sends the bodyless legacy request and renders each returned
note's text, status, evidence, and confidence. `main.jsx` mounts `App` under
React `StrictMode`; `App.css` and `index.css` only provide styling.

`backend/static/test.html` is the streaming smoke client. It starts and ends
meetings with REST, opens the meeting WebSocket, sends manual or canned events,
renders note updates, and displays raw state. **Load demo transcript** injects
fake caption events; the conversation controls show any proposed reply, allow
explicit TTS approval, and test cancellation with **Interrupt**.
It is served by FastAPI and does not require a separate frontend build.

## Browser Extension

The `extension/` directory contains a Manifest V3 extension targeting Google
Meet (`https://meet.google.com/*`). It is the intended live-caption client.

### Install Locally

1. Start the backend from `backend/` as described above.
2. Open `chrome://extensions` or the equivalent extensions page in a
   Chromium-based browser.
3. Enable **Developer mode**, choose **Load unpacked**, and select the
   repository's `extension/` directory.
4. Open Google Meet, enable captions, and click the Meeting Proxy extension
  action to open the custom right-side panel.
5. Use **Minutes of the Meeting** to start a meeting and review captions,
  notes, and saved meetings. Use **User Proxy** to enable the audio bridge
  after Google Meet has selected and enabled the intended microphone.

The panel is injected into the Google Meet page by `content.js`; it is not a
browser-owned Chrome side panel or Opera sidebar. This avoids relying on
browser-specific sidebar APIs. The embedded panel loads `sidepanel.html` from
the extension and communicates with the service worker through runtime
messages. Its **Minutes of the Meeting** tab contains session controls, the
live transcript, note matches/editor, and saved-meeting history. Its **User
Proxy** tab contains transcript questions, an interruptible conversation
panel, and the audio source and TTS controls.

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
  not truncate text when that threshold is exceeded. It also relays messages
  between the embedded extension panel and the page-world audio bridge.
- `background.js` handles `start-meeting`, `end-meeting`, `get-status`,
  `get-default-notes`, `update-notes`, `get-meetings`, `get-meeting`, and
  `caption` and `conversation-interrupt` messages. It starts meetings with
  the panel's note list, owns the WebSocket, forwards captions, relays
  transcript-driven conversation events,
  reconnects with exponential backoff up to 30 seconds, and sends ping
  keepalives. TTS requests go directly from `sidepanel.js` to FastAPI only
  after the user chooses to speak text.
- `sidepanel.js` manages live and saved-meeting views, starts and ends
  meetings, edits or uploads notes, saves note changes, renders the live
  transcript, renders note status/evidence, and resynchronizes from the
  service worker when reopened. It also implements accessible tab navigation,
  and manages transcript-based question suggestions. When **User Proxy** is
  opened, it requests suggestions; while that tab is visible, transcript
  changes are debounced for 1.2 seconds before another request. A manual
  **Refresh** action is available. Selecting one of the radio options copies
  it into the existing speech field. The script sends speech text to
  `/generate-tts`, transfers returned WAV bytes to the page bridge, and
  controls microphone/TTS source mode. Transcript-driven proposals appear in
  the conversation log and require an explicit **Speak response** action;
  **Interrupt** cancels an active observation or speech. During saved-meeting
  review, speaking and suggestions are disabled;
  **Return to live** restores the current session.
- `sidepanel.html` provides the custom panel UI. **Minutes of the Meeting**
  contains connection/session status, meeting controls, transcript, note
  matches, note editor, and saved-meeting history. **User Proxy** contains
  the transcript-driven conversation log and **Speak response** and
  **Interrupt** controls, three transcript-based question radio options, a
  **Refresh** button, the mixer enable control, exclusive microphone/TTS
  switch, text field, and **Speak** button. Question controls require a live
  meeting; speaking additionally requires the mixer and TTS mode.
- `extension/meet_audio_bridge.js` runs in the Google Meet page's MAIN world at
  `document_start`. It hooks `RTCPeerConnection` audio sender operations and
  connects Meet's selected outgoing microphone track and generated TTS to a
  `MediaStreamAudioDestinationNode`. It relays bridge status and commands via
  `window.postMessage`; `content.js` forwards these between the page and panel.
- `manifest.json` declares the service worker, Google Meet content script,
  MAIN-world audio bridge, toolbar action, storage/tabs/alarms permissions,
  local backend hosts, and web-accessible panel resources. It does not depend
  on `side_panel` or `sidebar_action` browser APIs.

The extension uses common Manifest V3 APIs and a page-injected custom panel so
the panel behavior is portable across browsers that support the extension
APIs. It still requires the caption DOM selectors in `content.js` to continue
matching the Google Meet UI.

### User Proxy Audio

1. In Google Meet, select the physical microphone to use and turn the Meet
  microphone on. The extension reuses the audio track Meet is already sending;
  it does not call `getUserMedia()` for a second microphone stream.
2. Open **User Proxy** and select **Enable microphone mix**. The bridge creates
  a page-world `AudioContext`, wraps the active outgoing audio sender with a
  Web Audio destination, and uses the selected Meet microphone as the initial
  source. If no outgoing audio sender exists yet, turn on the Meet microphone
  and retry or wait for Meet to create its sender.
3. Use the switch to choose **Microphone** or **TTS**. These are exclusive
  modes: microphone mode sets the microphone gain to 1 and TTS gain to 0; TTS
  mode sets the microphone gain to 0 and TTS gain to 1. Switching back to
  Microphone stops any active TTS playback.
4. In TTS mode, either select a suggested question or enter text, then select
  **Speak**. A proposed conversational reply can be sent only by selecting
  **Speak response**. In either case the panel sends a JSON POST to
  `/generate-tts`, reads the `audio/wav` response as an `ArrayBuffer`, and
  transfers it through `content.js` to `meet_audio_bridge.js`. The audio is
  played into the current outgoing WebRTC audio track; **Interrupt** stops
  current proxy playback.
5. Disabling the mixer or closing the panel asks the bridge to restore the
  original microphone track and close its audio graph.

The extension page itself does not locally monitor the generated speech; it
is routed to Meet's outgoing sender. Verify audibility with another meeting
participant or a Meet recording, rather than expecting to hear the outgoing
audio locally.

This bridge is an implementation-specific workaround, not a Google Meet
extension API. It wraps WebRTC sender creation and `replaceTrack()` behavior in
the page's MAIN world, so Meet changes, different browser implementations, or
unobserved sender paths can break or interfere with routing. The bridge has
been checked with mocked WebRTC/Web Audio objects, but still requires live
testing in each supported browser and Meet configuration. The extension should
be reloaded and the Meet tab refreshed after changing the manifest or bridge.

## Voice Examples

The scripts in `examples/` are independent Pocket TTS utilities and are not
part of meeting analysis. `recordvoice.py` records
`my_recorded_voice.wav`; `voice_clone.py` provides a Tkinter recording and
cloning UI; `run_clone.py` generates `clone_output.wav` from a recorded voice;
and `basictts.py` demonstrates generation with the built-in `alba` voice and
returns the generated audio tensor. The backend API uses the separate
`backend/app/basictts.py` helper to encode generated samples as WAV bytes.

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
- Pocket TTS uses a lazily loaded model; its first TTS request may take longer
  than later requests. The backend requirements file is empty, so Pocket TTS,
  PyTorch, and SciPy must already be installed in the active environment.
- Meet audio replacement is experimental and depends on WebRTC internals.
  Actual transmission must be verified in a live Meet session; the current
  automated bridge check uses mocks and cannot establish real browser/Meet
  compatibility.