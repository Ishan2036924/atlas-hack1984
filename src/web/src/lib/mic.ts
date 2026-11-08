// The mic behind every message box (loaded on the first screen with a box, outside the first load; lib/dictation is
// the small hook that drives it). Two ways in: live dictation (the browser's own speech recognition: Safari uses
// Apple's, Chrome Google's) writes words into the box as you speak; without it the mic records a note that lifeos-api
// transcribes (/v1/voice) and drops into the box the same way.
// - One mic for the whole app: starting it on another screen first stops the one before, keeping its words.
// - A screen that closes takes the mic with it: every track stopped, the recognizer aborted (no orange dot left on).
// - Live dictation keeps listening until the person taps stop: when the browser ends a session by itself (a pause, its
//   own time limit) it is started again, with the words so far kept; it gives up only after a long quiet, a long
//   dictation or a few sessions in a row that die at once. One recognizer is reused for the visit.
// - The microphone is asked for once per visit: a stream the browser has allowed is kept for a few minutes after a
//   note (and while live dictation runs), so the next tap doesn't ask again (WebKit asks again a minute after capture
//   stopped); it is let go on the idle timer, when the page is hidden or closed, and on sign-out.
// - A note stops itself at the server's limit (/v1/voice/limits, asked once a session) and is never lost: a send that
//   fails keeps the note in memory and offers Try again, until the screen closes.
import { ApiError, type Api, type VoiceLimits } from "./api";
import { PRODUCT_NAME } from "./brand";
import { errorWords } from "./errorWords";
import { onForgetAccount } from "./sessionCaches";

export type MicState = "idle" | "listening" | "recording" | "sending";
/** What the box shows: `seconds` recorded so far and `max` the note's limit (both while recording); `say` is for a
 * screen reader's status line (start and stop only, never every second). */
export type MicView = { state: MicState; error: string | null; retry: (() => void) | null; seconds: number; max: number | null; say: string };
export type MicDeps = {
  api: () => Api;
  text: () => string; // what the box holds now
  setText: (next: string | ((cur: string) => string)) => void;
  show: (view: MicView) => void;
  audio?: MediaTrackConstraints; // talk mode asks for the echo-safe microphone (ECHO_SAFE); the box asks for any
};
/** `listen`: the spoken answer to a card (no tap started it): live recognition only, never a note (a note would only
 *  stop on a tap), and a browser that refuses it there is left as it was for the next tap */
export type Mic = { toggle: () => void; stop: () => void; dispose: () => void; listen: () => void };

export const IDLE: MicView = { state: "idle", error: null, retry: null, seconds: 0, max: null, say: "" };

// the server's own words for a day's voice minutes used up (mcp budget.reserve_voice, over_voice), as a sentence
export const USED_UP = "Today's voice minutes are used up. Typing still works, and voice is back tomorrow.";
export const NO_VOICE = "Voice isn't available right now. Typing still works.";
export const NOT_SENT = "Your voice note didn't send. Try again, or type it.";
const NOT_HEARD = "Couldn't hear that. Try again.";
const NO_LIVE = "Live dictation is off here. Tap the mic again to record a voice note instead.";
/** Talk mode's microphone: the browser cancels the speaker's sound, quiets the room and evens the level, so Atlas's own
 * voice isn't heard as yours (Media Capture and Streams: echoCancellation, noiseSuppression, autoGainControl). */
