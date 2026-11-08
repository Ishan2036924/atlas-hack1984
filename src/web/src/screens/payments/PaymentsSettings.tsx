// Settings › Payments (the payments section; owner only, Stripe test mode): every payment Atlas made for you, newest
// first, one row each (to whom, why, how much, where it stands); a tap opens it in full (PaymentSheet). Below, two quiet
// rows, each opening its own sheet: your limits (one payment, a day, payments a day, within the code's maximums;
// lowering one is instant, raising one asks first) and the next payment's test card (normal, or one the bank disputes,
// to try the dispute steps; used once). While payments are off for this account the page says so and nothing else.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useRef, useState } from "react";

import { errorWords } from "../../lib/errorWords";
import { useWideLayout } from "../../lib/layout";
import { ApiError } from "../../lib/net";
import { type LimitsIn, money, type Payment, PER_DAY, PER_DAY_COUNT, PER_PAYMENT, type PaymentLimits, type PaymentList, raises, sentence, statusTone, statusWord, type TestCard } from "../../lib/payments";
import { Icon, Section } from "../../ui/kit";
import { Drawer } from "../../ui/overlay/Drawer";
import { Button } from "../../ui/primitives/Button";
import { Pill } from "../../ui/primitives/Chip";
import { EmptyState } from "../../ui/primitives/EmptyState";
import { IconButton } from "../../ui/primitives/IconButton";
import { RowButton } from "../../ui/primitives/RowButton";
import { Segmented } from "../../ui/primitives/Segmented";
import { SkeletonRows } from "../../ui/Skeleton";
import { PaymentSheet, type PaymentsApi } from "./PaymentSheet";

export type PaymentsSettingsApi = PaymentsApi & { payments: () => Promise<PaymentList>; setPaymentLimits: (b: LimitsIn) => Promise<PaymentLimits> };

const when = (iso: string): string => new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const short = (n: number) => money(n).replace(/\.00$/, "");
const disputed = (p: Payment) => Boolean(p.dispute?.open);

export function PaymentsSettings({ api, initialPayment }: { api: PaymentsSettingsApi; initialPayment?: string }) {
  const q = useQuery({ queryKey: ["payments"], queryFn: api.payments, retry: false });
  const [open, setOpen] = useState<string | null>(initialPayment ?? null); // the chat's line opens one payment
  const [sheet, setSheet] = useState<"limits" | "card" | null>(null);
  if (q.isPending) return <SkeletonRows rows={4} label="Payments" />;
  if (q.isError) {
    const off = q.error instanceof ApiError && q.error.status === 404;
    return off ? (
      <EmptyState icon="coin" title="Payments are off" hint="They're on only for the account's owner while they're being tested." />
    ) : (
      <EmptyState icon="coin" title="Payments couldn't load" hint={errorWords(q.error, "Try again in a moment.")} action={{ label: "Try again", onClick: () => void q.refetch() }} />
    );
  }
  const { items, limits } = q.data;
  return (
    <>
      <Section title="Payments" quiet>
        {items.length === 0 ? (
          <p className="m-0 px-4 py-4 text-body text-pretty text-fg-2">No payments yet. When you ask Atlas to pay for something, it shows here with every step.</p>
        ) : (
          items.map((p) => <PaymentRow key={p.id} p={p} onOpen={() => setOpen(p.id)} />)
        )}
      </Section>
      <Section title="Limits and test card" quiet>
        <NavRow label="Limits" line={`${short(limits.per_payment_cents)} a payment, ${short(limits.per_day_cents)} a day, ${limits.per_day_count} payment${limits.per_day_count === 1 ? "" : "s"} a day`} onClick={() => setSheet("limits")} />
        <NavRow label="Test card" line={limits.test_card === "dispute" ? "Next payment gets disputed" : "Normal"} onClick={() => setSheet("card")} />
      </Section>
      <p className="m-0 px-1 text-small text-fg-2">Test mode: Stripe's test card pays, no real money.</p>
      <PaymentSheet api={api} id={open} onClose={() => setOpen(null)} />
      <LimitsSheet api={api} limits={limits} which={sheet} onClose={() => setSheet(null)} />
    </>
  );
}

function PaymentRow({ p, onOpen }: { p: Payment; onOpen: () => void }) {
  const word = disputed(p) ? "Disputed" : statusWord(p.status);
  return (
    <RowButton onClick={onOpen} aria-label={`${p.payee}, ${money(p.amount_cents)}, ${word}`} className="items-center">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="min-w-0 truncate text-callout font-medium text-fg-1">{p.payee}</span>
        <span className="min-w-0 truncate text-small text-fg-2">{sentence(p.reason)}</span>
        <span className="text-caption text-fg-3 tabular-nums">{when(p.created_at)}</span>
      </span>
      <span className="flex shrink-0 flex-col items-end gap-1">
        <span className="text-callout font-semibold text-fg-1 tabular-nums">{money(p.amount_cents)}</span>
        {/* one quiet status; the bank's dispute (or a payment still being checked) is the one accent */}
        <Pill tone={disputed(p) || statusTone(p.status) === "warn" ? "warn" : "plain"} dot>
          {word}
        </Pill>
      </span>
    </RowButton>
  );
}

function NavRow({ label, line, onClick }: { label: string; line: string; onClick: () => void }) {
  return (
    <RowButton onClick={onClick} className="items-center">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-callout font-medium text-fg-1">{label}</span>
        <span className="min-w-0 text-small text-pretty text-fg-2">{line}</span>
      </span>
      <span aria-hidden className="shrink-0 text-fg-3">
        <Icon name="chevron" size={14} stroke={2} />
      </span>
    </RowButton>
  );
}

