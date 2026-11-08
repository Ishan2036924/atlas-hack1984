// The Stripe test payments (lifeos.payments): the quiet line under an answer once a test payment went through, "Paid
// $12.00 to Blue Bottle in test mode", with one button, Open in Payments. Payments are charged at once (Ishan,
// 2026-11-07), and the chat carries no payment controls: Refund, Report a problem and the evidence pack live in
// Settings › Payments (screens/payments). The server takes the chip only from the pay tool's own result
// (lifeos.paychip) and checks it before storing it; it is read a third time here: the amount, its currency, the payee
// in one line, the payment's id and until when Refund works (never a link); a chip from a payment made as a hold before
// then also carries capture_at. Where it stands now comes from the server (GET /v1/payments/{id}); the chip alone is
// the fallback. Kept apart from the components.
import { createContext } from "react";

import type { PaymentDetail } from "./payments";

export type PaidChip = { amountCents: number; currency: string; payee: string; paymentId: string; until: number; captureAt: number | null };

const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PAYEE_MAX = 60;

// one plain line: controls and invisible marks become spaces, spaces collapse, cut at `max`
function line(x: unknown, max: number): string {
  if (typeof x !== "string") return "";
  const plain = [...x].map((c) => {
    const n = c.codePointAt(0) ?? 0;
    const hidden = n < 32 || n === 127 || (n >= 0x200b && n <= 0x200f) || (n >= 0x202a && n <= 0x202e) || (n >= 0x2060 && n <= 0x2064) || n === 0xfeff;
    return hidden ? " " : c;
  });
  const flat = plain.join("").replace(/\s+/g, " ").trim();
  return flat.length <= max ? flat : `${flat.slice(0, max - 1).trimEnd()}…`;
}

/** The chip `x` is, or null when it isn't exactly a test payment's (the pilot's range, usd, an id, a refund time). */
export function paidOf(x: unknown): PaidChip | null {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  const cents = o.amount_cents;
  const payee = line(o.payee, PAYEE_MAX);
  const until = typeof o.refund_until === "string" ? Date.parse(o.refund_until) : Number.NaN;
  const capture = typeof o.capture_at === "string" ? Date.parse(o.capture_at) : o.capture_at === undefined ? null : Number.NaN;
  if (o.kind !== "paid" || typeof cents !== "number" || !Number.isInteger(cents) || cents < 50 || cents > 5000) return null;
  if (o.currency !== "usd" || !payee || typeof o.payment_id !== "string" || !ID.test(o.payment_id) || Number.isNaN(until) || Number.isNaN(capture)) return null;
  return { amountCents: cents, currency: "usd", payee, paymentId: o.payment_id, until, captureAt: capture };
}

/** "$12.00": whole cents as dollars, never rounded. */
export function money(cents: number): string {
  return `$${Math.floor(cents / 100).toLocaleString("en-US")}.${String(cents % 100).padStart(2, "0")}`;
}



/** The test payments of the steps that worked, each once, in the order they came. */
export function stepPaid(steps: Array<{ ok: boolean | null; chip?: unknown }>): PaidChip[] {
  const out: PaidChip[] = [];
  for (const s of steps) {
    const c = s.ok === true ? paidOf(s.chip) : null;
    if (c && !out.some((o) => o.paymentId === c.paymentId)) out.push(c);
  }
  return out;
}

/** What the line may do with its payment. The chat provides it (each part only when it can); with none (the ask
 *  panel), the line shows alone, from its chip. */
export type PaymentLine = {
  payment?: (paymentId: string) => Promise<PaymentDetail>; // where it stands now
  open?: (paymentId: string) => void; // Open in Payments: Settings › Payments with this payment open
};

export const PaymentLineContext = createContext<PaymentLine | null>(null);