export const ECHO_SAFE: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
const START_MS = 3000; // a recognizer that hasn't started this long after start() (the mic already allowed) never will
// errors that mean live recognition can't work in this browser (no speech service, no such language): record notes
const CANT_HERE = new Set(["network", "language-not-supported"]);
const MARGIN_S = 0.5; // a note stops this far inside its limit, so its measured length never goes over
const ZERO_RECHECK_MS = 10 * 60_000; // "no minutes left" is asked again after this long (a new day may have begun)
// live dictation that the browser keeps ending: listening again is the point, so these only stop a loop or a forgotten mic
export const RESTART_MS = 250; // a recognizer needs a moment after `end` before it can start again
export const QUIET_STOP_MS = 2 * 60_000; // nothing said for this long: stop listening
export const DICTATION_MAX_MS = 10 * 60_000; // one tap listens for at most this long
export const FAST_ENDS = 3; // sessions in a row that end within FAST_END_MS with no words: the recognizer isn't working
const FAST_END_MS = 1500;
export const HOLD_MS = 3 * 60_000; // an allowed microphone is kept this long after its last use
export const GRACE_MS = 20_000; // ... and this long after the last message box closed

/** Where to turn the microphone back on, in this browser's words (the same reading of the user agent as lib/push). */
export function micOff(ua: string = navigator.userAgent): string {
  const where = /iPhone|iPad/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua)
    ? "the aA menu › Website Settings › Microphone (choose Allow), or Settings › Apps › Safari › Microphone"
    : /Chrome|CriOS|Edg/.test(ua)
    ? "the site settings beside the address bar"
    : /Mac/.test(ua) && /Safari/.test(ua)
    ? "Safari › Settings › Websites › Microphone"
    : "your browser's site settings";
  return `The microphone is off for ${PRODUCT_NAME}. You can turn it on in ${where}.`;
}

export function joinSpoken(base: string, spoken: string): string {
  const s = spoken.trim();
  if (!s) return base;
  const b = base.trimEnd();
  return b ? `${b} ${s}` : s;
}

type Alternative = { transcript: string };
type SpeechResult = { isFinal: boolean; 0: Alternative };
type SpeechEvent = { results: ArrayLike<SpeechResult> };
type Recognizer = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SpeechEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

export function spokenText(results: ArrayLike<SpeechResult>): string {
  let out = "";
  for (let i = 0; i < results.length; i++) out += results[i][0]?.transcript ?? "";
  return out.replace(/\s+/g, " ").trim();
}

/** Dictation's words in the box: they start at `at`, are `len` long, and `last` is the box as dictation last left it. */
export type Owned = { at: number; len: number; last: string };

/** Where dictation's words are now, given what the box holds: typing before them moves them, typing after them leaves
 * them where they are, and an edit inside them (or one that can't be told apart from one) makes them the user's: -1. */
export function placeOwned(o: Owned, cur: string): number {
  if (cur === o.last) return o.at;
  const a = o.last;
  const n = Math.min(a.length, cur.length);
  let p = 0;
  while (p < n && a[p] === cur[p]) p++;
  let q = 0;
  while (q < n - p && a[a.length - 1 - q] === cur[cur.length - 1 - q]) q++;
  if (a.length - q <= o.at) return o.at + cur.length - a.length; // the change ends before the words
  if (p >= o.at + o.len) return o.at; // it starts after them
  return -1;
}

/** The box with dictation's words replaced by what was heard; null when the user has made them their own. */
export function placeSpoken(o: Owned, cur: string, spoken: string): { text: string; owned: Owned } | null {
  const at = placeOwned(o, cur);
  if (at < 0) return null;
  const before = cur.slice(0, at);
  const piece = spoken && before && !/\s$/.test(before) ? ` ${spoken}` : spoken;
  const text = before + piece + cur.slice(at + o.len);
  return { text, owned: { at, len: piece.length, last: text } };
}

// One recognizer for the visit: Safari on iPhone asks for the microphone again for every new recognizer, so a session
// that ends is started again on the same one. A second box starting while it still runs gets a recognizer of its own.
let shared: Recognizer | null = null;
const running = new WeakSet<Recognizer>();

function recognizer(): Recognizer | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognizer; webkitSpeechRecognition?: new () => Recognizer };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) return null;
  if (shared && !running.has(shared)) return shared;
  const r = new Ctor();
  shared ??= r;
  return r;
}

