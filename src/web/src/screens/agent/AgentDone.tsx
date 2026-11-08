// The Done moment's own parts (AM-3): the title and the line above the strip, and the hint, the Undo link and the Done button
// under it. They come in one after the other (the title, then the line, then the footer) once the run's words have gone.
// The title and the line are the server's, written from facts; a full answer (a run that needed no app) sits inline under the
// line as plain text: whatever it holds is words, never a link or a tag.
import { type Ref } from "react";

import type { RunView } from "../../lib/agentRun";
import { Fade } from "../../ui/agent/Fade";
import { Markdown } from "../../ui/Markdown";
import { Button } from "../../ui/primitives/Button";
import { DONE, KEEP_GOING, KEYS, tapAStep, UNDO_LINK } from "./words";

const STAGGER = 60;

type TopProps = { title: string; line: string; answer: string | null; spot: Ref<HTMLElement> };

/** the room above the strip: empty while the run works (so the strip keeps its place from Run to Done), then the title and the line */
export function DoneTop({ done, ...rest }: { done: boolean } & TopProps) {
  return <div className="flex min-h-28 w-full max-w-xl flex-col items-center justify-end text-center">{done && <TitleAndLine {...rest} />}</div>;
}

/** the title and the line (and the answer) */
function TitleAndLine({ title, line, answer, spot }: TopProps) {
  return (
    <>
      <Fade>
        <h1 ref={spot as Ref<HTMLHeadingElement>} data-lift-top data-mochi-avoid className="m-0 text-display font-bold break-words text-fg-1">
          {title}
        </h1>
      </Fade>
      {line && (
        <Fade delay={STAGGER}>
          <p data-mochi-avoid className="m-0 mt-2 text-callout break-words text-fg-2">{line}</p>
        </Fade>
      )}
      {answer && (
        <Fade delay={STAGGER * 2} className="w-full">
          {/* the model's own words, in markdown as chat's are: the same renderer (text in as text, never HTML) */}
          <div data-mochi-avoid className="mt-6 text-left text-body text-pretty break-words text-fg-1">
            <Markdown text={answer} />
          </div>
        </Fade>
      )}
    </>
  );
}

/** the hint under the strip (only when there are steps to tap). While a panel is open it goes (the mockup's lifted Done has none), and while a step's
 *  peek is open (the peek hangs over the room the hint stands in, so it takes the hint's place instead of covering it):
 *  its box stays, so nothing reflows, and the stage's rise (ui/agent/stageLift) never counts it as a line to keep above the sheet */
export function DoneHint({ wide, labelled, hidden = false }: { wide: boolean; labelled: boolean; hidden?: boolean }) {
  return (
    <Fade delay={STAGGER * 2} data-lift-ignore className={`min-h-12 ${labelled ? "mt-14" : "mt-6"}`}>
      {/* it comes in with the title and the line; it goes at once when a peek or a panel takes its place (never the slow start of a leaving moment) */}
      <p data-mochi-avoid aria-hidden={hidden || undefined} className={`m-0 text-small text-fg-2 transition-opacity duration-(--duration-instant) ease-standard ${hidden ? "opacity-0" : "opacity-100"}`}>
        {tapAStep(wide)}
      </p>
    </Fade>
  );
}

type BottomProps = { view: RunView; wide: boolean; /** the keys are shown (a keyboard, and a pointer that hovers) */ hints: boolean; onDone: () => void; onUndo: (runId: number) => void; onKeepGoing?: () => void };

/** the Undo link (only while something can still go back: the server says so, `view.undo.available`) and the one ink button.
 *  A run that stopped on a limit (capped) leads with Keep going (the ink button, and Enter): Done is secondary and Undo
 *  comes last, so a reflex Enter never ends a run that isn't finished */
export function DoneBottom({ view, wide, hints, onDone, onUndo, onKeepGoing }: BottomProps) {
  const size = wide ? "min-h-14 w-56" : "min-h-14 w-full max-w-md";
  const undo = view.undo.available && (
    <Button variant="text" size="xl" data-undo-link onClick={() => onUndo(view.id)}>
      {UNDO_LINK}
    </Button>
  );
  if (view.status === "capped" && onKeepGoing)
    return (
      <Fade delay={STAGGER * 3} className="flex w-full flex-col items-center gap-2">
        <Button variant="primary" size="xl" keys={hints ? KEYS.done : undefined} aria={wide ? "Enter" : undefined} onClick={onKeepGoing} className={size}>
          {KEEP_GOING}
        </Button>
        <Button variant="secondary" size="xl" onClick={onDone} className={size}>
          {DONE}
        </Button>
        {undo}
      </Fade>
    );
  return (
    <Fade delay={STAGGER * 3} className="flex w-full flex-col items-center gap-2">
      {undo}
      <Button variant="primary" size="xl" keys={hints ? KEYS.done : undefined} aria={wide ? "Enter" : undefined} onClick={onDone} className={size}>
        {DONE}
      </Button>
    </Fade>
  );
}
