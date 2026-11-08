// Settings › Atlas Shield › What the AI saw (Open Pseudonym): the Shield page has the row only while it is on for this
// account; the list says where and when each message came from and what was hidden, never the words; one tap shows your
// words beside what the AI got, stand-ins marked, and whether every receipt checked out; a receipt that didn't is said
// plainly; the receipts sit one tap further down; a list that won't load says so and offers to try again.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Api } from "../lib/api";
import { hiddenLine, jobLabel, segments, shortHash, type Turn, type TurnPage, wordsOf } from "../lib/pseudonym";
import type { ShieldState } from "../lib/shield";
import { AiSawSettings, TurnView } from "./AiSaw";
import { ShieldSettings } from "./Shield";

afterEach(cleanup);

const ON: ShieldState = { enabled: true, level: "private", groups: [], visible: [], names: "cues" };
const NOW = new Date("2026-11-07T15:00:00");
const ROW = { id: "t-1", at: "2026-11-07T14:10:00", job: "chat", model: "gpt-test", calls: 2, words: { CARD: 1, EMAIL: 1 }, stand_ins: { CARD: 1, EMAIL: 2 } };
const SEEN = "Pay with [CARD_1] and copy casey.hart@harbor.example";
const TURN: Turn = {
  id: "t-1", at: ROW.at, job: "chat", real: "Pay with 4111 1111 1111 1111 and copy sam@example.com", seen: SEEN,
  marks: [[9, 17, "CARD"], [27, 52, "EMAIL"]], stand_ins: ROW.stand_ins, ok: true,
  calls: [1, 2].map((seq) => ({ seq, at: ROW.at, model: "gpt-test", payload: "sha-256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG", stand_ins: ROW.stand_ins, ok: true, why: null, token: "a.b.c" })),
};

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);
}

function fakeApi(over: Record<string, unknown> = {}) {
  const known: Record<string, unknown> = {
    shield: vi.fn(async () => ON),
    pseudonym: vi.fn(async () => ({ enabled: true, key: { kid: "sha-256:k" } })),
    pseudonymTurns: vi.fn(async (): Promise<TurnPage> => ({ turns: [ROW], more: false })),
    pseudonymTurn: vi.fn(async () => TURN),
    ...over,
  };
  return new Proxy(known, { get: (t, k: string) => (k in t ? t[k] : () => new Promise(() => {})) }) as unknown as Api & Record<string, ReturnType<typeof vi.fn>>;
}

describe("the words", () => {
  it("say what was hidden in plain words, singular and plural, and calmly when nothing was", () => {
    expect(hiddenLine({ CARD: 1, EMAIL: 2 })).toBe("1 card and 2 email addresses hidden");
    expect(hiddenLine({ PERSON: 3, ORG: 1, PHONE: 1 })).toBe("1 company, 3 names and 1 phone number hidden");
    expect(hiddenLine({})).toBe("Nothing private in it");
    expect(hiddenLine({}, "Nothing private in your words")).toBe("Nothing private in your words");
    expect(wordsOf(SEEN, [...TURN.marks, [0, 3, "PERSON"], [9, 17, "CARD"]])).toEqual({ CARD: 1, EMAIL: 1, PERSON: 1 });
    expect(jobLabel("agent_mode_step")).toBe("Agent mode");
    expect(jobLabel("something_new")).toBe("Background work");
    expect(shortHash("sha-256:abcdefghijklmnopqrstuvwxyz")).toBe("abcdefgh…uvwxyz");
  });

  it("mark only the stand-ins, and leave bad marks as text", () => {
    expect(segments(SEEN, TURN.marks).filter((s) => s.kind).map((s) => s.text)).toEqual(["[CARD_1]", "casey.hart@harbor.example"]);
    expect(segments("abc", [[1, 9, "X"], [-1, 2, "Y"]])).toEqual([{ text: "abc", kind: null }]);
  });
});

describe("Atlas Shield", () => {
  it("offers What the AI saw only while it is on for this account", async () => {
    const onSaw = vi.fn();
    wrap(<ShieldSettings api={fakeApi()} onSaw={onSaw} />);
    fireEvent.click(await screen.findByRole("button", { name: /your recent messages/i }));
    expect(onSaw).toHaveBeenCalled();
    cleanup();
    wrap(<ShieldSettings api={fakeApi({ pseudonym: vi.fn(async () => ({ enabled: false })) })} onSaw={onSaw} />);
    await screen.findByRole("radiogroup", { name: "Shield level" });
    expect(screen.queryByRole("button", { name: /your recent messages/i })).toBeNull();
  });
});

describe("What the AI saw", () => {
  it("lists where, when and what was hidden, never the words, and opens a message", async () => {
    const api = fakeApi();
    wrap(<AiSawSettings api={api} now={NOW} />);
    const row = await screen.findByRole("button", { name: /chat, today/i });
    expect(row.textContent).toContain("1 card and 1 email address hidden"); // what was hidden in your words
    expect(document.body.textContent).not.toContain("4111");
    fireEvent.click(row);
    await waitFor(() => expect(api.pseudonymTurn).toHaveBeenCalledWith("t-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Close" }, { timeout: 5000 })); // a visible way out on every size
  });

  it("shows your words beside what the AI got, the stand-ins marked, and the receipts one tap down", () => {
    wrap(<TurnView turn={TURN} />);
    const got = screen.getByText("The AI got").closest("figure") as HTMLElement;
    expect([...got.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["[CARD_1]", "casey.hart@harbor.example"]);
    expect(screen.getByText("You wrote").closest("figure")?.textContent).toContain("4111 1111 1111 1111");
    expect(got.textContent).toContain("Some look real so the AI can write naturally");
    expect(document.body.textContent).toContain("1 card and 1 email address hidden in your words. Each of its 2 requests has a signed receipt, and all check out.");
    expect(screen.queryByText(/Request 1/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Each request" }));
    expect(screen.getByText(/Request 1/)).toBeTruthy();
    expect(screen.getAllByText("Hidden with the chat so far: 1 card and 2 email addresses")).toHaveLength(2);
  });

  it("says plainly when a receipt didn't check out", () => {
    const bad = { ...TURN, ok: false, calls: [TURN.calls[0], { ...TURN.calls[1], ok: false, why: "changed" }] };
    wrap(<TurnView turn={bad} />);
    expect(document.body.textContent).toContain("One receipt doesn't match what Atlas signed when this message was sent, so it can't be trusted.");
    expect(screen.getByText("The stored copy was changed after it was signed")).toBeTruthy(); // the fold opens on it
  });

  it("says when your words can't be shown, instead of showing nothing", () => {
    wrap(<TurnView turn={{ ...TURN, real: null }} />);
    expect(within(screen.getByText("You wrote").closest("figure") as HTMLElement).getByText(/can't be shown/)).toBeTruthy();
  });

  it("explains an empty list, and offers to try again when it won't load", async () => {
    wrap(<AiSawSettings api={fakeApi({ pseudonymTurns: vi.fn(async () => ({ turns: [], more: false })) })} now={NOW} />);
    expect(await screen.findByText("Nothing here yet")).toBeTruthy();
    cleanup();
    const api = fakeApi({ pseudonymTurns: vi.fn(async () => Promise.reject(new Error("down"))) });
    wrap(<AiSawSettings api={api} now={NOW} />);
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.pseudonymTurns).toHaveBeenCalledTimes(2));
  });
});
