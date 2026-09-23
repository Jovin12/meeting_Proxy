const $ = (id) => document.getElementById(id);

// ----------------------------------------------------------------
// Keepalive port — holds the SW resident while the sidebar is open
// ----------------------------------------------------------------

const port = chrome.runtime.connect({ name: "keepalive" });
port.onDisconnect.addListener(() => {
  console.warn("[sidepanel] keepalive port disconnected");
});

// ----------------------------------------------------------------
// UI helpers
// ----------------------------------------------------------------

const setWs = (open) => {
  const el = $("ws");
  el.dataset.open = String(open);
  el.textContent = open ? "connected" : "not connected";
};

const setStatus = (s) => { $("status").textContent = s; };

function renderNotes(notes) {
  const ul = $("notes");
  ul.innerHTML = "";
  for (const n of notes) {
    const li = document.createElement("li");
    li.dataset.status = n.status;

    const text = document.createElement("div");
    text.className = "text";
    text.textContent = n.text;

    const meta = document.createElement("div");
    meta.className = "n-meta";
    meta.innerHTML = `
      <span>${n.status}</span>
      <span>${n.timestamp ?? "—"}</span>
      <span>${n.confidence ?? "—"}</span>
    `;

    const evidence = document.createElement("div");
    evidence.className = "evidence";
    evidence.textContent = n.evidence ?? "";

    li.appendChild(text);
    li.appendChild(meta);
    if (n.evidence) li.appendChild(evidence);
    ul.appendChild(li);
  }
}

function applyState(state) {
  if (!state) return;
  $("meta").textContent = `meeting_id: ${state.meeting_id ?? "—"}`;
  if (Array.isArray(state.notes)) renderNotes(state.notes);
}

// ----------------------------------------------------------------
// Broadcasts from the SW
// ----------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "state") {
    applyState(msg.state);
    setStatus("state updated");
  } else if (msg.type === "ws-status") {
    setWs(msg.open);
  } else if (msg.type === "error") {
    setStatus(`error: ${msg.detail}`);
  } else if (msg.type === "ended") {
    setWs(false);
    $("start").disabled = false;
    $("end").disabled = true;
    setStatus("ended");
  }
});

// ----------------------------------------------------------------
// Buttons
// ----------------------------------------------------------------

$("start").addEventListener("click", async () => {
  setStatus("starting…");
  const res = await chrome.runtime.sendMessage({ type: "start-meeting" });
  if (!res?.ok) {
    setStatus(`start failed: ${res?.error ?? "unknown"}`);
    return;
  }
  if (res.state) applyState(res.state);
  $("start").disabled = true;
  $("end").disabled = false;
  setStatus("started");
});

$("end").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "end-meeting" });
  $("start").disabled = false;
  $("end").disabled = true;
});

// ----------------------------------------------------------------
// Initial resync — mirror the SW's current state on mount
// ----------------------------------------------------------------

(async () => {
  try {
    const status = await chrome.runtime.sendMessage({ type: "get-status" });
    if (status?.meetingId) {
      $("start").disabled = true;
      $("end").disabled = false;
      setStatus("already in meeting");
    }
    if (status?.state) applyState(status.state);
    setWs(!!status?.wsOpen);
  } catch (e) {
    console.warn("[sidepanel] initial get-status failed:", e);
    setStatus("SW not responding");
  }
})();