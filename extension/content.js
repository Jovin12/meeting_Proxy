// ----------------------------------------------------------------
// Google Meet caption scraper
//
// Meet's caption DOM works like this:
//   - One "live" row at a time, class .nMcdL.bj4p3b
//   - Its text grows word-by-word as you speak
//   - When you pause long enough, Meet finalizes it and
//     eventually replaces the row for the next utterance
//
// Strategy: watch the whole document for characterData changes,
// debounce per-row, and emit when a row's text has been stable
// for STABLE_MS.
// ----------------------------------------------------------------

const CONTAINER_CANDIDATES = [
  ".a4cQT",
  '[jsname="dsyhDe"]',
];

const ROW_SELECTOR = ".nMcdL.bj4p3b";
const TEXT_SELECTOR = ".ygicle.VbkSUe";
const NAME_SELECTOR = ".NWpY1d";

const STABLE_MS = 800;         // how long text must be unchanged before we emit
const MAX_EVENT_CHARS = 500;   // safety cap
const PANEL_HOST_ID = "meeting-proxy-panel-host";
let panelFrame = null;
let draftOverlay = null;
let draftOverlayText = null;
let draftOverlayStatus = null;
let approveDraftButton = null;
let pendingDraftRequestId = null;
let pendingDraftAction = false;

// ----------------------------------------------------------------
// Browser-independent panel
// ----------------------------------------------------------------

