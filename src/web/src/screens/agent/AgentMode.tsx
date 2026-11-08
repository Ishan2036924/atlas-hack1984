// Agent mode, the one-stage screen (AM-3). It loads as its own chunk, whose download starts at boot (lib/agentMode
// preloadAgentMode). It owns the window while Agent mode is on: no shell, no sidebar, and never the roaming companion:
// Mochi lives on this stage only (UI_CONTRACT §21). One stage that changes: Ask (talk or type one request), Run (the few
// real steps, Mochi on the one being done, one line of what is happening) and Done (what happened, honestly). Everything
// that moves follows the beat on show (screens/agent/useBeats), never the latest poll. The end card and the question
// (Which Sam?) come as one panel at a time (AM-4: panels/EndCard, panels/WhichPanel on ui/agent/StagePanel); while one is
// open the strip group and Mochi rise to stay in view (ui/agent/Lifted, stageLift), and on a wide screen the rest of the
// stage is dimmed and inert. Opening a doc, sheet or table goes to the full app at that item.
import { useQueryClient } from "@tanstack/react-query";
import { Fragment, lazy, type ReactNode, type Ref, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { type AgentModeApi, createAgentModeApi, isFinished, type ItemRef, type RunStep, type RunView, useAgentRun } from "../../lib/agentRun";
import { externalUrl, type StageUi, stageOf, stripOf } from "../../lib/agentStage";
import { type Mode, readSeen, writeSeen } from "../../lib/agentMode";
import { type Api, ApiError, createApi } from "../../lib/api";
import { useDictation, type Voice } from "../../lib/dictation";
import { errorWords } from "../../lib/errorWords";
import { routePath } from "../../lib/routes";
import { USAGE_KEY, useUsage } from "../../lib/usageHooks";
import { HOP, DURATION } from "../../lib/motion";
import { closeRunNotifications } from "../../lib/runNotifications";
import type { Phase } from "../../lib/view";
import type { TreeRef } from "../../lib/workspaceTree";
import { Lifted } from "../../ui/agent/Lifted";
import { ModeBar } from "../../ui/agent/ModeBar";
import { Perch } from "../../ui/agent/Perch";
import { type PanelFit, sameFit, useStageLift } from "../../ui/agent/stageLift";
import { stepAt } from "../../ui/agent/stepAt";
import { useStaged } from "../../ui/agent/useStaged";
import { type AskSpots, AskMoment } from "./AskMoment";
import { EndCard } from "./panels/EndCard";
import type { PanelContext } from "./panels/types";
import { UndoPanel } from "./panels/UndoPanel";
import { WhichPanel } from "./panels/WhichPanel";
import { PhoneFace, PhoneSky } from "./phone/PhoneFace";
import { usePhoneSky } from "./phone/phoneSky";
import { RunMoment, type RunActions, type RunSpots } from "./RunMoment";
import { useBeats } from "./useBeats";
import { useStageLayout } from "./useStageLayout";
import { NOTHING_HEARD } from "./words";
import { type Usage, WALL } from "../../lib/usage";
import type { StageLayout } from "../../lib/agentStage";

/** where the stage starts, for the previews and the tests (a real visit starts empty) */
// the month's wall only shows at zero, so its code loads then and stays out of the first load (size budget)
const PoolWall = lazy(() => import("../../ui/AskForMore").then((m) => ({ default: m.PoolWall })));

export type Preset = {
  run?: number | null;
  typing?: boolean;
  text?: string;
  offline?: boolean;
  usedUp?: boolean;
  queuedMs?: number;
  layout?: StageLayout;
  /** false: the latest run does not open by itself (a preview of Ask with its last run) */
  reopen?: boolean;
  /** the Undo panel is open from the start, once the run is on Done (a preview of it) */
  undo?: boolean;
  /** the peek of this step is open from the start (a preview of it) */
  peek?: number;
  /** this card was closed from the start: the run is "Ready for your OK" with its card shut (a preview of it) */
  closedCard?: string;
};

export type { PanelContext };

export type AgentModeProps = {
  phase: Phase;
  onMode: (next: Mode) => void; // the switch: only it changes the saved mode
  onOpenItem?: (ref: TreeRef) => void; // "Open doc" and its kin: the full app at that item
  /** the door to the server's runs (tests and previews hand in a fake) */
  api?: AgentModeApi;
  /** the mic's door (voice notes) */
  voiceApi?: Api;
  /** the door to this month's AI usage (the thin line by the input, and the wall at zero); the app's own api when none is handed in */
  usageApi?: Pick<Api, "usage" | "askForMore">;
  /** a tap on the usage line: Settings › AI usage (the full app at that page when none is handed in) */
  onUsage?: () => void;
  /** a mic of its own (the previews) */
  voice?: Voice;
  /** the door to the server's approvals (the yes and the pick); the app's own api when none is handed in (tests and previews hand in a fake) */
  approvals?: Pick<Api, "approve">;
  from?: Preset;
  /** the end card and the question: Atlas's own panels unless a test or a preview hands in others */
  panel?: (context: PanelContext) => ReactNode;
  /** the phone face (LIFEOS_PHONE_VOICE, the profile's phone_voice): on a phone the stage is one speak button and the island */
  phone?: boolean;
};

const sessionToken = async () => (await import("../../lib/supabase")).accessToken();
const SLOW_QUEUE_MS = 3000;
const CELEBRATE_MS = 2400;
const MOCHI = { ask: 108, listen: 52, perched: { phone: 52, tablet: 60, wide: 66 } } as const;
const ACTIVE = new Set<RunView["status"]>(["queued", "running", "waiting"]);

type Spot = { kind: "ask" | "type" | "strip" | "title" } | { kind: "step"; index: number };

export function AgentMode({ phase, onMode, onOpenItem, api: apiProp, voiceApi, usageApi: usageProp, onUsage, voice: voiceProp, approvals, from, panel, phone = false }: AgentModeProps) {
  const detected = useStageLayout();
  const layout = from?.layout ?? detected;
  const wide = layout === "wide";
  const phoneFace = phone && layout === "phone"; // tablets and laptops keep the stage as it is
  const phoneNight = usePhoneSky().night; // the phone face's sky at night: the logo above it is the light-ink one
  const api = useMemo(() => apiProp ?? createAgentModeApi({ token: sessionToken }), [apiProp]);
  const micApi = useMemo(() => voiceApi ?? createApi({ token: sessionToken }), [voiceApi]);
  const usageApi = usageProp ?? micApi;
  const qc = useQueryClient();
  const usage = useUsage(usageApi); // the same shared answer as chat and Settings: the thin line by the input from 75% on
  const openUsage = onUsage ?? (() => window.location.assign(`${routePath({ place: "settings" })}?settings=ai`));

  const [text, setText] = useState(from?.text ?? "");
  const [typing, setTyping] = useState(from?.typing ?? false);
  const typed = typing && !wide; // a wide screen has no separate Type moment: its field is always there (an iPad turned sideways while typing)
  const [runId, setRunId] = useState<number | null>(from?.run ?? null);
  const [sending, setSending] = useState(false);
  const [usedUp, setUsedUp] = useState<string | null>(from?.usedUp ? WALL : null);
  const [problem, setProblem] = useState<string | null>(null);
  const [peek, setPeek] = useState<number | null>(from?.peek ?? null);
  const [closedCard, setClosedCard] = useState<string | null>(from?.closedCard ?? null);
  const [last, setLast] = useState<RunView | null>(null);
  const [acting, setActing] = useState(false);
  const [slow, setSlow] = useState(false);
  const [calm, setCalm] = useState(false);
  const [hop, setHop] = useState(0);
  const [fit, setFit] = useState<PanelFit>(null); // what the panel on show says about its size (the stage rises for it)
  const [pausing, setPausing] = useState(false); // a Pause the server took, but the run has not parked yet (it stops at its next step)
  const [undoOpen, setUndoOpen] = useState(from?.undo ?? false); // the Undo panel (AM-9), opened by Undo… on Done
  const [deciding, setDeciding] = useState<StageUi["deciding"]>(null); // the card the person has just said yes to: Sending… until the run says what became of it

  const run = useAgentRun(api, runId, last);
  const view = run.view;
  const { beat, pending, rev: beatsRev } = useBeats(view);
  const unseen = view !== null && beatsRev !== view.rev; // a poll drawn before the show has taken it in (useBeats)
  const live = useDictation(micApi, text, setText);
  const voice = voiceProp ?? live;
  const listening = voice.state === "listening" || voice.state === "recording";
  const online = !from?.offline && !run.offline;

  // the person has begun something here (typed, talked, started): a late answer about the last run never takes over
  const begun = useRef(false);
  useEffect(() => {
    if (typing || text !== "" || sending || listening || runId !== null) begun.current = true;
  });

  // reopening: the latest run of the last days decides where the stage opens. One still going (or waiting on the person:
  // paused, failed, a card) opens on Run; a finished one this device has not shown opens on Done, once; otherwise Ask,
  // with the quiet "Last run" line.
  useEffect(() => {
    let alive = true;
    api.latest().then(
      (r) => {
        if (!alive) return;
        setLast(r);
        if (!r || begun.current || from?.reopen === false) return;
        if (isFinished(r.status) && readSeen() === r.id) return;
        setRunId(r.id);
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per door; `from` is only a preview's preset
  }, [api]);

  // the run is open, so its notifications are old news (AM-7): closed when it opens and when it asks something new while open
  useEffect(() => {
    if (runId !== null) void closeRunNotifications(runId);
  }, [runId, view?.card?.id, view?.status]);

  // used up for now: a day or a refill may have passed while the window was away, so coming back asks again (a tap on Start finds out)
  const limited = usedUp !== null;
  useEffect(() => {
    if (!limited) return;
    const back = () => {
      if (document.visibilityState !== "hidden") setUsedUp(null);
    };
    document.addEventListener("visibilitychange", back);
    return () => document.removeEventListener("visibilitychange", back);
  }, [limited]);

  // queued for a while: 'You're next'
  useEffect(() => {
    setSlow(false);
    if (view?.status !== "queued") return;
    const t = window.setTimeout(() => setSlow(true), SLOW_QUEUE_MS);
    return () => window.clearTimeout(t);
  }, [view?.status, view?.id]);

  const starting = sending || (runId !== null && view === null);
  const stage = stageOf(view, { typing: typed, listening, peek, closedCard, online, usedUp: usedUp !== null, beatsPending: pending, queuedMs: from?.queuedMs ?? (slow ? SLOW_QUEUE_MS : 0), starting, deciding });
  // a finished run waits for the show to finish (its last found line) before Done comes; on the phone face Done comes at
  // once (the last found line stays in its row on Done, so holding it only kept the island saying Done under the old title)
  const moment = stage.moment === "done" && pending && !phoneFace ? "run" : stage.moment;
  const done = moment === "done";
  const undoShown = undoOpen && done && view !== null && runId !== null; // the Undo panel is only ever on Done
  // a Done that has been on show is seen: the next visit shows it as "Last run", not as Done again
  const doneId = done && view ? view.id : null;
  useEffect(() => {
    if (doneId !== null) writeSeen(doneId);
  }, [doneId]);
  const { shown, leaving } = useStaged(moment === "ask" || moment === "type" ? "ask" : "run");
  const entering = shown === "ask" && leaving; // Ask is going out because the run is coming in

  // the beat on show draws the strip and the words while Atlas works; the poll's own state otherwise
  const onShow = beat && view && ACTIVE.has(view.status) && online ? beat : null;
  const strip = view ? (onShow ? stripOf(view, onShow) : { steps: view.steps, current: view.current }) : { steps: [], current: null };
  const words = onShow && !stage.sending ? { status: onShow.status, sub: onShow.sub } : { status: stage.status, sub: stage.sub };
  const askLine = usedUp ? usedUp : stage.status;
  // the run's last picture, so that it can fade out whole when the person goes back to Ask (the run itself is already gone)
  const onRun = { view, stage, strip, words, done };
  const kept = useRef(onRun);
  useEffect(() => {
    if (moment === "run" || moment === "done") kept.current = onRun;
  });
  const shownRun = shown === "run" && leaving && (moment === "ask" || moment === "type") ? kept.current : onRun;

  // ---- what a person does ----
  const submit = useCallback(async () => {
    const asked = text.trim();
    if (!asked || sending) return;
    if (voice.state !== "idle") voice.stop();
    setSending(true);
    setProblem(null);
    setUsedUp(null);
    try {
      const r = await api.start(asked);
      setRunId(r.id);
      setSending(false);
      setClosedCard(null);
      setPeek(null);
    } catch (e) {
      setSending(false);
      if (e instanceof ApiError && e.status === 402) {
        // the server's own sentence when it is a plain one (the day's wall: "back tomorrow"); the month's wall (WALL), and anything
        // that is no sentence, are the same calm wall as chat (D241): the words, what still works, and Ask for more
        const said = errorWords(e, WALL);
        setUsedUp(said);
        if (said === WALL) {
          // the server just said so: the shared answer says it too at once (a cached 78% must not read "not enough for this"), then asks again
          qc.setQueryData<Usage>(USAGE_KEY, (u) => (u ? { ...u, allowance: { ...u.allowance, used_pct: 100, limit_reached: true } } : u));
          void qc.invalidateQueries({ queryKey: USAGE_KEY });
        }
        setTyping(false);
      } else setProblem(errorWords(e));
    }
  }, [api, qc, sending, text, voice]);

  const act = useCallback(
    async (f: (id: number) => Promise<void>) => {
      if (runId === null || acting) return;
      setActing(true);
      setProblem(null);
      try {
        await f(runId);
        run.refresh();
      } catch (e) {
        setProblem(errorWords(e));
      } finally {
        setActing(false);
      }
    },
    [acting, run, runId],
  );
  const clear = useCallback(() => {
    setRunId(null);
    setTyping(false);
    setText("");
    setPeek(null);
    setClosedCard(null);
    setProblem(null);
    setUsedUp(null);
    setCalm(false);
    setUndoOpen(false);
    setDeciding(null);
  }, []);
  const finish = useCallback(() => {
    if (view) setLast(view);
    clear();
  }, [view, clear]);
  // a run the server says is not there (any more): back to Ask, quietly
  useEffect(() => {
    if (!run.gone) return;
    setLast((l) => (l && l.id === runId ? null : l));
    clear();
  }, [run.gone, runId, clear]);
  // a Pause that is on its way waits for the run to say it has parked (or has moved on some other way)
  useEffect(() => {
    setPausing(false);
  }, [view?.status, runId]);
  const actions: RunActions = {
    pause: () => {
      if (pausing) return;
      void act(async (id) => {
        await api.pause(id);
        setPausing(true);
      });
    },
    pausing: pausing && (view?.status === "running" || view?.status === "queued"),
    resume: () => void act((id) => api.resume(id)),
    stop: () => void act((id) => api.stop(id)),
    retry: () => void act((id) => api.resolve(id, "retry")),
    skip: () => void act((id) => api.resolve(id, "skip")),
    done: finish,
    undo: () => setUndoOpen(true), // (an open peek closes by itself: a peek and a panel are never open together)
    busy: acting,
    // a run that stopped on a limit: the rest as a new run for the same request, which the stage then follows
    keepGoing: api.keepGoing
      ? () =>
          void act(async (id) => {
            const next = await (api.keepGoing as NonNullable<typeof api.keepGoing>)(id);
            if (view) setLast(view);
            setClosedCard(null);
            setPeek(null);
            setRunId(next.id);
          })
      : undefined,
  };
  const talk = () => {
    if (!wide && !phoneFace) setTyping(true); // the phone face listens in its island: no field
    setProblem(null);
    if (voice.state === "idle") voice.toggle();
  };
  // the phone face's island: the arrow starts once the words are in (a recorded note is written down first), × throws them away
  const startWhenHeard = useRef(false);
  const cancelTalk = () => {
    startWhenHeard.current = false;
    if (voice.state !== "idle") voice.stop();
    setText("");
  };
  const startTalk = () => {
    if (voice.state === "idle") {
      if (text.trim()) void submit();
      else setProblem(NOTHING_HEARD);
      return;
    }
    startWhenHeard.current = true;
    voice.stop();
  };
  // the end card's spoken answer (phone face): the box is emptied, then the mic listens; stopping empties it again
  const answerListen = useCallback(() => {
    setText("");
    // live recognition only (lib/mic listen): a recorded note would only stop on a tap, so "send it" would never be read
    if (voice.state === "idle") (voice.listen ?? voice.toggle)();
  }, [voice]);
  const stopListen = useCallback(() => {
    voice.stop(); // safe when it isn't listening, and right even when called from an older render (there is one mic)
    setText("");
  }, [voice]);
  useEffect(() => {
    if (!startWhenHeard.current || voice.state !== "idle") return;
    startWhenHeard.current = false;
    if (text.trim()) void submit();
    else setProblem(NOTHING_HEARD);
  }, [voice.state, text, submit]);
  // what a card's row or a step's peek can open: a doc, sheet or table in the full app, an event or a place on Google's own page
  const openRef = (ref: ItemRef | null): (() => void) | undefined => {
    if (!ref) return undefined;
    if ("id" in ref) return onOpenItem ? () => onOpenItem({ kind: ref.kind, id: ref.id }) : undefined;
    const url = externalUrl(ref);
    return url ? () => void window.open(url, "_blank", "noopener,noreferrer") : undefined;
  };
  const openFor = (step: RunStep): (() => void) | undefined => openRef(step.ref);
  const openStatus = stage.chevron === "panel" ? () => setClosedCard(null) : stage.chevron === "peek" && strip.current !== null ? () => setPeek(strip.current) : undefined;

  // a panel and a peek are never open together
  useEffect(() => {
    if (stage.panel || undoShown) setPeek(null);
  }, [stage.panel, undoShown]);
  useEffect(() => {
    if (peek !== null && view && peek >= view.steps.length) setPeek(null);
  }, [peek, view]);

  // wide keys: Space pauses and resumes outside a field; ↵ is Done
  useEffect(() => {
    if (!wide) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest("input, textarea, select, button, a, summary, [contenteditable], [role='button'], [role='radio'], [role='tab'], [role='menuitem'], [role='slider']")) return;
      if (e.key === " " && shown === "run" && !done && peek === null && !stage.panel) {
        if (stage.footer === "leave") actions.pause();
        else if (stage.footer === "paused") actions.resume();
        else return;
        e.preventDefault();
      } else if (e.key === "Enter" && done && shown === "run" && !undoShown) {
        e.preventDefault();
        // a run that stopped on a limit: Enter keeps going (its ink button), never ends what isn't finished
        if (view?.status === "capped" && actions.keepGoing) actions.keepGoing();
        else finish();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  // ---- Mochi: where it sits ----
  const spots = useRef<Record<"ask" | "type" | "strip" | "title" | "group" | "root", HTMLElement | null>>({ ask: null, type: null, strip: null, title: null, group: null, root: null });
  const [, rerender] = useState(0);
  const put = useCallback((name: keyof typeof spots.current) => (el: HTMLElement | null) => {
    if (spots.current[name] !== el) {
      spots.current[name] = el;
      rerender((n) => n + 1);
    }
  }, []);
  const refs = useMemo(() => ({ ask: put("ask"), type: put("type"), strip: put("strip"), title: put("title"), group: put("group"), root: put("root") }), [put]);

  // Mochi sets off when the run starts coming in (over Ask, which is still going), not when Ask has gone: its flight is not held behind the words
  const onAsk = shown === "ask" && !entering;
  const want: Spot = onAsk ? { kind: typed ? "type" : "ask" } : strip.steps.length === 0 ? { kind: done ? "title" : "strip" } : done ? { kind: "step", index: strip.steps.length - 1 } : strip.current === null ? { kind: "strip" } : { kind: "step", index: strip.current };
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [arc, setArc] = useState<number>(HOP.arcPx);
  const was = useRef<Spot["kind"] | null>(null);
  useLayoutEffect(() => {
    const el = want.kind === "step" ? stepAt(spots.current.strip, want.index) : spots.current[want.kind];
    const carried = (was.current === "ask" || was.current === "type") && (want.kind === "strip" || want.kind === "step");
    const next = done ? HOP.doneArcPx : carried ? HOP.carryArcPx : wide ? HOP.arcWidePx : HOP.arcPx;
    if (el !== target) {
      setTarget(el);
      setArc(next); // the hop that this move is: the perch reads it as it sets off
    }
    was.current = want.kind;
  });

  // the small hop on Done, and the celebration that settles
  const celebrating = stage.mood === "celebrate";
  useEffect(() => {
    if (!done || shown !== "run" || !stage.hop) return;
    const t = window.setTimeout(() => {
      setArc(HOP.doneArcPx);
      setHop((n) => n + 1);
    }, DURATION.fast + 60);
    return () => window.clearTimeout(t);
  }, [done, shown, stage.hop, view?.id]);
  useEffect(() => {
    setCalm(false);
    if (!celebrating || !done) return;
    const t = window.setTimeout(() => setCalm(true), CELEBRATE_MS);
    return () => window.clearTimeout(t);
  }, [celebrating, done, view?.id]);

  // resting at 108 on Ask; 52 at the listen spot, and while the plan is being made; then the size of the layout's steps
  const planning = !onAsk && !done && (strip.steps.length === 0 || strip.current === null);
  const size = onAsk ? (typed ? MOCHI.listen : MOCHI.ask) : planning ? MOCHI.listen : MOCHI.perched[layout];
  const mood = stage.mood === "celebrate" && calm ? "happy" : stage.mood;
  const lastDone = last && (last.status === "done" || last.status === "stopped" || last.status === "capped") ? { title: last.done?.title || last.title } : null;
  const askSpots: AskSpots = { ask: refs.ask, type: refs.type };
  const runSpots: RunSpots = { strip: refs.strip, title: refs.title, group: refs.group };
  const card = view?.card ?? null;

  // ---- the panel: one at a time; the stage rises for it ----
  const panelOpen = Boolean(stage.panel && view && card && runId !== null) || undoShown;
  const wideOpen = panelOpen && wide; // a card over a dimmed stage: the stage behind it is inert
  const onFit = useCallback((next: PanelFit) => setFit((was) => (sameFit(was, next) ? was : next)), []);
  const lift = useStageLift({ root: spots.current.root, group: spots.current.group, anchor: spots.current.strip, wide }, panelOpen ? fit : null);
  useEffect(() => {
    if (!panelOpen) setFit(null); // the next panel says what it is itself
  }, [panelOpen]);
  // a panel that goes gives focus back to the step that waits (unless it already went somewhere on its own: the opener)
  const wasOpen = useRef(false);
  const waiting = strip.current; // the step the card hangs from
  useEffect(() => {
    if (wasOpen.current && !panelOpen) {
      const at = waiting === null ? document.querySelector<HTMLElement>("[data-undo-link]") : stepAt(spots.current.strip, waiting)?.querySelector<HTMLElement>("[data-node]"); // Undo… on Done, else the step that waits
      const now = document.activeElement;
      if (at && (!now || now === document.body || !document.contains(now))) at.focus({ preventScroll: true });
    }
    wasOpen.current = panelOpen;
  }, [panelOpen, waiting]);
  const panels = panel ?? ((c: PanelContext) => (c.kind === "ask" ? <WhichPanel {...c} /> : <EndCard {...c} />));

  // the month's wall (only at zero), and Ask: one element, drawn by the stage and by the phone face's Type moment
  const wall =
    usedUp === WALL ? (
      <Suspense fallback={null}>
        <PoolWall api={usageApi} center />
      </Suspense>
    ) : null;
  const askMoment = (
    <AskMoment
      phase={phase}
      layout={layout}
      typing={typing}
      line={askLine}
      text={text}
      onText={setText}
      onStart={() => void submit()}
      onType={() => setTyping(true)}
      onTalk={talk}
      voice={voice}
      problem={problem}
      last={lastDone}
      onLast={() => {
        if (last) setRunId(last.id);
      }}
      spots={askSpots}
      leaving={leaving}
      limited={limited}
      onFull={() => onMode("full")}
      wall={wall}
      usage={usage.data}
      onUsage={openUsage}
    />
  );

  if (phoneFace) {
    const endCard = stage.panel === "end" && view && card && runId !== null && !unseen ? card : null;
    const cardClosed = Boolean(view?.card && view.card.kind === "end" && closedCard === view.card.id && !stage.sending);
    const runMoment = moment === "ask" || moment === "type" ? "ask" : "run";
    // the Type page is only for writing the request: once it has started, the run is on the face like a spoken one's
    const writing = typed && runMoment === "ask";
    return (
      <div ref={refs.root as Ref<HTMLDivElement>} data-root data-face="phone" className="relative flex min-h-dvh flex-col bg-page">
        {!writing && <PhoneSky />}
        <div className="relative">
          <ModeBar mode="agent" onMode={onMode} onSky={!writing} onDark={!writing && phoneNight} />
        </div>
        <main className="relative flex flex-1 flex-col px-4">
          {writing ? (
            askMoment
          ) : (
            <PhoneFace
              phase={phase}
              moment={runMoment}
              line={askLine}
              text={text}
              voice={voice}
              problem={problem}
              last={lastDone}
              onLast={() => {
                if (last) setRunId(last.id);
              }}
              limited={limited}
              wall={wall}
              onSpeak={talk}
              onType={() => {
                if (done) finish(); // from Done, a new request: the run folds into "Last run", as Tap to speak does
                setTyping(true);
              }}
              onCancel={cancelTalk}
              onStart={startTalk}
              view={shownRun.view}
              request={shownRun.view?.request ?? text.trim()}
              stage={shownRun.stage}
              steps={shownRun.strip.steps}
              words={shownRun.words}
              done={shownRun.done}
              actions={actions}
              card={endCard}
              cardClosed={cardClosed}
              deciding={deciding !== null}
              onOpenCard={() => setClosedCard(null)}
              onCloseCard={(decided) => {
                if (card) setClosedCard(card.id);
                if (decided) run.refresh(); // they answered: ask the run at once what came of it
              }}
              onDeciding={(on) => setDeciding(on && card ? { id: card.id, verb: card.verb } : null)}
              approve={(approvals ?? micApi).approve}
              preview={runId !== null ? (n) => api.heldItem(runId, n) : undefined}
              open={openRef}
              onSpeakAgain={() => {
                finish();
                talk();
              }}
              offline={!online}
              current={shownRun.strip.current}
              waitKind={shownRun.view?.card?.kind ?? null}
              onAnswerListen={answerListen}
              onStopListen={stopListen}
              speakAsk={runId !== null && api.askAloud ? (cardId) => api.askAloud!(runId, cardId) : undefined}
            />
          )}
        </main>
        {stage.panel === "ask" && view && card && runId !== null && (
          <Fragment key={`ask-${card.id}`}>
            {panels({
              kind: "ask",
              card,
              layout,
              runId,
              onClose: (decided) => {
                setClosedCard(card.id);
                if (decided) run.refresh();
              },
              onDeciding: (on) => setDeciding(on ? { id: card.id, verb: card.verb } : null),
              preview: (n) => api.heldItem(runId, n),
              approve: (approvals ?? micApi).approve,
              open: openRef,
              onFit,
              place: lift,
            })}
          </Fragment>
        )}
        {undoShown && view && runId !== null && (
          <UndoPanel
            key="undo"
            layout={layout}
            runId={runId}
            steps={view.steps}
            api={api}
            open={openRef}
            onClose={(undone) => {
              setUndoOpen(false);
              if (undone) run.refresh();
            }}
            onNeverMind={(words) => {
              finish();
              setTyping(true);
              setText(words);
            }}
            onFit={onFit}
            place={lift}
          />
        )}
      </div>
    );
  }

  return (
    <div ref={refs.root as Ref<HTMLDivElement>} data-root className="relative flex min-h-dvh flex-col bg-page">
      <div inert={wideOpen}>
        <ModeBar mode="agent" onMode={onMode} />
      </div>
      <main inert={wideOpen} className="grid flex-1 grid-cols-1 grid-rows-1 px-4 expanded:px-8">
        {shown === "ask" && (
          <div inert={leaving} className="col-start-1 row-start-1 flex flex-col">
            {askMoment}
          </div>
        )}
        {/* the run comes in while Ask goes (one over the other, never one after the other): the typed words and the request line are
            on show together, so there is no blank between them. Going back to Ask is the other way round: the run goes first */}
        {(shown === "run" || entering) && (
          <div className="col-start-1 row-start-1 flex flex-col">
            <RunMoment
              layout={layout}
              view={shownRun.view}
              request={shownRun.view?.request ?? text.trim()}
              stage={shownRun.stage}
              strip={shownRun.strip}
              words={shownRun.words}
              done={shownRun.done}
              leaving={shown === "run" && leaving}
              peek={peek}
              onStep={(i) => setPeek((p) => (p === i ? null : i))}
              onClosePeek={() => setPeek(null)}
              onOpenRef={openFor}
              onStatus={openStatus}
              actions={actions}
              problem={problem}
              spots={runSpots}
              lift={lift.lift}
              panelOpen={panelOpen}
            />
          </div>
        )}
      </main>
      <Lifted by={lift.lift} className="pointer-events-none absolute inset-0">
        <Perch target={target} mood={mood} size={size} arc={arc} hop={hop} />
      </Lifted>
      {stage.panel && view && card && runId !== null && (
        <Fragment key={`${stage.panel}-${card.id}`}>
          {panels({
            kind: stage.panel,
            card,
            layout,
            runId,
            onClose: (decided) => {
              setClosedCard(card.id);
              if (decided) run.refresh(); // they answered: ask the run at once what came of it
            },
            onDeciding: (on) => setDeciding(on ? { id: card.id, verb: card.verb } : null),
            preview: (n) => api.heldItem(runId, n),
            approve: (approvals ?? micApi).approve,
            open: openRef,
            onFit,
            place: lift,
          })}
        </Fragment>
      )}
      {undoShown && view && runId !== null && (
        <UndoPanel
          key="undo"
          layout={layout}
          runId={runId}
          steps={view.steps}
          api={api}
          open={openRef}
          onClose={(undone) => {
            setUndoOpen(false);
            if (undone) run.refresh(); // something went back (or nothing was left): ask the run at once what is so now
          }}
          onNeverMind={(words) => {
            // the person's own field, filled in: they read it and press Start, or don't. Nothing is sent from here
            finish();
            setTyping(true);
            setText(words);
          }}
          onFit={onFit}
          place={lift}
        />
      )}
    </div>
  );
}
