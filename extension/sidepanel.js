const $ = (id) => document.getElementById(id);

const notesInput = $("notesInput");
const notesFieldset = $("notesFieldset");
const historySelect = $("history");
const transcriptList = $("transcript");
const notesList = $("notes");
const startButton = $("start");
const endButton = $("end");
const returnToLiveButton = $("returnToLive");
const hasExtensionRuntime = typeof chrome !== "undefined" && Boolean(chrome.runtime);

let currentMeetingId = null;
let isMeetingActive = false;
let meetingHasEnded = false;
let isStartingMeeting = false;
let isReviewingHistory = false;
let latestLiveState = null;
let notesDirty = false;
let savedMeetings = [];
let suggestedQuestions = [];
let selectedQuestionIndex = 0;
let questionRequestSequence = 0;
let lastQuestionTranscriptCount = 0;
let questionRefreshTimer = null;
let questionsLoading = false;

async function getBackendUrl() {
  const { backendUrl = "http://127.0.0.1:8000" } = await chrome.storage.sync.get({
    backendUrl: "http://127.0.0.1:8000",
  });
  return backendUrl.replace(/\/$/, "");
}

class AudioMixer {
  constructor() {
    this.enabled = false;
    this.mode = "microphone";
    this.pendingRequests = new Map();
    this.requestSequence = 0;
    window.addEventListener("message", (event) => {
      if (event.source !== window.parent || event.origin !== "https://meet.google.com") return;
      if (event.data?.source !== "meeting-proxy-audio-bridge" || event.data.type !== "state") return;

      this.enabled = event.data.enabled === true;
      this.mode = event.data.mode === "tts" ? "tts" : "microphone";
      updateAudioMixerUI();
      if (event.data.requestId) {
        const pending = this.pendingRequests.get(event.data.requestId);
        if (pending) {
          clearTimeout(pending.timeout);
          this.pendingRequests.delete(event.data.requestId);
          if (event.data.error) pending.reject(new Error(event.data.error));
          else pending.resolve(event.data);
        }
      }
      if (event.data.error) {
        setStatus(`Meet audio bridge error. ${event.data.error}`, "error");
      } else if (this.enabled && event.data.senderCount === 0) {
        setStatus("Mixer enabled. Turn on the Meet microphone to route audio.");
      } else if (this.enabled) {
        setStatus(this.mode === "tts" ? "Generated speech selected for Meet." : "Microphone selected for Meet.", "success");
      }
    });
  }

  get isActive() {
    return this.enabled;
  }

