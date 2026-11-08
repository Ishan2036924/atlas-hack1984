// One test payment in full (the payments section; lib/payments): a bottom sheet on the phone, a panel on the right from
// the tablet up. On top: how much, to whom, why and where it stands (one quiet status; the bank's dispute is the one
// accent, with what it means); then the taps that fit its state (Refund once it's paid, all or part with a reason;
// Report a problem always; payments are charged at once, so there is no Cancel), its steps as a timeline and the evidence pack to download. Refund and Report a
// problem open in the same sheet; Back returns. Opened from Settings › Payments (and there from the chat line's Open in
// Payments). A refund can't be taken back, and the form says so above its button.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, type RefObject, useMemo, useRef, useState } from "react";

import type { Api } from "../../lib/api";
import { saveFile } from "../../lib/docExport";
import { errorWords } from "../../lib/errorWords";
import { useWideLayout } from "../../lib/layout";
import { centsOf, eventWord, money, NOTE_MAX, type Payment, type PaymentDetail, type PaymentEvent, PROBLEMS, type ProblemReason, REFUND_REASONS, type RefundReason, sentence, sourceWord, statusTone, statusWord, tapId } from "../../lib/payments";
import { Drawer } from "../../ui/overlay/Drawer";
import { toast } from "../../ui/overlay/Toast";
import { Button } from "../../ui/primitives/Button";
import { Chip, Pill } from "../../ui/primitives/Chip";
import { IconButton } from "../../ui/primitives/IconButton";
import { Input, Textarea } from "../../ui/primitives/Input";
import { Skeleton } from "../../ui/primitives/Skeleton";

export type PaymentsApi = Pick<Api, "payment" | "refundPayment" | "reportPayment" | "paymentEvidence">;

export type SheetMode = "view" | "refund" | "problem";

const at = (iso: string): string => new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export function PaymentSheet({ api, id, onClose, start = "view" }: { api: PaymentsApi; id: string | null; onClose: () => void; start?: SheetMode }) {
  const side = useWideLayout() ? ("right" as const) : ("bottom" as const);
  const head = useRef<HTMLHeadingElement>(null);
  return (
    <Drawer open={id !== null} onOpenChange={(o) => !o && onClose()} title="Payment" side={side} showTitle={false} detents={[0.92]} fit initialFocus={head}>
      {id && <Body key={`${id}:${start}`} api={api} id={id} start={start} onClose={onClose} head={head} />}
    </Drawer>
  );
}

function Body({ api, id, start, onClose, head }: { api: PaymentsApi; id: string; start: SheetMode; onClose: () => void; head: RefObject<HTMLHeadingElement | null> }) {
  const qc = useQueryClient();
  const [want, setMode] = useState<SheetMode>(start);
  const [reported, setReported] = useState(false);
  const q = useQuery({ queryKey: ["payment", id], queryFn: () => api.payment(id), retry: false });
  // Refund only while the payment allows it (it may have changed since the line was drawn)
  const mode: SheetMode = want === "refund" && q.data && !q.data.payment.actions.includes("refund") ? "view" : want;
  const fresh = (d?: PaymentDetail) => {
    if (d) qc.setQueryData(["payment", id], d);
    void qc.invalidateQueries({ queryKey: ["payment", id] });
    void qc.invalidateQueries({ queryKey: ["payments"] });
  };
  return (
    <div className="flex flex-col pb-[max(env(safe-area-inset-bottom),calc(var(--spacing)*4))]">
      <div className="flex items-center justify-between gap-3 pt-1 pb-3 medium:pt-3">
        {mode === "view" ? (
          <h2 ref={head} tabIndex={-1} className="m-0 min-w-0 text-callout font-semibold text-fg-2 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus">
            Payment
          </h2>
        ) : (
          <Button variant="text" size="sm" icon="back" className="-ml-2" onClick={() => setMode("view")}>
            Back
          </Button>
        )}
        <IconButton icon="close" label="Close" size="md" variant="solid" className="-mr-1 shrink-0" onClick={onClose} />
      </div>
      {q.isPending && <Loading />}
      {q.isError && <p className="m-0 py-6 text-body text-fg-2">{errorWords(q.error, "This payment couldn't be opened. Try again in a moment.")}</p>}
      {q.data && mode === "view" && (
        <View
          d={q.data}
          reported={reported}
          onRefund={() => setMode("refund")}
          onProblem={() => setMode("problem")}
          onEvidence={() => void evidence(api, q.data.payment)}
        />
      )}
      {q.data && mode === "refund" && (
        <RefundForm
          api={api}
          p={q.data.payment}
          onDone={(left) => {
            toast({ title: left > 0 ? `Refunded. ${money(left)} is left on the payment.` : "Refunded to the test card." });
            fresh();
            setMode("view");
          }}
        />
      )}
      {q.data && mode === "problem" && (
        <ProblemForm
          api={api}
          p={q.data.payment}
          onDone={(d) => {
            fresh(d);
            setReported(true);
            setMode("view");
          }}
        />
      )}
    </div>
  );
}

