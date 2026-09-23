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