  request(type, payload = {}, transfer = []) {
    const requestId = `voice-${Date.now()}-${++this.requestSequence}`;
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pendingRequests.delete(requestId);
        reject(new Error("Meet did not respond to the audio bridge. Reload the Meet tab and reopen the panel."));
      }, 5000);
      this.pendingRequests.set(requestId, { resolve, reject, timeout });
      window.parent.postMessage({
        source: "meeting-proxy-extension",
        type,
        requestId,
        ...payload,
      }, "https://meet.google.com", transfer);
    });
  }

  async start() {
    return this.request("enable");
  }

  async stop() {
    if (!this.enabled) return;
    return this.request("disable");
  }

  async setMode(mode) {
    return this.request("mode", { mode });
  }

  async speak(text) {
    if (!this.enabled) throw new Error("Enable the microphone mix first.");
    if (this.mode !== "tts") throw new Error("Switch the audio source to TTS before speaking.");

    const response = await fetch(`${await getBackendUrl()}/generate-tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `TTS request failed (${response.status}).`);
    }

    const audio = await response.arrayBuffer();
    return this.request("speak", { audio }, [audio]);
  }
}

const audioMixer = new AudioMixer();

function updateAudioMixerUI() {
  const active = audioMixer.isActive;
  const ttsSelected = active && audioMixer.mode === "tts";
  $("toggleAudioMixer").textContent = active ? "Disable microphone mix" : "Enable microphone mix";
  $("toggleAudioMixer").setAttribute("aria-pressed", String(active));
  $("sourceMode").checked = audioMixer.mode === "tts";
  $("sourceMode").setAttribute("aria-checked", String(audioMixer.mode === "tts"));
  $("sourceMode").disabled = !active;
  $("sourceSwitch").dataset.mode = audioMixer.mode;
  $("speechText").disabled = !ttsSelected;
  $("speak").disabled = !ttsSelected;
  $("refreshQuestions").disabled = questionsLoading || !isMeetingActive || !currentMeetingId || isReviewingHistory || !latestLiveState?.transcript?.length;
  $("questionsList").disabled = questionsLoading || !suggestedQuestions.length || !isMeetingActive || isReviewingHistory;
}

const viewTabs = Array.from(document.querySelectorAll('[role="tab"]'));

function selectViewTab(tab, focus = false) {
  for (const viewTab of viewTabs) {
    const selected = viewTab === tab;
    viewTab.setAttribute("aria-selected", String(selected));
    viewTab.tabIndex = selected ? 0 : -1;
    $(viewTab.getAttribute("aria-controls")).hidden = !selected;
  }
  if (focus) tab.focus();
  if (tab.id === "userProxyTab" && latestLiveState?.transcript?.length) {
    scheduleQuestionSuggestions();
  }
}

function handleViewTabKeydown(event) {
  const currentIndex = viewTabs.indexOf(event.currentTarget);
  let nextIndex = currentIndex;

  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % viewTabs.length;
  else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + viewTabs.length) % viewTabs.length;
  else if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = viewTabs.length - 1;
  else return;

  event.preventDefault();
  selectViewTab(viewTabs[nextIndex], true);
}

async function toggleAudioMixer() {
  const button = $("toggleAudioMixer");
  button.disabled = true;
  try {
    if (audioMixer.isActive) {
      await audioMixer.stop();
      setStatus("Microphone mix disabled.");
    } else {
      setStatus("Connecting to the Meet microphone sender…");
      const state = await audioMixer.start();
      audioMixer.mode = state.mode === "tts" ? "tts" : "microphone";
      if (state.senderCount > 0) setStatus("Microphone selected for Meet.", "success");
      else setStatus("Mixer enabled. Turn on the Meet microphone to route audio.");
    }
  } catch (error) {
    setStatus(`Could not start the microphone mix. ${error.message}`, "error");
  } finally {
    button.disabled = false;
    updateAudioMixerUI();
  }
}

async function changeAudioMode(event) {
  const toggle = event.currentTarget;
  const mode = toggle.checked ? "tts" : "microphone";
  toggle.disabled = true;
  try {
    const state = await audioMixer.setMode(mode);
    audioMixer.mode = state.mode;
    setStatus(mode === "tts" ? "Generated speech selected for Meet." : "Microphone selected for Meet.", "success");
  } catch (error) {
    toggle.checked = audioMixer.mode === "tts";
    setStatus(`Could not switch the audio source. ${error.message}`, "error");
  } finally {
    updateAudioMixerUI();
  }
}

async function speakText() {
  const text = $("speechText").value.trim();
  if (!text) {
    setStatus("Enter text to speak.", "error");
    return;
  }

  const button = $("speak");
  button.disabled = true;
  button.textContent = "Generating…";
  setStatus("Generating speech…");
  try {
    await audioMixer.speak(text);
    setStatus("TTS audio sent to Meet.", "success");
  } catch (error) {
    setStatus(`Could not generate speech. ${error.message}`, "error");
  } finally {
    button.textContent = "Speak";
    button.disabled = !audioMixer.isActive;
  }
}

function setStatus(message = "", kind = "info") {
  const status = $("status");
  status.textContent = message;
  status.dataset.kind = kind;
}

function sendMessage(message) {
  if (!hasExtensionRuntime) {
    return Promise.reject(new Error("Open this panel from the Meeting Proxy extension on Google Meet."));
  }
  return chrome.runtime.sendMessage(message);
}

function setConnection(open) {
  const connection = $("ws");
  connection.dataset.open = String(Boolean(open));
  connection.querySelector(".connection-label").textContent = open
    ? "Connected"
    : "Not connected";
}

function updateSessionUI() {
  const session = $("session");
  const title = $("sessionTitle");
  const context = $("sessionContext");
  const description = $("sessionDescription");
  const badge = $("sessionBadgeText");

  if (isReviewingHistory) {
    session.dataset.view = "reviewing";
    context.textContent = "Saved meeting";
    title.textContent = "Meeting review";
    description.textContent = "Viewing saved captions and note matches.";
    badge.textContent = "Saved";
    startButton.hidden = true;
    endButton.hidden = true;
    returnToLiveButton.hidden = false;
    notesFieldset.disabled = true;
    return;
  }

  returnToLiveButton.hidden = true;
  notesFieldset.disabled = false;

  if (isStartingMeeting) {
    session.dataset.view = "starting";
    context.textContent = "Current meeting";
    title.textContent = "Starting meeting…";
    description.textContent = "Connecting to the local service.";
    badge.textContent = "Starting";
  } else if (isMeetingActive) {
    session.dataset.view = "active";
    context.textContent = "Current meeting";
    title.textContent = "Session active";
    description.textContent = "Enable Google Meet captions to see updates.";
    badge.textContent = "Active";
  } else if (meetingHasEnded) {
    session.dataset.view = "ended";
    context.textContent = "Current meeting";
    title.textContent = "Meeting ended";
    description.textContent = "Review the transcript and note matches above.";
    badge.textContent = "Ended";
  } else {
    session.dataset.view = "idle";
    context.textContent = "Current meeting";
    title.textContent = "Ready for your meeting";
    description.textContent = "Start a meeting to see captions and note matches here.";
    badge.textContent = "Idle";
  }

  startButton.hidden = isMeetingActive;
  startButton.disabled = isStartingMeeting;
  startButton.textContent = isStartingMeeting ? "Starting…" : "Start meeting";
  endButton.hidden = !isMeetingActive;
  endButton.disabled = !isMeetingActive;
}

function statusLabel(status) {
  switch (status) {
    case "partial":
      return "Partly discussed";
    case "completed":
      return "Discussed";
    case "open":
    default:
      return "Not yet discussed";
  }
}

function formatConfidence(value) {
  if (value === null || value === undefined || value === "") return null;
  const confidence = Number(value);
  if (!Number.isFinite(confidence)) return null;
  return `${Math.round(Math.max(0, Math.min(1, confidence)) * 100)}%`;
}

function createTextElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function appendEmptyState(list, message) {
  const item = createTextElement("li", "empty-state", message);
  list.replaceChildren(item);
}

function renderTranscript(transcript) {
  const entries = Array.isArray(transcript) ? transcript : [];
  const hadTranscriptEntries = transcriptList.querySelector(".transcript-entry") !== null;
  const shouldStickToBottom =
    !hadTranscriptEntries || transcriptList.scrollHeight - transcriptList.scrollTop - transcriptList.clientHeight < 36;

  $("transcriptCount").textContent = `${entries.length} ${entries.length === 1 ? "entry" : "entries"}`;

  if (!entries.length) {
    appendEmptyState(
      transcriptList,
      isMeetingActive
        ? "Waiting for captions. Make sure captions are enabled in Google Meet."
        : "No captions received yet. Start a meeting and enable captions in Google Meet."
    );
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const event of entries) {
    const item = document.createElement("li");
    item.className = "transcript-entry";

    const meta = document.createElement("div");
    meta.className = "transcript-meta";
    meta.appendChild(createTextElement("span", "transcript-speaker", event.speaker || "Unknown"));

    if (event.timestamp) {
      const time = createTextElement("time", "transcript-time", event.timestamp);
      time.dateTime = event.timestamp;
      meta.appendChild(time);
    }

    const text = createTextElement("p", "transcript-copy", event.text || "");
    item.append(meta, text);
    fragment.appendChild(item);
  }

  transcriptList.replaceChildren(fragment);
  if (shouldStickToBottom) transcriptList.scrollTop = transcriptList.scrollHeight;
}

function renderEvidence(note, status, confidenceLabel) {
  if (!note.evidence) {
    if (status !== "open") return null;
    return createTextElement("p", "empty-state note-empty-evidence", "No supporting excerpt yet.");
  }

  const details = document.createElement("details");
  details.className = "note-evidence";
  details.open = status === "partial";

  const summary = document.createElement("summary");
  summary.appendChild(createTextElement("span", "", "Evidence in transcript"));
  details.appendChild(summary);

  const evidence = createTextElement("p", "evidence-copy", note.evidence);
  details.appendChild(evidence);

  const meta = document.createElement("p");
  meta.className = "evidence-meta";
  if (note.timestamp) {
    const time = createTextElement("time", "", note.timestamp);
    time.dateTime = note.timestamp;
    meta.appendChild(time);
  }
  if (confidenceLabel) {
    meta.appendChild(createTextElement("span", "", `Confidence ${confidenceLabel}`));
  }
  if (meta.childElementCount) details.appendChild(meta);
  return details;
}

function renderNotes(notes) {
  const noteItems = Array.isArray(notes) ? notes : [];
  $("notesCount").textContent = `${noteItems.length} ${noteItems.length === 1 ? "note" : "notes"}`;

  if (!noteItems.length) {
    appendEmptyState(notesList, "No meeting notes yet. Add notes below before starting.");
    return;
  }

  const statusRank = { open: 0, partial: 1, completed: 2 };
  const orderedNotes = noteItems
    .map((note, index) => ({ note, index }))
    .sort((a, b) => (statusRank[a.note.status] ?? 0) - (statusRank[b.note.status] ?? 0) || a.index - b.index);

  const fragment = document.createDocumentFragment();
  for (const { note } of orderedNotes) {
    const status = ["open", "partial", "completed"].includes(note.status) ? note.status : "open";
    const item = document.createElement("li");
    item.className = "note-item";
    item.dataset.status = status;

    const statusLine = document.createElement("div");
    statusLine.className = "note-status-line";
    const dot = document.createElement("span");
    dot.className = "status-dot";
    dot.setAttribute("aria-hidden", "true");
    statusLine.append(dot, createTextElement("span", "", statusLabel(status)));

    const main = document.createElement("div");
    main.className = "note-main";
    main.appendChild(createTextElement("h3", "note-text", note.text || "Untitled note"));

    const confidenceLabel = formatConfidence(note.confidence);
    if (confidenceLabel && status !== "partial") {
      main.appendChild(createTextElement("span", "note-confidence", confidenceLabel));
    }

    item.append(statusLine, main);
    const evidence = renderEvidence(note, status, confidenceLabel);
    if (evidence) item.appendChild(evidence);
    fragment.appendChild(item);
  }

  notesList.replaceChildren(fragment);
}

function updateNotesEditor(state) {
  if (notesDirty || isReviewingHistory || !Array.isArray(state?.notes)) return;
  notesInput.value = state.notes.map((note) => note.text).join("\n");
}

function renderMeetingState(state) {
  if (!state) return;
  $("meta").textContent = `Meeting ID ${state.meeting_id || "unavailable"}`;
  renderNotes(state.notes);
  renderTranscript(state.transcript);
  updateNotesEditor(state);
}

function renderSuggestedQuestions(message = "") {
  const list = $("questionsList");
  const legend = list.querySelector("legend");
  list.replaceChildren(legend);

  if (!suggestedQuestions.length) {
    list.appendChild(createTextElement("p", "empty-state", message || "Question suggestions will appear here."));
    return;
  }

  suggestedQuestions.forEach((question, index) => {
    const label = document.createElement("label");
    label.className = "question-option";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "suggestedQuestion";
    radio.value = String(index);
    radio.checked = index === selectedQuestionIndex;
    const text = createTextElement("span", "", question);
    label.append(radio, text);
    list.appendChild(label);
  });
}

async function loadSuggestedQuestions() {
  if (!currentMeetingId || !isMeetingActive || isReviewingHistory) return;

  const meetingId = currentMeetingId;
  const requestSequence = ++questionRequestSequence;
  questionsLoading = true;
  renderSuggestedQuestions("Generating questions…");
  updateAudioMixerUI();

  try {
    const response = await fetch(`${await getBackendUrl()}/meeting/${encodeURIComponent(meetingId)}/questions`, {
      method: "POST",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Question request failed (${response.status}).`);
    if (requestSequence !== questionRequestSequence || meetingId !== currentMeetingId) return;

    suggestedQuestions = Array.isArray(data.questions) ? data.questions : [];
    selectedQuestionIndex = 0;
    renderSuggestedQuestions();
    if (suggestedQuestions.length) {
      $("speechText").value = suggestedQuestions[0];
      setStatus("Three questions are ready to ask.", "success");
    } else {
      renderSuggestedQuestions("No question suggestions were returned. Refresh to try again.");
    }
  } catch (error) {
    if (requestSequence !== questionRequestSequence) return;
    suggestedQuestions = [];
    renderSuggestedQuestions(`Could not load suggestions. ${error.message}`);
    setStatus(`Could not generate questions. ${error.message}`, "error");
  } finally {
    if (requestSequence === questionRequestSequence) {
      questionsLoading = false;
      updateAudioMixerUI();
    }
  }
}

