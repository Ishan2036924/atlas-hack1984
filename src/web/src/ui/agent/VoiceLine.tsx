// The line in the listening island (the phone face): it moves with what is heard, not on a loop. Each new word (`beat`
// changes) lifts it and it settles back over about a second; between words it is almost still. Drawn on the
// display's clock (requestAnimationFrame) only while something is moving; under Reduce Motion it is a still line.
import { useEffect, useRef } from "react";

import { useReducedMotion } from "../../lib/motion";

const W = 318;
const H = 36;
const SETTLE_MS = 900; // a word lifts it; it settles slowly, like a breath

function pathAt(level: number, t: number, phase = 0): string {
  let d = "";
  for (let x = 0; x <= W; x += 2) {
    const env = Math.sin((x / W) * Math.PI) ** 1.5; // quiet at the ends, fullest in the middle
    const y = H / 2 + env * level * 11 * Math.sin(x * 0.048 + t * 4.6 + phase) * Math.cos(x * 0.018 - t * 2.2);
    d += `${x ? "L" : "M"}${x} ${y.toFixed(2)}`;
  }
  return d;
}

export function VoiceLine({ beat, active }: { beat: number; active: boolean }) {
  const path = useRef<SVGPathElement>(null);
  const echo = useRef<SVGPathElement>(null);
  const kick = useRef(0);
  const still = useReducedMotion();
  useEffect(() => {
    if (beat > 0) kick.current = performance.now();
  }, [beat]);
  useEffect(() => {
    if (still || !active) {
      path.current?.setAttribute("d", pathAt(0, 0));
      echo.current?.setAttribute("d", pathAt(0, 0));
      return;
    }
    let raf = 0;
    const frame = (now: number) => {
      const since = now - kick.current;
      const k = Math.max(0, 1 - since / SETTLE_MS);
      const level = 0.1 + 0.9 * k * k * (3 - 2 * k); // eased: no edge when a word's lift fades
      path.current?.setAttribute("d", pathAt(level, now / 1000));
      echo.current?.setAttribute("d", pathAt(level * 0.7, now / 1000, 1.9));
      raf = window.requestAnimationFrame(frame);
    };
    raf = window.requestAnimationFrame(frame);
    return () => window.cancelAnimationFrame(raf);
  }, [active, still]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} aria-hidden className="block h-9 w-full">
      <path ref={echo} d={pathAt(0, 0)} fill="none" stroke="currentColor" strokeOpacity="0.3" strokeWidth="1.5" strokeLinecap="round" />
      <path ref={path} d={pathAt(0, 0)} fill="none" stroke="currentColor" strokeOpacity="0.9" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
