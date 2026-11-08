// The states of Agent mode's preview (dev only): what each one starts from. See Preview.tsx for the list and how to open one.
import { extras, FIXTURE, saturday } from "../../lib/agentFixture";
import type { RunView } from "../../lib/agentRun";
import type { Voice } from "../../lib/dictation";
import type { Preset } from "./AgentMode";
import type { FakeOptions } from "./fakeApi";

const REQUEST = "Plan Saturday with Sam: find a time, pick lunch, write it up and send it to Sam";
// a long, mixed request: Latin with accents, an emoji and Japanese
const SAMPLE = "Ünïcödé façade très longue 🎉 東京の週末 ";
export const WORST_REQUEST = SAMPLE.repeat(Math.ceil(2000 / SAMPLE.length)).slice(0, 2000);

/** `usage`: this month's AI usage in the preview, in percent (100: used up); none: the owner, with no limit and no line */
export type Setup = { fake?: FakeOptions; from?: Preset; voice?: Voice; usage?: number };

const mic = (state: Voice["state"], seconds = 0): Voice => ({ state, error: null, retry: null, seconds, max: null, say: "", toggle: () => undefined, stop: () => undefined });
const frozen = (v: RunView, from: Preset = {}, fake: FakeOptions = {}): Setup => ({ fake: { script: [v], started: true, latest: saturday("done"), ...fake }, from: { run: v.id, ...from } });
const withWords = (v: RunView, request: string): RunView => ({ ...v, request });
const longTitle = "Ünïcödé façade très longue ".repeat(6).trim();

// the end card by what it will do (the copy table's rows), and its worst case; the question and its worst case
const ready = () => saturday("ready for your ok");
const withCard = (patch: Partial<NonNullable<RunView["card"]>>, over: Partial<RunView> = {}): RunView => {
  const v = { ...ready(), ...over };
  return { ...v, card: { ...(v.card as NonNullable<RunView["card"]>), ...patch } };
};
const changeYou: RunView = {
  ...extras.change_card,
  card: { ...(extras.change_card.card as NonNullable<RunView["card"]>), title: "Make this change?", recipients: [], items: [{ title: "Move lunch to 1:00", sub: "On your calendar only", ref: null }] },
};
const mixed = (): RunView =>
  withCard({
    verb: "mixed",
    title: "Do these for Sam?",
    items: [
      { title: "Add the plan to Saturday", sub: "In your own doc", ref: { kind: "note", id: "n-saturday-plan" } },
      { title: "Email with the plan", sub: "\u201cSaturday with Sam\u201d is attached", ref: null },
    ],
  });
const worstItems = Array.from({ length: 5 }, (_, i) => ({ title: `${i + 1}. ${longTitle}`, sub: longTitle, ref: null }));
const worstCard = (): RunView => withCard({ items: worstItems, recipients: ["Zoë Ünïcödé-Façade", "東京の友だち"], title: `Send to ${longTitle}?` });
const worstChoices = Array.from({ length: 6 }, (_, i) => ({ label: `Sam ${longTitle} ${i}`, sub: `${i}.${longTitle}@example.com`, initial: "S" }));

// a Done answer as a model writes one: bold, a long list, evidence labels (chips), a closing line
const LONG_ANSWER = [
  "Here's tomorrow, all in one place. [Seen in Calendar: tomorrow] Nothing new from the office either. [Seen in Gmail: inbox]",
  "",
  "- **Morning:** Nothing booked before 10:00 AM",
  "- **Calendar:** One reminder at 9:00 AM",
  "- **Deadlines:** The quarterly report is due Thursday at 5:00 PM",
  "- **Calls:** Tuesday and Thursday, 2:00 to 3:30 PM",
  "- **Book club:** Wednesday at 6:00 PM, 45 minutes",
  "- **Gym:** Free from 4:00 to 5:30 PM",
  "- **Travel:** 12 minutes between the office and the gym [My guess]",
  "- **Errands:** The pharmacy closes at 8:00 PM",
  "- **Dinner:** Nothing planned yet",
  "- **Weather:** Light rain after 3:00 PM",
  "- **Reading:** Two chapters left in this month's book",
  "",
  "**Best open day: Wednesday, November\u00a011 [Seen in Calendar: next week]**",
].join("\n");

// a run that plays by itself moves on a little faster than the screen asks (every 2 s), so that each poll brings something new
const PLAY_MS = 1800;