function scheduleQuestionSuggestions(force = false) {
  const transcriptCount = latestLiveState?.transcript?.length || 0;
  if (!currentMeetingId || !isMeetingActive || isReviewingHistory || !transcriptCount) return;
  if (!force && transcriptCount <= lastQuestionTranscriptCount) return;

  lastQuestionTranscriptCount = transcriptCount;
  clearTimeout(questionRefreshTimer);
  questionRefreshTimer = setTimeout(loadSuggestedQuestions, 1200);
}

function applyLiveState(state) {
  if (!state) return;
  if (state.meeting_id && state.meeting_id !== latestLiveState?.meeting_id) {
    clearTimeout(questionRefreshTimer);
    questionRequestSequence += 1;
    suggestedQuestions = [];
    selectedQuestionIndex = 0;
    lastQuestionTranscriptCount = 0;
    questionsLoading = false;
    renderSuggestedQuestions("Start a meeting to get question suggestions.");
  }
  latestLiveState = state;
  if (state.meeting_id) currentMeetingId = state.meeting_id;
  isMeetingActive = state.active === true || (state.active === undefined && Boolean(currentMeetingId));
  meetingHasEnded = state.active === false;
  if (!isReviewingHistory) {
    renderMeetingState(state);
    updateSessionUI();
  }
  updateAudioMixerUI();
  if ($("userProxyTab").getAttribute("aria-selected") === "true") {
    scheduleQuestionSuggestions();
  }
}

