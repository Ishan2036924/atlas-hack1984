// How the judge scored an agent's runs (the learning loop; GET /v1/agents/{id}/scores, lifeos.judge rubric r1), read
// into plain fields, and the calm words the agent's page shows. Scores and reason codes only: the judge never reads a
// run's content, and nothing here names an amount (D241). Saying no is the person in charge, never the agent's defect:
// a "no" shows as what the agent is learning, never as something to fix.

export type ScoreLine = { run_id: number; score: number; safety_ok: boolean; reasons: string[] };
export type RunScores = {
  window_days: number;
  judged: number;
  average: number | null;
  unsafe: number;
  common: { reason: string; runs: number }[];
  last: ScoreLine[];
};

/** below this many judged runs it's too early to tell, so the page says nothing about scores */
export const MIN_JUDGED = 3;

const obj = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

export function toRunScores(raw: unknown): RunScores {
  const o = obj(raw);
  return {
    window_days: num(o.window_days, 28),
    judged: num(o.judged),
    average: typeof o.average === "number" && Number.isFinite(o.average) ? o.average : null,
    unsafe: num(o.unsafe),
    common: (Array.isArray(o.common) ? o.common : []).map(obj).filter((c) => typeof c.reason === "string").map((c) => ({ reason: c.reason as string, runs: num(c.runs) })),
    last: (Array.isArray(o.last) ? o.last : [])
      .map(obj)
      .filter((r) => typeof r.run_id === "number")
      .map((r) => ({ run_id: r.run_id as number, score: num(r.score), safety_ok: r.safety_ok !== false, reasons: strs(r.reasons) })),
  };
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;

// what can get better, said about the agent; the person's own "no" and the safety codes are never here
const IMPROVE: Record<string, string> = {
  tried_blocked_action: "It tried something it isn't allowed to",
  did_not_finish: "It didn't finish",
  stopped: "It was stopped",
  tool_errors: "A step failed",
  claimed_done_on_failure: "It said done when it wasn't",
  over_run_cap: "It used more AI than a run allows",
  too_many_steps: "It took more steps than a run allows",
  too_slow: "It took longer than a run allows",
  thumbs_down: "A run wasn't right for you",
  big_edit: "Its drafts needed your edits",
};
// what a "no" teaches it, by the reason the person gave
const LEARNING: Record<string, string> = {
  wrong_person: "It's learning who to send things to.",
  wrong_time: "It's learning a better time to ask.",
  wrong_words: "It's learning how you like things worded.",
  wrong_amount: "It's learning the amounts you expect.",
  not_needed: "It's learning what you don't need.",
};

// why you said no, as the chips say it (NO_REASONS in lifeos.learnsignals; not_now and not_safe stay server codes)
export const NO_WORDS: Record<string, string> = {
  wrong_person: "Wrong person",
  wrong_time: "Wrong time",
  wrong_words: "Wrong words",
  wrong_amount: "Wrong amount",
  not_needed: "Not needed",
  other: "Something else",
};

/** the reasons that fit the action (at most four, then "Something else"): an email isn't the wrong amount */
export function reasonsFor(kind: string | undefined): string[] {
  const fit: Record<string, string[]> = {
    email: ["wrong_person", "wrong_time", "wrong_words", "not_needed"],
    slack: ["wrong_person", "wrong_time", "wrong_words", "not_needed"],
    invite: ["wrong_person", "wrong_time", "not_needed"],
    event: ["wrong_person", "wrong_time", "not_needed"],
    payment: ["wrong_amount", "wrong_time", "not_needed"],
  };
  return [...(fit[kind ?? ""] ?? ["wrong_time", "wrong_words", "not_needed"]), "other"];
}

/** "Going well" / "Mostly well" / "Could be better"; null until MIN_JUDGED runs were judged (too early to tell) */
export function howItRan(s: RunScores): string | null {
  if (s.judged < MIN_JUDGED || s.average === null) return null;
  const avg = Math.round(s.average);
  return avg >= 85 ? "Going well" : avg >= 65 ? "Mostly well" : "Could be better";
}

/** the score, one tap down: "86 of 100 over 12 runs in the last 4 weeks" */
export function scoreDetail(s: RunScores): string | null {
  if (s.judged < MIN_JUDGED || s.average === null) return null;
  const weeks = Math.round(s.window_days / 7);
  return `${Math.round(s.average)} of 100 over ${plural(s.judged, "run")} in the last ${weeks === 1 ? "week" : `${weeks} weeks`}`;
}

/** asking first: said only for an agent that has something to ask about (an ask-first tool) */
export function safetyLine(s: RunScores, asksFirst: boolean): string | null {
  if (s.judged < MIN_JUDGED) return null;
  if (s.unsafe > 0) return `${plural(s.unsafe, "run")} to look at: it acted without a yes on record.`;
  return asksFirst ? "Asked you first every time." : null;
}

/** the most common thing to get better at, never your own "no"; null when nothing came up */
export function toImprove(s: RunScores): string | null {
  const top = s.common.find((c) => IMPROVE[c.reason]);
  return top ? `${IMPROVE[top.reason]} ${top.runs === 1 ? "once" : `in ${plural(top.runs, "run")}`}` : null;
}

// the same, as one calm line under the verdict when nothing else is said there
const WORKING_ON: Record<string, string> = {
  tried_blocked_action: "It sometimes tries things it isn't allowed to.",
  did_not_finish: "Some runs don't finish.",
  stopped: "Some runs were stopped.",
  tool_errors: "A step sometimes fails.",
  claimed_done_on_failure: "It sometimes says done too early.",
  over_run_cap: "Some runs use more AI than they're allowed.",
  too_many_steps: "Some runs take more steps than they're allowed.",
  too_slow: "Some runs take longer than they're allowed.",
  thumbs_down: "Some runs weren't right for you.",
  big_edit: "Its drafts sometimes need your edits.",
};

/** "A step sometimes fails. It's working on that."; null when nothing came up */
export function workingOn(s: RunScores): string | null {
  const top = s.common.find((c) => WORKING_ON[c.reason]);
  return top ? `${WORKING_ON[top.reason]} It's working on that.` : null;
}

/** what your "no"s are teaching it, from the most common reason you gave; null without one */
export function learningLine(s: RunScores): string | null {
  const top = s.common.find((c) => c.reason.startsWith("no_") && LEARNING[c.reason.slice(3)]);
  return top ? LEARNING[top.reason.slice(3)] : null;
}
