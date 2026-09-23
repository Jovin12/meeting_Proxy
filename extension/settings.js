const DEFAULTS = {
  autoStart: false,
  backendUrl: "http://127.0.0.1:8000",
  backendWsUrl: "ws://127.0.0.1:8000",
};

export async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

export async function setSettings(partial) {
  await chrome.storage.sync.set(partial);
  return getSettings();
}