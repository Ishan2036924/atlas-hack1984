// Agent mode's read model (AM-2 started it with the shapes the strip and the peek draw; AM-3 adds the API client and
// the hook that follows a run). These types are the contract both sides test against
// (mcp/tests/fixtures/agent_mode_view.json): change them only together with the server's `view_of`.
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, errorOf } from "./api";
import { type Card, cardOf } from "./approval";
import { withoutLabels } from "./markdown";
import { headersFor, resilientFetch } from "./net";

export type AppKind = "calendar" | "places" | "docs" | "sheets" | "mail" | "tasks" | "people" | "web" | "pictures" | "notes" | "files" | "other";
export type StepState = "ghost" | "now" | "wait" | "fail" | "done" | "paused" | "skipped";
export type ItemRef = { kind: "workbook" | "note" | "table"; id: string } | { kind: "event" | "map"; url: string };

export type RunStep = {
  id: string;
  app: AppKind;
  label: string;
  now: string;
  found: string | null;
  detail: string | null;
  state: StepState;
  ref: ItemRef | null;
};

export type RunCard = {
  id: string;
  kind: "end" | "ask";
  verb: "send" | "change" | "do" | "mixed" | null;
  title: string;
  items: { title: string; sub: string; ref: ItemRef | null }[];
  choices: { label: string; sub: string | null; initial: string }[] | null;
  contacts: boolean;
  email: boolean;
  recipients: string[];
  expires_at_ms: number;
  plan_sha: string | null;
};

export type RunView = {
  id: number;
  rev: number;
  status: "queued" | "running" | "waiting" | "paused" | "failed" | "done" | "stopped" | "capped";
  request: string;
  title: string;
  steps: RunStep[];
  current: number | null;
  card: RunCard | null;
  decision: "sent" | "changed" | "not_sent" | "partial" | null;
  failure: { app: AppKind; changed: boolean } | null;
  done: { title: string; line: string; answer: string | null } | null;
  undo: { available: boolean };
};

const STATUSES: ReadonlyArray<RunView["status"]> = ["queued", "running", "waiting", "paused", "failed", "done", "stopped", "capped"];
const STATES: ReadonlyArray<StepState> = ["ghost", "now", "wait", "fail", "done", "paused", "skipped"];
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** whether an answer from the server really has the shape of a RunView (a poll is checked before it is drawn) */
/** A run's one-line words without evidence labels (the model writes "[Seen in Calendar: tomorrow]" after most facts):
 *  a step's row, the Done title and line are plain words with no room for a chip, so the label is dropped there; the
 *  Done answer keeps them (it is drawn as chat's answer is, the labels as chips). */
export function plainLines(v: RunView): RunView {
  const one = (s: string | null) => (s === null ? s : withoutLabels(s));
  return {
    ...v,
    title: withoutLabels(v.title),
    steps: v.steps.map((s) => ({ ...s, now: withoutLabels(s.now), found: one(s.found), detail: one(s.detail) })),
    done: v.done ? { ...v.done, title: withoutLabels(v.done.title), line: withoutLabels(v.done.line) } : v.done,
  };
}

export function isRunView(v: unknown): v is RunView {
  if (!isObj(v) || typeof v.id !== "number" || typeof v.rev !== "number" || !STATUSES.includes(v.status as RunView["status"])) return false;
  if (typeof v.request !== "string" || typeof v.title !== "string" || !Array.isArray(v.steps)) return false;
  if (!v.steps.every((s) => isObj(s) && typeof s.id === "string" && typeof s.label === "string" && typeof s.now === "string" && STATES.includes(s.state as StepState))) return false;
  if (v.current !== null && typeof v.current !== "number") return false;
  return isObj(v.undo) && typeof v.undo.available === "boolean";
}

