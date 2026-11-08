// Agent mode's phone face (LIFEOS_PHONE_VOICE) on the fake API and the shared fixture: only on a phone with the flag on;
// the island listens (× cancels, the arrow starts once the words are in; nothing heard is said plainly); one row per real
// step; Stop; the end card on the stage with the amber yes on the island (the same signed approval as the sheet: every
// item and the plan it showed), Not now and Review; Done gives the speak button back; the toggle is the switch.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import symbolOnDark from "../../../assets/brand/atlas-a-symbol-on-dark.svg";
import { extras, SATURDAY, saturday } from "../../../lib/agentFixture";
import type { RunCard } from "../../../lib/agentRun";
import { DURATION, FOUND_HOLD_MS } from "../../../lib/motion";
import type { AgentModeApi } from "../../../lib/agentRun";
import { AgentMode, type AgentModeProps } from "../AgentMode";
import { createFakeAgentModeApi, type FakeOptions } from "../fakeApi";
import { InQuery, QUIET } from "../usageHarness";
import { DONE, EMAIL_LINE, FINISHING_UP, NOTHING_HEARD } from "../words";
import { HEARD_MS } from "./voiceAnswer";

vi.mock("../../../ui/agent/Perch", () => ({ Perch: () => <span data-perch /> }));

// the audio element the tap unlocked (talk's prime): none unless a test hands one in (then Atlas's voice may play)
const sound: { el: { play: () => Promise<void>; pause: () => void; onended: (() => void) | null; onerror: (() => void) | null; src: string } | null } = { el: null };
vi.mock("../../chat/talk/prime", () => ({ primeTalk: () => {}, primedAudio: () => sound.el }));

// the mic: a stand-in that keeps the box's setText and a state the test can read
const mic: { setText: (t: string | ((c: string) => string)) => void; toggled: number; stopped: number; listened: number } = { setText: () => {}, toggled: 0, stopped: 0, listened: 0 };
vi.mock("../../../lib/dictation", async () => {
  const React = await import("react");
  return {
    useDictation: (_api: unknown, _text: string, setText: (t: string | ((c: string) => string)) => void) => {
      const [state, setState] = React.useState<"idle" | "listening">("idle");
      mic.setText = setText;
      return {
        state,
        error: null,
        retry: null,
        seconds: 0,
        max: null,
        say: "",
        toggle: () => {
          mic.toggled++;
          setState("listening");
        },
        // the spoken answer's listening (live only): counted with the taps' too, so "the mic is on" reads the same
        listen: () => {
          mic.toggled++;
          mic.listened++;
          setState("listening");
        },
        stop: () => {
          mic.stopped++;
          setState("idle");
        },
      };
    },
  };
});

const had = window.matchMedia;
function viewport(layout: "phone" | "tablet") {
  const width = layout === "phone" ? 390 : 820;
  window.matchMedia = ((q: string) => {
    const min = /min-width:\s*(\d+)px/.exec(q);
    const matches = q.includes("hover: hover") ? false : min ? width >= Number(min[1]) : false;
    return { matches, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false };
  }) as typeof window.matchMedia;
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  mic.toggled = 0;
  mic.stopped = 0;
  mic.listened = 0;
});
afterEach(() => {
  cleanup();
  sound.el = null;
  hide(false);
  vi.useRealTimers();
  window.matchMedia = had;
  vi.restoreAllMocks();
  localStorage.clear();
});

