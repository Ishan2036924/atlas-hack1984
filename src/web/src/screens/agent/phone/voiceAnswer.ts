// The spoken answer to the end card on the phone face (Ishan, 2026-11-07: "it can take approval by asking … give both
// options"): Atlas asks out loud while the card is on screen, and the person can tap Send or say it. This is the only
// place a word can lead to a yes, so it is narrow on purpose:
//   - only while that card is open, only the person's own words heard on this device, matched here by a fixed rule
//     (never by the model, never from anything the run read);
//   - the mic opens only after Atlas has finished asking, and words that are only Atlas's own question (an echo from the
//     speaker) are not an answer; the question itself never holds a yes word (words.ts askAloud, tested);
//   - a short, plain yes only ("send it", "yes", "go ahead"; at most five words); any "no", "not", "don't", "wait",
//     "cancel" or "stop" anywhere means no;
//   - a yes never sends at once: what was heard shows first, then a countdown with Cancel (on the wall clock, a second a
//     number), and it keeps listening, so any other word stops it; then the same signed approval as the tap.
export type Heard = "yes" | "no" | null;

export const COUNTDOWN_S = 3;
/** how long "Heard “send it”" shows before the countdown starts */
export const HEARD_MS = 800;
const MAX_WORDS = 5;
const NO_ANYWHERE = /\b(no|nope|not|don'?t|do not|wait|cancel|stop|never)\b/;
const YES_START = /^(yes|yeah|yep|yup|sure|okay|ok|send|go ahead|do it|change it|please send)\b/;

const plain = (words: string): string =>
  words
    .toLowerCase()
    .replace(/[^a-z' ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/** the words are only (part of) what Atlas asked: its own voice coming back through the mic */
export function echoOf(words: string, asked: string): boolean {
  const w = plain(words);
  return w !== "" && ` ${plain(asked)} `.includes(` ${w} `);
}

/** what the words heard so far say: a yes, a no, or nothing yet (`asked`: Atlas's question, never an answer) */
export function answerOf(words: string, asked = ""): Heard {
  const w = plain(words);
  if (!w || (asked && echoOf(w, asked))) return null;
  if (NO_ANYWHERE.test(w)) return "no";
  if (w.split(" ").length > MAX_WORDS) return null;
  return YES_START.test(w) ? "yes" : null;
}