export type StartResult = { id: number; busy: boolean };
/** What "Undo…" on Done would do, thing by thing (GET …/runs/{id}/undo; the shared fixture mcp/tests/fixtures/agent_mode_undo.json).
 *  reverts: it goes back when the person taps. stays: an event with guests (its link opens Google Calendar) or a change they
 *  edited since. cannot: a sent email (`never_mind` is the words for their own field, never sent on their behalf) and anything
 *  an outside app did. `count` is how many revert; `email` says an email went out (the lead then says it can't be unsent). */
export type UndoItem = { step_id: string; title: string; effect: "reverts" | "stays" | "cannot"; note: string; link: ItemRef | null; never_mind: string | null };
export type UndoPreview = { items: UndoItem[]; count: number; email: boolean };

const EFFECTS: ReadonlyArray<UndoItem["effect"]> = ["reverts", "stays", "cannot"];
const isRef = (v: unknown): v is ItemRef => isObj(v) && typeof v.kind === "string" && (typeof v.id === "string" || typeof v.url === "string");

/** whether an answer from the server really has the shape of an UndoPreview (it is checked before it is drawn) */
export function isUndoPreview(v: unknown): v is UndoPreview {
  if (!isObj(v) || !Array.isArray(v.items) || typeof v.count !== "number" || typeof v.email !== "boolean") return false;
  return v.items.every((i) => isObj(i) && typeof i.step_id === "string" && typeof i.title === "string" && typeof i.note === "string" && EFFECTS.includes(i.effect as UndoItem["effect"]) && (i.link === null || isRef(i.link)) && (i.never_mind === null || typeof i.never_mind === "string"));
}

/** What one held item of the end card will send, exactly (GET …/runs/{id}/items/{n}; the shared fixture
 *  mcp/tests/fixtures/agent_mode_held.json): the call's own arguments laid out by kind (an email's to, subject and body). */
export type HeldItem = { card: Card };

/** The routes Agent mode uses (GET /v1/agent-mode/runs/latest, GET and POST under /v1/agent-mode/runs/{id}). */
export type AgentModeApi = {
  /** a new request; busy: one was already going, and `id` is that one (L10) */
  start(text: string): Promise<StartResult>;
  /** the newest run of the last 7 days, or none */
  latest(): Promise<RunView | null>;
  /** the run, or `unchanged` when the rev sent is still the newest (one cheap read on the server) */
  view(id: number, rev?: number): Promise<RunView | { unchanged: true }>;
  pause(id: number): Promise<void>;
  resume(id: number): Promise<void>;
  stop(id: number): Promise<void>;
  resolve(id: number, action: "retry" | "skip"): Promise<void>;
  /** Keep going: a run that stopped on a limit (capped) goes on as a new run for the same request; its id */
  keepGoing?(id: number): Promise<{ id: number }>;
  undoPreview(id: number): Promise<UndoPreview>;
  undo(id: number): Promise<void>;
  /** one held item of the card the run waits on, as it will go out (read-only) */
  heldItem(id: number, n: number): Promise<HeldItem>;
  /** the phone face's spoken ask: the server's question for the end card that waits, in the person's read-aloud voice
   *  (absent: the device's voice asks, as before) */
  askAloud?(id: number, cardId: string): Promise<{ text: string; audio: string; type: string }>;
};

type Deps = { token: () => Promise<string | null>; fetch?: typeof fetch; base?: string };
const ROOT = "/v1/agent-mode/runs";
/** the 409s of a control that a person is told (agentmode.Conflict): the run cannot be picked up, another request is going, there is nothing to skip */
const SAID_409 = new Set(["cannot_resume", "busy", "nothing_to_skip"]);