function LimitsSheet({ api, limits, which, onClose }: { api: PaymentsSettingsApi; limits: PaymentLimits; which: "limits" | "card" | null; onClose: () => void }) {
  const side = useWideLayout() ? ("right" as const) : ("bottom" as const);
  const head = useRef<HTMLHeadingElement>(null);
  const qc = useQueryClient();
  const [asking, setAsking] = useState<LimitsIn | null>(null);
  const save = useMutation({
    mutationFn: (b: LimitsIn) => api.setPaymentLimits(b),
    onSuccess: (next) => {
      qc.setQueryData<PaymentList>(["payments"], (old) => (old ? { ...old, limits: next } : old));
      setAsking(null);
    },
  });
  const now: LimitsIn = { per_payment_cents: limits.per_payment_cents, per_day_cents: limits.per_day_cents, per_day_count: limits.per_day_count, test_card: limits.test_card };
  const change = (patch: Partial<LimitsIn>) => {
    const next = { ...now, ...patch };
    if (raises(limits, next)) setAsking(next);
    else save.mutate(next);
  };
  const title = which === "card" ? "Test card" : "Limits";
  return (
    <>
      <Drawer open={which !== null} onOpenChange={(o) => { if (!o) { setAsking(null); onClose(); } }} title={title} side={side} showTitle={false} fit initialFocus={head}>
        <div className="flex flex-col gap-5 pb-[max(env(safe-area-inset-bottom),calc(var(--spacing)*4))]">
          <div className="flex items-start justify-between gap-3 pt-1 medium:pt-3">
            <div className="flex min-w-0 flex-col gap-1">
              <h2 ref={head} tabIndex={-1} className="m-0 text-title font-semibold text-fg-1 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus">
                {title}
              </h2>
              {which !== "card" && <p className="m-0 text-body text-pretty text-fg-2">The most Atlas may ask you to pay. Lowering one is instant; raising one asks you first.</p>}
            </div>
            <IconButton icon="close" label="Close" size="md" variant="solid" className="-mr-1 shrink-0" onClick={onClose} />
          </div>
          {asking ? (
            // raising asks first, here in the sheet (a dialog stacked on a sheet read as one shape)
            <section aria-label="Raise your limit?" className="flex flex-col gap-4 rounded-card bg-surface-field p-4">
              <div className="flex flex-col gap-1">
                <p className="m-0 text-callout font-semibold text-fg-1">Raise your limit?</p>
                <p className="m-0 text-body text-pretty text-fg-2">{raiseWords(limits, asking)} Each payment still needs your Approve on its card.</p>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <Button variant="secondary" onClick={() => setAsking(null)}>
                  Keep it
                </Button>
                <Button variant="primary" loading={save.isPending} onClick={() => save.mutate({ ...asking, confirm_raise: true })}>
                  Raise it
                </Button>
              </div>
            </section>
          ) : which === "card" ? (
            <Choice label="Next payment" line={limits.test_card === "dispute" ? "It goes through, then the bank disputes it, so you can try the dispute steps. After that, back to normal." : "Stripe's normal test card."}>
              <Segmented<TestCard> label="Test card for the next payment" value={limits.test_card} options={[{ id: "visa", label: "Normal" }, { id: "dispute", label: "Gets disputed" }]} onChange={(v) => change({ test_card: v })} />
            </Choice>
          ) : (
            <>
              <Choice label="One payment" line="The most for any single payment">
                <Segmented label="Most for one payment" value={String(limits.per_payment_cents)} options={PER_PAYMENT.map((n) => ({ id: String(n), label: short(n) }))} onChange={(v) => change({ per_payment_cents: Number(v) })} />
              </Choice>
              <Choice label="All payments in a day" line="Together, from midnight to midnight">
                <Segmented label="Most in a day" value={String(limits.per_day_cents)} options={PER_DAY.map((n) => ({ id: String(n), label: short(n) }))} onChange={(v) => change({ per_day_cents: Number(v) })} />
              </Choice>
              <Choice label="Payments a day" line="Refunded ones still count">
                <Segmented label="Most payments a day" value={String(limits.per_day_count)} options={PER_DAY_COUNT.map((n) => ({ id: String(n), label: String(n) }))} onChange={(v) => change({ per_day_count: Number(v) })} />
              </Choice>
            </>
          )}
          {save.isError && (
            <p role="alert" className="m-0 text-small text-status-warn">
              {errorWords(save.error, "That didn't save; try again in a moment.")}
            </p>
          )}
        </div>
      </Drawer>
    </>
  );
}

function raiseWords(now: PaymentLimits, next: LimitsIn): string {
  if (next.per_payment_cents > now.per_payment_cents) return `Atlas could ask you for up to ${money(next.per_payment_cents)} in one payment.`;
  if (next.per_day_cents > now.per_day_cents) return `Atlas could ask you for up to ${money(next.per_day_cents)} in a day.`;
  return `Atlas could ask you for up to ${next.per_day_count} payments a day.`;
}

function Choice({ label, line, children }: { label: string; line: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2.5">
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-callout font-medium text-fg-1">{label}</span>
        <span className="text-small text-pretty text-fg-2">{line}</span>
      </span>
      <span>{children}</span>
    </div>
  );
}