type Setup = { layout?: "phone" | "tablet"; fake?: FakeOptions; askAloud?: AgentModeApi["askAloud"] } & Partial<AgentModeProps>;
function setup({ layout = "phone", fake, askAloud, ...props }: Setup = {}) {
  viewport(layout);
  const api = Object.assign(createFakeAgentModeApi(fake), askAloud ? { askAloud } : {});
  const onMode = vi.fn();
  const approve = vi.fn(async (id: string) => {
    api.advance(); // the server took it: the next poll is what came of it
    return { id, status: "approved" };
  });
  const view = render(
    <InQuery>
      <AgentMode usageApi={QUIET} phase="evening" onMode={onMode} api={api} approvals={{ approve } as never} phone {...props} />
    </InQuery>,
  );
  return { api, onMode, approve, ...view };
}
const flush = async (ms = 0) => {
  let left = ms;
  do {
    const step = Math.min(left, 50);
    await act(async () => void (await vi.advanceTimersByTimeAsync(step)));
    left -= step;
  } while (left > 0);
};
const poll = async (api: ReturnType<typeof createFakeAgentModeApi>, n = 1) => {
  for (let i = 0; i < n; i++) api.advance();
  await flush(2000);
  await flush(DURATION.fast + 50);
};
/** the app goes to the background (or comes back) */
function hide(on: boolean) {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (on ? "hidden" : "visible") });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => on });
  act(() => void document.dispatchEvent(new Event("visibilitychange")));
}
const REQUEST = "Plan Saturday with Sam: find a time, pick lunch, write it up and send it to Sam";
const island = () => screen.getByRole("region", { name: "Tap to speak" });

/** speak the request into the island and start it */
async function speakAndStart(words = REQUEST) {
  fireEvent.click(within(island()).getByRole("button", { name: /Tap to speak/ }));
  await flush(DURATION.fast + 50);
  act(() => mic.setText(words));
  await flush(0);
  fireEvent.click(within(island()).getByRole("button", { name: "Start" }));
  await flush(400);
}