function togglePanel() {
  const existing = document.getElementById(PANEL_HOST_ID);
  if (existing) {
    window.postMessage({ source: "meeting-proxy-content", type: "disable" }, location.origin);
    existing.remove();
    panelFrame = null;
    draftOverlay = null;
    pendingDraftRequestId = null;
    pendingDraftAction = false;
    return;
  }

  const host = document.createElement("div");
  host.id = PANEL_HOST_ID;
  const shadow = host.attachShadow({ mode: "closed" });

  const frame = document.createElement("iframe");
  frame.title = "Meeting Proxy";
  frame.allow = "microphone";
  frame.src = chrome.runtime.getURL("sidepanel.html");
  panelFrame = frame;

  const close = document.createElement("button");
  close.type = "button";
  close.title = "Close Meeting Proxy";
  close.textContent = "×";
  close.addEventListener("click", () => {
    window.postMessage({ source: "meeting-proxy-content", type: "disable" }, location.origin);
    host.remove();
    panelFrame = null;
    draftOverlay = null;
    pendingDraftRequestId = null;
    pendingDraftAction = false;
  });

  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    .panel {
      position: fixed;
      z-index: 2147483647;
      top: 0;
      right: 0;
      bottom: 0;
      width: min(380px, 92vw);
      background: Canvas;
      box-shadow: -4px 0 18px rgb(0 0 0 / 25%);
    }
    iframe { width: 100%; height: 100%; border: 0; display: block; }
    button {
      position: absolute;
      z-index: 1;
      top: 8px;
      right: 8px;
      width: 28px;
      height: 28px;
      border: 0;
      border-radius: 50%;
      background: rgb(0 0 0 / 12%);
      color: CanvasText;
      font: 22px/24px sans-serif;
      cursor: pointer;
    }
  `;

  const panel = document.createElement("div");
  panel.className = "panel";
  panel.append(frame, close);
  const overlayStyle = document.createElement("style");
  overlayStyle.textContent = `
    .draft-overlay {
      position: fixed;
      z-index: 2147483647;
      left: 24px;
      bottom: 24px;
      width: min(440px, calc(100vw - 428px));
      max-height: min(48vh, 380px);
      overflow: auto;
      padding: 18px;
      border: 1px solid #355263;
      border-radius: 18px;
      background: #08263a;
      color: #dae7e3;
      box-shadow: 0 12px 36px rgb(0 0 0 / 35%);
      font: 14px/1.45 system-ui, sans-serif;
    }
    .draft-overlay[hidden] { display: none; }
    .draft-overlay h2 { margin: 0 0 8px; font: 700 16px/1.2 system-ui, sans-serif; }
    .draft-overlay p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
    .draft-overlay-status { margin-top: 10px !important; color: #97b2ae; font-size: 12px; }
    .draft-overlay-actions { display: flex; gap: 8px; margin-top: 14px; }
    .draft-overlay button {
      position: static;
      width: auto;
      height: auto;
      min-height: 42px;
      padding: 0 14px;
      border: 1px solid #355263;
      border-radius: 999px;
      background: transparent;
      color: inherit;
      font: 650 13px system-ui, sans-serif;
    }
    .draft-overlay button:focus-visible { outline: 2px solid #02fdf6; outline-offset: 2px; }
    .draft-overlay .draft-approve { border-color: transparent; background: #02fdf6; color: #061d2d; }
    .draft-overlay button:disabled { cursor: wait; opacity: .6; }
    @media (max-width: 760px) {
      .draft-overlay { right: 16px; bottom: 16px; left: 16px; width: auto; max-height: 40vh; }
    }
  `;

  const draft = document.createElement("section");
  draft.className = "draft-overlay";
  draft.hidden = true;
  draft.setAttribute("role", "dialog");
  draft.setAttribute("aria-labelledby", "meeting-proxy-draft-heading");
  draft.setAttribute("aria-live", "polite");
  const heading = document.createElement("h2");
  heading.id = "meeting-proxy-draft-heading";
  heading.textContent = "Meeting Proxy draft";
  const preview = document.createElement("p");
  draftOverlayText = preview;
  const status = document.createElement("p");
  status.className = "draft-overlay-status";
  status.textContent = "Ctrl/Cmd + Enter to approve · Esc to discard";
  draftOverlayStatus = status;
  const actions = document.createElement("div");
  actions.className = "draft-overlay-actions";
  const approve = document.createElement("button");
  approve.type = "button";
  approve.className = "draft-approve";
  approve.textContent = "Approve and speak";
  approveDraftButton = approve;
  approve.addEventListener("click", approveCurrentDraft);
  const discard = document.createElement("button");
  discard.type = "button";
  discard.textContent = "Discard";
  discard.addEventListener("click", discardCurrentDraft);
  actions.append(approve, discard);
  draft.append(heading, preview, status, actions);
  draftOverlay = draft;
  shadow.append(style, overlayStyle, panel, draft);
  document.documentElement.appendChild(host);
}

function sendDraftAction(type) {
  if (!panelFrame?.contentWindow) return;
  const extensionOrigin = new URL(chrome.runtime.getURL("")).origin;
  panelFrame.contentWindow.postMessage({
    source: "meeting-proxy-overlay",
    type,
    requestId: pendingDraftRequestId,
  }, extensionOrigin);
}

function approveCurrentDraft() {
  if (!pendingDraftRequestId || pendingDraftAction) return;
  pendingDraftAction = true;
  approveDraftButton.disabled = true;
  draftOverlayStatus.textContent = "Sending approved reply…";
  sendDraftAction("draft-approve");
}

function discardCurrentDraft() {
  if (!pendingDraftRequestId) return;
  sendDraftAction("draft-discard");
  draftOverlay.hidden = true;
  pendingDraftRequestId = null;
  pendingDraftAction = false;
}

function handleDraftShortcut(event) {
  if (!draftOverlay || draftOverlay.hidden) return;
  if (event.target instanceof Element && event.target.closest(
    "input, textarea, select, [contenteditable='true'], [role='textbox']"
  )) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopImmediatePropagation();
    discardCurrentDraft();
  } else if (
    event.key === "Enter"
    && (event.ctrlKey || event.metaKey)
  ) {
    event.preventDefault();
    event.stopImmediatePropagation();
    approveCurrentDraft();
  }
}

document.addEventListener("keydown", handleDraftShortcut, true);

window.addEventListener("message", (event) => {
  const extensionOrigin = new URL(chrome.runtime.getURL("")).origin;
  if (
    event.source === window
    && event.data?.source === "meeting-proxy-audio-bridge"
  ) {
    if (panelFrame?.contentWindow) {
      panelFrame.contentWindow.postMessage(event.data, extensionOrigin);
    }
    return;
  }

  if (
    event.source !== panelFrame?.contentWindow
    || event.origin !== extensionOrigin
    || event.data?.source !== "meeting-proxy-extension"
  ) return;

  if (event.data.type === "draft-show") {
    pendingDraftRequestId = event.data.requestId || null;
    pendingDraftAction = false;
    draftOverlayText.textContent = event.data.response || "";
    draftOverlayStatus.textContent = "Ctrl/Cmd + Enter to approve · Esc to discard";
    approveDraftButton.disabled = false;
    draftOverlay.hidden = !pendingDraftRequestId || !event.data.response;
  } else if (event.data.type === "draft-hide") {
    draftOverlay.hidden = true;
    pendingDraftRequestId = null;
    pendingDraftAction = false;
  } else if (event.data.type === "draft-error") {
    pendingDraftAction = false;
    approveDraftButton.disabled = false;
    draftOverlayStatus.textContent = event.data.detail || "Could not speak the reply. Review it and try again.";
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "toggle-panel") togglePanel();
});

// ----------------------------------------------------------------
// Timestamp helper
// ----------------------------------------------------------------

const meetingStartedAt = Date.now();

function elapsed() {
  const s = Math.floor((Date.now() - meetingStartedAt) / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

// ----------------------------------------------------------------
// Send to service worker
// ----------------------------------------------------------------

function sendCaption(speaker, text) {
  chrome.runtime
    .sendMessage({
      type: "caption",
      timestamp: elapsed(),
      speaker: speaker || "Unknown",
      text,
    })
    .catch((err) => {
      console.debug("[content] sendMessage failed:", err);
    });
}

// ----------------------------------------------------------------
// Debounce state
//
// For each row element we track:
//   lastText  — most recent text we've seen
//   lastSent  — text we've already emitted for this row
//   timer     — pending debounce timer
// ----------------------------------------------------------------

const rowState = new WeakMap();

function scheduleEmit(row) {
  let state = rowState.get(row);
  if (!state) {
    state = { lastSent: "", timer: null };
    rowState.set(row, state);
  }

  if (state.timer) clearTimeout(state.timer);

  state.timer = setTimeout(() => {
    // Re-read the row's current text at emit time. Meet may have
    // replaced the row already; if so this element is detached
    // and textContent is still readable but we won't re-observe.
    const nameEl = row.querySelector(NAME_SELECTOR);
    const textEl = row.querySelector(TEXT_SELECTOR);
    if (!textEl) return;

    const speaker = nameEl ? nameEl.textContent.trim() : null;
    const text = textEl.textContent.trim();

    if (!text || text === state.lastSent) return;
    if (text.length > MAX_EVENT_CHARS) {
      // Safety: only emit the tail
      // (in practice this shouldn't happen)
    }

    state.lastSent = text;
    console.log("[content] caption:", speaker, "—", text);
    sendCaption(speaker, text);
  }, STABLE_MS);
}

// ----------------------------------------------------------------
// Row discovery / processing
// ----------------------------------------------------------------

function processRow(row) {
  if (!row.querySelector(TEXT_SELECTOR)) return;
  scheduleEmit(row);
}

function scanAll(root) {
  root.querySelectorAll(ROW_SELECTOR).forEach(processRow);
}

// ----------------------------------------------------------------
// Observer — watch the whole body for any caption-relevant mutation
// ----------------------------------------------------------------

let observer = null;

function startObserver() {
  if (observer) observer.disconnect();

  observer = new MutationObserver((mutations) => {
    let touched = false;

    for (const m of mutations) {
      // Character data changes (text growing inside a row)
      if (m.type === "characterData") {
        const parent = m.target.parentElement;
        if (parent?.closest?.(ROW_SELECTOR)) {
          processRow(parent.closest(ROW_SELECTOR));
          touched = true;
        }
        continue;
      }

      // Node additions/removals — new rows, replaced rows
      if (m.type === "childList") {
        for (const node of m.addedNodes) {
          if (!(node instanceof HTMLElement)) continue;
          if (node.matches?.(ROW_SELECTOR)) {
            processRow(node);
            touched = true;
          } else if (node.querySelector?.(ROW_SELECTOR)) {
            node.querySelectorAll(ROW_SELECTOR).forEach((r) => {
              processRow(r);
              touched = true;
            });
          }
        }
      }
    }

    // We don't do anything with `touched` — the debounce handles
    // everything. Just here in case we want to log in the future.
    void touched;
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  console.log("[content] observing caption region");
}

// ----------------------------------------------------------------
// Boot
// ----------------------------------------------------------------

function boot() {
  // Attach the observer immediately — no need to wait for the
  // container, we observe the whole body.
  startObserver();

  // Catch any rows that already exist.
  scanAll(document.body);
}

boot();