// --- the allowed microphone, kept for the visit (a note and, when the browser reports it allowed, live dictation) ---
let alive = 0; // message boxes (and talk mode) with a mic that hasn't been disposed
let held: { stream: MediaStream; key: string; uses: number; timer: number | undefined } | null = null;
const stopStream = (stream: MediaStream) => stream.getTracks().forEach((t) => t.stop());

function dropHeld() {
  if (!held) return;
  window.clearTimeout(held.timer);
  stopStream(held.stream);
  held = null;
}

/** The kept stream when it is still live and was asked for with the same constraints; otherwise asks the browser. */
async function takeStream(constraints?: MediaTrackConstraints): Promise<MediaStream> {
  const key = JSON.stringify(constraints ?? true);
  if (held && (held.key !== key || held.stream.getTracks().some((t) => t.readyState === "ended"))) dropHeld();
  if (!held) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: constraints ?? true });
    if (held) stopStream(stream); // another start got there first
    else held = { stream, key, uses: 0, timer: undefined };
  }
  window.clearTimeout(held.timer);
  held.uses++;
  return held.stream;
}

/** Done with a stream from takeStream: kept for HOLD_MS (`keep`), or stopped once nobody else uses it. */
function giveStream(stream: MediaStream, keep: boolean) {
  if (!held || held.stream !== stream) return stopStream(stream);
  held.uses = Math.max(0, held.uses - 1);
  if (held.uses > 0) return;
  if (!keep) return dropHeld();
  window.clearTimeout(held.timer);
  held.timer = window.setTimeout(dropHeld, HOLD_MS);
}

if (typeof window !== "undefined") {
  // a page that is closed or put away has no use for the microphone (and iOS ends capture in the background anyway)
  window.addEventListener("pagehide", dropHeld);
  document.addEventListener("visibilitychange", () => document.visibilityState === "hidden" && !held?.uses && dropHeld());
}

function pickType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const t of ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"]) if (MediaRecorder.isTypeSupported(t)) return t;
  return undefined;
}

// --- the server's limits: asked once a session (a "none left" answer is asked again after ZERO_RECHECK_MS) ---
let limits: { p: Promise<VoiceLimits>; v?: VoiceLimits; at: number } | null = null;

/** Seconds one note may run now: the server's per-note limit, or less when today's minutes are fewer. */
export function noteSeconds(v: VoiceLimits): number {
  const left = v.minutes_left_today === null ? Infinity : Math.max(0, v.minutes_left_today * 60);
  return Math.min(v.max_seconds, left);
}

function voiceLimits(api: Api): Promise<VoiceLimits> {
  const stale = limits?.v && noteSeconds(limits.v) < 1 && Date.now() - limits.at > ZERO_RECHECK_MS;
  if (!limits || stale) {
    const entry: NonNullable<typeof limits> = { p: api.voiceLimits(), at: Date.now() };
    limits = entry;
    entry.p.then(
      (v) => (entry.v = v),
      () => limits === entry && (limits = null), // a failed ask is asked again on the next tap
    );
  }
  return limits.p;
}

/** A note went through: today's minutes left go down by its length, without asking the server again. */
function spent(seconds: number) {
  const v = limits?.v;
  if (v && v.minutes_left_today !== null) v.minutes_left_today = Math.max(0, v.minutes_left_today - seconds / 60);
}

let owner: { finish: () => void } | null = null;

/** The session's limits belong to the person signed in: forgotten on sign-out or an account switch (sessionCaches). */
export function forgetLimits() {
  limits = null;
}
onForgetAccount(forgetLimits);
onForgetAccount(dropHeld);

/** Talk mode's live conversation takes the one microphone too: whoever had it stops first (its words kept), and
 * `release` gives it back. `finish` is called if another screen takes it later. */
export function takeMic(finish: () => void): { release: () => void } {
  const self = { finish };
  if (owner && owner !== self) owner.finish();
  owner = self;
  return { release: () => void (owner === self && (owner = null)) };
}

