// Read aloud (chat lane phase 4): an answer's plain words: the source labels, Markdown marks, link addresses and code
// blocks are left out. Speech only says words: nothing in them is ever run, followed or sent. One answer at a time;
// Stop, another answer, the chat closing, the page going away or the answer leaving the screen stops it.
// In the person's read-aloud voice (Settings › Personalize › Read aloud: Atlas's voice, POST /v1/voice/speak whole) when
// they have it (the chat sets it: setReadVoice), played by an audio element the tap unlocked (iOS plays nothing
// otherwise); where that voice can't read it (no minutes left, Atlas Shield, the flag, a failure) and for everyone
// else, the browser's own speech ($0). This module is the only place that reads answers aloud.
import { useSyncExternalStore } from "react";

import { plainAnswer } from "../../lib/answerLabels";
import { primedAudio, primeTalk } from "./talk/prime";
import { decode } from "./talk/voices";

const PIECE = 220; // characters per utterance: some browsers stop one long utterance after about 15 s

let speaking: number | null = null; // the answer being read (its message id)
const listeners = new Set<() => void>();
function set(id: number | null) {
  speaking = id;
  for (const f of listeners) f();
}

export function canReadAloud(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof window.SpeechSynthesisUtterance === "function";
}

/** The answer as it is said: plain words in pieces of about PIECE characters, split at sentence ends. */
export function spokenWords(text: string): string[] {
  const plain = plainAnswer(text)
    .replace(/```[\s\S]*?```/g, " (a code block) ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "a link")
    .replace(/^\s{0,3}(?:#{1,6}|[-*+]|\d+[.)]|>)\s+/gm, "")
    .replace(/[*_~|#>]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const out: string[] = [];
  for (const s of plain.match(/[^.!?]+[.!?]*\s*/g) ?? []) {
    const last = out.length - 1;
    if (last >= 0 && out[last].length + s.length <= PIECE) out[last] += s;
    else out.push(s);
  }
  return out.map((s) => s.trim()).filter(Boolean);
}

/** Atlas's voice reading one answer (by its id): its audio. */
export type Reader = (id: number) => Promise<{ audio: string; type: string }>;
let reader: Reader | null = null;
/** The person's read-aloud voice, when they have Atlas's (the chat sets it); null: the browser's own speech. */
export function setReadVoice(next: Reader | null): void {
  reader = next;
}

let run = 0; // each read; an older one's late audio never plays
let audio: { el: HTMLAudioElement; url: string } | null = null;
function dropAudio() {
  if (!audio) return;
  audio.el.onended = audio.el.onerror = null;
  audio.el.pause();
  URL.revokeObjectURL(audio.url);
  audio = null;
}

/** Read this answer aloud (stops whatever was being read first). */
export function readAloud(id: number, text: string): void {
  if (!canReadAloud() && !reader) return;
  const cancelled = stopAll();
  const mine = ++run;
  if (reader) {
    primeTalk(); // in the tap: an audio element iOS lets play once the words come back
    const el = primedAudio();
    if (el) {
      set(id);
      reader(id).then(
        (got) => {
          if (mine !== run || speaking !== id) return;
          const url = URL.createObjectURL(decode(got.audio, got.type));
          audio = { el, url };
          el.src = url;
          el.onended = () => mine === run && (dropAudio(), set(null));
          el.onerror = () => mine === run && (dropAudio(), device(id, text, false));
          el.play().catch(() => mine === run && (dropAudio(), device(id, text, false)));
        },
        () => mine === run && speaking === id && device(id, text, false), // a wall, Atlas Shield, a failure: the device's voice
      );
      return;
    }
  }
  device(id, text, cancelled);
}

/** The browser's own speech. iOS says nothing for a speak() in the same moment as a cancel(), so a cancel happens only
 *  when something is being said (stopAll), and then the words follow a moment later (`cancelled`). */
function device(id: number, text: string, cancelled: boolean): void {
  if (!canReadAloud()) return set(null);
  const synth = window.speechSynthesis;
  const parts = spokenWords(text);
  if (!parts.length) return set(null);
  set(id);
  const mine = run;
  const go = () => {
    if (mine !== run || speaking !== id) return;
    parts.forEach((part, i) => {
      const u = new window.SpeechSynthesisUtterance(part);
      const done = () => {
        if (speaking === id) set(null);
      };
      if (i === parts.length - 1) u.onend = done;
      u.onerror = done;
      synth.speak(u);
    });
  };
  if (cancelled) window.setTimeout(go, AFTER_CANCEL_MS);
  else go();
}
const AFTER_CANCEL_MS = 120;

/** Stops whatever is being read; whether the browser's speech had to be cancelled. */
function stopAll(): boolean {
  run++;
  dropAudio();
  if (!canReadAloud() || !(window.speechSynthesis.speaking || window.speechSynthesis.pending)) return false;
  window.speechSynthesis.cancel();
  return true;
}

/** Stop reading (Stop, the chat closing, the answer leaving the screen). */
export function stopReading(only?: number): void {
  if (speaking === null || (only !== undefined && speaking !== only)) return;
  set(null);
  run++;
  dropAudio();
  if (canReadAloud()) window.speechSynthesis.cancel();
}

/** Read this answer, or stop if it is the one being read (phase 9's L key; the button does the same). */
export function toggleReading(id: number, text: string): void {
  if (speaking === id) stopReading();
  else readAloud(id, text);
}

const subscribe = (f: () => void) => {
  listeners.add(f);
  return () => listeners.delete(f);
};

/** The answer being read now (its id), or null. */
export function useSpeaking(): number | null {
  return useSyncExternalStore(
    subscribe,
    () => speaking,
    () => null,
  );
}