async function evidence(api: PaymentsApi, p: Payment) {
  try {
    saveFile(await api.paymentEvidence(p.id), `atlas-payment-${p.id.slice(0, 8)}-evidence.json`);
  } catch (e) {
    toast({ title: errorWords(e, "The evidence pack couldn't be made. Try again in a moment.") });
  }
}

function Loading() {
  return (
    <div aria-busy className="flex flex-col gap-3 py-2">
      <Skeleton className="h-9 w-32" />
      <Skeleton className="h-5 w-48" />
      <Skeleton className="mt-4 h-24 w-full" />
    </div>
  );
}

function View({ d, reported, onRefund, onProblem, onEvidence }: { d: PaymentDetail; reported: boolean; onRefund: () => void; onProblem: () => void; onEvidence: () => void }) {
  const p = d.payment;
  const disputed = Boolean(p.dispute?.open);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <p className="m-0 text-display font-semibold tracking-tight text-fg-1 tabular-nums">{money(p.amount_cents)}</p>
        <p className="m-0 min-w-0 text-callout font-semibold wrap-anywhere text-fg-1">{p.payee}</p>
        <p className="m-0 min-w-0 text-body text-pretty wrap-anywhere text-fg-2">{sentence(p.reason)}</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {disputed ? (
            <Pill tone="warn" dot>
              Disputed by the bank
            </Pill>
          ) : (
            <Pill tone={statusTone(p.status) === "warn" ? "warn" : "plain"} dot>
              {statusWord(p.status)}
            </Pill>
          )}
        </div>
        {disputed && <p className="m-0 mt-2 text-small text-pretty text-fg-2">The bank is looking into this payment. You can't refund it while that's open. Download the evidence pack if you want to respond.</p>}
        {!disputed && p.status === "held" && <p className="m-0 mt-2 text-small text-pretty text-fg-2">It goes through shortly; Atlas settles it on its own.</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        {p.actions.includes("refund") && (
          <Button variant="secondary" onClick={onRefund}>
            Refund
          </Button>
        )}
        {disputed && (
          <Button variant="secondary" icon="file" onClick={onEvidence}>
            Download evidence pack
          </Button>
        )}
        {/* the quiet one: its text lined up with the left edge (minus its 6 px padding) when it is the only one */}
        <Button variant="text" className={p.actions.includes("refund") || disputed ? "" : "-ml-1.5"} onClick={onProblem}>
          Report a problem
        </Button>
      </div>
      {reported && (
        <div className="flex flex-col gap-2 rounded-card bg-surface-field p-4">
          <p className="m-0 text-body text-pretty text-fg-1">Added to this payment's steps. The evidence pack holds what you approved, every step and the signed receipts, to share with the shop or your bank.</p>
          <div>
            <Button variant="secondary" size="sm" icon="file" onClick={onEvidence}>
              Download evidence pack
            </Button>
          </div>
        </div>
      )}
      <Steps events={d.events} p={p} />
      <Details p={p} onEvidence={onEvidence} />
    </div>
  );
}

