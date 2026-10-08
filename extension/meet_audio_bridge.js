(() => {
  const bridgeFlag = "__meetingProxyAudioBridgeInstalled";
  if (window[bridgeFlag]) return;
  window[bridgeFlag] = true;

  const extensionOriginPattern = /^(chrome|moz|opera)-extension:\/\//;
  const peerConnections = new Set();
  const senderOriginalTracks = new Map();
  const inputSources = new Map();
  const ttsSources = new Set();
  const NativePeerConnection = window.RTCPeerConnection;
  const senderPrototype = window.RTCRtpSender?.prototype;
  const nativeReplaceTrack = senderPrototype?.replaceTrack;
  let audioContext = null;
  let destination = null;
  let microphoneGain = null;
  let ttsGain = null;
  let enabled = false;
  let microphoneVolume = 1;
  let ttsVolume = 1;

  function mixedTrack() {
    return destination?.stream.getAudioTracks()[0] ?? null;
  }

  function publishState(requestId, error = "") {
    window.postMessage({
      source: "meeting-proxy-audio-bridge",
      type: "state",
      requestId,
      enabled,
      microphoneVolume,
      ttsVolume,
      speaking: ttsSources.size > 0,
      senderCount: senderOriginalTracks.size,
      error,
    }, location.origin);
  }

  function connectInputTrack(track) {
    let source = inputSources.get(track);
    if (!source) {
      source = audioContext.createMediaStreamSource(new MediaStream([track]));
      source.connect(microphoneGain);
      inputSources.set(track, source);
      track.addEventListener("ended", () => {
        source.disconnect();
        inputSources.delete(track);
      }, { once: true });
    }
    return mixedTrack();
  }

  async function replaceWithMix(sender, track) {
    if (!nativeReplaceTrack || !track || track.kind !== "audio") return;
    senderOriginalTracks.set(sender, track);
    await nativeReplaceTrack.call(sender, connectInputTrack(track));
  }

  async function enableMix(volumes = {}) {
    if (enabled) return;
    if (!NativePeerConnection || !nativeReplaceTrack) {
      throw new Error("This browser does not expose the required WebRTC sender APIs.");
    }

    const context = new AudioContext();
    await context.resume();
    if (context.state !== "running") {
      await context.close();
      throw new Error("Audio is blocked. Click the Meet page once, then enable the mix again.");
    }
    audioContext = context;
    destination = context.createMediaStreamDestination();
    microphoneGain = context.createGain();
    ttsGain = context.createGain();
    microphoneVolume = normalizeVolume(volumes.microphoneVolume, 1);
    ttsVolume = normalizeVolume(volumes.ttsVolume, 1);
    microphoneGain.gain.value = microphoneVolume;
    ttsGain.gain.value = ttsVolume;
    microphoneGain.connect(destination);
    ttsGain.connect(destination);
    enabled = true;

    for (const peerConnection of peerConnections) {
      for (const sender of peerConnection.getSenders()) {
        if (sender.track?.kind === "audio") await replaceWithMix(sender, sender.track);
      }
    }
  }

  async function disableMix() {
    enabled = false;
    for (const [sender, originalTrack] of senderOriginalTracks) {
      try {
        await nativeReplaceTrack.call(sender, originalTrack);
      } catch {
        senderOriginalTracks.delete(sender);
      }
    }
    senderOriginalTracks.clear();
    for (const source of ttsSources) {
      try {
        source.stop();
      } catch {
      }
      source.disconnect();
    }
    ttsSources.clear();
    for (const source of inputSources.values()) source.disconnect();
    inputSources.clear();
    await audioContext?.close();
    audioContext = null;
    destination = null;
    microphoneGain = null;
    ttsGain = null;
  }

  async function playTts(audioData) {
    if (!enabled || ttsVolume === 0 || !audioContext || Object.prototype.toString.call(audioData) !== "[object ArrayBuffer]") {
      throw new Error("Enable the microphone mix and set TTS volume above zero before speaking.");
    }

    const buffer = await audioContext.decodeAudioData(audioData);
    if (!enabled || ttsVolume === 0) throw new Error("TTS was muted before playback started.");
    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(ttsGain);
    ttsSources.add(source);
    source.addEventListener("ended", () => {
      ttsSources.delete(source);
      source.disconnect();
      publishState();
    }, { once: true });
    source.start();
  }

  function stopTtsSources() {
    for (const source of ttsSources) {
      try {
        source.stop();
      } catch {
      }
      source.disconnect();
    }
    ttsSources.clear();
  }

  function normalizeVolume(value, fallback = 1) {
    const volume = Number(value);
    return Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : fallback;
  }

  function setVolumes(volumes = {}) {
    if (!enabled || !audioContext) throw new Error("Enable the microphone mix before adjusting source volume.");
    microphoneVolume = normalizeVolume(volumes.microphoneVolume, microphoneVolume);
    ttsVolume = normalizeVolume(volumes.ttsVolume, ttsVolume);
    const now = audioContext.currentTime;
    microphoneGain.gain.setValueAtTime(microphoneVolume, now);
    ttsGain.gain.setValueAtTime(ttsVolume, now);
    if (ttsVolume === 0) stopTtsSources();
  }

  if (NativePeerConnection && nativeReplaceTrack) {
    const peerConnectionPrototype = NativePeerConnection.prototype;
    const nativeAddTrack = peerConnectionPrototype.addTrack;
    peerConnectionPrototype.addTrack = function (track, ...streams) {
      const outgoingTrack = enabled && track?.kind === "audio" ? connectInputTrack(track) : track;
      const sender = nativeAddTrack.call(this, outgoingTrack, ...streams);
      if (outgoingTrack !== track) senderOriginalTracks.set(sender, track);
      if (outgoingTrack !== track) publishState();
      return sender;
    };

    const nativeAddTransceiver = peerConnectionPrototype.addTransceiver;
    if (nativeAddTransceiver) {
      peerConnectionPrototype.addTransceiver = function (trackOrKind, init) {
        if (enabled && typeof trackOrKind !== "string" && trackOrKind?.kind === "audio") {
          const transceiver = nativeAddTransceiver.call(this, connectInputTrack(trackOrKind), init);
          senderOriginalTracks.set(transceiver.sender, trackOrKind);
          publishState();
          return transceiver;
        }
        return nativeAddTransceiver.call(this, trackOrKind, init);
      };
    }

    const WrappedPeerConnection = new Proxy(NativePeerConnection, {
      construct(target, argumentsList, newTarget) {
        const peerConnection = Reflect.construct(target, argumentsList, newTarget);
        peerConnections.add(peerConnection);
        peerConnection.addEventListener("connectionstatechange", () => {
          if (peerConnection.connectionState === "closed") peerConnections.delete(peerConnection);
        });
        return peerConnection;
      },
    });
    window.RTCPeerConnection = WrappedPeerConnection;

    senderPrototype.replaceTrack = function (track) {
      if (enabled && track?.kind === "audio" && track !== mixedTrack()) {
        return replaceWithMix(this, track);
      }
      if (track === null) senderOriginalTracks.delete(this);
      return nativeReplaceTrack.call(this, track);
    };
  }

  window.addEventListener("message", async (event) => {
    const message = event.data;
    const fromExtension = extensionOriginPattern.test(event.origin);
    const fromContentScript = event.source === window && message?.source === "meeting-proxy-content";
    if ((!fromExtension || event.source === window) && !fromContentScript) return;
    if (message?.source !== "meeting-proxy-extension" && !fromContentScript) return;

    try {
      if (message.type === "enable") await enableMix(message);
      else if (message.type === "disable") await disableMix();
      else if (message.type === "speak") await playTts(message.audio);
      else if (message.type === "stop") stopTtsSources();
      else if (message.type === "volumes") setVolumes(message);
      else return;
      publishState(message.requestId);
    } catch (error) {
      publishState(message.requestId, error.message);
    }
  });
})();