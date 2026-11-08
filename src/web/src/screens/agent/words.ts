// Every word Agent mode says (the plan's copy table, exactly): plain, calm, no blame, no dot strings. The screens and the
// stage logic take their words from here and nowhere else, so a changed word is changed once and words.test.ts checks
// it against the table. Counts from one to nine are words in a sentence ("Two things to send"); on buttons they stay
// numerals ("Undo 2 things"). The step greeting on Ask (GREETING) lives with Home's own words.
import type { AppKind, RunCard } from "../../lib/agentRun";

type Verb = NonNullable<RunCard["verb"]> | null;

const COUNT_WORDS = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];
/** 1 → "One" … 9 → "Nine", then numerals (for the start of a sentence) */
export const countWord = (n: number): string => (n >= 1 && n <= 9 ? COUNT_WORDS[n] : String(n));

/** the name of an app in "In {App}" and "Couldn't reach {App}" */
export const APP_NAME: Record<AppKind, string> = {
  calendar: "Calendar",
  places: "Places",
  docs: "Docs",
  sheets: "Sheets",
  mail: "Mail",
  tasks: "Tasks",
  people: "Contacts",
  web: "the web",
  pictures: "Pictures",
  notes: "Notes",
  files: "Files",
  other: "Atlas",
};
export const inApp = (app: AppKind): string => `In ${APP_NAME[app]}`;

// Ask
export const ASK_LINE = "What should I take care of?";
export const TALK = "Talk";
export const TYPE = "Type";
export const lastRun = (title: string): string => `Last run: ${title}`;
export const FIELD_PLACEHOLDER = "Type what you need";
export const FIELD_HINT = "Press ↵ to start. In an empty field, hold Space to talk.";
export const TALK_INSTEAD = "Talk instead";
export const START = "Start";
export const LISTENING = "Listening";
export const STOP_LISTENING = "Stop";
// The month's used-up words are the shared wall's (lib/usage WALL, with Ask for more, exactly as in chat; D241). A day's wall
// comes in the server's own sentence and is said as it came.
/** with nothing to start, the way on (the switch at the top says it too) */
export const OPEN_FULL_APP = "Open the full app";

// Run
export const MAKING_A_PLAN = "Making a plan";
/** the phone face's island once every step is done and the run is writing up what it did (Done comes with the run) */
export const FINISHING_UP = "Finishing up";
export const YOURE_NEXT = "You're next";
export const STARTING_SOON = "Starting in a moment.";
export const LEAVE_LINE = "You can leave. I'll only buzz you if I need you.";
export const PAUSE = "Pause";
export const RESUME = "Resume";
export const STOP = "Stop";
export const READY = "Ready for your OK";

export const PAUSED = "Paused";
export const PAUSED_SUB = "Nothing more happens until you resume.";
export const PAUSED_FOOTER = "Stop keeps what's done. You can undo it after.";
export const TRY_AGAIN = "Try again";
export const SKIP_IT = "Skip it";
/** back from a read-only look at one item of the end card to the card (the copy table has no word for it: the one plain verb) */
export const BACK = "Back";
export const OFFLINE_STATUS = "You're offline";
export const OFFLINE_SUB = "I'm still working. This updates when you're back.";
export const CAPPED_LINE = "I stopped here to save your AI usage.";
/** the server's line for a run that outgrew one go (its steps or time): not the money (agentmode.LIMIT_LINE) */
export const LIMIT_LINE = "I stopped here: that was a lot for one go.";

/** the key hints on a wide screen (Kbd boxes, one per word) */
export const KEYS = { approve: "⌘ ↵", pause: "Space", done: "↵" } as const;

export function failedWords(app: AppKind, changed: boolean): { status: string; sub: string } {
  return { status: `Couldn't reach ${APP_NAME[app]}`, sub: changed ? "Part of it was saved. Try again, or skip it." : "Nothing was changed. Try again, or skip it." };
}

