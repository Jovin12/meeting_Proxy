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
  let ttsPercent = 0;

  function mixedTrack() {
    return destination?.stream.getAudioTracks()[0] ?? null;
  }

  function publishState(requestId, error = "") {
    window.postMessage({
      source: "meeting-proxy-audio-bridge",
      type: "state",
      requestId,
      enabled,
      ttsPercent,
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

  async function enableMix() {
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
    ttsPercent = 0;
    microphoneGain.gain.value = 1 - ttsPercent / 100;
    ttsGain.gain.value = ttsPercent / 100;
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
    ttsPercent = 0;
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
    if (!enabled || !audioContext || Object.prototype.toString.call(audioData) !== "[object ArrayBuffer]") {
      throw new Error("Enable the microphone mix before speaking.");
    }

    const buffer = await audioContext.decodeAudioData(audioData);
    if (!enabled || !audioContext) throw new Error("The microphone mix was disabled before playback started.");
    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(ttsGain);
    ttsSources.add(source);
    source.addEventListener("ended", () => {
      ttsSources.delete(source);
      source.disconnect();
    }, { once: true });
    source.start();
  }

  function setMix(nextTtsPercent) {
    if (!enabled || !audioContext) throw new Error("Enable the microphone mix before adjusting the mix.");
    if (!Number.isFinite(nextTtsPercent) || nextTtsPercent < 0 || nextTtsPercent > 100) {
      throw new Error("Mix percentage must be between 0 and 100.");
    }

    ttsPercent = nextTtsPercent;
    const now = audioContext.currentTime;
    const smoothingTime = 0.015;
    microphoneGain.gain.setTargetAtTime(1 - ttsPercent / 100, now, smoothingTime);
    ttsGain.gain.setTargetAtTime(ttsPercent / 100, now, smoothingTime);
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
      if (message.type === "enable") await enableMix();
      else if (message.type === "disable") await disableMix();
      else if (message.type === "speak") await playTts(message.audio);
      else if (message.type === "mix") setMix(message.ttsPercent);
      else return;
      publishState(message.requestId);
    } catch (error) {
      publishState(message.requestId, error.message);
    }
  });
})();