const STATES: Record<string, () => Setup> = {
  ask: () => ({ fake: {} }),
  "ask-last": () => ({ fake: { latest: saturday("done") }, from: { reopen: false } }),
  type: () => ({ fake: {}, from: { typing: true } }),
  "type-filled": () => ({ fake: {}, from: { typing: true, text: REQUEST } }),
  listening: () => ({ fake: {}, from: { typing: true, text: "Plan the dinner on Friday" }, voice: mic("listening") }),
  recording: () => ({ fake: {}, from: { typing: true, text: "" }, voice: mic("recording", 7) }),
  "used-up": () => ({ fake: {}, from: { usedUp: true, text: REQUEST }, usage: 100 }),
  // AI usage at 78%: the thin line by the input (D241), never amber
  "ask-usage": () => ({ fake: {}, usage: 78 }),
  "type-usage": () => ({ fake: {}, from: { typing: true, text: REQUEST }, usage: 78 }),
  planning: () => frozen(saturday("planning")),
  queued: () => frozen(saturday("queued")),
  "queued-slow": () => frozen(saturday("queued"), { queuedMs: 3000 }),
  plan: () => frozen(saturday("plan")),
  run: () => frozen(saturday("calendar done")),
  found: () => frozen(saturday("places done")),
  // the found beat itself: the last step finishes between two polls, and its line is held for a moment (take the picture the
  // moment "Picked Tandoor House" is on show). The second poll comes by itself, about two seconds in
  "found-beat": () => ({ fake: { script: [saturday("calendar done"), saturday("places done")], started: true, auto: 1500, latest: saturday("done") }, from: { run: saturday("places done").id } }),
  // the peek of a step: a doc with its Open doc, and a place with its Google Maps link
  peek: () => frozen(saturday("docs done"), { peek: 2 }),
  "peek-place": () => frozen(saturday("docs done"), { peek: 1 }),
  // the peek of the step that held the calls, on Done: what went out and to whom (never "ready, nothing has gone out yet"), over the room of the hint
  "done-peek": () => frozen(saturday("done"), { peek: 3 }),
  "ask-card": () => frozen(saturday("which sam")),
  which: () => frozen(saturday("which sam")),
  "which-worst": () => frozen(withCard({ kind: "ask", verb: null, title: `Which ${longTitle}?`, items: [], choices: worstChoices, contacts: true, email: false, recipients: [], plan_sha: null }, saturday("which sam"))),
  ready: () => frozen(ready()),
  // Ready for your OK with its card shut: the status line reopens it
  "ready-closed": () => frozen(ready(), { closedCard: ready().card?.id }),
  end: () => frozen(ready()),
  "end-change": () => frozen(extras.change_card),
  "end-change-you": () => frozen(changeYou),
  "end-do": () => frozen(extras.do_card),
  "end-mixed": () => frozen(mixed()),
  "end-wide": () => frozen(ready(), { layout: "wide" }),
  "end-worst": () => frozen(worstCard()),
  "after-yes": () => frozen(saturday("after the yes")),
  sent: () => frozen(saturday("sent")),
  done: () => frozen(saturday("done")),
  undo: () => frozen(saturday("done"), { undo: true }, { undo: "one" }),
  "undo-mixed": () => frozen(saturday("done"), { undo: true }, { undo: "mixed" }),
  plain: () => frozen(FIXTURE.plain[FIXTURE.plain.length - 1].view),
  // a Done answer written in markdown (bold, a list), shown as chat shows it
  "done-markdown": () =>
    frozen({
      ...FIXTURE.plain[FIXTURE.plain.length - 1].view,
      title: "Tomorrow at a glance",
      done: { title: "Tomorrow at a glance", line: "One reminder, and a free afternoon.", answer: LONG_ANSWER },
    }),
  "done-no-steps": () => frozen(FIXTURE.plain[FIXTURE.plain.length - 1].view), // (the same as plain, by the plan's name)
  paused: () => frozen(extras.paused),
  failed: () => frozen(extras.failed),
  "failed-changed": () => frozen(extras.failed_changed),
  offline: () => frozen(saturday("calendar done"), { offline: true }),
  capped: () => frozen(extras.capped),
  // a run that outgrew one go (its steps or time, after its slices): what finished is ticked, the rest is as it was,
  // the plain line, and Keep going (the rest as a new run)
  "capped-size": () => {
    const v = saturday("docs done");
    return frozen({
      ...v,
      status: "capped",
      current: null,
      steps: v.steps.map((st) => (st.state === "done" ? st : { ...st, state: "ghost" as const })),
      done: { title: "Saturday's plan", line: "I stopped here: that was a lot for one go. What's done is saved.", answer: null },
      undo: { available: true },
    });
  },
  "not-sent": () => frozen(extras.not_sent_saved),
  stopped: () => frozen(extras.stopped),
  worst: () => frozen(withWords(saturday("places done"), WORST_REQUEST)),
  "worst-type": () => ({ fake: {}, from: { typing: true, text: WORST_REQUEST } }),
  "worst-done": () => frozen({ ...saturday("done"), done: { title: longTitle, line: "Ünïcödé façade très longue ".repeat(12).trim(), answer: null } }),
  // a tablet held upright: the phone's stage with labels under the nodes and bigger nodes (the window decides it; this
  // state says so for itself, so it is meant for a window about 820 wide)
  tablet: () => frozen(saturday("places done"), { layout: "tablet" }),
  play: () => ({ fake: { auto: PLAY_MS, latest: saturday("done") }, from: { reopen: false } }),
  // the same, for a question that needs no app (no steps; the answer comes inline on Done)
  "play-plain": () => ({ fake: { auto: PLAY_MS, script: FIXTURE.plain.map((s) => s.view), latest: null }, from: { reopen: false } }),
};

export const AGENT_PREVIEW_STATES = Object.keys(STATES);
export const setupOf = (state: string): Setup => (STATES[state] ?? STATES.ask)();