describe("where it shows", () => {
  it("a phone with the flag: one speak button and a keyboard at the thumb, no Talk and Type pair", async () => {
    setup();
    await flush(50);
    expect(within(island()).getByRole("button", { name: /Tap to speak/ })).toBeTruthy();
    expect(within(island()).getByRole("button", { name: "Type instead" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Talk" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Good evening");
  });

  it("a tablet with the flag, or a phone without it: the stage as it is (Talk and Type)", async () => {
    setup({ layout: "tablet" });
    await flush(50);
    expect(screen.getByRole("button", { name: "Talk" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Tap to speak" })).toBeNull();
    cleanup();
    setup({ phone: false });
    await flush(50);
    expect(screen.getByRole("button", { name: "Talk" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Tap to speak" })).toBeNull();
  });

  it("the words on the sky are ink by day and white on the night's sky, and so is the logo", async () => {
    vi.setSystemTime(new Date(2026, 10, 7, 14, 0));
    setup();
    await flush(50);
    expect(screen.getByRole("heading", { level: 1 }).className).toContain("text-fg-1");
    expect(document.querySelectorAll('[data-logo="symbol"] img')).toHaveLength(2); // both files; the theme picks one
    cleanup();
    vi.setSystemTime(new Date(2026, 10, 7, 22, 0));
    setup();
    await flush(50);
    expect(screen.getByRole("heading", { level: 1 }).className).toContain("text-white");
    const logo = document.querySelectorAll<HTMLImageElement>('[data-logo="symbol"] img');
    expect(logo).toHaveLength(1);
    expect(logo[0].getAttribute("src")).toBe(symbolOnDark); // the light-ink file
  });

    it("the toggle at the top is the Agent | Full app switch", async () => {
    const s = setup();
    await flush(50);
    fireEvent.click(screen.getByRole("radio", { name: "Full app" }));
    expect(s.onMode).toHaveBeenCalledWith("full");
  });
});

describe("the island listens", () => {
  it("Tap to speak turns the mic on in the island (no field): words show as heard, × and the arrow", async () => {
    setup();
    await flush(50);
    fireEvent.click(within(island()).getByRole("button", { name: /Tap to speak/ }));
    await flush(DURATION.fast + 50);
    expect(mic.toggled).toBe(1);
    expect(screen.queryByRole("textbox")).toBeNull();
    act(() => mic.setText("Email Sam that I'm late"));
    await flush(0);
    expect(island().textContent).toContain("Email Sam that I'm late"); // each word settles in on its own
    expect(within(island()).getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(within(island()).getByRole("button", { name: "Start" })).toBeTruthy();
  });

  it("the arrow stops the mic and starts the request with the words heard", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    expect(mic.stopped).toBeGreaterThanOrEqual(1);
    expect(s.api.calls.start).toEqual([REQUEST]);
  });

  it("nothing heard: nothing starts, and it says so plainly", async () => {
    const s = setup();
    await flush(50);
    fireEvent.click(within(island()).getByRole("button", { name: /Tap to speak/ }));
    await flush(DURATION.fast + 50);
    fireEvent.click(within(island()).getByRole("button", { name: "Start" }));
    await flush(400);
    expect(s.api.calls.start).toEqual([]);
    expect(screen.getByRole("alert").textContent).toContain(NOTHING_HEARD);
  });

  it("× throws the words away and gives the speak button back", async () => {
    const s = setup();
    await flush(50);
    fireEvent.click(within(island()).getByRole("button", { name: /Tap to speak/ }));
    await flush(DURATION.fast + 50);
    act(() => mic.setText("never mind"));
    fireEvent.click(within(island()).getByRole("button", { name: "Cancel" }));
    await flush(DURATION.fast + 100);
    expect(mic.stopped).toBe(1);
    expect(s.api.calls.start).toEqual([]);
    expect(within(island()).getByRole("button", { name: /Tap to speak/ })).toBeTruthy();
  });

  it("Type instead opens today's Type moment (the field)", async () => {
    setup();
    await flush(50);
    fireEvent.click(within(island()).getByRole("button", { name: "Type instead" }));
    await flush(400);
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("a typed request starts the run on the phone face: the request as the title, its rows and Stop (never the Type page left blank)", async () => {
    const s = setup();
    await flush(50);
    fireEvent.click(within(island()).getByRole("button", { name: "Type instead" }));
    await flush(400);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: REQUEST } });
    await flush(0);
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flush(400);
    expect(s.api.calls.start).toEqual([REQUEST]);
    await poll(s.api, 3);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(REQUEST);
    expect(screen.getAllByRole("listitem").map((r) => r.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("Calendar")]));
    expect(within(island()).getByRole("button", { name: "Stop" })).toBeTruthy();
  });
});

describe("the run", () => {
  it("on Done, the keyboard opens the Type page for a new request (the run folds into Last run)", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 12);
    await flush(FOUND_HOLD_MS + 400);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Saturday is planned");
    fireEvent.click(within(island()).getByRole("button", { name: "Type instead" }));
    await flush(400);
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("one row per real step, the request as the title, and Stop on the island while it works", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 3);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(REQUEST);
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("Calendar"), expect.stringContaining("Send")]));
    expect(screen.getByText("Four things. I'll ask before anything is sent.")).toBeTruthy();
    fireEvent.click(within(island()).getByRole("button", { name: "Stop" }));
    await flush(100);
    expect(s.api.calls.stop).toBe(1);
  });

  it("the end card: the exact items on the stage, one amber yes on the island, which sends every item and the plan shown", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    expect(screen.getByRole("region", { name: "Send to Sam?" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Tap to speak/ })).toBeNull(); // the island is the yes now
    expect(s.approve).not.toHaveBeenCalled();
    fireEvent.click(within(island()).getByRole("button", { name: "Send both" }));
    await flush(50);
    expect(s.approve).toHaveBeenCalledTimes(1);
    expect(s.approve).toHaveBeenCalledWith("c-end-1", true, { items: [0, 1], plan_sha: (saturday("ready for your ok").card as RunCard).plan_sha });
  });

  it("after a tapped yes the island and the rows tell one story: Sending… on both, then done on both", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    const card = screen.getByRole("region", { name: "Send to Sam?" });
    expect(within(card).getByText(EMAIL_LINE)).toBeTruthy(); // what can't come back is on the card: a tap is told too
    fireEvent.click(within(island()).getByRole("button", { name: "Send both" }));
    let sending = 0;
    for (let i = 0; i < 60; i++) {
      await flush(150);
      if (i % 10 === 9) s.api.advance();
      const says = island().querySelector("output")?.textContent ?? "";
      const rows = [...document.querySelectorAll("main [data-row]")].map((r) => r.textContent ?? "");
      expect(says.includes("Sending…"), `at ${i}: island "${says}", rows ${rows.join(" | ")}`).toBe(rows.some((r) => r.includes("Sending…")));
      if (says.includes("Sending…")) sending++;
    }
    expect(sending).toBeGreaterThan(0);
  });

  it("after the last row the island says it is finishing (never Done under the old title), and Done comes with the run, no hold", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    fireEvent.click(within(island()).getByRole("button", { name: "Send both" }));
    const doneTitle = saturday("done").done?.title ?? "";
    let finishing = false;
    let polledAt = -1; // the first poll that brought the run's done
    let viewsAtDone = -1;
    let titled = -1;
    for (let t = 0; t < 15_000 && titled < 0; t += 100) {
      await flush(100);
      if (t % 1000 === 900 && s.api.at() < SATURDAY.length - 1) {
        s.api.advance();
        if (s.api.at() === SATURDAY.length - 1) viewsAtDone = s.api.calls.view;
      }
      if (viewsAtDone >= 0 && polledAt < 0 && s.api.calls.view > viewsAtDone) polledAt = t;
      const title = screen.getByRole("heading", { level: 1 }).textContent;
      if (title === doneTitle) titled = t;
      else {
        const says = island().querySelector("output")?.textContent ?? "";
        expect(says, `at ${t}`).not.toBe(DONE); // the island never says Done while the screen hasn't
        if (says === FINISHING_UP) finishing = true;
      }
    }
    expect(finishing).toBe(true); // every row done, the run not yet: it says so
    expect(polledAt).toBeGreaterThanOrEqual(0);
    expect(titled - polledAt).toBeLessThan(FOUND_HOLD_MS); // Done with the poll that brought it, never after the found line's hold
  });

  it("a run whose last step finishes in the same poll as the run shows Done at once (its row keeps the found line)", async () => {
    // a run with no card at its end: the last step's found line would be held (FOUND_HOLD_MS) before Done on a wide stage
    const done = { ...SATURDAY[SATURDAY.length - 1], decision: null };
    const script = [SATURDAY[0], SATURDAY[1], SATURDAY[2], SATURDAY[6], SATURDAY[7], SATURDAY[8], done];
    const s = setup({ fake: { script } });
    await flush(50);
    await speakAndStart();
    await poll(s.api, 5); // the last step still working
    s.api.advance(); // it finishes, and the run with it
    const views = s.api.calls.view;
    const doneTitle = done.done?.title ?? "";
    let polledAt = -1;
    let titled = -1;
    for (let t = 0; t < 6000 && titled < 0; t += 50) {
      await flush(50);
      if (polledAt < 0 && s.api.calls.view > views) polledAt = t;
      if (screen.getByRole("heading", { level: 1 }).textContent === doneTitle) titled = t;
    }
    expect(polledAt).toBeGreaterThanOrEqual(0);
    expect(titled - polledAt).toBeLessThan(400); // never the found line's 1.2 s hold
  });

  it("Not now closes the card and keeps it waiting: the island says so, Review opens it again", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    const card = screen.getByRole("region", { name: "Send to Sam?" });
    fireEvent.click(within(island()).getByRole("button", { name: "Not now" }));
    await flush(DURATION.sheet + 100);
    expect(screen.getByRole("region", { name: "Send to Sam?" })).toBe(card); // the card shrinks back into its row first, and stays until that ends
    fireEvent.transitionEnd(card, { propertyName: "clip-path" });
    await flush(0);
    expect(screen.queryByRole("region", { name: "Send to Sam?" })).toBeNull();
    expect(island().querySelector("output")?.textContent).toBe("Ready for your OK");
    fireEvent.click(within(island()).getByRole("button", { name: "Review" }));
    await flush(DURATION.fast + 100);
    expect(screen.getByRole("region", { name: "Send to Sam?" })).toBeTruthy();
    expect(s.approve).not.toHaveBeenCalled();
  });

  it("a card opened again while it shrinks away comes back whole, never stuck faded", async () => {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    const card = screen.getByRole("region", { name: "Send to Sam?" });
    fireEvent.click(within(island()).getByRole("button", { name: "Not now" }));
    await flush(DURATION.fast);
    expect(card.style.opacity).toBe("0"); // on its way out: shrinking into its row, fading
    fireEvent.click(within(island()).getByRole("button", { name: "Review" }));
    await flush(DURATION.fast);
    expect(screen.getByRole("region", { name: "Send to Sam?" })).toBe(card);
    expect(card.style.opacity).toBe("");
    expect(card.style.clipPath).toBe("inset(0px 0px 0px 0px round 24px)");
  });

  it("a card that comes with the last find waits for that find's moment, and is never drawn before it (not even one frame)", async () => {
    const before = saturday("places done");
    const s = setup({ fake: { script: [before, saturday("ready for your ok")], started: true, latest: null }, from: { run: before.id } });
    await flush(2000);
    const drawn: number[] = [];
    const watch = new MutationObserver((list) => {
      for (const m of list)
        m.addedNodes.forEach((n) => {
          if (n instanceof HTMLElement && (n.matches('section[aria-label="Send to Sam?"]') || n.querySelector('section[aria-label="Send to Sam?"]'))) drawn.push(Date.now());
        });
    });
    watch.observe(document.body, { childList: true, subtree: true });
    s.api.advance(); // the next poll brings Docs' find and the card together
    await flush(2050);
    expect(drawn).toEqual([]); // Docs' find has its moment first: the card isn't in the page, not even for one paint
    await flush(FOUND_HOLD_MS + 400);
    expect(screen.getByRole("region", { name: "Send to Sam?" })).toBeTruthy();
    expect(drawn).toHaveLength(1); // then it comes, once
    watch.disconnect();
  });

  it("a run that stopped on a limit says so plainly, and Keep going starts the rest in one tap", async () => {
    const s = setup({ fake: { script: [extras.capped], started: true, latest: null }, from: { run: extras.capped.id } });
    await flush(2400);
    expect(screen.getByText(/I stopped here/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep going" }));
    await flush(50);
    expect(s.api.calls.keepGoing).toBe(1);
  });

  it("Done right after the yes: Undo… waits for the card to have gone, then shows in its place (never over the card, never moving)", async () => {
    const ready = saturday("ready for your ok");
    const s = setup({ fake: { script: [ready, saturday("done")], started: true, latest: null }, from: { run: ready.id } });
    await flush(2000);
    await flush(FOUND_HOLD_MS + 400);
    fireEvent.click(within(island()).getByRole("button", { name: "Send both" }));
    await flush(300);
    expect(s.approve).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Saturday is planned");
    expect(screen.queryByRole("region", { name: "Send to Sam?" })).toBeTruthy(); // the card still shrinking into its row
    expect(screen.queryByRole("button", { name: "Undo…" })).toBeNull();
    await flush(DURATION.sheet * 3 + 100);
    expect(screen.queryByRole("region", { name: "Send to Sam?" })).toBeNull();
    expect(screen.getByRole("button", { name: "Undo…" })).toBeTruthy();
  });

  it("Done: what happened at the top, and the speak button again (a new request starts listening)", async () => {
    const s = setup({ fake: { script: [saturday("done")], started: true, latest: saturday("done") }, from: { run: saturday("done").id } });
    await flush(2000);
    await flush(DURATION.fast + 100);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(saturday("done").done?.title);
    // the way back is a real button to the eye (a text one read as plain words, as Refund and the chat's Undo did)
    expect(screen.getByRole("button", { name: "Undo…" }).getAttribute("data-variant")).toBe("secondary");
    fireEvent.click(within(island()).getByRole("button", { name: /Tap to speak/ }));
    await flush(DURATION.fast + 100);
    expect(mic.toggled).toBe(1);
    void s;
  });
});

