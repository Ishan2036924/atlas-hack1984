// Talk mode's voices for an answer (phase 5): the device's own (L1, the browser's speechSynthesis, $0) and Atlas's
// (L2, POST /v1/voice/speak: OpenAI's speech model on the person's allowance, metered on the server, which reads only
// the answer's own words by its id). Both were unlocked inside the tap that started talk mode (prime.ts), so nothing
// plays without a gesture, and both stop at once on a tap (barge-in) or when talk mode ends.
import { ApiError, type Api } from "../../../lib/api";
import { errorWords } from "../../../lib/errorWords";
import { sentences } from "./spoken";

export type SayEnd = "done" | "cut" | "failed";
/** what was said: the words (Atlas's voice reads the server's own line), how it ended, a calm note when Atlas's voice
 * gave way to the device's, and the minutes of Atlas's voice left today when the server said */
export type Said = { end: SayEnd; text: string; note?: string; minutesLeft?: number | null };

export type Voice = {
  kind: "device" | "atlas";
  say: (line: string, messageId: number | null) => Promise<Said>;
  cancel: () => void;
};

type Synth = Pick<SpeechSynthesis, "speak" | "cancel" | "getVoices"> & Partial<Pick<SpeechSynthesis, "speaking" | "pending">>;
const AFTER_CANCEL_MS = 120; // iOS says nothing for a speak() in the same moment as a cancel()
const START_MS = 3000; // speech that hasn't started by then never will (a phone that kept it from playing)
const MS_PER_CHAR = 150; // once started, a generous ceiling for the line (slow voices included), so nothing hangs
const synth = (): Synth | null => (typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : null);

export const GAVE_WAY = "Atlas's voice isn't available right now, so your device's voice is reading instead.";
export const NO_DEVICE_VOICE = "This browser can't read aloud. The answers are in the chat.";

/** The device's own voice: the line in sentences, queued; `say` ends when the last one does, or "cut" on cancel. */
export function deviceVoice(): Voice {
  let run = 0;
  let finish: ((end: SayEnd) => void) | null = null;
  const pick = (s: Synth) => {
    const lang = (navigator.language || "en-US").toLowerCase();
    const voices = s.getVoices?.() ?? [];
    return voices.find((v) => v.lang.toLowerCase() === lang && v.default) ?? voices.find((v) => v.lang.toLowerCase() === lang) ?? null;
  };
  return {
    kind: "device",
    say(line) {
      const s = synth();
      if (!s || typeof SpeechSynthesisUtterance === "undefined") return Promise.resolve({ end: "failed", text: line, note: NO_DEVICE_VOICE });
      if (!line.trim()) return Promise.resolve({ end: "done", text: line });
      const mine = ++run;
      const busy = Boolean(s.speaking || s.pending);
      if (busy) s.cancel(); // only when something is being said (iOS)
      return new Promise<Said>((resolve) => {
        let watch: number | undefined;
        finish = (end) => {
          window.clearTimeout(watch);
          finish = null;
          resolve({ end, text: line });
        };
        const parts = sentences(line);
        const voice = pick(s);
        const go = () => {
          if (mine !== run) return;
          // never left on Speaking: speech that doesn't start is given up on, and started speech has a ceiling
          watch = window.setTimeout(() => mine === run && (s.cancel(), finish?.("failed")), START_MS);
          parts.forEach((part, i) => {
            const u = new SpeechSynthesisUtterance(part);
            u.lang = navigator.language || "en-US";
            if (voice) u.voice = voice;
            if (i === 0)
              u.onstart = () => {
                window.clearTimeout(watch);
                watch = window.setTimeout(() => mine === run && (s.cancel(), finish?.("done")), line.length * MS_PER_CHAR + START_MS);
              };
            if (i === parts.length - 1) u.onend = () => mine === run && finish?.("done");
            u.onerror = (e) => mine === run && finish?.(e.error === "interrupted" || e.error === "canceled" ? "cut" : "failed");
            s.speak(u);
          });
        };
        if (busy) window.setTimeout(go, AFTER_CANCEL_MS);
        else go();
      });
    },
    cancel() {
      run++;
      synth()?.cancel();
      finish?.("cut");
    },
  };
}

export function decode(b64: string, type: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type });
}

/** Atlas's voice: the server reads the answer's own line (by its id) and the primed audio element plays it. When it
 * can't (no minutes left today, the allowance, a failure) the device's voice reads instead and says why. A line with
 * no answer behind it (talk mode's own words, e.g. the card line) is always the device's. */
export function atlasVoice(api: Api, el: HTMLAudioElement | null, fallback: Voice = deviceVoice()): Voice {
  let url: string | null = null;
  let run = 0;
  let finish: ((end: SayEnd) => void) | null = null;
  const drop = () => {
    if (url) URL.revokeObjectURL(url);
    url = null;
  };
  return {
    kind: "atlas",
    async say(line, messageId) {
      const mine = ++run;
      if (messageId === null || !el) return fallback.say(line, messageId);
      let got;
      try {
        got = await api.speak(messageId);
      } catch (e) {
        if (mine !== run) return { end: "cut", text: line };
        // the server's own words for a wall, a flag or Atlas Shield (402, 403, 409); anything else, ours
        const why = e instanceof ApiError && [402, 403, 409].includes(e.status) ? errorWords(e, GAVE_WAY) : GAVE_WAY;
        return { ...(await fallback.say(line, messageId)), note: why };
      }
      if (mine !== run) return { end: "cut", text: got.text };
      const audio = el;
      drop();
      url = URL.createObjectURL(decode(got.audio, got.type));
      audio.src = url;
      return new Promise<Said>((resolve) => {
        finish = (end) => {
          finish = null;
          resolve({ end, text: got.text, minutesLeft: got.minutes_left_today });
        };
        audio.onended = () => mine === run && finish?.("done");
        audio.onerror = () => mine === run && finish?.("failed");
        audio.play().catch(() => mine === run && finish?.("failed"));
      });
    },
    cancel() {
      run++;
      el?.pause();
      fallback.cancel();
      finish?.("cut");
      drop();
    },
  };
}
