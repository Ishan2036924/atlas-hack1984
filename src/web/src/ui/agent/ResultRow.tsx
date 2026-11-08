// One thing Atlas does, as one row (the phone face): a grey glyph tile, the step's name, one line of detail and one state at
// the right. A row that waits for the person says so as its second line (amber) with only a chevron; the detail line ends
// in "…" before the right-hand column, so nothing can collide. The whole row is the button when it opens something.
// Colour has a job: blue = done (Atlas made it), amber = waits for you, grey = everything else.
// The material is light glass over the sky (blur, a lit top edge, a soft shadow). Changes settle rather than switch: the
// row rises in, a new detail line cross-fades over the old one, the tick arrives with a small, quick settle.
import type { ReactNode } from "react";

import { Icon } from "../kit";
import { COLOR_EASE, FOCUS_RING, HOVER_FILL, cx } from "../primitives/shared";
import { useEntered } from "./useEntered";

export type RowState = "next" | "now" | "done" | "you" | "fail" | "still";

type Props = {
  glyph: string;
  title: string;
  /** one line of detail: what it is doing, what it found, or what it waits for */
  sub: string;
  state: RowState;
  /** the row opens something (an item, the card that waits); none: it is only read */
  onOpen?: () => void;
  /** where it is in the list: rows arrive one after another, rising from the island */
  index?: number;
  /** a control of its own at the right (Try again), instead of the state mark */
  action?: ReactNode;
  /** it sits over a dark sky (the night's): a more solid glass, so the grey detail line keeps its contrast */
  onDark?: boolean;
};

const SUB: Record<RowState, string> = { next: "text-fg-2", now: "text-fg-2", done: "text-fg-2", you: "font-semibold text-status-you-ink", fail: "text-fg-1", still: "text-fg-2" };

/** something that arrives: in from a little below (or small), settling; under Reduce Motion a plain fade (the tokens) */
function Arrive({ children, className, from = "rise" }: { children: ReactNode; className?: string; from?: "rise" | "small" }) {
  const entered = useEntered();
  const off = from === "rise" ? "translate-y-[calc(var(--motion-shift)*0.75)] opacity-0" : "scale-(--motion-scale-in) opacity-0";
  return <span className={cx("transition-[opacity,translate,scale] duration-(--duration-page) ease-(--ease-out)", entered ? "translate-y-0 scale-100 opacity-100" : off, className)}>{children}</span>;
}

function Mark({ state, opens }: { state: RowState; opens: boolean }) {
  if (state === "now")
    return (
      <svg viewBox="0 0 24 24" width={16} height={16} fill="none" aria-hidden className="animate-[spin_1.6s_linear_infinite] text-fg-2 motion-reduce:animate-none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.18" strokeWidth="2.5" />
        <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    );
  return (
    <span className="flex items-center gap-2">
      {state === "done" && (
        <Arrive key="tick" from="small" className="grid size-5 shrink-0 place-items-center rounded-pill bg-info text-fg-on-ink shadow-elev-1">
          <Icon name="check" size={12} stroke={3} />
        </Arrive>
      )}
      {opens && (
        <span aria-hidden className="text-fg-3">
          <Icon name="chevron" size={16} stroke={1.75} />
        </span>
      )}
    </span>
  );
}

export function ResultRow({ glyph, title, sub, state, onOpen, index = 0, action, onDark = false }: Props) {
  const entered = useEntered();
  const body = (
    <>
      <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-control bg-surface-field/80 text-fg-2 shadow-[inset_0_0_0_calc(var(--spacing)*0.125)_color-mix(in_srgb,var(--fg-1)_6%,transparent)]">
        <Icon name={glyph} size={18} stroke={1.75} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col text-left">
        <span className="truncate text-body font-semibold text-fg-1">{title}</span>
        {/* a new detail line comes in over the old one (keyed by its words), so "Picking lunch" becomes "Picked …" softly */}
        <Arrive key={`${state}:${sub}`} className={cx("flex min-w-0 items-center gap-1.5 text-small", SUB[state])}>
          {state === "you" && <span aria-hidden className="size-1.5 shrink-0 rounded-pill bg-status-you" />}
          <span className="truncate">{sub}</span>
        </Arrive>
      </span>
      <span className="flex shrink-0 justify-end pl-1">{action ?? <Mark key={state} state={state} opens={Boolean(onOpen)} />}</span>
    </>
  );
  const look = cx(
    "flex min-h-16 w-full min-w-0 items-center gap-3 rounded-card px-3.5 py-2.5",
    // light glass: mostly white (the detail line keeps its contrast over the bluest sky), the sky blurred behind it; light
    // catches the top edge; a soft shadow lifts it
    onDark ? "bg-surface-panel/90" : "bg-surface-panel/82",
    "backdrop-blur-2xl backdrop-saturate-[1.2]",
    "shadow-[inset_0_calc(var(--spacing)*0.25)_0_color-mix(in_srgb,var(--switch-knob)_85%,transparent),inset_0_0_0_calc(var(--spacing)*0.125)_color-mix(in_srgb,var(--switch-knob)_45%,transparent),var(--shadow-elev-2)]",
    "transition-[opacity,translate,scale] duration-(--duration-hop) ease-(--ease-out)",
    entered ? "translate-y-0 scale-100 opacity-100" : "translate-y-[calc(var(--motion-shift)*6)] scale-(--motion-scale-in) opacity-0",
  );
  const delay = { transitionDelay: `calc(var(--duration-instant) * ${index})` };
  if (onOpen && !action)
    return (
      <li className="list-none">
        <button type="button" data-row={state} onClick={onOpen} style={delay} className={cx(look, HOVER_FILL, FOCUS_RING, COLOR_EASE)}>
          {body}
        </button>
      </li>
    );
  return (
    <li data-row={state} style={delay} className={cx(look, "list-none")}>
      {body}
    </li>
  );
}