function parseNotes() {
  return notesInput.value
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*[-*]\s+/, "").trim())
    .filter(Boolean);
}

function formatDate(value) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

async function loadHistory() {
  const response = await sendMessage({ type: "get-meetings" });
  if (!response?.ok) throw new Error(response?.error || "Saved meetings are unavailable.");

  savedMeetings = Array.isArray(response.meetings) ? response.meetings : [];
  const placeholder = new Option("Select a meeting to review", "");
  historySelect.replaceChildren(placeholder);

  for (const meeting of savedMeetings) {
    const count = Number.isFinite(meeting.note_count) ? meeting.note_count : 0;
    const label = `${formatDate(meeting.started_at)} · ${count} ${count === 1 ? "note" : "notes"}`;
    historySelect.appendChild(new Option(label, meeting.meeting_id));
  }
}

function setEditorBusy(isBusy) {
  $("updateNotes").disabled = isBusy || isReviewingHistory;
  $("uploadNotes").disabled = isBusy || isReviewingHistory;
}

async function saveNotes() {
  const saveButton = $("updateNotes");
  const previousLabel = saveButton.textContent;
  setEditorBusy(true);
  saveButton.textContent = "Saving…";
  setStatus("Saving meeting notes…");

  try {
    const response = await sendMessage({ type: "update-notes", notes: parseNotes() });
    if (!response?.ok) throw new Error(response?.error || "Notes could not be saved.");
    notesDirty = false;
    if (response.state) applyLiveState(response.state);
    renderNotes(response.state?.notes || parseNotes().map((text, id) => ({ id, text, status: "open" })));
    setStatus("Meeting notes saved.", "success");
    try {
      await loadHistory();
    } catch (error) {
      setStatus(`Notes saved, but meeting history could not be refreshed: ${error.message}`, "error");
    }
  } catch (error) {
    setStatus(`Could not save notes. ${error.message}`, "error");
  } finally {
    saveButton.textContent = previousLabel;
    setEditorBusy(false);
  }
}