describe("the spoken answer to the end card (tap Send or say it)", () => {
  async function toCard() {
    const s = setup();
    await flush(50);
    await speakAndStart();
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    return s;
  }

  it("when the card opens Atlas asks and listens; a plain yes shows what was heard, counts down with Cancel, then the same signed yes", async () => {
    const s = await toCard();
    await flush(100);
    expect(mic.toggled).toBeGreaterThan(0); // the mic is on for the answer (no device voice in a test: it listens at once)
    expect(within(island()).getByText("Say “send it”, or tap Send both.")).toBeTruthy(); // the hint names the button it shows
    act(() => mic.setText("send"));
    await flush(50);
    act(() => mic.setText("send it"));
    await flush(50);
    expect(within(island()).getByText("Heard “send it”")).toBeTruthy(); // what was heard shows first, before any number
    expect(within(island()).queryByText("Sending in 3")).toBeNull();
    await flush(HEARD_MS);
    expect(within(island()).getByText("Sending in 3")).toBeTruthy();
    expect(s.approve).not.toHaveBeenCalled(); // never at once
    await flush(800); // the count began 100 ms ago (the yes was heard at "send")
    expect(within(island()).getByText("Sending in 3")).toBeTruthy(); // a whole second a number
    await flush(150);
    expect(within(island()).getByText("Sending in 2")).toBeTruthy();
    await flush(2100);
    expect(s.approve).toHaveBeenCalledTimes(1);
    expect(s.approve).toHaveBeenCalledWith("c-end-1", true, { items: [0, 1], plan_sha: (saturday("ready for your ok").card as RunCard).plan_sha });
  });

  it("Cancel during the countdown sends nothing, and Send still works by tap", async () => {
    const s = await toCard();
    act(() => mic.setText("yes"));
    await flush(HEARD_MS + 50);
    fireEvent.click(within(island()).getByRole("button", { name: "Cancel" }));
    await flush(4000);
    expect(s.approve).not.toHaveBeenCalled();
    fireEvent.click(within(island()).getByRole("button", { name: "Send both" }));
    await flush(50);
    expect(s.approve).toHaveBeenCalledTimes(1);
  });

  it("it keeps listening after the yes: a no or more words stop the countdown, and so does leaving the app", async () => {
    const s = await toCard();
    act(() => mic.setText("send it"));
    await flush(HEARD_MS + 50);
    act(() => mic.setText("send it no wait"));
    await flush(4000);
    expect(s.approve).not.toHaveBeenCalled();
    expect(within(island()).getByRole("button", { name: "Send both" })).toBeTruthy(); // the card stays; a tap decides
    fireEvent.click(within(island()).getByRole("button", { name: "Answer by voice" }));
    await flush(50);
    act(() => mic.setText("yes"));
    await flush(HEARD_MS + 50);
    hide(true);
    await flush(4000);
    expect(s.approve).not.toHaveBeenCalled();
    hide(false);
  });

  it("the question is said in the person's read-aloud voice when it can be, and the mic opens only after it", async () => {
    const played = vi.fn(async () => {});
    sound.el = { play: played, pause: vi.fn(), onended: null, onerror: null, src: "" };
    URL.createObjectURL = vi.fn(() => "blob:ask");
    URL.revokeObjectURL = vi.fn();
    const askAloud = vi.fn(async () => ({ text: "These are ready. Should they go now?", audio: btoa("RIFF"), type: "audio/wav" }));
    const s = setup({ askAloud });
    await flush(50);
    await speakAndStart();
    const before = mic.toggled;
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    expect(askAloud).toHaveBeenCalledWith(expect.any(Number), "c-end-1");
    expect(played).toHaveBeenCalled();
    expect(mic.toggled).toBe(before); // still asking: the mic waits for the question to end
    act(() => sound.el?.onended?.());
    await flush(50);
    expect(mic.toggled).toBe(before + 1);
  });

  it("while Atlas asks, the request said earlier is never shown as heard nor taken as the answer (the mic isn't open for it yet)", async () => {
    sound.el = { play: vi.fn(async () => {}), pause: vi.fn(), onended: null, onerror: null, src: "" };
    URL.createObjectURL = vi.fn(() => "blob:ask");
    URL.revokeObjectURL = vi.fn();
    const askAloud = vi.fn(async () => ({ text: "These are ready. Should they go now?", audio: btoa("RIFF"), type: "audio/wav" }));
    const s = setup({ askAloud });
    await flush(50);
    await speakAndStart("send it"); // a request that would read as a yes, still in the box when the card opens
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    expect(sound.el.play).toHaveBeenCalled(); // still asking
    expect(within(island()).queryByText(/^Heard/)).toBeNull();
    expect(within(island()).getByText("Say “send it”, or tap Send both.")).toBeTruthy();
    await flush(4000);
    expect(s.approve).not.toHaveBeenCalled();
    act(() => sound.el?.onended?.()); // the question ends: the mic opens, empty
    await flush(50);
    expect(within(island()).queryByText(/^Heard/)).toBeNull();
    act(() => mic.setText("send it"));
    await flush(50);
    expect(within(island()).getByText("Heard “send it”")).toBeTruthy(); // what is said now is the answer
  });

  it("the card's answer is listened for live only (never a recorded note, which would only stop on a tap)", async () => {
    await toCard();
    await flush(100);
    expect(mic.listened).toBe(1);
  });

  it("the question's sound never ends (the phone kept it from playing): the mic opens anyway, so the spoken yes still works", async () => {
    sound.el = { play: vi.fn(async () => {}), pause: vi.fn(), onended: null, onerror: null, src: "" };
    URL.createObjectURL = vi.fn(() => "blob:ask");
    URL.revokeObjectURL = vi.fn();
    const askAloud = vi.fn(async () => ({ text: "These are ready. Should they go now?", audio: btoa("RIFF"), type: "audio/wav" }));
    const s = setup({ askAloud });
    await flush(50);
    await speakAndStart();
    const before = mic.listened;
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    expect(mic.listened).toBe(before); // still asking
    await flush(10_100);
    expect(mic.listened).toBe(before + 1);
    act(() => mic.setText("send it"));
    await flush(HEARD_MS + 50);
    expect(within(island()).getByText("Sending in 3")).toBeTruthy();
  });

  it("when Atlas's voice can't ask, the device's voice does, and the answer still works", async () => {
    sound.el = { play: vi.fn(async () => {}), pause: vi.fn(), onended: null, onerror: null, src: "" };
    const askAloud = vi.fn(async () => {
      throw new Error("403");
    });
    const s = setup({ askAloud });
    await flush(50);
    await speakAndStart();
    await poll(s.api, 9);
    await flush(FOUND_HOLD_MS + 400);
    expect(askAloud).toHaveBeenCalled();
    expect(sound.el.play).not.toHaveBeenCalled();
    act(() => mic.setText("send it"));
    await flush(HEARD_MS + 50);
    expect(within(island()).getByText("Sending in 3")).toBeTruthy();
  });

  it("Atlas's own question coming back through the mic is not an answer", async () => {
    const s = await toCard();
    act(() => mic.setText("These are ready. Should they go now?"));
    await flush(4000);
    expect(within(island()).queryByText(/^Heard/)).toBeNull();
    expect(s.approve).not.toHaveBeenCalled();
  });

  it("a spoken no closes the card unsent; other words do nothing", async () => {
    const s = await toCard();
    act(() => mic.setText("what does it say"));
    await flush(1000);
    expect(screen.getByRole("region", { name: "Send to Sam?" })).toBeTruthy();
    act(() => mic.setText("no, not now"));
    await flush(DURATION.sheet * 3 + 100);
    expect(screen.queryByRole("region", { name: "Send to Sam?" })).toBeNull();
    await flush(4000);
    expect(s.approve).not.toHaveBeenCalled();
  });
});