// who did a step, said only when its own words don't already say it ("You approved it" needs no "You")
const SAYS_WHO = new Set(["approved", "problem_reported"]);

function Steps({ events, p }: { events: PaymentEvent[]; p: Payment }) {
  if (events.length === 0) return null;
  const header = `${p.payee}: ${p.reason}`;
  return (
    <section aria-label="Steps" className="flex flex-col gap-2">
      <h3 className="m-0 text-small font-semibold text-fg-2">Steps</h3>
      <ol className="m-0 flex list-none flex-col p-0">
        {events.map((e, i) => {
          const note = e.note && e.note !== header ? e.note : null; // the approved step's note repeats the header
          const who = SAYS_WHO.has(e.kind) || e.source === "atlas" ? null : e.source === "stripe" ? "From Stripe" : `By ${sourceWord(e.source).toLowerCase()}`;
          return (
            <li key={`${e.at}-${i}`} className="relative flex gap-3 pb-4 last:pb-0">
              {i < events.length - 1 && <span aria-hidden className="absolute top-4 bottom-0 left-1.25 w-px bg-edge" />}
              <span aria-hidden className={`mt-1.5 size-2.75 shrink-0 rounded-full border-2 ${i === events.length - 1 ? "border-fg-1 bg-fg-1" : "border-edge-strong bg-surface-panel"}`} />
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 text-body font-medium text-fg-1">
                    {eventWord(e.kind)}
                    {e.amount_cents !== null && (e.kind === "refunded" || e.kind === "refund_failed") && <span className="text-fg-2 tabular-nums"> {money(e.amount_cents)}</span>}
                  </span>
                  <span className="shrink-0 text-caption text-fg-3 tabular-nums">{at(e.at)}</span>
                </div>
                {note && <span className="min-w-0 text-small text-pretty wrap-anywhere text-fg-2">{note}</span>}
                {who && <span className="text-caption text-fg-3">{who}</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Details({ p, onEvidence }: { p: Payment; onEvidence: () => void }) {
  const rows: Array<[string, ReactNode]> = [];
  if (p.refunded_cents > 0) rows.push(["Refunded", <span key="refunded" className="tabular-nums">{money(p.refunded_cents)}</span>]);
  if (p.stripe_id) rows.push(["Stripe id", <span key="stripe" className="font-mono text-caption wrap-anywhere">{p.stripe_id}</span>]);
  rows.push(["Made", at(p.created_at)]);
  if (p.actions.includes("refund") && p.refund_until) rows.push(["Refund until", at(p.refund_until)]);
  rows.push(["Card", "Stripe's test card, no real money"]);
  return (
    <section aria-label="Details" className="flex flex-col gap-2">
      <h3 className="m-0 text-small font-semibold text-fg-2">Details</h3>
      <dl className="m-0 flex flex-col divide-y divide-edge rounded-card border border-edge">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-4 px-4 py-2.5">
            <dt className="shrink-0 text-small text-fg-2">{k}</dt>
            <dd className="m-0 min-w-0 text-right text-small text-fg-1">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="pt-1">
        <Button variant="text" size="sm" icon="file" className="-ml-1.5" onClick={onEvidence}>
          Download evidence pack
        </Button>
      </div>
    </section>
  );
}

function RefundForm({ api, p, onDone }: { api: PaymentsApi; p: Payment; onDone: (left: number) => void }) {
  const left = p.refund_left_cents;
  const [text, setText] = useState(() => (left / 100).toFixed(2));
  const [reason, setReason] = useState<RefundReason | null>(null);
  const cents = centsOf(text);
  // one id per amount and reason: a retry of the same tap refunds once; a changed one is a new tap (Stripe refuses a
  // reused key with other values)
  const request = useMemo(() => tapId(), [cents, reason]); // eslint-disable-line react-hooks/exhaustive-deps
  const valid = cents !== null && cents >= 1 && cents <= left;
  const go = useMutation({
    mutationFn: () => api.refundPayment(p.id, { amount_cents: cents ?? left, reason: reason ?? "other", request }),
    onSuccess: (r) => onDone(r.left_cents ?? 0),
  });
  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && reason) go.mutate();
      }}
    >
      <div className="flex flex-col gap-1">
        <h3 className="m-0 text-title font-semibold wrap-anywhere text-fg-1">Refund {p.payee}</h3>
        <p className="m-0 text-body text-fg-2">
          {left === p.amount_cents ? `All ${money(left)} goes back to the test card, or part of it.` : `Up to ${money(left)} of ${money(p.amount_cents)} goes back to the test card.`}
        </p>
      </div>
      <div className="flex flex-col gap-2">
        <span aria-hidden className="text-small font-semibold text-fg-2">
          How much
        </span>
        <Input label="How much to refund" size="lg" inputMode="decimal" enterKeyHint="done" value={text} onChange={(e) => setText(e.target.value)} invalid={text.trim() !== "" && !valid} icon="coin" className="text-callout tabular-nums" />
      </div>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-2 p-0 text-small font-semibold text-fg-2">Why</legend>
        <div className="flex flex-wrap gap-2">
          {REFUND_REASONS.map((r) => (
            <Chip key={r.id} size="lg" variant={reason === r.id ? "picked" : "outline"} pressed={reason === r.id} onClick={() => setReason(r.id)}>
              {r.label}
            </Chip>
          ))}
        </div>
      </fieldset>
      {go.isError && (
        <p role="alert" className="m-0 text-small text-status-warn">
          {errorWords(go.error, "The refund didn't go through; try again in a moment.")}
        </p>
      )}
      <div className="flex flex-col gap-2">
        <p className="m-0 text-small text-fg-2">Refunds can't be undone.</p>
        <Button type="submit" variant="primary" size="lg" fullWidth loading={go.isPending} disabled={!valid || !reason}>
          {valid ? `Refund ${money(cents)}` : "Refund"}
        </Button>
      </div>
    </form>
  );
}

function ProblemForm({ api, p, onDone }: { api: PaymentsApi; p: Payment; onDone: (d: PaymentDetail) => void }) {
  const [why, setWhy] = useState<ProblemReason | null>(null);
  const [note, setNote] = useState("");
  const go = useMutation({
    mutationFn: () => api.reportPayment(p.id, { reason: why ?? "other", ...(note.trim() ? { note: note.trim() } : {}) }),
    onSuccess: onDone,
  });
  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (why) go.mutate();
      }}
    >
      <div className="flex flex-col gap-1">
        <h3 className="m-0 text-title font-semibold text-fg-1">What went wrong?</h3>
        <p className="m-0 text-body text-pretty text-fg-2">Atlas adds it to this payment's steps and gives you the evidence pack to share. Nothing is sent to the shop or your bank.</p>
      </div>
      <fieldset aria-label="What went wrong" className="m-0 flex flex-wrap gap-2 border-0 p-0">
        {PROBLEMS.map((r) => (
          <Chip key={r.id} size="lg" variant={why === r.id ? "picked" : "outline"} pressed={why === r.id} onClick={() => setWhy(r.id)}>
            {r.label}
          </Chip>
        ))}
      </fieldset>
      <Textarea label="Anything to add (optional)" placeholder="Anything to add (optional)" rows={3} maxLength={NOTE_MAX} value={note} onChange={(e) => setNote(e.target.value)} />
      {go.isError && (
        <p role="alert" className="m-0 text-small text-status-warn">
          {errorWords(go.error, "That didn't save; try again in a moment.")}
        </p>
      )}
      <Button type="submit" variant="primary" size="lg" fullWidth loading={go.isPending} disabled={!why}>
        Report it
      </Button>
    </form>
  );
}