/** Tests only: forget the session's limits and the mic's owner. */
export function resetMic() {
  forgetLimits();
  owner = null;
  dropHeld();
  shared = null;
  alive = 0;
}

export function createMic(d: MicDeps): Mic {
  alive++;
  let view = IDLE;
  let gone = false;
  let live = true; // false once the browser refuses live recognition: record notes instead
  let seq = 0; // each start; a microphone granted to an older start (or after the screen closed) goes off at once
  let rec: Recognizer | null = null;
  let owned: Owned | null = null;
  let media: { r: MediaRecorder; stream: MediaStream; chunks: Blob[]; t0: number; timers: number[] } | null = null;
  let pending: { blob: Blob; seconds: number } | null = null;

  const show = (patch: Partial<MicView>) => {
    view = { ...view, ...patch };
    if (!gone) d.show(view);
  };
  const self = { finish: () => finish() };
  const claim = () => {
    if (owner && owner !== self) owner.finish(); // one mic at a time: the other screen's stops, its words kept
    owner = self;
  };
  // the stream kept while live dictation runs (only when the browser says the microphone is already allowed)
  let lent: MediaStream | null = null;
  const unlend = () => {
    if (lent) giveStream(lent, !gone);
    lent = null;
  };
  const release = () => {
    unlend();
    if (owner === self) owner = null;
  };

  // the person still wants to be heard (a tap started it and nothing has stopped it): a recognizer that the browser ends
  // by itself (Safari does at a pause, Chrome after a time) is started again, so a long request is never cut off
  let wanted = false;
  let again: number | undefined; // the pending start of the next session
  const noMore = () => {
    wanted = false;
    window.clearTimeout(again);
    again = undefined;
  };

  function finish() {
    seq++;
    const waiting = again !== undefined;
    noMore();
    rec?.stop(); // the last words still land in the box
    if (waiting) {
      // between two sessions: nothing is listening, so nothing will end by itself
      release();
      show({ state: "idle", say: "Stopped listening" });
    }
    if (media) void stopNote();
  }

  /** How one tap's listening goes on across the recognizer's sessions. */
  type Run = { answer: boolean; t0: number; lastHeard: number; fast: number };

  /** Live dictation. `answer`: the spoken answer to a card (no tap: never a note, and a refusal leaves `live` alone). */
  function dictate(answer = false, run: Run = { answer, t0: Date.now(), lastHeard: Date.now(), fast: 0 }): boolean {
    const r = live ? recognizer() : null;
    if (!r) return false;
    claim();
    wanted = true;
    const t = d.text();
    owned = { at: t.length, len: 0, last: t };
    const began = Date.now();
    let started = false;
    let heard = false; // words since this recognizer started
    let instead = false; // it can't work here: a note is recorded instead, once it has ended
    let watch: number | undefined;
    const drop = () => {
      window.clearTimeout(watch);
      r.onresult = r.onerror = r.onstart = null;
      r.onend = () => running.delete(r); // a late end from the browser frees it for the next start
      if (rec === r) rec = null;
      owned = null;
    };
    const notes = () => {
      // live recognition doesn't work in this browser: from now on (and now, unless it was an answer) a recorded note
      live = false;
      drop();
      noMore();
      release();
      show({ state: "idle", say: "" }); // a note shows Recording once its recorder starts; until then (or if it can't) idle
      if (!answer && !gone) void record();
    };
    const over = () => {
      const now = Date.now();
      return now - run.lastHeard > QUIET_STOP_MS || now - run.t0 > DICTATION_MAX_MS || run.fast >= FAST_ENDS;
    };
    const stopped = () => {
      noMore();
      release();
      show({ state: "idle", say: "Stopped listening" });
    };
    // once the browser says the microphone is allowed, the stream is kept while this listens and for HOLD_MS after, so
    // the next tap doesn't ask again (never asked for here when it isn't allowed yet: that would be a second prompt)
    const keepAllowed = () => {
      if (lent || held) return;
      void navigator.permissions
        ?.query({ name: "microphone" as PermissionName })
        .then((p) => (p.state === "granted" && !lent && !gone && rec === r ? takeStream(d.audio) : null))
        .then((stream) => {
          if (!stream) return;
          if (rec === r) lent = stream;
          else giveStream(stream, !gone);
        })
        .catch(() => undefined);
    };
    r.lang = navigator.language || "en-US";
    r.continuous = true;
    r.interimResults = true;
    r.onstart = () => {
      started = true;
      window.clearTimeout(watch);
      keepAllowed();
    };
    r.onresult = (e) => {
      const next = owned && placeSpoken(owned, d.text(), spokenText(e.results));
      if (!next) {
        // the user edited the dictated words: they are theirs now, and dictation ends quietly
        r.onresult = null;
        owned = null;
        noMore();
        r.stop();
        return;
      }
      heard = true;
      run.lastHeard = Date.now();
      run.fast = 0;
      owned = next.owned;
      d.setText(next.text);
    };
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        noMore();
        if (answer) return; // started without a tap (the card's question): the next tap still dictates live
        live = false;
        show({ error: NO_LIVE });
      } else if (CANT_HERE.has(e.error) && !heard) instead = true;
      else if (e.error !== "no-speech" && e.error !== "aborted") show({ error: NOT_HEARD });
    };
    r.onend = () => {
      running.delete(r);
      if (instead) return notes();
      drop();
      if (!heard && Date.now() - began < FAST_END_MS) run.fast++;
      // ended by the browser, not by the person: listen again, quietly, until they stop it (or it has gone on far too
      // long, or kept dying at once)
      if (!(wanted && !gone && live && !over())) return stopped();
      window.clearTimeout(again);
      again = window.setTimeout(() => {
        again = undefined;
        if (!(wanted && !gone && live && dictate(answer, run))) stopped();
      }, RESTART_MS);
    };
    rec = r;
    try {
      running.add(r);
      r.start();
    } catch {
      running.delete(r);
      drop();
      noMore();
      return false;
    }
    // a recognizer that takes start() and never starts (Chromium without its speech service) would leave Listening on
    // for ever: once the mic is known to be allowed (so no permission prompt is open), it gets START_MS to start
    void navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((p) => {
        if (p.state === "granted" && !started && rec === r) watch = window.setTimeout(() => !started && rec === r && (r.abort(), notes()), START_MS);
      })
      .catch(() => undefined);
    show({ state: "listening", error: null, retry: null, say: "Listening" });
    return true;
  }

  async function record() {
    claim();
    const my = ++seq;
    show({ error: null, retry: null });
    const api = d.api();
    const known = limits?.v && Date.now() - limits.at <= ZERO_RECHECK_MS ? limits.v : undefined;
    if (known && noteSeconds(known) < 1) return void (release(), show({ error: USED_UP })); // the mic never turns on
    const [lim, got] = await Promise.allSettled([voiceLimits(api), takeStream(d.audio)]);
    const stream = got.status === "fulfilled" ? got.value : null;
    if (my !== seq || gone) return void (stream && giveStream(stream, !gone)); // the screen closed, or the mic moved on meanwhile
    const fail = (error: string) => {
      if (stream) giveStream(stream, false); // nothing will be recorded: the microphone goes off
      release();
      show({ error });
    };
    if (lim.status === "rejected") return fail(NO_VOICE);
    const cap = noteSeconds(lim.value);
    if (cap < 1) return fail(USED_UP);
    if (!stream) return fail(micOff());
    let r: MediaRecorder;
    try {
      const type = pickType();
      r = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    } catch {
      return fail(NOT_SENT);
    }
    // the size limit too: at the recorder's bitrate (256 kbps when it doesn't say), with room to spare
    const max = Math.min(cap, (lim.value.max_bytes * 8 * 0.9) / (r.audioBitsPerSecond || 256_000));
    const m = { r, stream, chunks: [] as Blob[], t0: -1, timers: [] as number[] };
    media = m;
    r.ondataavailable = (e) => e.data.size && m.chunks.push(e.data);
    r.onstop = () => media === m && void stopNote(); // it stopped by itself (the microphone was taken away): send what it has
    r.onstart = () => {
      m.t0 = performance.now();
      show({ state: "recording", seconds: 0, max: Math.floor(max), say: "Recording" });
      m.timers.push(
        window.setInterval(() => {
          const s = Math.floor((performance.now() - m.t0) / 1000);
          if (s !== view.seconds) show({ seconds: s });
        }, 250),
        window.setTimeout(() => void stopNote(), (max - MARGIN_S) * 1000), // the note stops itself at the limit
      );
    };
    r.start();
  }

  async function stopNote() {
    const m = media;
    if (!m) return;
    media = null;
    m.timers.forEach((t) => (clearInterval(t), clearTimeout(t)));
    const end = performance.now();
    const done = m.r.state === "inactive" ? Promise.resolve() : new Promise<void>((res) => (m.r.onstop = () => res()));
    if (m.r.state !== "inactive") m.r.stop();
    giveStream(m.stream, true); // kept a few minutes (HOLD_MS) so the next tap isn't asked for again, not through the upload
    release();
    show({ state: "sending", say: "Recording stopped. Sending your voice note" });
    await done;
    if (gone) return;
    // its length from the recorder's own start (the server checks it against the audio)
    void send({ blob: new Blob(m.chunks, { type: m.r.mimeType || "audio/webm" }), seconds: m.t0 < 0 ? 0 : (end - m.t0) / 1000 });
  }

  async function send(note: { blob: Blob; seconds: number }) {
    pending = note;
    show({ state: "sending", error: null, retry: null });
    try {
      const { text } = await d.api().voice(note.blob, note.seconds);
      pending = null;
      spent(note.seconds);
      if (!gone && text) d.setText((cur) => joinSpoken(cur, text));
      show({ state: "idle", say: "" });
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) limits = null; // the cap moved: ask again on the next tap
      // the note stays in memory: Try again sends the same one (only when the user asks: never on its own)
      show({ state: "idle", say: "", error: errorWords(e, NOT_SENT), retry: () => void (pending && !gone && send(pending)) });
    }
  }

  return {
    toggle() {
      if (gone) return;
      if (view.state === "listening") return finish();
      if (view.state === "recording") return void stopNote();
      if (view.state === "sending") return;
      if (!dictate()) void record();
    },
    listen() {
      if (gone || view.state !== "idle") return;
      dictate(true);
    },
    stop() {
      // Send pressed: late words must not land back in the emptied box; a note being recorded is sent
      const waiting = again !== undefined;
      noMore();
      if (rec) {
        rec.onresult = null;
        rec.stop();
      } else if (waiting) {
        release(); // between two sessions: nothing will end by itself
        show({ state: "idle", say: "Stopped listening" });
      }
      if (media) void stopNote();
    },
    dispose() {
      if (gone) return;
      gone = true;
      alive--;
      noMore();
      seq++;
      pending = null;
      if (rec) {
        const r = rec;
        r.onresult = r.onerror = r.onstart = null;
        r.onend = () => running.delete(r);
        r.abort();
        rec = null;
      }
      const m = media;
      media = null;
      if (m) {
        m.timers.forEach((t) => (clearInterval(t), clearTimeout(t)));
        m.r.ondataavailable = m.r.onstop = null;
        if (m.r.state !== "inactive") m.r.stop();
        giveStream(m.stream, false);
      }
      release();
      // the last box is gone: the kept microphone is let go soon (a screen change that makes a new box takes it up again)
      if (alive <= 0 && held && held.uses === 0) {
        window.clearTimeout(held.timer);
        held.timer = window.setTimeout(dropHeld, GRACE_MS);
      }
    },
  };
}