// The end card (what waits for one OK), by what it will do
const things = (verb: Verb, n: number): string => {
  if (verb === "send") return n === 1 ? "thing to send" : "things to send";
  if (verb === "change") return n === 1 ? "change to make" : "changes to make";
  return n === 1 ? "thing to do" : "things to do";
};
/** "Ready for your OK" is followed by this: "Two things to send", "One change to make", "Three things to do" */
export const readySub = (card: RunCard): string => `${countWord(card.items.length)} ${things(card.verb, card.items.length)}`;

/** who it concerns, in a title: "Sam", "Sam and Priya", "3 people" */
function names(who: string[]): string {
  if (who.length <= 1) return who[0] ?? "";
  return who.length === 2 ? `${who[0]} and ${who[1]}` : `${who.length} people`;
}
export const EMAIL_LINE = "Sent email can't be unsent.";
/** the button: one, both, all {n} (numerals on buttons) */
const many = (one: string, both: string, all: string, n: number): string => (n <= 1 ? one : n === 2 ? both : `${all} ${n}`);

export function endCardWords(card: RunCard): { title: string; lead: string[]; button: string } {
  const n = card.items.length;
  const who = card.recipients;
  const first = who[0] ?? "";
  if (card.verb === "change") {
    const button = many("Change it", "Change both", "Change all", n);
    if (!who.length) return { title: "Make this change?", lead: ["Nothing changes until you say yes."], button };
    return { title: `Change it for ${names(who)}?`, lead: [`Nothing changes until you say yes. ${who.length === 1 ? first : "They"} will see the change.`], button };
  }
  if (card.verb === "do") return { title: "Do this now?", lead: ["Nothing happens until you say yes."], button: many("Do it", "Do both", "Do all", n) };
  if (card.verb === "mixed") {
    return { title: who.length === 1 ? `Do these for ${first}?` : who.length ? `Do these for ${names(who)}?` : "Do these now?", lead: ["Nothing happens until you say yes.", ...(card.email ? [EMAIL_LINE] : [])], button: many("Do it", "Do both", "Do all", n) };
  }
  return { title: who.length === 1 ? `Send to ${first}?` : "Send these?", lead: ["Nothing goes out until you send.", ...(card.email ? [EMAIL_LINE] : [])], button: many("Send", "Send both", "Send all", n) };
}

/** what the status says from the tap of the yes until the run says what became of it (the person's yes is taken at once; the
 *  rest takes a moment, and the card's own "Ready for your OK" is never shown again after a yes) */
export function sendingWords(verb: Verb): string {
  if (verb === "change") return "Changing…";
  if (verb === "do") return "Doing it…";
  return verb === "mixed" ? "Working on it…" : "Sending…";
}

/** what the status says after the yes, until Done */
export function afterYes(verb: Verb, recipients: string[]): { status: string; sub: string } {
  if (verb === "change") return { status: "Changed", sub: recipients.length ? (recipients.length === 1 ? `${recipients[0]} gets the update.` : "Everyone gets the update.") : "" };
  if (verb === "do" || verb === "mixed") return { status: "All set", sub: "" };
  return { status: "Sent", sub: recipients.length === 1 ? `It's on its way to ${recipients[0]}.` : "It's on its way." };
}

/** what it says after a no, or a card that ran out: by the card's verb; the sub only when a change was already made earlier in the run */
export function afterNo(verb: Verb, wrote: boolean): { status: string; sub: string } {
  const status = verb === "send" ? "Nothing was sent" : verb === "change" ? "Nothing was changed" : "Nothing happened";
  return { status, sub: wrote ? "The rest is saved." : "" };
}

// Done
export const tapAStep = (wide: boolean): string => (wide ? "Click a step to open it" : "Tap a step to open it");
export const UNDO_LINK = "Undo…";
/** a run that stopped on a limit: the rest of the request as a new run */
export const KEEP_GOING = "Keep going";
export const DONE = "Done";

