// The Agent | Full app switch (AM-1). ModeSwitch is the switch alone: the full app shows it beside the sidebar's logo
// and in Home's top-right corner while Agent mode is on for the person. ModeBar is Agent mode's top row: the one logo,
// top-left (UI_CONTRACT §21), and the switch top-right (Home's phone corner uses the same slot, MODE_SLOT).
// The switch is glass (Ishan, 2026-11-07: "transparent and more amazing and more glass effect"): a frosted pill in the
// island's family (the sky blurred through it, a lit top edge, a soft ring) with one solid thumb that slides under the
// chosen half (transform only; it jumps under Reduce Motion). The words are drawn twice: once in the pill's own colour,
// once in the thumb's, clipped to the thumb and riding with it, so a word changes colour exactly where the thumb's edge
// passes it. The buttons on top are the targets (the words they carry are for screen readers): two equal halves, each
// a 44 px target on a touch screen. Its tone follows what it sits on: light glass on a day sky or a page; dark glass,
// white words, on the night's sky (an ancestor marked `group/sky` with data-night: the phone face's top row, Home).
// On a plain page (no sky behind it) the track is a soft grey instead. Reduce Transparency: solid, no blur; Increase
// Contrast: a firm ring.
import { type KeyboardEvent, useRef } from "react";

import { PRODUCT_NAME } from "../../lib/brand";
import type { Mode } from "../../lib/agentMode";
import { Logo } from "../Logo";
import { cx, FOCUS_RING, TOUCH } from "../primitives/shared";

const OPTIONS: ReadonlyArray<{ id: Mode; label: string }> = [
  { id: "agent", label: "Agent" },
  { id: "full", label: "Full app" },
];

/** where the switch sits at the top of a phone screen: the phone face's top row and Home's corner alike */
export const MODE_SLOT = "safe-top flex min-h-14 shrink-0 items-center gap-3 px-4 pt-2 expanded:px-8";

const GLASS = cx(
  // on a page (no sky behind it: the sidebar, a wide Agent mode) a soft grey fill, so the white thumb stands off it; on
  // a day sky (an ancestor `group/sky` with data-sky="day") clear glass, the sky blurred through it; the night's below
  "bg-surface-field/90 backdrop-blur-xl backdrop-saturate-[1.6] group-data-[sky=day]/sky:bg-surface-panel/60",
  // a hairline of light along the top edge and a faint ring (on a white page the ring is what draws it)
  "shadow-[inset_0_calc(var(--spacing)*0.25)_0_color-mix(in_srgb,var(--switch-knob)_70%,transparent),inset_0_0_0_calc(var(--spacing)*0.25)_color-mix(in_srgb,var(--fg-1)_8%,transparent),var(--shadow-elev-1)]",
  "group-data-[night]/sky:bg-toast/40",
  "group-data-[night]/sky:shadow-[inset_0_calc(var(--spacing)*0.25)_0_color-mix(in_srgb,var(--switch-knob)_35%,transparent),inset_0_0_0_calc(var(--spacing)*0.25)_color-mix(in_srgb,var(--switch-knob)_14%,transparent),var(--shadow-elev-1)]",
  "[@media(prefers-reduced-transparency:reduce)]:bg-surface-panel [@media(prefers-reduced-transparency:reduce)]:backdrop-blur-none group-data-[night]/sky:[@media(prefers-reduced-transparency:reduce)]:bg-toast",
  "contrast-more:shadow-[inset_0_0_0_calc(var(--spacing)*0.25)_color-mix(in_srgb,var(--fg-1)_30%,transparent)]",
);
// the thumb: white on a light theme; on a dark theme the raised grey taken toward white, so it stands off the track
const THUMB = cx(
  "bg-[color-mix(in_srgb,var(--surface-raised)_78%,var(--switch-knob))]",
  "shadow-[inset_0_calc(var(--spacing)*0.25)_0_color-mix(in_srgb,var(--switch-knob)_90%,transparent),var(--shadow-elev-1)]",
  "group-data-[night]/sky:bg-switch-knob/92",
);
const WORD = "flex min-w-0 items-center justify-center px-3 text-small font-semibold whitespace-nowrap";
const SLIDE = "transition-transform duration-(--duration-panel) ease-(--ease-drawer) motion-reduce:transition-none";

type Props = { mode: Mode; onMode: (next: Mode) => void };

/** `full`: the switch fills the row (the sidebar's top); otherwise its two halves are as wide as "Full app" needs */
export function ModeSwitch({ mode, onMode, full = false }: Props & { full?: boolean }) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const at = Math.max(0, OPTIONS.findIndex((o) => o.id === mode));
  const right = at === 1;
  // one tab stop; the arrows, Home and End pick (a radio group's keys)
  const key = (e: KeyboardEvent, from: number) => {
    const to = e.key === "Home" ? 0 : e.key === "End" ? OPTIONS.length - 1 : ["ArrowRight", "ArrowDown"].includes(e.key) ? (from + 1) % OPTIONS.length : ["ArrowLeft", "ArrowUp"].includes(e.key) ? (from + OPTIONS.length - 1) % OPTIONS.length : null;
    if (to === null) return;
    e.preventDefault();
    onMode(OPTIONS[to].id);
    buttons.current[to]?.focus();
  };
  return (
    <div role="radiogroup" aria-label="View" className={cx("relative isolate grid h-10 shrink-0 grid-cols-2 rounded-pill p-0.5", full ? "w-full" : "w-44", GLASS)}>
      {/* the words in the pill's colour */}
      <span aria-hidden className="pointer-events-none absolute inset-0.5 grid grid-cols-2 text-fg-1/70 group-data-[night]/sky:text-switch-knob/80">
        {OPTIONS.map((o) => (
          <span key={o.id} className={WORD}>
            {o.label}
          </span>
        ))}
      </span>
      {/* the thumb, and in it the same words in its colour, held still while the thumb slides over them */}
      <span aria-hidden data-thumb="" className={cx("pointer-events-none absolute inset-y-0.5 left-0.5 w-[calc(50%-var(--spacing)*0.5)] overflow-hidden rounded-pill", SLIDE, THUMB, right ? "translate-x-full" : "translate-x-0")}>
        <span className={cx("absolute inset-y-0 left-0 grid w-[200%] grid-cols-2 text-fg-1 group-data-[night]/sky:text-toast", SLIDE, right ? "-translate-x-1/2" : "translate-x-0")}>
          {OPTIONS.map((o) => (
            <span key={o.id} className={WORD}>
              {o.label}
            </span>
          ))}
        </span>
      </span>
      {OPTIONS.map((o, i) => (
        <button
          key={o.id}
          ref={(el) => {
            buttons.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={i === at}
          tabIndex={i === at ? 0 : -1}
          onClick={() => onMode(o.id)}
          onKeyDown={(e) => key(e, i)}
          className={cx(WORD, "rounded-pill text-transparent", TOUCH, FOCUS_RING)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** `onSky`: the row sits on a sky (the phone face): the switch is clear glass; `onDark`: on the night's sky, so the logo
 *  is its light-ink file and the switch dark glass */
export function ModeBar({ mode, onMode, onSky = false, onDark = false }: Props & { onSky?: boolean; onDark?: boolean }) {
  return (
    <div data-sky={onSky ? (onDark ? "night" : "day") : undefined} data-night={onDark ? "" : undefined} className={cx("group/sky justify-between", MODE_SLOT)}>
      <Logo kind="symbol" height={28} label={PRODUCT_NAME} onDark={onDark} />
      <ModeSwitch mode={mode} onMode={onMode} />
    </div>
  );
}
