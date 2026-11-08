// The payments section (lifeos.payments; lifeos-api /v1/payments): Atlas's test payments for the owner, each with its
// timeline, the taps that fit its state (Refund once it's paid, Report a problem, the evidence pack; payments are charged
// at once since 2026-11-07, so nothing is canceled any more) and the
// person's own limits. Types, plain words and small rules only; the screens are screens/payments.
// Money is whole cents, never rounded. Every word here is calm: what happened and what you can do next.

export type PaymentStatus = "pending" | "held" | "paid" | "partially_refunded" | "refunded" | "canceled" | "failed" | "unknown";
export type PaymentAction = "refund" | "problem" | "evidence";

export type Payment = {
  id: string;
  created_at: string;
  amount_cents: number;
  currency: string;
  payee: string;
  reason: string;
  status: PaymentStatus;
  stripe_id: string | null;
  capture_at: string | null;
  captured_at: string | null;
  canceled_at: string | null;
  refunded_cents: number;
  refund_left_cents: number;
  refund_until: string | null;
  dispute: { id: string; status: string; open: boolean } | null; // open: the bank is still handling it (no refund)
  actions: PaymentAction[];
  test_mode: true;
};

export type PaymentEvent = { at: string; kind: string; source: "atlas" | "you" | "stripe"; amount_cents: number | null; ref: string | null; note: string | null };
export type PaymentDetail = { payment: Payment; events: PaymentEvent[] };

export type TestCard = "visa" | "dispute";
export type PaymentLimits = { per_payment_cents: number; per_day_cents: number; per_day_count: number; test_card: TestCard; max: { per_payment_cents: number; per_day_cents: number; per_day_count: number } };
export type PaymentList = { enabled: boolean; test_mode: boolean; limits: PaymentLimits; items: Payment[] };
export type LimitsIn = { per_payment_cents: number; per_day_cents: number; per_day_count: number; test_card: TestCard; confirm_raise?: boolean };

export type RefundReason = "not_wanted" | "duplicate" | "not_received" | "other";
export type ProblemReason = "didnt_arrive" | "not_what_i_approved" | "i_didnt_approve" | "other";
export type RefundIn = { amount_cents?: number; reason?: RefundReason; request?: string };
export type RefundOut = { refunded: boolean; already?: boolean; payment_id: string; refund_id: string; amount: string; left_cents?: number; status?: PaymentStatus };

export const REFUND_REASONS: ReadonlyArray<{ id: RefundReason; label: string }> = [
  { id: "not_wanted", label: "Didn't want it" },
  { id: "duplicate", label: "Paid twice" },
  { id: "not_received", label: "It didn't arrive" },
  { id: "other", label: "Something else" },
];

export const PROBLEMS: ReadonlyArray<{ id: ProblemReason; label: string }> = [
  { id: "didnt_arrive", label: "It didn't arrive" },
  { id: "not_what_i_approved", label: "Not what I approved" },
  { id: "i_didnt_approve", label: "I didn't approve this" },
  { id: "other", label: "Something else" },
];

export const NOTE_MAX = 200;

/** "$12.00": whole cents as dollars, never rounded. */
export function money(cents: number): string {
  return `$${Math.floor(cents / 100).toLocaleString("en-US")}.${String(cents % 100).padStart(2, "0")}`;
}

/** "team coffee" → "Team coffee": a reason shown on its own line starts with a capital. */
export const sentence = (t: string): string => (t ? t[0].toLocaleUpperCase() + t.slice(1) : t);

/** "12.00" → 1200; null when it isn't a plain amount with at most two decimals. */
export function centsOf(text: string): number | null {
  const t = text.trim().replace(/^\$/, "").replaceAll(",", "");
  if (!/^\d{1,5}(\.\d{1,2})?$/.test(t)) return null;
  const [whole, part = ""] = t.split(".");
  return Number(whole) * 100 + Number(part.padEnd(2, "0"));
}

// the status as one or two words (a list row, the sheet's header)
const STATUS: Record<PaymentStatus, string> = {
  pending: "Going through",
  held: "On hold",
  paid: "Paid",
  partially_refunded: "Partly refunded",
  refunded: "Refunded",
  canceled: "Canceled",
  failed: "Didn't go through",
  unknown: "Being checked",
};
export const statusWord = (s: PaymentStatus): string => STATUS[s] ?? "Being checked";

/** The tone of the status mark: live (it went through), quiet (no money moved or it came back), warn (being checked). */
export function statusTone(s: PaymentStatus): "live" | "quiet" | "warn" {
  if (s === "paid" || s === "partially_refunded") return "live";
  if (s === "unknown" || s === "pending") return "warn";
  return "quiet";
}


/** The chat's line under an answer: what happened to this payment, in plain words (its controls live in Payments). */
export function paymentLine(p: Pick<Payment, "status" | "amount_cents" | "payee" | "refunded_cents">): string {
  const amt = money(p.amount_cents);
  switch (p.status) {
    case "held":
      // a payment made as a hold before 2026-11-07's immediate charge: the server settles it on its own
      return `Holding ${amt} for ${p.payee}; it goes through shortly.`;
    case "paid":
      return `Paid ${amt} to ${p.payee} in test mode.`;
    case "partially_refunded":
      return `Paid ${amt} to ${p.payee} in test mode; ${money(p.refunded_cents)} refunded.`;
    case "refunded":
      return `Refunded ${amt} to the test card.`;
    case "canceled":
      return `Canceled the hold for ${p.payee}. No money moved.`;
    case "failed":
      return `The payment to ${p.payee} didn't go through. Nothing was paid.`;
    default:
      return `Checking the payment to ${p.payee} with Stripe.`;
  }
}

// a step on the timeline, in words (the note from the server says the rest)
const EVENT: Record<string, string> = {
  approved: "You approved it",
  held: "Held on the test card",
  captured: "Paid",
  paid: "Paid",
  canceled: "Hold canceled",
  declined: "Declined",
  failed: "Didn't go through",
  unknown: "No clear answer yet",
  refunded: "Refunded",
  refund_failed: "Refund failed",
  dispute_opened: "The bank opened a dispute",
  dispute_updated: "Dispute updated",
  dispute_closed: "Dispute closed",
  problem_reported: "You reported a problem",
};
export const eventWord = (kind: string): string => EVENT[kind] ?? "Updated";
export const sourceWord = (s: PaymentEvent["source"]): string => (s === "you" ? "You" : s === "stripe" ? "Stripe" : "Atlas");

/** The three limits a person picks from (all within the code's maximums: $50 a payment, $50 and 5 a day). */
export const PER_PAYMENT = [500, 1000, 2000, 5000] as const;
export const PER_DAY = [1000, 2000, 5000] as const;
export const PER_DAY_COUNT = [1, 2, 3, 5] as const;

/** Whether `next` raises any limit above `now` (raising asks first; lowering is instant). */
export function raises(now: Pick<PaymentLimits, "per_payment_cents" | "per_day_cents" | "per_day_count">, next: Pick<LimitsIn, "per_payment_cents" | "per_day_cents" | "per_day_count">): boolean {
  return next.per_payment_cents > now.per_payment_cents || next.per_day_cents > now.per_day_cents || next.per_day_count > now.per_day_count;
}

/** A fresh id for one Refund tap (a retry of the same tap refunds once at most). */
export function tapId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : "00000000-0000-4000-8000-000000000000".replace(/0/g, () => Math.floor(Math.random() * 16).toString(16));
}
