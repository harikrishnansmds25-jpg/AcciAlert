"use strict";

/*
 * AcciAlert voice alerts: one speech queue shared by the demo simulation and live GPS.
 *
 * - One On/Off setting for both modes (buttons marked data-voice-toggle), remembered per browser.
 * - Picks an installed English voice (prefers en-IN, local voices first).
 * - Speaks alerts one at a time, in order, and keeps a reference to the utterance in flight
 *   (Chrome can otherwise drop its events).
 * - Recovers when the browser speech engine is stuck: clears stale speech before speaking and
 *   retries once if an alert does not start within 3 seconds.
 * - unlock() is called from Start buttons / key presses so mobile browsers allow later speech.
 */
(function exposeVoiceAlerts(root) {
  const STORAGE_KEY = "accialert.voiceAlerts";
  const START_TIMEOUT_MS = 3000;
  const MAX_UTTERANCE_MS = 20000;
  const MAX_QUEUE = 3;
  const synth = root.speechSynthesis;
  const supported = Boolean(synth && root.SpeechSynthesisUtterance);

  let enabled = true;
  try {
    const saved = root.localStorage.getItem(STORAGE_KEY);
    if (saved === "off") enabled = false;
  } catch (error) { /* storage unavailable: default to on */ }

  let voice = null;
  let queue = [];
  let current = null;
  let timer = null;
  let status = !supported ? "Voice alerts are not supported in this browser."
    : enabled ? "Voice alerts are on." : "Voice alerts are off.";
  const listeners = new Set();

  function notify(message) {
    if (message) status = message;
    const snapshot = { enabled, supported, status };
    listeners.forEach((listener) => {
      try { listener(snapshot); } catch (error) { console.error(error); }
    });
  }

  function pickVoice() {
    if (!supported) return;
    const voices = (synth.getVoices() || []).filter((item) => /^en([-_]|$)/i.test(item.lang));
    const score = (item) => (/^en[-_]IN$/i.test(item.lang) ? 4 : 0) + (item.localService ? 2 : 0) + (item.default ? 1 : 0);
    voices.sort((a, b) => score(b) - score(a));
    voice = voices[0] || null;
  }

  function clearTimer() {
    if (timer) { root.clearTimeout(timer); timer = null; }
  }

  function finish(utterance) {
    if (current !== utterance) return;
    clearTimer();
    current = null;
    root.setTimeout(pump, 120);
  }

  function speakNow(item) {
    const utterance = new root.SpeechSynthesisUtterance(item.text);
    if (voice) { utterance.voice = voice; utterance.lang = voice.lang; } else { utterance.lang = "en-IN"; }
    utterance.rate = 1.1;
    utterance.pitch = 1;
    utterance.volume = 1;
    let started = false;
    current = utterance;

    utterance.onstart = () => {
      started = true;
      clearTimer();
      timer = root.setTimeout(() => { if (current === utterance) { synth.cancel(); finish(utterance); } }, MAX_UTTERANCE_MS);
      notify(`Speaking: “${item.text}”`);
    };
    utterance.onend = () => {
      if (current === utterance) notify(enabled ? "Voice alerts are on." : "Voice alerts are off.");
      finish(utterance);
    };
    utterance.onerror = (event) => {
      if (event.error === "not-allowed") {
        notify("The browser blocked sound. Tap the Voice alerts button once to allow voice.");
      } else if (event.error !== "interrupted" && event.error !== "canceled") {
        notify(`Voice could not play (${event.error}). Check that the tab and device are not muted.`);
      }
      finish(utterance);
    };

    try {
      if (synth.paused) synth.resume();
      synth.speak(utterance);
    } catch (error) {
      current = null;
      notify("Voice could not start in this browser.");
      return;
    }
    // Chrome's speech engine can get stuck and never start. Reset it and retry once.
    timer = root.setTimeout(() => {
      if (current !== utterance || started) return;
      synth.cancel();
      current = null;
      if (!item.retried) queue.unshift({ ...item, retried: true });
      else notify("Voice did not start. Tap the Voice alerts button to re-enable sound.");
      root.setTimeout(pump, 150);
    }, START_TIMEOUT_MS);
  }

  function pump() {
    if (!supported || !enabled || current || !queue.length) return;
    if (!voice) pickVoice();
    const item = queue.shift();
    if (synth.speaking || synth.pending) {
      // Nothing of ours is playing, so this is stale or stuck speech: clear it first.
      synth.cancel();
      const token = {};
      current = token;
      root.setTimeout(() => {
        if (current !== token) return; // stop() was called meanwhile
        current = null;
        speakNow(item);
      }, 80);
      return;
    }
    speakNow(item);
  }

  function say(text) {
    if (!text) return;
    if (!supported) { notify("Voice alerts are not supported in this browser."); return; }
    if (!enabled) return;
    while (queue.length >= MAX_QUEUE) queue.shift();
    queue.push({ text });
    pump();
  }

  function stop() {
    queue = [];
    clearTimer();
    current = null;
    if (supported) { try { synth.cancel(); } catch (error) { /* ignore */ } }
  }

  // Call from a click / tap / key press so mobile browsers permit speech later on.
  function unlock() {
    if (!supported || !enabled) return;
    try {
      pickVoice();
      if (synth.paused) synth.resume();
      if (!current && !queue.length && !synth.speaking) {
        const primer = new root.SpeechSynthesisUtterance(".");
        primer.volume = 0;
        if (voice) { primer.voice = voice; primer.lang = voice.lang; }
        synth.speak(primer);
      }
    } catch (error) { /* speech stays unavailable; text alerts still show */ }
  }

  function setEnabled(value, { confirm = true } = {}) {
    enabled = Boolean(value);
    try { root.localStorage.setItem(STORAGE_KEY, enabled ? "on" : "off"); } catch (error) { /* ignore */ }
    if (!supported) { notify(); return; }
    if (enabled) {
      stop();
      pickVoice();
      notify("Voice alerts are on.");
      if (confirm) say("Voice alerts on.");
    } else {
      stop();
      notify("Voice alerts are off.");
    }
  }

  function onChange(listener) {
    listeners.add(listener);
    listener({ enabled, supported, status });
    return () => listeners.delete(listener);
  }

  function bindControls() {
    document.querySelectorAll("[data-voice-toggle]").forEach((button) => {
      onChange(({ enabled: on, supported: ok }) => {
        button.disabled = !ok;
        button.setAttribute("aria-pressed", String(on && ok));
        button.textContent = !ok ? "Voice unavailable" : on ? "🔊 Voice alerts: On" : "🔇 Voice alerts: Off";
      });
      button.addEventListener("click", () => setEnabled(!enabled));
    });
    document.querySelectorAll("[data-voice-status]").forEach((element) => {
      onChange(({ status: text }) => { element.textContent = text; });
    });
  }

  if (supported) {
    pickVoice();
    if (typeof synth.addEventListener === "function") synth.addEventListener("voiceschanged", pickVoice);
    else synth.onvoiceschanged = pickVoice;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && synth.paused) synth.resume();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindControls);
  else bindControls();

  root.TrafficVoice = Object.freeze({
    say, stop, unlock, setEnabled, onChange,
    isEnabled: () => enabled, isSupported: () => supported,
  });
}(window));