export function createAgentModeApi({ token, fetch: doFetch = resilientFetch, base = "" }: Deps): AgentModeApi {
  async function send(path: string, init: RequestInit = {}, accept: number[] = []): Promise<Response> {
    const headers = headersFor(init);
    const tok = await token();
    if (tok) headers.set("authorization", `Bearer ${tok}`);
    if (init.body) headers.set("content-type", "application/json");
    const res = await doFetch(`${base}${path}`, { ...init, headers });
    if (!res.ok && !accept.includes(res.status)) throw await errorOf(res, headers);
    return res;
  }
  const post = (path: string, body?: unknown) => send(path, { method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const read = async <T>(path: string): Promise<T> => (await send(path)).json() as Promise<T>;
  /** One of the person's buttons. A 409 means the run is not in the state the tap meant, and mostly that is because
   *  another device got there first: that is no error (the next look shows where the run is). Three say why it cannot go
   *  on, in words the server wrote for the person, and those are said. */
  async function control(path: string, body?: unknown): Promise<void> {
    const res = await send(path, { method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) }, [409]);
    if (res.status !== 409) return;
    const said: unknown = await res.json().catch(() => null);
    const code = isObj(said) && typeof said.code === "string" ? said.code : "";
    const words = isObj(said) && typeof said.detail === "string" ? said.detail : "";
    if (SAID_409.has(code) && words) throw new ApiError(409, words, [], words);
  }
  return {
    async start(text) {
      const res = await send(ROOT, { method: "POST", body: JSON.stringify({ text }) }, [409]);
      const body: unknown = await res.json().catch(() => null);
      const holder = isObj(body) && isObj(body.detail) ? body.detail : isObj(body) ? body : {};
      if (typeof holder.id !== "number") throw new ApiError(res.status, "Couldn't start that. Try again.");
      return { id: holder.id, busy: res.status === 409 };
    },
    async latest() {
      const body = await read<{ run: unknown }>(`${ROOT}/latest`);
      return isRunView(body.run) ? plainLines(body.run) : null;
    },
    async view(id, rev) {
      const body = await read<unknown>(rev === undefined ? `${ROOT}/${id}` : `${ROOT}/${id}?rev=${rev}`);
      if (isObj(body) && body.unchanged === true) return { unchanged: true };
      if (!isRunView(body)) throw new ApiError(502, "Couldn't read that run.");
      return plainLines(body);
    },
    pause: (id) => control(`${ROOT}/${id}/pause`),
    resume: (id) => control(`${ROOT}/${id}/resume`),
    stop: (id) => control(`${ROOT}/${id}/stop`),
    resolve: (id, action) => control(`${ROOT}/${id}/resolve`, { action }),
    async keepGoing(id) {
      const body: unknown = await (await post(`${ROOT}/${id}/continue`, {})).json();
      if (!isObj(body) || typeof body.id !== "number") throw new ApiError(502, "Couldn't read that.");
      return { id: body.id };
    },
    async undoPreview(id) {
      const body = await read<unknown>(`${ROOT}/${id}/undo`);
      if (!isUndoPreview(body)) throw new ApiError(502, "Couldn't read that.");
      return body;
    },
    async undo(id) {
      await post(`${ROOT}/${id}/undo`);
    },
    async heldItem(id, n) {
      const body = await read<unknown>(`${ROOT}/${id}/items/${n}`);
      const card = isObj(body) ? cardOf(body.card) : undefined; // checked field by field: an unexpected card is not drawn
      if (!card) throw new ApiError(502, "Couldn't read that.");
      return { card };
    },
    async askAloud(id, cardId) {
      const body: unknown = await (await post(`${ROOT}/${id}/ask-aloud`, { card_id: cardId })).json();
      if (!isObj(body) || typeof body.text !== "string" || typeof body.audio !== "string" || typeof body.type !== "string") throw new ApiError(502, "Couldn't read that.");
      return { text: body.text, audio: body.audio, type: body.type };
    },
  };
}

const WORKING_MS = 2000; // while Atlas works
const WAITING_MS = 5000; // while it waits on the person (a card, a pause, a failed step)
const QUIET_MS = 10_000; // once nothing has changed for QUIET_AFTER_MS
const QUIET_AFTER_MS = 120_000;

/** How long to wait before asking again (L18); null: the run has ended, there is nothing more to ask. */
export function pollDelay(status: RunView["status"] | undefined, quietMs: number): number | null {
  if (status === "done" || status === "stopped" || status === "capped") return null;
  if (quietMs >= QUIET_AFTER_MS) return QUIET_MS;
  return status === "waiting" || status === "paused" || status === "failed" ? WAITING_MS : WORKING_MS;
}

const onLine = () => typeof navigator === "undefined" || navigator.onLine !== false;
const visible = () => typeof document === "undefined" || document.visibilityState !== "hidden";

const FINISHED: ReadonlyArray<RunView["status"]> = ["done", "stopped", "capped"];
/** a run that has ended: its Done is what is shown, and nothing more is asked of the server about it */
export const isFinished = (status: RunView["status"]): boolean => FINISHED.includes(status);

/** Follows one run: its newest view, whether the person's device can't reach Atlas right now, whether the run is gone
 *  (the server says it is not there: nothing to follow), and `refresh` for after something the person did. It asks at the
 *  pace of `pollDelay`, never while the page is hidden, sends the rev it holds so an unchanged run costs one cheap read,
 *  and keeps showing what it knew when a poll fails. `seed`: the run as the server last told it (the latest), shown at once
 *  in place of a blank wait; an active run's first look sends its rev, a finished one is read afresh (its Undo changes
 *  without the run changing). */
export function useAgentRun(api: Pick<AgentModeApi, "view">, id: number | null, seed: RunView | null = null): { view: RunView | null; offline: boolean; gone: boolean; refresh: () => void } {
  const [view, setView] = useState<RunView | null>(null);
  const [failures, setFailures] = useState(0);
  const [gone, setGone] = useState(false);
  const [online, setOnline] = useState(onLine);
  const now = useRef<() => void>(() => undefined);
  const handed = useRef(seed);
  useEffect(() => {
    handed.current = seed;
  });

  useEffect(() => {
    setView(null);
    setFailures(0);
    setGone(false);
    if (id === null) {
      now.current = () => undefined;
      return;
    }
    const known = handed.current && handed.current.id === id ? handed.current : null;
    let ended = false;
    let timer: number | undefined;
    let rev: number | undefined = known && !isFinished(known.status) ? known.rev : undefined;
    let status: RunView["status"] | undefined = known?.status;
    let changedAt = Date.now();
    let busy = false;
    let failed = 0;

    const later = () => {
      window.clearTimeout(timer);
      if (ended || !visible()) return;
      const wait = pollDelay(status, Date.now() - changedAt);
      if (wait !== null) timer = window.setTimeout(poll, failed ? Math.max(wait, WAITING_MS) : wait);
    };
    async function poll() {
      if (busy || ended) return;
      busy = true;
      try {
        const got = await api.view(id as number, rev);
        if (ended) return;
        failed = 0;
        setFailures(0);
        if (!("unchanged" in got)) {
          if (got.rev !== rev) changedAt = Date.now();
          rev = got.rev;
          status = got.status;
          setView(got);
        }
      } catch (e) {
        if (ended) return;
        if (e instanceof ApiError && e.status === 404) {
          ended = true; // not there (any more): nothing to follow, and nothing to call "offline"
          setGone(true);
          return;
        }
        failed++;
        setFailures(failed);
      } finally {
        busy = false;
        later();
      }
    }
    now.current = () => {
      changedAt = Date.now();
      window.clearTimeout(timer);
      void poll();
    };
    const wake = () => {
      if (visible()) void poll();
      else window.clearTimeout(timer);
    };
    const back = () => {
      setOnline(true);
      void poll();
    };
    const lost = () => setOnline(false);
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", back);
    window.addEventListener("offline", lost);
    setOnline(onLine());
    if (visible()) void poll();
    return () => {
      ended = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", back);
      window.removeEventListener("offline", lost);
      now.current = () => undefined;
    };
  }, [api, id]);

  const refresh = useCallback(() => now.current(), []);
  return { view: view ?? (id !== null && seed && seed.id === id ? seed : null), offline: !online || failures >= 2, gone, refresh };
}