async function startMeeting() {
  isStartingMeeting = true;
  updateSessionUI();
  setStatus("Starting meeting…");

  try {
    const response = await sendMessage({ type: "start-meeting", notes: parseNotes() });
    if (!response?.ok) throw new Error(response?.error || "The meeting could not be started.");
    currentMeetingId = response.meetingId || response.state?.meeting_id || null;
    meetingHasEnded = false;
    isMeetingActive = true;
    if (response.state) applyLiveState(response.state);
    setStatus("Meeting started.", "success");
    try {
      await loadHistory();
    } catch {
      // Starting the meeting should not be reported as failed if only history failed.
    }
  } catch (error) {
    setStatus(`Could not start meeting. ${error.message}`, "error");
  } finally {
    isStartingMeeting = false;
    updateSessionUI();
  }
}

async function endMeeting() {
  endButton.disabled = true;
  endButton.textContent = "Ending…";
  setStatus("Ending meeting…");

  try {
    const response = await sendMessage({ type: "end-meeting" });
    if (response?.ok === false) throw new Error(response.error || "The meeting could not be ended.");
    await audioMixer.stop();
    updateAudioMixerUI();
    currentMeetingId = null;
    isMeetingActive = false;
    meetingHasEnded = true;
    if (latestLiveState) {
      latestLiveState = { ...latestLiveState, active: false };
      if (!isReviewingHistory) renderMeetingState(latestLiveState);
    }
    setStatus("Meeting ended. Its transcript and note matches are saved.", "success");
    try {
      await loadHistory();
    } catch {
      // Preserve the end-meeting result if refreshing history alone fails.
    }
  } catch (error) {
    setStatus(`Could not end meeting. ${error.message}`, "error");
  } finally {
    endButton.textContent = "End meeting";
    updateSessionUI();
  }
}

