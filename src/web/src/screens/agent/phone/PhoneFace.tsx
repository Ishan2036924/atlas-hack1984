// Agent mode's phone face (LIFEOS_PHONE_VOICE, the approved look "B. Island", 2026-11-07): one speak button, then actions.
// The same stage as Agent mode (the run, its beats, its card and its Undo all come from AgentMode); only the drawing is
// new. At the top: the greeting, then the request (the same words, never a bubble), then what happened. In the middle: one
// row per real step of the run (a grey line of detail; a blue tick when done; amber only when it waits for the person),
// and the email card when one waits. At the thumb: the island, which is always the one control needed now: Tap to speak,
// the words being heard (× cancels, the arrow starts), Working on it with Stop, and the amber yes. When the end card
// opens, Atlas also asks out loud and listens: the person taps Send or says it (voiceAnswer.ts: only while that card is
// open, matched on this device, never by the model; a spoken yes runs a 3-second countdown with Cancel first). Either way
// the yes is the end card's own (the plan it showed, every item, one HMAC-signed approval). The ask card (Which Sam?) and
// Undo… stay today's panels.
import { type CSSProperties, type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { HeldItem, ItemRef, RunCard, RunStep, RunView } from "../../../lib/agentRun";
import type { Stage } from "../../../lib/agentStage";
import type { Api } from "../../../lib/api";
import type { Voice } from "../../../lib/dictation";
import { errorWords } from "../../../lib/errorWords";
import { DURATION, usePageHidden, useReducedMotion } from "../../../lib/motion";
import type { Phase } from "../../../lib/view";
import "../../../ui/agent/icons";
import { Fade } from "../../../ui/agent/Fade";
import { Island, IslandButton, IslandIconButton, type IslandLook } from "../../../ui/agent/Island";
import { ResultRow, type RowState } from "../../../ui/agent/ResultRow";
import { GLYPH } from "../../../ui/agent/StepStrip";
import { useStaged } from "../../../ui/agent/useStaged";
import { VoiceLine } from "../../../ui/agent/VoiceLine";
import { Icon } from "../../../ui/kit";
import { Markdown } from "../../../ui/Markdown";
import { Button } from "../../../ui/primitives/Button";
import { cx } from "../../../ui/primitives/shared";
import { HomeSky } from "../../../ui/sky/HomeSky";
import { GREETING } from "../../home/homeCopy";
import { glyphOf } from "../panels/glyph";
import { ItemPreview } from "../panels/ItemPreview";
import { primedAudio, primeTalk } from "../../chat/talk/prime";
import { decode, deviceVoice } from "../../chat/talk/voices";
import { useEntered } from "../../../ui/agent/useEntered";
import { usePhoneSky } from "./phoneSky";
import { answerOf, COUNTDOWN_S, echoOf, HEARD_MS } from "./voiceAnswer";
import type { RunActions } from "../RunMoment";
import * as W from "../words";

export type PhoneFaceProps = {
  phase: Phase;
  /** Ask, or the run (with Done) */
  moment: "ask" | "run";
  // Ask
  line: string;
  text: string;
  voice: Voice;
  problem: string | null;
  last: { title: string } | null;
  onLast: () => void;
  limited: boolean;
  wall: ReactNode;
  onSpeak: () => void;
  onType: () => void;
  onCancel: () => void;
  onStart: () => void;
  // the run
  view: RunView | null;
  request: string;
  stage: Stage;
  steps: RunStep[];
  words: { status: string; sub: string };
  done: boolean;
  actions: RunActions;
  /** the end card that waits for the yes, open on the stage (null: none, or the person closed it with Not now) */
  card: RunCard | null;
  /** an end card waits but was closed: the island offers Review */
  cardClosed: boolean;
  /** the person has just said yes: Sending… until the run says what became of it */
  deciding: boolean;
  onOpenCard: () => void;
  onCloseCard: (decided: boolean) => void;
  onDeciding: (on: boolean) => void;
  approve?: Api["approve"];
  preview?: (n: number) => Promise<HeldItem>;
  open: (ref: ItemRef | null) => (() => void) | undefined;
  /** Done: speak again (the run folds into "Last run") */
  onSpeakAgain: () => void;
  /** the run can't be heard from right now: the island says so and offers nothing it can't do */
  offline: boolean;
  /** the step being done now (its row speaks the live line) */
  current: number | null;
  /** what the run waits on, when it waits: the end card (a yes) or a question */
  waitKind: "end" | "ask" | null;
  /** the island listens for the spoken answer to the end card (the box is emptied first) */
  onAnswerListen: () => void;
  /** it stops listening (and empties the box) */
  onStopListen: () => void;
  /** the end card's question said in the person's read-aloud voice (the server's words for that card); absent, or when
   *  it can't, the device's voice asks */
  speakAsk?: (cardId: string) => Promise<{ text: string; audio: string; type: string }>;
};

/** a light tap where the browser can (Android Chrome); iOS Safari has none, and nothing depends on it */
function tap(): void {
  try {
    navigator.vibrate?.(10);
  } catch {
    // no vibration here
  }
}

/** the words at the top sit on the sky: ink on a day sky, white with Home's shade on the night's */
type OnSky = { night: boolean; paint: CSSProperties };
const skyHead = (sky: OnSky) => ({ className: cx("pt-4", sky.night && "sky-words"), style: sky.night ? sky.paint : undefined });
const skyInk = (sky: OnSky) => (sky.night ? "text-white" : "text-fg-1");
const skySoft = (sky: OnSky) => (sky.night ? "text-white/85" : "text-fg-1/80");

/** the sky behind the phone face: Home's, from the top of the screen, gone into the page by under half its height (the sky
 *  draws in its top 78% and fades out from the middle of that). Home's deeper patch behind its white greeting is left out:
 *  the words here are ink, and the patch would be a dark band mid-screen */
export function PhoneSky() {
  const sky = usePhoneSky();
  const hidden = usePageHidden();
  return (
    <div aria-hidden style={sky.paint} className="pointer-events-none absolute inset-x-0 top-0 h-[58dvh] overflow-hidden [&_[data-part=scrim]]:hidden">
      <HomeSky now={sky.now} day={sky.day} condition={sky.condition} alive={!hidden} />
    </div>
  );
}

export function PhoneFace(p: PhoneFaceProps) {
  const listening = p.voice.state !== "idle";
  const { night, paint } = usePhoneSky();
  const sky: OnSky = { night, paint };
  // Ask to the run: the run is there at once (its request line flies up from the island, never fading) while the greeting
  // leaves over it; the run back to Ask: leave before arrive, as everywhere
  const { shown, leaving } = useStaged(p.moment);
  const [askLeaving, setAskLeaving] = useState(false);
  const was = useRef(p.moment);
  useLayoutEffect(() => {
    const from = was.current;
    was.current = p.moment;
    if (from !== "ask" || p.moment !== "run") return;
    setAskLeaving(true);
    const t = window.setTimeout(() => setAskLeaving(false), DURATION.fast);
    return () => window.clearTimeout(t);
  }, [p.moment]);
  const onRun = p.moment === "run";
  // where the words last stood in the island: the request line flies up from there, once
  const heard = useRef<DOMRect | null>(null);
  const keep = useCallback((at: DOMRect | null) => {
    heard.current = at;
  }, []);
  const take = useCallback(() => {
    const at = heard.current;
    heard.current = null;
    return at;
  }, []);
  return (
    <div className="relative flex flex-1 flex-col">
      <div className="grid flex-1 grid-cols-1 grid-rows-1">
        {onRun ? (
          <div className="relative col-start-1 row-start-1 flex flex-col px-2">
            <RunTop {...p} sky={sky} flightFrom={take} />
          </div>
        ) : (
          <Fade key={shown} leaving={leaving} className="relative col-start-1 row-start-1 flex flex-col px-2">
            {shown === "ask" ? <AskTop {...p} sky={sky} listening={listening} /> : <RunTop {...p} sky={sky} />}
          </Fade>
        )}
        {askLeaving && (
          <Fade leaving className="pointer-events-none relative col-start-1 row-start-1 flex flex-col px-2">
            <AskTop {...p} sky={sky} listening={false} />
          </Fade>
        )}
      </div>
      <Island look={lookOf(p, listening)} label={W.TAP_TO_SPEAK}>
        {(look) => <IslandBody look={look} p={p} heard={keep} />}
      </Island>
    </div>
  );
}

/** which look the island has now */
function lookOf(p: PhoneFaceProps, listening: boolean): IslandLook {
  if (p.moment === "ask") return listening ? "listen" : "idle";
  if (p.done) return "idle";
  if (p.card && !p.deciding) return "ok";
  return "work";
}

// ---- the top ----

/** nothing heard or typed yet (the quiet "Last run" line only shows then, so it can never flash back on Start) */
const nothingSaid = (text: string) => text.trim() === "";

function AskTop({ phase, line, problem, voice, last, onLast, wall, listening, text, sky }: PhoneFaceProps & { listening: boolean; sky: OnSky }) {
  const words = problem ?? voice.error;
  return (
    <>
      <div {...skyHead(sky)}>
        <h1 className={cx("m-0 text-display font-bold break-words", skyInk(sky))}>{GREETING[phase]}</h1>
        {wall ? <div className="mt-2">{wall}</div> : <p className={cx("m-0 mt-2 text-callout", skySoft(sky))}>{line}</p>}
        <output className="sr-only">{voice.say}</output>
        {words && (
          <p role="alert" className={cx("m-0 mt-4 flex flex-wrap items-center gap-x-2 text-body", skySoft(sky))}>
            {words}
            {!problem && voice.retry && (
              <Button variant="text" size="sm" onClick={voice.retry}>
                {W.TRY_AGAIN}
              </Button>
            )}
          </p>
        )}
      </div>
      <div className="flex-1" />
      {last && !listening && nothingSaid(text) && (
        <Button variant="text" size="xl" iconEnd="chevron" aria-label={W.lastRun(last.title)} onClick={onLast} className="self-center">
          <span className="text-fg-2">Last run:</span> <span className="font-semibold">{last.title}</span>
        </Button>
      )}
    </>
  );
}

/** the request line arrives from where the words were heard (the island), one piece: a FLIP on transform only */
function useFlight(from: (() => DOMRect | null) | undefined) {
  const title = useRef<HTMLDivElement>(null);
  const light = useRef<HTMLSpanElement>(null);
  const still = useReducedMotion();
  const [flew, setFlew] = useState(false);
  useLayoutEffect(() => {
    const start = from?.();
    const el = title.current;
    if (!start || !el || still) return;
    const end = el.getBoundingClientRect();
    if (!end.width) return;
    setFlew(true);
    el.style.transformOrigin = "0 0";
    el.style.transition = "none";
    el.style.transform = `translate(${start.left - end.left}px, ${start.top - end.top}px) scale(${start.width / end.width})`;
    const words = light.current;
    if (words) {
      words.style.transition = "none"; // the words leave the island exactly as they were there: light, at once
      words.style.opacity = "1";
    }
    const raf = window.requestAnimationFrame(() => {
      el.style.transition = "transform var(--duration-hop) var(--ease-drawer)"; // a longer travel: a drawer's curve, not a front-loaded one
      el.style.transform = "";
      if (words) {
        words.style.transition = "";
        words.style.opacity = "0";
      }
    });
    return () => window.cancelAnimationFrame(raf);
  }, [from, still]);
  return { title, light, flew };
}

function RunTop(p: PhoneFaceProps & { sky: OnSky; flightFrom?: () => DOMRect | null }) {
  const { view, done, stage, steps, request, card } = p;
  const note = steps.length > 0 ? W.planNote(steps.length) : p.words.status;
  const flight = useFlight(p.flightFrom);
  // the card leaves the way it came: it shrinks back into the row it grew from before the rows return
  const [kept, setKept] = useState<RunCard | null>(card);
  const [closing, setClosing] = useState(false);
  const still = useReducedMotion();
  useLayoutEffect(() => {
    if (card) {
      setKept(card);
      setClosing(false);
      return;
    }
    if (!kept) return;
    if (still) return setKept(null);
    setClosing(true); // the card goes once its shrink has finished (Card's onGone), never on a timer that could cut it
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a card that comes or goes moves this
  }, [card]);
  const gone = useCallback(() => {
    setKept(null);
    setClosing(false);
  }, []);
  return (
    <>
      <div {...skyHead(p.sky)}>
        {done ? (
          <>
            <h1 className={cx("m-0 line-clamp-3 text-title font-semibold text-balance break-words", skyInk(p.sky))}>
              <SettleWords text={stage.status} step={40} />
            </h1>
            {stage.sub && <p className={cx("m-0 mt-1 text-body break-words", skySoft(p.sky))}>{stage.sub}</p>}
          </>
        ) : (
          <>
            <div ref={flight.title} className="relative z-(--z-sidebar)">
              <h1 className={cx("m-0 line-clamp-4 text-title font-semibold text-balance break-words", skyInk(p.sky))}>{request}</h1>
              {/* the same words as they were in the island, turning to the ink line while they leave the dark glass (opacity only) */}
              <span ref={flight.light} aria-hidden className="pointer-events-none absolute inset-0 line-clamp-4 text-title font-semibold text-balance break-words text-toast-ink opacity-0 transition-opacity duration-(--duration-fast) ease-standard">
                {request}
              </span>
            </div>
            <Fade delay={flight.flew ? DURATION.hop : 0}>
              <p className={cx("m-0 mt-2 text-small", skySoft(p.sky))}>{note}</p>
            </Fade>
          </>
        )}
      </div>
      <div className="mt-5 grid grid-cols-1 grid-rows-1">
        <ul
          inert={Boolean(kept)}
          className={cx("col-start-1 row-start-1 m-0 flex flex-col gap-2.5 p-0 transition-opacity", kept && !closing ? "opacity-0 duration-(--duration-fast) ease-accelerate" : closing ? "opacity-100 delay-(--duration-instant) duration-(--duration-panel) ease-decelerate" : "opacity-100 duration-(--duration-sheet) ease-decelerate")}
        >
          {steps.map((s, i) => (
            <Row key={s.id} step={s} index={i} {...p} />
          ))}
        </ul>
        {kept && <Card {...p} card={kept} closing={closing} onGone={gone} />}
      </div>
      {done && view?.done?.answer && (
        // the answer is markdown, as chat's is (the same renderer), on the rows' light glass, so its ink reads on any sky
        // (the night's too) and below it; a long one grows the page, which scrolls above the island
        <div
          data-answer=""
          className="mt-4 rounded-card bg-surface-panel/90 px-4 py-3.5 text-callout text-pretty break-words text-fg-1 shadow-[inset_0_calc(var(--spacing)*0.25)_0_color-mix(in_srgb,var(--switch-knob)_85%,transparent),inset_0_0_0_calc(var(--spacing)*0.125)_color-mix(in_srgb,var(--switch-knob)_45%,transparent),var(--shadow-elev-2)] backdrop-blur-2xl backdrop-saturate-[1.2]"
        >
          <Markdown text={view.done.answer} />
        </div>
      )}
      {done && view?.status === "capped" && p.actions.keepGoing && !kept && (
        // it stopped on a limit (the line says so): the rest of the request, in one tap
        <Fade className="mt-4 self-center">
          <Button variant="primary" size="lg" onClick={p.actions.keepGoing} disabled={p.actions.busy} className="min-w-52">
            {W.KEEP_GOING}
          </Button>
        </Fade>
      )}
      {done && view?.undo.available && !kept && (
        // the way back is a real button to the eye (a text one read as plain words, as Refund and the chat's Undo did); it
        // comes once the card has shrunk away, in its own place, so it never shows over the card nor moves after
        <Fade className="mt-4 self-center">
          <Button variant="secondary" size="md" onClick={() => p.actions.undo(view.id)}>
            {W.UNDO_LINK}
          </Button>
        </Fade>
      )}
      {p.problem && (
        <p role="alert" className="m-0 mt-3 text-small text-fg-1/80">
          {p.problem}
        </p>
      )}
    </>
  );
}

/** one step as one row: what it is doing, what it found, or that it waits for the person */
function Row({ step, index, card, cardClosed, deciding, onOpenCard, open, offline, current, words, stage, waitKind, sky }: PhoneFaceProps & { step: RunStep; index: number; sky: OnSky }) {
  let state: RowState = "next";
  let sub: string = W.UP_NEXT;
  let onOpen: (() => void) | undefined;
  switch (step.state) {
    case "done":
      state = "done";
      sub = step.found ?? step.detail ?? W.DONE;
      onOpen = open(step.ref);
      break;
    case "now":
      state = offline ? "still" : "now";
      // the live line is the current step's; after the yes the step that sends says so
      sub = deciding || stage.sending ? W.sendingWords(card?.verb ?? "send") : !offline && index === current && words.status ? words.status : step.now;
      break;
    case "wait":
      if (deciding || stage.sending) {
        state = "now";
        sub = W.sendingWords(card?.verb ?? "send");
      } else {
        state = "you";
        sub = waitKind === "ask" ? W.WAITING_FOR_YOU : W.READY;
        if (card || cardClosed || waitKind === "ask") onOpen = onOpenCard;
      }
      break;
    case "fail":
      state = "fail";
      sub = W.failedWords(step.app, false).status;
      break;
    case "paused":
      state = "still";
      sub = W.PAUSED;
      break;
    case "skipped":
      state = "still";
      sub = "Skipped";
      break;
  }
  return <ResultRow glyph={GLYPH[step.app]} title={step.label} sub={sub} state={state} index={index} onOpen={onOpen} onDark={sky.night} />;
}

/** the end card on the stage, above the island: what goes out, exactly as it will (the server's own record of each held call) */
function Card({ card, preview, open, steps, closing = false, onGone }: PhoneFaceProps & { card: RunCard; closing?: boolean; /** the shrink back into its row has finished */ onGone?: () => void }) {
  const words = W.endCardWords(card);
  const [viewing, setViewing] = useState<number | null>(card.items.length === 1 ? 0 : null);
  const item = viewing !== null ? card.items[viewing] : undefined;
  const box = useRef<HTMLElement>(null);
  const still = useReducedMotion();
  const [from] = useState(() => Math.max(0, steps.findIndex((s) => s.state === "wait")));
  const band = useCallback(() => {
    const h = box.current?.getBoundingClientRect().height ?? 0;
    // the row it grew from: a row is 64 tall and 10 apart, from the same top as the card; a row below the card's own end
    // is met at the card's bottom edge (a clip can't reach outside the card: past it, there would be nothing to show)
    const top = Math.max(0, Math.min(from * 74, h - 64));
    return `inset(${top}px 0px ${Math.max(0, h - top - 64)}px 0px round 20px)`;
  }, [from]);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || still) return;
    el.style.transition = "none";
    el.style.clipPath = band();
    const raf = window.requestAnimationFrame(() => {
      el.style.transition = "clip-path var(--duration-hop) var(--ease-drawer)";
      el.style.clipPath = "inset(0px 0px 0px 0px round 24px)";
    });
    return () => window.cancelAnimationFrame(raf);
  }, [band, still]);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || still || !onGone) return;
    if (!closing) {
      // it came back before it was gone (the card opened again): it grows back from where it is
      if (el.style.opacity !== "0") return;
      el.style.transition = "clip-path var(--duration-hop) var(--ease-drawer), opacity var(--duration-fast) var(--ease-decelerate)";
      el.style.clipPath = "inset(0px 0px 0px 0px round 24px)";
      el.style.opacity = "";
      return;
    }
    // it shrinks back into its row and fades on the way; the rows come back once it has gone (RunTop), so two lines of
    // words never cross
    el.style.transition = "clip-path var(--duration-sheet) var(--ease-accelerate), opacity var(--duration-base) var(--ease-out)";
    el.style.clipPath = band();
    el.style.opacity = "0";
    const end = (e: TransitionEvent) => {
      if (e.target === el && e.propertyName === "clip-path") onGone();
    };
    el.addEventListener("transitionend", end);
    const late = window.setTimeout(onGone, DURATION.sheet * 3); // no transition ran (a hidden page): it still goes
    return () => {
      el.removeEventListener("transitionend", end);
      window.clearTimeout(late);
    };
  }, [closing, band, still, onGone]);
  return (
    <section
      ref={box}
      aria-label={words.title}
      className="relative col-start-1 row-start-1 self-start rounded-sheet bg-surface-panel/82 p-5 shadow-[inset_0_calc(var(--spacing)*0.25)_0_color-mix(in_srgb,var(--switch-knob)_90%,transparent),inset_0_0_0_calc(var(--spacing)*0.125)_color-mix(in_srgb,var(--switch-knob)_50%,transparent),var(--shadow-elev-3)] backdrop-blur-3xl backdrop-saturate-[1.8]"
    >
      <h2 className="m-0 text-title font-semibold text-fg-1">{words.title}</h2>
      {/* what can't come back ("Sent email can't be unsent."), on the card itself, so a tap is told as plainly as a spoken yes */}
      <p className="m-0 mt-1 text-small text-fg-2">{words.lead[words.lead.length - 1]}</p>
      {item && viewing !== null && preview ? (
        <>
          <ItemPreview key={viewing} item={item} load={() => preview(viewing)} open={open(item.ref)} />
          {card.items.length > 1 && (
            <Button variant="text" size="lg" onClick={() => setViewing(null)} className="mt-2">
              {W.BACK}
            </Button>
          )}
        </>
      ) : (
        <ul className="m-0 mt-3 flex flex-col gap-2 p-0">
          {card.items.map((it, i) => (
            <ResultRow key={`${i}-${it.title}`} glyph={glyphOf(it.ref, card.email)} title={it.title} sub={it.sub} state="still" onOpen={preview ? () => setViewing(i) : open(it.ref)} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** words that settle in one by one as they arrive (each new or changed word rises a little and fades in) */
function SettleWords({ text, step = 0 }: { text: string; step?: number }) {
  const parts = text.split(/(\s+)/);
  let n = 0;
  return <>{parts.map((w, i) => (w === "" || /^\s+$/.test(w) ? w : <Word key={`${i}:${w}`} word={w} delay={step * n++} />))}</>;
}

function Word({ word, delay }: { word: string; delay: number }) {
  const entered = useEntered();
  return (
    <span
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
      className={cx("inline-block transition-[opacity,translate] duration-(--duration-page) ease-(--ease-out)", entered ? "translate-y-0 opacity-100" : "translate-y-[calc(var(--motion-shift)*0.75)] opacity-0")}
    >
      {word}
    </span>
  );
}

// ---- the island ----

function IslandBody({ look, p, heard }: { look: IslandLook; p: PhoneFaceProps; heard: (at: DOMRect | null) => void }) {
  switch (look) {
    case "idle":
      return <IdleBody p={p} />;
    case "listen":
      return <ListenBody p={p} heard={heard} />;
    case "ok":
      return p.card ? <YesBody p={p} card={p.card} /> : null;
    default:
      return <WorkBody p={p} />;
  }
}

function IdleBody({ p }: { p: PhoneFaceProps }) {
  const speak = p.moment === "run" ? p.onSpeakAgain : p.onSpeak;
  const off = p.moment === "ask" && p.limited;
  return (
    <div className="flex h-16 items-center gap-2 pr-2.5 pl-2">
      <IslandButton
        look="bare"
        align="start"
        disabled={off}
        onClick={() => {
          tap();
          primeTalk();
          speak();
        }}
      >
        <Icon name="mic" size={22} stroke={1.75} />
        <span className="text-callout font-semibold text-toast-ink">{W.TAP_TO_SPEAK}</span>
      </IslandButton>
      <span className="flex-1" />
      <IslandIconButton label={W.TYPE_INSTEAD} icon="keyboard" onClick={p.onType} />
    </div>
  );
}

function ListenBody({ p, heard }: { p: PhoneFaceProps; heard: (at: DOMRect | null) => void }) {
  const live = p.voice.state === "listening" || p.voice.state === "recording";
  const said = useRef<HTMLParagraphElement>(null);
  useLayoutEffect(() => {
    // where the words stand now: if the request starts, its line flies up from here
    heard(p.text.trim() && said.current ? said.current.getBoundingClientRect() : null);
  });
  const words = p.text.trim() !== "" ? p.text : p.voice.state === "sending" ? W.WRITING_DOWN : W.LISTENING;
  return (
    <div className="flex h-49 flex-col px-5 pt-4 pb-3">
      <VoiceLine beat={p.text.length} active={live} />
      {/* the newest words stay in view; older lines slide up under a soft edge, never a hard cut */}
      <div className="mt-2 flex min-h-0 flex-1 flex-col justify-end overflow-hidden [mask-image:linear-gradient(to_bottom,transparent,black_calc(var(--spacing)*5))]">
        {/* once the request starts, these words are the ones flying up: they leave the island at once, never twice on screen */}
        <p ref={said} aria-live="polite" className={cx("m-0 text-callout font-semibold break-words text-toast-ink", p.moment === "run" && "opacity-0")}>
          <SettleWords text={words} />
        </p>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <IslandIconButton label={W.CANCEL} icon="close" onClick={p.onCancel} />
        <IslandIconButton label={W.START} icon="send" look="solid" onClick={p.onStart} />
      </div>
    </div>
  );
}

function WorkBody({ p }: { p: PhoneFaceProps }) {
  const running = p.steps.filter((s) => s.state === "now").length;
  const { footer } = p.stage;
  // the island says Sending… exactly while a row does (one picture of the one thing that can't come back); once every
  // row is done and the run is still writing up what it did, it says it is finishing: Done comes with the run (the
  // title and the island together), never before it
  const sendingRow = (p.deciding || p.stage.sending) && p.steps.some((s) => s.state === "now" || s.state === "wait");
  const allDone = p.steps.length > 0 && p.steps.every((s) => s.state === "done" || s.state === "skipped");
  let label: string = p.steps.length === 0 ? W.MAKING_A_PLAN : W.workingOn(running);
  let controls: ReactNode = (
    <IslandButton onClick={p.actions.stop} disabled={p.actions.busy}>
      {W.STOP}
    </IslandButton>
  );
  let spin = true;
  let sub: string | null = null;
  if (p.offline) {
    spin = false;
    label = W.OFFLINE_STATUS;
    sub = W.OFFLINE_SUB; // Atlas keeps going on the server: this says so, and that the screen catches up
    controls = null;
  } else if (sendingRow) {
    label = W.sendingWords(p.card?.verb ?? "send");
    controls = null;
  } else if (allDone) {
    label = W.FINISHING_UP;
    controls = null;
  } else if (p.cardClosed) {
    spin = false;
    label = W.READY;
    controls = (
      <IslandButton look="sun" onClick={p.onOpenCard}>
        {W.REVIEW}
      </IslandButton>
    );
  } else if (footer === "paused") {
    spin = false;
    label = W.PAUSED;
    controls = (
      <>
        <IslandButton onClick={p.actions.stop} disabled={p.actions.busy}>
          {W.STOP}
        </IslandButton>
        <IslandButton look="solid" onClick={p.actions.resume} disabled={p.actions.busy}>
          {W.RESUME}
        </IslandButton>
      </>
    );
  } else if (footer === "fail") {
    spin = false;
    label = ""; // the row says what didn't work; the island only offers what to do about it
    controls = (
      <>
        <IslandButton onClick={p.actions.skip} disabled={p.actions.busy}>
          {W.SKIP_IT}
        </IslandButton>
        <IslandButton look="solid" onClick={p.actions.retry} disabled={p.actions.busy}>
          {W.TRY_AGAIN}
        </IslandButton>
      </>
    );
  } else if (p.waitKind === "end") {
    spin = false; // the card is on its way (it opens once the last found line has shown)
    label = W.READY;
    controls = null;
  } else if (p.waitKind === "ask") {
    spin = false;
    label = W.WAITING_FOR_YOU;
  }
  return (
    <div className="flex h-16 items-center gap-3 pr-2.5 pl-5.5">
      {spin ? (
        <svg viewBox="0 0 24 24" width={16} height={16} fill="none" aria-hidden className="shrink-0 animate-spin motion-reduce:animate-none">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
          <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
      ) : (
        p.cardClosed && <span aria-hidden className="size-2 shrink-0 rounded-pill bg-status-you" />
      )}
      <output className="flex min-w-0 flex-1 flex-col text-callout text-toast-ink/90">
        <span className="truncate">{label}</span>
        {sub && <span className="text-small text-toast-ink/70">{sub}</span>}
      </output>
      {controls}
    </div>
  );
}

/** one line replaced by the next (keyed by its words): the new words rise in softly */
function Swap({ children }: { children: string }) {
  const entered = useEntered();
  return (
    <span className={cx("block truncate transition-[opacity,translate] duration-(--duration-page) ease-(--ease-out)", entered ? "translate-y-0 opacity-100" : "translate-y-[calc(var(--motion-shift)*0.75)] opacity-0")}>
      {children}
    </span>
  );
}

const COUNT_MS = COUNTDOWN_S * 1000;
/** the longest the card's question may take to be said (it is one short sentence) before the mic opens anyway */
const ASK_MAX_MS = 10_000;

/** the yes: the island becomes the one amber button under the thumb, and Atlas asks out loud and listens for the answer */
function YesBody({ p, card }: { p: PhoneFaceProps; card: RunCard }) {
  const asked = W.askAloud(card);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState(true);
  const [heard, setHeard] = useState<"listen" | "off">("listen");
  // a spoken yes: when it goes (on the wall clock, so a slow or busy page can only make it later, never shorter), the
  // words that said it, and the clock as last read (what shows, "Heard …" then 3, 2, 1, is worked out from these)
  const [due, setDue] = useState<number | null>(null);
  const [said, setSaid] = useState("");
  const [now, setNow] = useState(0);
  const sending = useRef(false);
  const hidden = usePageHidden();
  const { approve, onDeciding, onCloseCard, onAnswerListen, onStopListen, text } = p;
  const yes = useCallback(async () => {
    if (sending.current || !approve) return;
    sending.current = true; // one yes, however many taps or words
    tap();
    setBusy(true);
    setProblem(null);
    onStopListen();
    onDeciding(true);
    try {
      // everything it shows, and the plan it showed: the server runs exactly that, and nothing else
      await approve(card.id, true, { items: card.items.map((_, i) => i), ...(card.plan_sha ? { plan_sha: card.plan_sha } : {}) });
      onCloseCard(true);
    } catch (e) {
      sending.current = false;
      setBusy(false);
      setDue(null);
      setHeard("off");
      setProblem(errorWords(e));
      onDeciding(false);
    }
  }, [approve, card, onCloseCard, onDeciding, onStopListen]);
  /** stop listening and stop any countdown: from here only a tap says yes */
  const stop = useCallback(() => {
    onStopListen();
    setDue(null);
    setHeard("off");
  }, [onStopListen]);
  // Atlas asks out loud once, in the person's read-aloud voice when it can (the server's words for this card, the same as
  // `asked`), else the device's; the mic opens only once it has finished
  useEffect(() => {
    let alive = true;
    const voice = deviceVoice();
    const el = primedAudio(); // unlocked by the tap that started (Tap to speak)
    let url = "";
    const drop = () => {
      if (url) URL.revokeObjectURL(url);
      url = "";
    };
    let opened = false;
    const listen = () => {
      drop();
      if (!alive || opened) return;
      opened = true;
      window.clearTimeout(late);
      setAsking(false);
      onAnswerListen();
    };
    // a question whose sound never ends (a phone that kept it from playing, a voice that never says it finished): the mic
    // opens anyway, so the spoken yes still works
    const late = window.setTimeout(() => {
      voice.cancel();
      if (el && url) el.pause();
      listen();
    }, ASK_MAX_MS);
    const device = () => alive && void voice.say(asked, null).then(listen);
    if (p.speakAsk && el) {
      p.speakAsk(card.id).then((got) => {
        if (!alive) return;
        url = URL.createObjectURL(decode(got.audio, got.type));
        el.onended = listen;
        el.onerror = listen;
        el.src = url;
        Promise.resolve()
          .then(() => el.play())
          .catch(() => {
            drop();
            device();
          });
      }, device);
    } else device();
    return () => {
      alive = false;
      window.clearTimeout(late);
      voice.cancel();
      if (el && url) el.pause();
      drop();
      onStopListen();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per card
  }, [card.id]);
  // the words heard while this card is open: a plain yes starts the countdown, any no closes the card (Not now). After
  // the yes it keeps listening: words that are no longer a plain yes ("send it… no, wait") stop the countdown. While
  // Atlas still asks, the mic isn't open for the answer: the box still holds what was said before (the request), which
  // is never shown as heard nor taken as the answer (it is emptied when the mic opens)
  useEffect(() => {
    if (heard !== "listen" || asking) return;
    const answer = answerOf(text, asked);
    if (due === null) {
      if (answer === "yes") {
        tap();
        setSaid(text.trim());
        const at = Date.now();
        setNow(at);
        setDue(at + HEARD_MS + COUNT_MS);
      } else if (answer === "no") {
        onStopListen();
        onCloseCard(false);
      }
    } else if (answer === "yes") setSaid(text.trim());
    else if (text.trim()) stop();
  }, [text, heard, asking, due, asked, onStopListen, onCloseCard, stop]);
  // the countdown: it wakes when what shows changes (the end of "Heard …", each whole second) and at zero says the same yes as the tap
  useEffect(() => {
    if (due === null) return;
    const left = due - Date.now();
    if (left <= 0) {
      void yes();
      return;
    }
    const t = window.setTimeout(() => setNow(Date.now()), left > COUNT_MS ? left - COUNT_MS : left % 1000 || 1000);
    return () => window.clearTimeout(t);
  }, [due, now, yes]);
  // the app goes to the background: nothing counts down or listens unseen; back here, it takes a tap
  useEffect(() => {
    if (hidden) stop();
  }, [hidden, stop]);
  const listening = p.voice.state !== "idle" && heard === "listen";
  const voiceLine = (
    <span aria-hidden className={cx("block px-2 text-toast-ink transition-opacity duration-(--duration-page) ease-standard", listening ? "opacity-80" : "opacity-25")}>
      <VoiceLine beat={text.length} active={listening} />
    </span>
  );
  if (due !== null && !busy) {
    const left = due - now;
    const line = left > COUNT_MS ? W.HEARD(said) : W.goingIn(card.verb, Math.max(1, Math.ceil(left / 1000)));
    return (
      <div className="flex h-49 flex-col justify-end px-3 pt-3 pb-2">
        {voiceLine}
        {/* what can't come back is on the card above, all through the countdown: said once, not twice */}
        <output className="mt-2 mb-3 block text-center text-headline font-semibold text-toast-ink tabular-nums">
          <Swap key={line}>{line}</Swap>
        </output>
        <IslandButton look="quiet" wide onClick={stop}>
          {W.CANCEL}
        </IslandButton>
      </div>
    );
  }
  const heardWords = !asking && text.trim() && !echoOf(text, asked) ? text.trim() : "";
  // one line from the start (the question is said aloud and the card asks it; the line says how to answer), the words
  // as they are heard, and after Cancel or a no to the voice only the tap
  const line = problem ?? (asking || listening ? (heardWords ? W.HEARD(heardWords) : W.voiceHint(card)) : W.tapHint(card));
  return (
    <div className="flex h-49 flex-col justify-end px-3 pt-3 pb-2">
      {voiceLine}
      <p role={problem ? "alert" : undefined} className="m-0 mt-1.5 mb-2.5 truncate text-center text-small text-toast-ink/75">
        {line}
      </p>
      <IslandButton look="sun" wide busy={busy} onClick={() => void yes()}>
        {busy ? W.sendingWords(card.verb) : W.phoneSendWords(card)}
      </IslandButton>
      <div className="mt-1 flex items-center justify-center gap-2">
        <IslandButton look="bare" onClick={() => onCloseCard(false)}>
          {W.NOT_NOW}
        </IslandButton>
        {!listening && !asking && !busy && (
          <IslandButton
            look="bare"
            onClick={() => {
              setHeard("listen");
              onAnswerListen();
            }}
          >
            <Icon name="mic" size={18} stroke={1.75} />
            {W.ANSWER_BY_VOICE}
          </IslandButton>
        )}
      </div>
    </div>
  );
}