// The undo panel
export const UNDO_TITLE = "Undo what I can?";
export const undoLead = (n: number, emailSent: boolean): string => `${n === 1 ? "One thing goes back." : `${countWord(n)} things go back.`}${emailSent ? " The email can't be unsent." : ""}`;
/** The rows' words are the server's (mcp lifeos.agentmode UNDO_NOTE, kept equal by its test). The first four are the copy table's;
 *  the other three say the rest honestly: an edit that goes back, a change the person edited since, and what can't be undone here. */
export const UNDO_ROW = { reverts: "Moved to Trash for 30 days.", removed: "Removed.", sent: "Already sent.", guests: "Has guests, so it stays.", restored: "Put back as it was.", edited: "Changed since, so it stays.", cannot: "Can't be undone here." } as const;
export const NEVER_MIND = "Write a “never mind”";
export const OPEN_IN_CALENDAR = "Open in Google Calendar";
export const undoButton = (n: number): string => (n === 1 ? "Undo it" : `Undo ${n} things`);

// The Which panel
export const REMEMBER = "I'll remember for next time.";
export const SOMEONE_ELSE = "Someone else";
export const NAME_OR_EMAIL = "Name or email";
/** the button beside that field (the copy table has no word for it: the one plain verb for taking what was typed) */
export const USE_THIS = "Use this";

// The phone face (LIFEOS_PHONE_VOICE): one speak button, then actions. The island at the thumb says only what a tap does now.
export const TAP_TO_SPEAK = "Tap to speak";
export const TYPE_INSTEAD = "Type instead";
export const CANCEL = "Cancel";
export const NOT_NOW = "Not now";
export const REVIEW = "Review";
export const NOTHING_HEARD = "I didn't hear anything. Tap to try again, or type it.";
export const WRITING_DOWN = "Writing down what you said…";
export const WAITING_FOR_YOU = "Waiting for your answer";
export const UP_NEXT = "Up next";
export const PLAN_NOTE = "I'll ask before anything is sent.";
/** the count line under the request: only the plan's real steps ("Two things. I'll ask before anything is sent.") */
export const planNote = (n: number): string => `${countWord(n)} ${n === 1 ? "thing" : "things"}. ${PLAN_NOTE}`;
/** the island while Atlas works: only what is still running is counted, never what is done or waits */
export const workingOn = (n: number): string => (n <= 1 ? "Working on it" : `Working on ${countWord(n).toLowerCase()} things`);
/** the card's one button on a phone: the person's name when one email goes to one person ("Send to Sam") */
export function phoneSendWords(card: RunCard): string {
  const words = endCardWords(card);
  return card.verb === "send" && card.items.length === 1 && card.recipients.length === 1 ? `Send to ${card.recipients[0]}` : words.button;
}

// The phone face's spoken ask (the card is on screen at the same time; a tap works as always). The question never holds
// a word that could be heard as a yes ("send", "go ahead", "do it", "okay"…), so Atlas's own voice coming back through the
// mic can never answer it (voiceAnswer.ts; tested for every part of every question).
/** what Atlas asks out loud when the card opens */
export function askAloud(card: RunCard): string {
  const who = card.recipients;
  if (card.verb === "send") return who.length === 1 && card.items.length === 1 ? `It's ready for ${who[0]}. Should it go now?` : "These are ready. Should they go now?";
  if (card.verb === "change") return "The change is ready. Should I make it?";
  return "It's all ready. Should I start?";
}
/** the one line on the island while it listens for the answer (the button's own words, so the two always match) */
export const voiceHint = (card: RunCard): string => `Say “${card.verb === "send" ? "send it" : "yes"}”, or tap ${phoneSendWords(card)}.`;
export const ANSWER_BY_VOICE = "Answer by voice";
/** the line once it no longer listens (Cancel, a no to the voice, the app left): only the tap */
export const tapHint = (card: RunCard): string => `Tap ${phoneSendWords(card)} when you're ready.`;
/** the countdown after a spoken yes (Cancel stops it) */
export function goingIn(verb: Verb, n: number): string {
  if (verb === "send") return `Sending in ${n}`;
  if (verb === "change") return `Changing in ${n}`;
  return `Going ahead in ${n}`;
}
export const HEARD = (words: string): string => `Heard “${words}”`;