async function openSavedMeeting(meetingId) {
  if (!meetingId) {
    isReviewingHistory = false;
    if (latestLiveState) {
      renderMeetingState(latestLiveState);
    } else {
      renderNotes([]);
      renderTranscript([]);
    }
    updateSessionUI();
    updateAudioMixerUI();
    setStatus(isMeetingActive ? "Returned to the live meeting." : "Returned to the current meeting view.", "success");
    return;
  }

  historySelect.disabled = true;
  setStatus("Loading saved meeting…");
  try {
    const response = await sendMessage({ type: "get-meeting", meetingId });
    if (!response?.ok) throw new Error(response?.error || "The saved meeting could not be loaded.");
    const archiveState = response.meeting?.state;
    if (!archiveState) throw new Error("Saved meeting data was empty.");
    isReviewingHistory = true;
    renderMeetingState(archiveState);
    updateSessionUI();
    updateAudioMixerUI();
    const startedAt = formatDate(response.meeting.started_at);
    setStatus(`Reviewing saved meeting from ${startedAt}.`);
  } catch (error) {
    historySelect.value = "";
    setStatus(`Could not load saved meeting. ${error.message}`, "error");
  } finally {
    historySelect.disabled = false;
  }
}

function restoreLiveView() {
  isReviewingHistory = false;
  historySelect.value = "";
  if (latestLiveState) {
    renderMeetingState(latestLiveState);
  } else {
    renderNotes([]);
    renderTranscript([]);
  }
  updateSessionUI();
  updateAudioMixerUI();
  setStatus(isMeetingActive ? "Returned to the live meeting." : "Returned to the current meeting view.", "success");
}

