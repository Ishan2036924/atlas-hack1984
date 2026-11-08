// The phone face's island (LIFEOS_PHONE_VOICE): one dark glass shape at the thumb that becomes the one control needed now
// (Tap to speak, the words being heard, Stop, Send). Its box is as tall as its tallest look and stays put; what shows is
// clipped from the top, and the clip is a transition, so the shape grows and shrinks from where it is and nothing on the
// page moves. What is inside changes with leave before arrive (useStaged + Fade). Under Reduce Motion the clip still
// changes (the tokens make it short) and the contents only fade.
import type { CSSProperties, ReactNode } from "react";

import { Icon } from "../kit";
import { cx } from "../primitives/shared";
import { useEntered } from "./useEntered";
import { useStaged } from "./useStaged";

export type IslandLook = "idle" | "listen" | "work" | "ok";

/** how tall each look is, in spacing steps (4 px): Tap to speak 64, listening 196, working 64, the yes 196 */
const ISLAND_HEIGHT: Record<IslandLook, number> = { idle: 16, listen: 49, work: 16, ok: 49 };

type Props = { look: IslandLook; label: string; children: (look: IslandLook) => ReactNode };

/** what is inside the island: it settles in (a small rise and a breath of scale) after the old contents have gone */
function Layer({ leaving, children }: { leaving: boolean; children: ReactNode }) {
  const entered = useEntered();
  const on = entered && !leaving;
  return (
    <div
      className={cx(
        "absolute inset-x-0 bottom-0 origin-bottom transition-[opacity,translate,scale]",
        leaving ? "duration-(--duration-fast) ease-accelerate" : "delay-(--duration-instant) duration-(--duration-page) ease-(--ease-out)",
        on ? "translate-y-0 scale-100 opacity-100" : "translate-y-(--motion-shift) scale-(--motion-scale-in) opacity-0",
      )}
    >
      {children}
    </div>
  );
}

export function Island({ look, label, children }: Props) {
  const { shown, leaving } = useStaged(look);
  // the shape: one box as tall as the tallest look; the clip, the light on its top edge and its shadow all follow the
  // look's height on the same curve (clip-path and transform only), so it grows and shrinks like one piece of glass
  const style = { "--island-n": ISLAND_HEIGHT[look] } as CSSProperties;
  const move = "duration-(--duration-hop) ease-(--ease-drawer)";
  return (
    <div className="pointer-events-none sticky bottom-0 z-(--z-sticky) -mx-2 shrink-0 pb-3 safe-bottom">
      <div className="relative h-49" style={style}>
        <div
          aria-hidden
          className={cx("absolute inset-x-3 bottom-0 h-full origin-bottom scale-y-[calc(var(--island-n)/49)] rounded-[calc(var(--spacing)*8)] shadow-elev-3 transition-[scale]", move)}
        />
        <section
          aria-label={label}
          data-look={look}
          className={cx(
            "pointer-events-auto absolute inset-0 overflow-hidden rounded-[calc(var(--spacing)*8)] bg-toast/92 text-toast-ink backdrop-blur-2xl backdrop-saturate-[1.4] transition-[clip-path]",
            "[clip-path:inset(calc((49-var(--island-n))*var(--spacing))_0_0_0_round_calc(var(--spacing)*8))]",
            move,
          )}
        >
          {/* the light: a bright hairline where the glass's top edge is, a faint inner ring and a glow just under the edge */}
          <div
            aria-hidden
            className={cx(
              "pointer-events-none absolute inset-0 translate-y-[calc((49-var(--island-n))*var(--spacing))] rounded-[calc(var(--spacing)*8)] transition-[translate]",
              // the glass is made of light, not see-through: a 1 px lit edge, a faint ring, a soft glow fading down from the edge
              "shadow-[inset_0_calc(var(--spacing)*0.25)_0_color-mix(in_srgb,var(--switch-knob)_18%,transparent),inset_0_0_0_calc(var(--spacing)*0.125)_color-mix(in_srgb,var(--switch-knob)_7%,transparent),inset_0_calc(var(--spacing)*4)_calc(var(--spacing)*8)_calc(var(--spacing)*-4)_color-mix(in_srgb,var(--switch-knob)_11%,transparent)]",
              move,
            )}
          />
          <Layer key={shown} leaving={leaving}>
            {children(shown)}
          </Layer>
        </section>
      </div>
    </div>
  );
}

type ButtonLook = "quiet" | "solid" | "sun" | "bare";
const LOOK: Record<ButtonLook, string> = {
  quiet: "bg-toast-ink/12 text-toast-ink hover:bg-toast-ink/18",
  solid: "bg-toast-ink text-toast hover:bg-toast-ink/90",
  sun: "bg-status-you text-on-sun hover:bg-[color-mix(in_srgb,var(--status-you)_90%,var(--fg-1))]",
  bare: "bg-transparent text-toast-ink/80 hover:text-toast-ink",
};

type IslandButtonProps = { look?: ButtonLook; onClick: () => void; children: ReactNode; wide?: boolean; busy?: boolean; disabled?: boolean; /** start: the words sit at the left (Tap to speak) */ align?: "start" | "center" };

/** a button on the island: light on its dark glass, at least 44 px tall (a phone's thumb), the press a small scale */
export function IslandButton({ look = "quiet", onClick, children, wide = false, busy = false, disabled = false, align = "center" }: IslandButtonProps) {
  return (
    <button
      type="button"
      aria-busy={busy || undefined}
      disabled={disabled}
      onClick={onClick}
      className={cx(
        "press inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-pill px-4.5 text-callout font-semibold transition-[background-color,color,scale] duration-(--duration-fast) ease-standard disabled:opacity-50",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
        wide && "min-h-13 w-full rounded-tile",
        align === "start" && "justify-start px-3",
        LOOK[look],
      )}
    >
      {children}
    </button>
  );
}

/** a round 44 px button on the island that has no words (the × that cancels, the arrow that starts): `label` names it */
export function IslandIconButton({ label, icon, look = "quiet", onClick }: { label: string; icon: string; look?: ButtonLook; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className={cx("press grid size-11 shrink-0 place-items-center rounded-pill transition-[background-color,color,scale] duration-(--duration-fast) ease-standard focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus", LOOK[look])}
    >
      <Icon name={icon} size={20} stroke={2} />
    </button>
  );
}
