// ----------------------------------------------------------------
// Settings (inlined — Opera's MV3 SW doesn't reliably load `import`)
// ----------------------------------------------------------------

const DEFAULTS = {
  autoStart: false,
  backendUrl: "http://127.0.0.1:8000",
  backendWsUrl: "ws://127.0.0.1:8000",
};

async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

// ----------------------------------------------------------------
// State
// ----------------------------------------------------------------

let ws = null;
let meetingId = null;
let lastState = null;
let keepAliveTimer = null;
let reconnectTimer = null;
let reconnectAttempt = 0;

// ----------------------------------------------------------------
// Sidebar (Opera)
// ----------------------------------------------------------------

if (chrome.action?.onClicked && chrome.sidebarAction?.open) {
  chrome.action.onClicked.addListener(async (tab) => {
    try {
      await chrome.sidebarAction.open();
    } catch (e) {
      console.error("[bg] sidebarAction.open failed:", e);
    }
  });
}

// ----------------------------------------------------------------
// Keepalive #1 — port from the sidebar
//
// As long as the sidebar holds an open port to the SW, Opera keeps
// the SW resident. No permission required; released when the
// sidebar closes.
// ----------------------------------------------------------------

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "keepalive") return;
  console.log("[bg] keepalive port connected");
  port.onDisconnect.addListener(() => {
    console.log("[bg] keepalive port disconnected");
  });
});

// ----------------------------------------------------------------
// Keepalive #2 — chrome.alarms (fallback if permission present)
// ----------------------------------------------------------------

if (chrome.alarms?.create) {
  chrome.alarms.create("keepalive", { periodInMinutes: 20 / 60 });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "keepalive") {
      console.log("[bg] keepalive tick");
    }
  });
  console.log("[bg] alarms keepalive armed");
} else {
  console.warn("[bg] chrome.alarms unavailable, relying on port keepalive");
}

// ----------------------------------------------------------------
// Messaging helpers
// ----------------------------------------------------------------

function broadcastToSidePanel(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {
    // No receiver — sidebar closed or not open yet.
  });
}

// ----------------------------------------------------------------
// WebSocket lifecycle
// ----------------------------------------------------------------

async function openSocket(id) {
  const { backendWsUrl } = await getSettings();
  const url = `${backendWsUrl}/meeting/${id}/ws`;

  console.log("[bg] opening socket", url);
  ws = new WebSocket(url);

  ws.onopen = () => {
    console.log("[bg] socket open");
    reconnectAttempt = 0;
    startKeepAlive();
    broadcastToSidePanel({ type: "ws-status", open: true });
  };

  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (e) {
      console.error("[bg] bad json from backend", ev.data);
      return;
    }

    if (msg.type === "pong") return;

    if (msg.error) {
      console.error("[bg] backend error", msg.error);
      broadcastToSidePanel({ type: "error", detail: msg.error });
      return;
    }

    lastState = msg;
    broadcastToSidePanel({ type: "state", state: msg });
  };

  ws.onclose = () => {
    console.log("[bg] socket closed");
    stopKeepAlive();
    broadcastToSidePanel({ type: "ws-status", open: false });
    scheduleReconnect();
  };

  ws.onerror = (e) => {
    console.error("[bg] socket error", e);
  };
}

function scheduleReconnect() {
  if (!meetingId) return;
  if (reconnectTimer) return;

  const delay = Math.min(30_000, 1000 * 2 ** reconnectAttempt);
  reconnectAttempt += 1;

  console.log(`[bg] reconnecting in ${delay}ms (attempt ${reconnectAttempt})`);

  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    if (!meetingId) return;
    await openSocket(meetingId);
  }, delay);
}

function startKeepAlive() {
  stopKeepAlive();
  keepAliveTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 20_000);
}

function stopKeepAlive() {
  if (keepAliveTimer) {
    clearInterval(keepAliveTimer);
    keepAliveTimer = null;
  }
}

function closeSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  stopKeepAlive();
  if (ws) {
    try { ws.close(); } catch (_) {}
    ws = null;
  }
}

// ----------------------------------------------------------------
// Message API
// ----------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handleMessage(msg, sender)
    .then((res) => sendResponse(res ?? { ok: true }))
    .catch((err) => {
      console.error("[bg] handler error", err);
      sendResponse({ ok: false, error: String(err) });
    });
  return true;
});

async function handleMessage(msg, sender) {
  switch (msg.type) {
    case "start-meeting": {
      const { backendUrl } = await getSettings();
      const res = await fetch(`${backendUrl}/meeting/start`, { method: "POST" });
      if (!res.ok) throw new Error(`start failed: ${res.status}`);
      const state = await res.json();
      meetingId = state.meeting_id;
      lastState = state;
      await openSocket(meetingId);
      broadcastToSidePanel({ type: "state", state });
      return { ok: true, meetingId, state };
    }

    case "end-meeting": {
      if (!meetingId) return { ok: true };
      const { backendUrl } = await getSettings();
      await fetch(`${backendUrl}/meeting/${meetingId}/end`, { method: "POST" });
      closeSocket();
      meetingId = null;
      lastState = null;
      broadcastToSidePanel({ type: "ended" });
      return { ok: true };
    }

    case "get-status": {
      return {
        ok: true,
        meetingId,
        wsOpen: !!(ws && ws.readyState === WebSocket.OPEN),
        state: lastState,
      };
    }

    case "caption": {
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        console.warn("[bg] caption received but socket not open");
        return { ok: false, error: "socket not open" };
      }
      ws.send(
        JSON.stringify({
          type: "event",
          timestamp: msg.timestamp,
          speaker: msg.speaker,
          text: msg.text,
        })
      );
      return { ok: true };
    }

    default:
      console.warn("[bg] unknown message", msg);
      return { ok: false, error: `unknown message type: ${msg.type}` };
  }
}