function onRuntimeMessage(message) {
  if (message.type === "state") {
    applyLiveState(message.state);
  } else if (message.type === "ws-status") {
    setConnection(message.open);
  } else if (message.type === "error") {
    setStatus(message.detail || "The local service reported an error.", "error");
  } else if (message.type === "ended") {
    currentMeetingId = null;
    isMeetingActive = false;
    meetingHasEnded = true;
    if (latestLiveState) {
      latestLiveState = { ...latestLiveState, active: false };
      if (!isReviewingHistory) renderMeetingState(latestLiveState);
    }
    setConnection(false);
    updateSessionUI();
    updateAudioMixerUI();
    setStatus("Meeting ended.", "success");
  }
}

notesInput.addEventListener("input", () => {
  notesDirty = true;
});

$("updateNotes").addEventListener("click", saveNotes);
$("uploadNotes").addEventListener("click", () => $("notesFile").click());

$("notesFile").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const contents = await file.text();
    notesInput.value = contents
      .split(/\r?\n/)
      .map((line) => line.replace(/^\s*[-*]\s+/, "").trim())
      .filter((line) => line && !line.startsWith("#"))
      .join("\n");
    notesDirty = true;
    setStatus(`Loaded ${file.name}. Save notes to apply them.`);
  } catch (error) {
    setStatus(`Could not read ${file.name}. ${error.message}`, "error");
  } finally {
    event.target.value = "";
  }
});

$("start").addEventListener("click", startMeeting);
$("end").addEventListener("click", endMeeting);
$("returnToLive").addEventListener("click", restoreLiveView);
historySelect.addEventListener("change", (event) => openSavedMeeting(event.target.value));
for (const tab of viewTabs) {
  tab.addEventListener("click", () => selectViewTab(tab));
  tab.addEventListener("keydown", handleViewTabKeydown);
}
$("toggleAudioMixer").addEventListener("click", toggleAudioMixer);
$("speak").addEventListener("click", speakText);
$("sourceMode").addEventListener("change", changeAudioMode);
$("refreshQuestions").addEventListener("click", () => scheduleQuestionSuggestions(true));
$("questionsList").addEventListener("change", (event) => {
  const radio = event.target.closest('input[name="suggestedQuestion"]');
  if (!radio) return;
  selectedQuestionIndex = Number(radio.value);
  $("speechText").value = suggestedQuestions[selectedQuestionIndex] || "";
});
window.addEventListener("beforeunload", () => {
  window.parent.postMessage({ source: "meeting-proxy-extension", type: "disable" }, "https://meet.google.com");
});

updateAudioMixerUI();

async function initialize() {
  if (!hasExtensionRuntime) {
    renderNotes([]);
    renderTranscript([]);
    updateSessionUI();
    setStatus("Open this panel from the Meeting Proxy extension on Google Meet.");
    return;
  }

  try {
    const port = chrome.runtime.connect({ name: "keepalive" });
    port.onDisconnect.addListener(() => {
      console.warn("[sidepanel] keepalive port disconnected");
    });
  } catch (error) {
    console.warn("[sidepanel] keepalive connection failed:", error);
  }

  chrome.runtime.onMessage.addListener(onRuntimeMessage);

  try {
    const status = await sendMessage({ type: "get-status" });
    if (status?.meetingId) currentMeetingId = status.meetingId;
    if (status?.state) {
      applyLiveState(status.state);
    } else {
      isMeetingActive = Boolean(status?.meetingId);
      setConnection(Boolean(status?.wsOpen));
      updateSessionUI();
    }
    if (status?.state) setConnection(Boolean(status.wsOpen));
  } catch (error) {
    setStatus("Extension service is not responding. Reopen the panel and try again.", "error");
  }

  if (!latestLiveState) {
    try {
      const response = await sendMessage({ type: "get-default-notes" });
      if (response?.ok && Array.isArray(response.notes) && !notesDirty) {
        notesInput.value = response.notes.join("\n");
        if (!latestLiveState) {
          renderNotes(response.notes.map((text, id) => ({ id, text, status: "open" })));
        }
      }
    } catch {
      // Default notes are optional; the editor remains available if the backend is offline.
    }
  }

  try {
    await loadHistory();
  } catch (error) {
    if (!latestLiveState) setStatus(`Saved meetings unavailable. ${error.message}`, "error");
  }
}

initialize();
