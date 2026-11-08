"""Agent payments: an owner-only Stripe TEST MODE pilot (2026-11-07; D249 "payments = strictly gated, test mode first,
Ishan only, max $50"; plans .local/plans/payments-test.md and payments-section-build.md).

`pay` (tier C, Atlas's own chat only) pays an exact amount to a named payee for a reason, after the owner's tap on its
card. Enforced here, in code, never in a prompt:
- Off unless LIFEOS_PAYMENTS=owner, and then only for OWNER_USER_ID (enabled_for); never a custom agent's call, never
  a run nobody watches (off_reason). The tool isn't even listed otherwise (lifeos.server, lifeos.runtime.atlas).
- Stripe sandbox keys only (STRIPE_TEST_KEY, `sk_test_` / `rk_test_`: test_key). A Stripe answer that says livemode
  stops every payment in this process until it restarts (_LOCKED), whatever the key looked like.
- Whole cents, MIN_CENTS..MAX_CENTS, USD only; per person and day (LIFEOS_TZ) at most DAY_CENTS and DAY_COUNT, or the
  person's own lower limits (payment_settings), counting every payment that may have charged (COUNTED). Ledger.reserve
  checks and writes the row in one transaction under a per-person lock BEFORE Stripe is called, so two calls at once
  can't both pass.
- The card's grant covers the exact arguments (lifeos.approvals: amount, currency, payee, reason); the Stripe
  idempotency key comes from that grant's nonce, which the server spends once: one approval, at most one charge, retries
  included. A Stripe answer that never came leaves the row `unknown`: counted, never charged again blindly.
- Stripe's own test cards pay (TEST_CARDS): Atlas never sees, asks for or stores card data (RULES S8).

Charged at once (Ishan, 2026-11-07: "Charge immediately"): Stripe captures the approved payment right away. Refund
gives back all or part of it, with a reason, within REFUND_WINDOW_S (run_refund): the owner's tap in Settings ›
Payments, never a model tool, with its own event on the payment's timeline (public.payment_events) and its own signed
receipt (K4). Payments made as holds before that switch (status `held`) are still settled: payments-tick captures
them (or cancels one whose cancel was asked for) and the nightly check (check_all) keeps every row true to Stripe, as
Stripe's signed webhooks (on_stripe_event, lifeos.api.payments) do: a refund that fails later, a dispute. The card is
lifeos.cards (kind "payment"); the chat's line comes from the tool's own result (lifeos.paychip)."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any, Protocol, TypeVar

from psycopg import Connection

from lifeos import approvals, cards, config
from lifeos.when import tz

FLAG = "LIFEOS_PAYMENTS"  # owner | anything else (unset) = off
KEY = "STRIPE_TEST_KEY"
TOOL = "pay"
REFUND_TOOL = "pay_refund"  # the refund's receipt (a tap in the app, never a tool the model has)
ARGS = ("amount_cents", "currency", "payee", "reason")
MIN_CENTS, MAX_CENTS = 50, 5_000  # Stripe's $0.50 minimum; the pilot's $50 cap (D249)
DAY_CENTS, DAY_COUNT = 5_000, 5
CURRENCIES = frozenset({"usd"})
PAYEE_MAX, REASON_MAX, NOTE_MAX = 60, 140, 200
REFUND_WINDOW_S = 24 * 3600
HOLD_S = 600  # payments made as holds (before 2026-11-07's immediate charge) were captured after this
CHECK_DAYS = 30  # how far back the nightly check looks
# Stripe's test cards (docs.stripe.com/testing): they exist only in a sandbox. "dispute": the charge succeeds, then the
# bank disputes it as fraudulent, to try the dispute flow (the person's setting, for the next payment only)
TEST_CARDS = {"visa": "pm_card_visa", "dispute": "pm_card_createDispute"}
TEST_CARD = TEST_CARDS["visa"]
WENT_THROUGH = ("paid", "partially_refunded", "refunded")
COUNTED = ("pending", "unknown", "held", *WENT_THROUGH)  # may have charged; failed and canceled never did
OPEN = ("pending", "unknown", "held")  # not settled yet
# the person's words for a refund → Stripe's reason (duplicate, fraudulent or requested_by_customer)
REFUND_REASONS = {
    "not_wanted": ("requested_by_customer", "Didn't want it"),
    "duplicate": ("duplicate", "Paid twice"),
    "not_received": ("requested_by_customer", "It didn't arrive"),
    "other": ("requested_by_customer", "Something else"),
}
PROBLEMS = {
    "didnt_arrive": "It didn't arrive",
    "not_what_i_approved": "Not what I approved",
    "i_didnt_approve": "I didn't approve this",
    "other": "Something else",
}
_TEST_KEY = re.compile(r"(?:sk|rk)_test_[A-Za-z0-9]{16,247}")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
PID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_INTENT = re.compile(r"pi_[A-Za-z0-9]{6,250}")
_REFUND = re.compile(r"re_[A-Za-z0-9]{6,250}")
_DISPUTE = re.compile(r"(?:dp|du)_[A-Za-z0-9]{6,250}")
_EVENT = re.compile(r"evt_[A-Za-z0-9]{6,250}")
# a dispute still open (no refund while it is: Stripe may refuse one, and the money's fate is the bank's call), or lost
# (the money already went back to the card)
DISPUTE_BLOCKS_REFUND = frozenset({
    "warning_needs_response", "warning_under_review", "needs_response", "under_review", "appeal_needs_response",
    "appeal_under_review", "lost",
})  # fmt: skip
# Stripe's dispute reasons (docs.stripe.com/api/disputes/object) in plain words; anything else: DISPUTE_OTHER
DISPUTE_WORDS = {
    "fraudulent": "The bank says the card's owner didn't make this payment.",
    "unrecognized": "The card's owner didn't recognize this payment.",
    "product_not_received": "The bank says it never arrived.",
    "product_unacceptable": "The bank says it wasn't as described.",
    "duplicate": "The bank says it was charged twice.",
    "credit_not_processed": "The bank says a promised refund didn't come.",
    "subscription_canceled": "The bank says it had been canceled.",
}
DISPUTE_OTHER = "The bank is looking into this payment."
DISPUTE_END = {
    "won": "The bank closed it; the payment stands.",
    "lost": "The bank closed it; the money went back to the card.",
    "warning_closed": "The bank closed its question; nothing changed.",
    "prevented": "It was settled before it became a dispute.",
}
DISPUTE_STATUSES = frozenset({
    "warning_needs_response", "warning_under_review", "warning_closed", "needs_response", "under_review",
    "appeal_needs_response", "appeal_under_review", "won", "lost", "prevented",
})  # fmt: skip
_NUMBER_RUN = re.compile(r"\d[\d -]*\d")  # a card or account number written with spaces or dashes
_LOCKED = False  # a Stripe answer said livemode: no payment in this process until it restarts
IN_FLIGHT_S = 120  # a pending row younger than this may still be talking to Stripe: a new payment waits
SEARCH_LAG_S = (
    600  # Stripe's search can lag a new PaymentIntent by about a minute; "not found" counts only after this
)

OFF = "Payments aren't switched on for this account; nothing was paid."
NOT_HERE = "Payments run only in Atlas's own chat, with the person watching; nothing was paid."
NO_KEY = "Payments are off: Atlas pays only with a Stripe test key, and none is set up. Nothing was paid."
LOCKED = (
    "Payments are stopped: Stripe answered in live mode and Atlas makes test payments only. Nothing more is paid until "
    "Atlas restarts with a test key."
)
ONE_CARD = "A payment always gets its own card in Atlas's chat, one at a time; it can't be part of a plan. Nothing was paid."
PAID_NOTE = (
    "Test mode, no real money: it is paid. Refunds and any problem: the person uses Settings > Payments (you and undo "
    "can't). Reply in one short sentence, no source label."
)
IN_PROGRESS = "Another test payment is still going through, so nothing new was paid. Try again in a minute."
UNCLEAR = (
    "An earlier test payment has no clear answer from Stripe yet, so nothing new was paid. Try again in a few minutes, "
    "or check the Stripe test dashboard."
)


class PaymentError(ValueError):
    """Refused or failed, in plain words the model may repeat (never a key, never a Stripe request)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Declined(Exception):
    """Stripe said no (the card): nothing was charged."""


class Refused(Exception):
    """Stripe refused the request itself (key, permission, request, rate): nothing was made."""


class Unknown(Exception):
    """No clear answer (network, Stripe's own error): it may have charged."""


def money(cents: int, currency: str = "usd") -> str:
    return f"${cents // 100:,}.{cents % 100:02d}" if currency == "usd" else f"{cents} {currency}"


@dataclass(frozen=True)
class Payment:
    amount_cents: int
    currency: str
    payee: str
    reason: str

    def args(self) -> dict[str, Any]:
        """The tool's exact arguments: what the card shows and the grant covers."""
        return {
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "payee": self.payee,
            "reason": self.reason,
        }

    def money(self) -> str:
        return money(self.amount_cents, self.currency)


@dataclass(frozen=True)
class Limits:
    """The person's own limits (public.payment_settings), never above the code's maximums, and the next payment's test
    card. No row: these defaults."""

    per_payment_cents: int = MAX_CENTS
    per_day_cents: int = DAY_CENTS
    per_day_count: int = DAY_COUNT
    test_card: str = "visa"

    def raised_from(self, old: Limits) -> bool:
        return (
            self.per_payment_cents > old.per_payment_cents
            or self.per_day_cents > old.per_day_cents
            or self.per_day_count > old.per_day_count
        )

    def shown(self) -> dict[str, Any]:
        return {
            "per_payment_cents": self.per_payment_cents,
            "per_day_cents": self.per_day_cents,
            "per_day_count": self.per_day_count,
            "test_card": self.test_card,
            "max": {"per_payment_cents": MAX_CENTS, "per_day_cents": DAY_CENTS, "per_day_count": DAY_COUNT},
        }


MAXIMUMS = Limits()  # no row in payment_settings: the code's maximums


def limits_of(value: Mapping[str, Any]) -> Limits:
    """Limits from the app's request, or PaymentError: whole numbers within the code's maximums, a known test card."""
    out: dict[str, Any] = {}
    for name, low, high in (
        ("per_payment_cents", MIN_CENTS, MAX_CENTS),
        ("per_day_cents", MIN_CENTS, DAY_CENTS),
        ("per_day_count", 1, DAY_COUNT),
    ):
        v = value.get(name)
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise PaymentError("invalid", f"{name}: a whole number from {low} to {high}")
        out[name] = v
    card = value.get("test_card", "visa")
    if card not in TEST_CARDS:
        raise PaymentError("invalid", "test_card: visa or dispute")
    return Limits(test_card=card, **out)


# ---------- who may pay, with which key ----------


def enabled_for(user: str | None) -> bool:
    """LIFEOS_PAYMENTS=owner and `user` is OWNER_USER_ID. Read per call (unset = off for everyone)."""
    if config.get(FLAG, "").strip().lower() != "owner":
        return False
    owner = config.get("OWNER_USER_ID", "").strip().lower()
    return bool(owner) and str(user or "").strip().lower() == owner


def off_reason(user: str | None, *, agent: str | None, unattended: bool) -> str | None:
    """Why this call may not pay at all (None: it may ask). A custom agent's call or a run a trigger or a schedule
    started never pays, whatever its card would say."""
    if not enabled_for(user):
        return OFF
    if agent or unattended:
        return NOT_HERE
    return None


def test_key() -> str:
    """STRIPE_TEST_KEY when it is a sandbox key (sk_test_ / rk_test_); refused otherwise and once a live answer came.
    The error never carries the key."""
    if _LOCKED:
        raise PaymentError("live_mode", LOCKED)
    key = config.get(KEY, "").strip()
    if not _TEST_KEY.fullmatch(key):
        raise PaymentError("not_test_key", NO_KEY)
    return key


def _lock() -> None:
    global _LOCKED
    _LOCKED = True


# ---------- what may be paid ----------


def _line(value: Any, name: str, limit: int) -> str:
    """One plain line exactly as the card shows it: the card flattens spaces and drops hidden characters, so a value
    that would change on the card (and say something else than what is paid) is refused, never cleaned."""
    if not isinstance(value, str) or not value:
        raise PaymentError("invalid", f"{name}: plain text, not empty")
    if _CONTROL.search(value) or cards.flat(value) != value:
        raise PaymentError("invalid", f"{name}: one plain line, single spaces, no hidden characters")
    if len(value) > limit:
        raise PaymentError("invalid", f"{name}: at most {limit} characters")
    if any(len(re.sub(r"\D", "", run)) >= 12 for run in _NUMBER_RUN.findall(value)):
        raise PaymentError("invalid", f"{name}: no card or account numbers (Atlas never keeps them)")
    return value


def check(args: Mapping[str, Any]) -> Payment:
    """The payment these arguments name, or PaymentError: exactly ARGS, whole cents in range, usd, plain lines."""
    if set(args) != set(ARGS):
        raise PaymentError("invalid", "pay takes exactly amount_cents, currency, payee and reason")
    cents = args["amount_cents"]
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise PaymentError("invalid", "amount_cents: a whole number of cents, e.g. 1200 for $12.00")
    if not MIN_CENTS <= cents <= MAX_CENTS:
        raise PaymentError(
            "over_cap" if cents > MAX_CENTS else "invalid",
            f"amount_cents: from {MIN_CENTS} ({money(MIN_CENTS)}) to {MAX_CENTS} ({money(MAX_CENTS)}) in this test "
            "pilot; nothing was paid",
        )
    if not isinstance(args["currency"], str) or args["currency"] not in CURRENCIES:
        raise PaymentError("invalid", "currency: usd only in this test pilot")
    return Payment(
        cents,
        args["currency"],
        _line(args["payee"], "payee", PAYEE_MAX),
        _line(args["reason"], "reason", REASON_MAX),
    )


def refusal(args: Mapping[str, Any]) -> str | None:
    """For the gate (runtime.policy): why this payment can't even be shown on a card (None: it can)."""
    try:
        check(args)
    except PaymentError as e:
        return str(e)
    return None


def room(used_cents: int, used_count: int, cents: int, limits: Limits = MAXIMUMS) -> str | None:
    """Why one more payment of `cents` doesn't fit (None: it fits): the person's limit for one payment, then the
    day's. Refunded payments still count."""
    if cents > limits.per_payment_cents:
        return (
            f"That's over your limit of {money(limits.per_payment_cents)} a payment (Settings, Payments). Nothing was "
            "paid."
        )
    if used_count >= limits.per_day_count:
        return (
            f"That would be payment {used_count + 1} today; your limit is {limits.per_day_count} a day. Nothing was "
            "paid."
        )
    if used_cents + cents > limits.per_day_cents:
        return (
            f"That would make {money(used_cents + cents)} today; your limit is {money(limits.per_day_cents)} a day "
            "(refunded payments still count). Nothing was paid."
        )
    return None


def nonce_sha(owner: str, nonce: str) -> str:
    """The approval's one-time nonce, bound to its owner: stored on the row (unique) and the Stripe key's root."""
    return hashlib.sha256(f"lifeos-pay/v1\n{owner}\n{nonce}".encode()).hexdigest()


def idempotency_key(sha: str) -> str:
    return f"atlas-pay-{sha}"


def refund_key(pid: str, request: str | None = None) -> str:
    """One refund per tap: the app's request id makes a retry of the same tap refund once at most. Without one (the
    pilot's chat line), the whole amount, once per payment."""
    return f"atlas-refund-{pid}-{request}" if request else f"atlas-refund-{pid}"


def day_start(now: datetime) -> datetime:
    """Midnight of `now`'s day where the person lives (LIFEOS_TZ)."""
    zone = tz()
    return datetime.combine(now.astimezone(zone).date(), dtime.min, tzinfo=zone)


# ---------- the ledger (public.payments and its timeline, as the person: RLS) ----------

T = TypeVar("T")
Tx = Callable[[Callable[[Connection], Any]], Any]


def tx_for(owner: str) -> Tx:
    """Each step in its own committed transaction as `owner` (role lifeos_tools + their claims, so RLS holds)."""
    from lifeos import db

    def tx(fn: Callable[[Connection], T]) -> T:
        with db.connect() as conn, conn.transaction(), db.acting_as(conn, owner):
            return fn(conn)

    return tx


_COLUMNS = (
    "id, created_at, amount_cents, currency, payee, reason, status, intent_id, refund_id, capture_after, captured_at, "
    "canceled_at, refunded_cents, refunded_at, dispute_id, dispute_status, cancel_requested_at"
)
_NAMES = _COLUMNS.split(", ")
_EVENT_COLUMNS = "at, kind, source, amount_cents, ref, note"
EVENT_KINDS = frozenset({
    "approved", "held", "captured", "paid", "canceled", "declined", "failed", "unknown", "refunded", "refund_failed",
    "dispute_opened", "dispute_updated", "dispute_closed", "problem_reported",
})  # fmt: skip


def _row(r: Any) -> dict[str, Any]:
    out = dict(zip(_NAMES, r, strict=True))
    out["id"] = str(out["id"])
    return out


def _event(
    c: Connection, owner: str, pid: str, kind: str, source: str, *, amount: int | None = None,
    ref: str | None = None, note: str | None = None, at: datetime | None = None, stripe_event: str | None = None,
) -> bool:  # fmt: skip
    """One step on the payment's timeline; False when it was there already (the same Stripe refund, dispute or event)."""
    cur = c.execute(
        "insert into public.payment_events (owner_id, payment_id, at, kind, source, amount_cents, ref, note, "
        "stripe_event_id) values (%s, %s, coalesce(%s, now()), %s, %s, %s, %s, %s, %s) on conflict do nothing",
        (owner, pid, at, kind, source, amount, ref, (note or "")[:280] or None, stripe_event),
    )
    return cur.rowcount == 1


class Ledger:
    def __init__(self, tx: Tx) -> None:
        self.tx = tx

    # -- limits --

    def limits(self, owner: str) -> Limits:
        return self.tx(lambda c: _limits(c, owner))

    def set_limits(self, owner: str, new: Limits) -> None:
        self.tx(
            lambda c: c.execute(
                "insert into public.payment_settings (owner_id, per_payment_cents, per_day_cents, per_day_count, "
                "test_card) values (%s, %s, %s, %s, %s) on conflict (owner_id) do update set per_payment_cents = "
                "excluded.per_payment_cents, per_day_cents = excluded.per_day_cents, per_day_count = "
                "excluded.per_day_count, test_card = excluded.test_card, updated_at = now()",
                (owner, new.per_payment_cents, new.per_day_cents, new.per_day_count, new.test_card),
            )
        )

    # -- a new payment --

    def reserve(
        self, owner: str, p: Payment, sha: str, now: datetime, run_id: int | None = None
    ) -> tuple[str, str]:
        """The person's limits and the day's room checked and the row written as `pending`, with its "approved" step,
        under a per-person lock held to commit: before Stripe is called, so a crash after it leaves a row that still
        counts. Returns (payment id, test card); the dispute card is used once. Raises PaymentError (day_cap)."""

        def go(c: Connection) -> tuple[str, str]:
            c.execute("select pg_advisory_xact_lock(hashtextextended('payments:' || %s, 0))", (owner,))
            if c.execute(  # a live answer ever recorded stops payments for good, not only in the process that saw it
                "select 1 from public.payments where owner_id = %s and livemode limit 1", (owner,)
            ).fetchone():
                _lock()
                raise PaymentError("live_mode", LOCKED)
            lim = _limits(c, owner)
            used = c.execute(
                "select coalesce(sum(amount_cents), 0), count(*) from public.payments where owner_id = %s "
                "and created_at >= %s and status = any(%s)",
                (owner, day_start(now), list(COUNTED)),
            ).fetchone()
            said = room(int(used[0]), int(used[1]), p.amount_cents, lim) if used else None
            if said:
                raise PaymentError("day_cap", said)
            row = c.execute(
                "insert into public.payments (owner_id, created_at, amount_cents, currency, payee, reason, nonce_sha, "
                "run_id) values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
                (owner, now, p.amount_cents, p.currency, p.payee, p.reason, sha, run_id),
            ).fetchone()
            pid = str(row[0])
            if lim.test_card != "visa":
                c.execute(
                    "update public.payment_settings set test_card = 'visa' where owner_id = %s", (owner,)
                )
            _event(
                c, owner, pid, "approved", "you", amount=p.amount_cents, note=f"{p.payee}: {p.reason}", at=now
            )
            return pid, lim.test_card

        return self.tx(go)

    # -- what Stripe answered --

    def settle(
        self, owner: str, pid: str, status: str, intent: str | None = None, *, livemode: bool = False,
        capture_after: datetime | None = None, kind: str | None = None, source: str = "atlas", note: str | None = None,
        stripe_event: str | None = None,
    ) -> bool:  # fmt: skip
        """A payment with no clear answer yet (pending or unknown) gets Stripe's: held, paid, failed, canceled or
        unknown (a row only ever moves forward; a trigger refuses the rest). `livemode`: Stripe said live (the process
        locks; the row says so). False when the row had an answer already (nothing changes)."""

        def go(c: Connection) -> bool:
            cur = c.execute(
                "update public.payments set status = %s, intent_id = coalesce(%s, intent_id), settled_at = now(), "
                "livemode = livemode or %s, capture_after = coalesce(capture_after, %s), "
                "canceled_at = case when %s = 'canceled' then now() else canceled_at end, "
                "captured_at = case when %s = 'paid' then now() else captured_at end "
                "where id = %s and owner_id = %s and status in ('pending', 'unknown')",
                (status, intent, livemode, capture_after, status, status, pid, owner),
            )
            if cur.rowcount != 1:
                return False
            _event(c, owner, pid, kind or status, source, ref=intent, note=note, stripe_event=stripe_event)
            return True

        return bool(self.tx(go))

    def captured(
        self,
        owner: str,
        pid: str,
        *,
        source: str = "atlas",
        note: str | None = None,
        stripe_event: str | None = None,
    ) -> bool:
        """A hold became the payment (held → paid)."""

        def go(c: Connection) -> bool:
            cur = c.execute(
                "update public.payments set status = 'paid', captured_at = now(), settled_at = now() "
                "where id = %s and owner_id = %s and status = 'held' returning amount_cents, intent_id",
                (pid, owner),
            )
            got = cur.fetchone()
            if got is None:
                return False
            _event(c, owner, pid, "captured", source, amount=int(got[0]), ref=got[1], note=note,
                   stripe_event=stripe_event)  # fmt: skip
            return True

        return bool(self.tx(go))

    def canceled(
        self, owner: str, pid: str, *, source: str, note: str | None = None, stripe_event: str | None = None
    ) -> bool:
        """A hold released (or one that expired): no money moved."""

        def go(c: Connection) -> bool:
            cur = c.execute(
                "update public.payments set status = 'canceled', canceled_at = now(), settled_at = now() "
                "where id = %s and owner_id = %s and status in ('held', 'pending', 'unknown') returning intent_id",
                (pid, owner),
            )
            got = cur.fetchone()
            if got is None:
                return False
            _event(c, owner, pid, "canceled", source, ref=got[0], note=note, stripe_event=stripe_event)
            return True

        return bool(self.tx(go))

    def mark_live(self, owner: str, pid: str) -> None:
        """Stripe answered in live mode for this payment, whatever its state: the row says so for good."""
        self.tx(
            lambda c: c.execute(
                "update public.payments set livemode = true where id = %s and owner_id = %s", (pid, owner)
            )
        )

    def live(self, owner: str) -> bool:
        """Whether Stripe ever answered in live mode for this person's payments (payments stop)."""
        return bool(
            self.tx(
                lambda c: c.execute(
                    "select 1 from public.payments where owner_id = %s and livemode limit 1", (owner,)
                ).fetchone()
            )
        )

    def refunded(
        self, owner: str, row: Mapping[str, Any], refund: Refund, *, source: str, note: str | None, now: datetime,
        stripe_event: str | None = None,
    ) -> bool:  # fmt: skip
        """A refund Stripe made (succeeded or on its way): recorded once per Stripe refund id, whoever reports it first
        (the tap, the webhook, the nightly check). The person's own tap also writes its signed receipt."""
        pid = str(row["id"])

        def go(c: Connection) -> bool:
            failed = c.execute(  # Stripe said it failed first (events come in any order): it stays not refunded
                "select 1 from public.payment_events where owner_id = %s and payment_id = %s and kind = 'refund_failed' "
                "and ref = %s",
                (owner, pid, refund.id),
            ).fetchone()
            if failed:
                return False
            if not _event(c, owner, pid, "refunded", source, amount=refund.amount, ref=refund.id, note=note, at=now,
                          stripe_event=stripe_event):  # fmt: skip
                # recorded already (the webhook was first): the person's own tap still gets its receipt, once
                if source == "you" and not _receipted(c, owner, refund.id):
                    _refund_receipt(c, owner, row, refund, now)
                return False
            cur = c.execute(
                "update public.payments set refunded_cents = least(amount_cents, refunded_cents + %s), "
                "status = case when refunded_cents + %s >= amount_cents then 'refunded' else 'partially_refunded' end, "
                "refund_id = %s, refunded_at = %s where id = %s and owner_id = %s and status = any(%s)",
                (refund.amount, refund.amount, refund.id, now, pid, owner, list(WENT_THROUGH)),
            )
            if cur.rowcount != 1:
                raise PaymentError("not_paid", "Only a payment that went through can be refunded.")
            if source == "you":
                _refund_receipt(c, owner, row, refund, now)
            return True

        return bool(self.tx(go))

    def refund_failed(
        self, owner: str, pid: str, refund: Refund, *, note: str | None, stripe_event: str | None = None
    ) -> bool:
        """Stripe reports a refund failed (refund.failed, days later at most): the amount counts as paid again."""

        def go(c: Connection) -> bool:
            if not _event(c, owner, pid, "refund_failed", "stripe", amount=refund.amount, ref=refund.id, note=note,
                          stripe_event=stripe_event):  # fmt: skip
                return False
            counted = c.execute(
                "select 1 from public.payment_events where owner_id = %s and payment_id = %s and kind = 'refunded' "
                "and ref = %s",
                (owner, pid, refund.id),
            ).fetchone()
            if counted:
                c.execute(
                    "update public.payments set refunded_cents = greatest(0, refunded_cents - %s), "
                    "status = case when refunded_cents - %s <= 0 then 'paid' else 'partially_refunded' end "
                    "where id = %s and owner_id = %s and status = any(%s)",
                    (refund.amount, refund.amount, pid, owner, list(WENT_THROUGH)),
                )
            return True

        return bool(self.tx(go))

    def disputed(
        self, owner: str, pid: str, dispute_id: str, status: str, kind: str, *, amount: int | None, note: str,
        stripe_event: str | None,
    ) -> bool:  # fmt: skip
        """The bank disputed the payment (in test mode: Stripe's dispute card): its id, status and the step."""

        def go(c: Connection) -> bool:
            if not _event(c, owner, pid, kind, "stripe", amount=amount, ref=dispute_id, note=note,
                          stripe_event=stripe_event):  # fmt: skip
                return False
            c.execute(  # a closed dispute stays closed: an older event arriving late never reopens it
                "update public.payments set dispute_id = coalesce(dispute_id, %s), dispute_status = case when "
                "dispute_status in ('won', 'lost', 'warning_closed', 'prevented') then dispute_status else %s end "
                "where id = %s and owner_id = %s",
                (dispute_id, status, pid, owner),
            )
            return True

        return bool(self.tx(go))

    def problem(self, owner: str, pid: str, why: str, note: str | None, now: datetime) -> bool:
        """The person reported a problem with a payment (a step on its timeline, nothing sent anywhere)."""

        def go(c: Connection) -> bool:
            if (
                c.execute(
                    "select 1 from public.payments where id = %s and owner_id = %s", (pid, owner)
                ).fetchone()
                is None
            ):
                return False
            text = PROBLEMS[why] + (f": {note}" if note else "")
            return _event(c, owner, pid, "problem_reported", "you", note=text, at=now)

        return bool(self.tx(go))

    # -- reading --

    def _select(self, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = self.tx(
            lambda c: c.execute(f"select {_COLUMNS} from public.payments where {where}", params).fetchall()
        )
        return [_row(r) for r in rows]

    def unsettled(self, owner: str, since: datetime) -> list[dict[str, Any]]:
        """The person's payments since `since` that never got a clear answer (pending or unknown), oldest first."""
        return self._select(
            "owner_id = %s and created_at >= %s and status in ('pending', 'unknown') order by created_at",
            (owner, since),
        )

    def due(self, owner: str, now: datetime) -> list[dict[str, Any]]:
        """Holds whose time has come (payments-tick captures them) and holds the person asked to cancel (it retries the
        cancel), oldest first."""
        return self._select(
            "owner_id = %s and status = 'held' and (capture_after <= %s or cancel_requested_at is not null) "
            "order by capture_after limit 50",
            (owner, now),
        )

    def recent(self, owner: str, since: datetime) -> list[dict[str, Any]]:
        """Every payment since `since` that may still change (the nightly check): all but failed and canceled."""
        return self._select(
            "owner_id = %s and created_at >= %s and status not in ('failed', 'canceled') order by created_at "
            "limit 500",
            (owner, since),
        )

    def load(self, owner: str, pid: str) -> dict[str, Any] | None:
        got = self._select("id = %s and owner_id = %s", (pid, owner))
        return got[0] if got else None

    def by_intent(self, owner: str, intent: str) -> dict[str, Any] | None:
        got = self._select("intent_id = %s and owner_id = %s", (intent, owner))
        return got[0] if got else None

    def listing(self, owner: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._select(
            "owner_id = %s order by created_at desc limit %s", (owner, max(1, min(int(limit), 200)))
        )

    def events(self, owner: str, pid: str) -> list[dict[str, Any]]:
        rows = self.tx(
            lambda c: c.execute(
                f"select {_EVENT_COLUMNS} from public.payment_events where owner_id = %s and payment_id = %s "
                "order by id limit 200",
                (owner, pid),
            ).fetchall()
        )
        return [dict(zip(_EVENT_COLUMNS.split(", "), r, strict=True)) for r in rows]

    def receipts(self, owner: str, intent: str) -> list[dict[str, Any]]:
        """The signed receipts that name this payment's Stripe id (the payment and its refunds)."""
        rows = self.tx(
            lambda c: c.execute(
                "select body from public.receipts where owner_id = %s and body -> 'args' -> 'show' ->> 'payment' = %s "
                "order by seq limit 20",
                (owner, intent),
            ).fetchall()
        )
        return [dict(r[0]) for r in rows]


def _limits(c: Connection, owner: str) -> Limits:
    got = c.execute(
        "select per_payment_cents, per_day_cents, per_day_count, test_card from public.payment_settings "
        "where owner_id = %s",
        (owner,),
    ).fetchone()
    if got is None:
        return Limits()
    return Limits(
        min(int(got[0]), MAX_CENTS), min(int(got[1]), DAY_CENTS), min(int(got[2]), DAY_COUNT),
        got[3] if got[3] in TEST_CARDS else "visa",
    )  # fmt: skip


def _receipted(c: Connection, owner: str, refund_id: str) -> bool:
    return bool(
        c.execute(
            "select 1 from public.receipts where owner_id = %s and body -> 'args' -> 'show' ->> 'refund' = %s limit 1",
            (owner, refund_id),
        ).fetchone()
    )


def _refund_receipt(c: Connection, owner: str, row: Mapping[str, Any], refund: Refund, now: datetime) -> None:
    from lifeos.consent import receipts

    if not receipts.enabled():
        return
    pid, currency = str(row["id"]), str(row["currency"])
    receipts.append(
        c,
        owner,
        {
            "actor": {"type": "chat", "id": "main"},  # the person's tap in the app
            "run": None,
            "conversation": None,
            "tool": REFUND_TOOL,
            "tier": "B",
            "authority": {"kind": "tier"},
            "args_summary": f"Refund {money(refund.amount, currency)} paid to {row['payee']} (Stripe test mode)",
            "args_sha": approvals.digest(REFUND_TOOL, {"payment_id": pid, "amount_cents": refund.amount}),
            "inputs": [],
            "ok": True,
            "result_sha_": receipts.result_sha({"refund_id": refund.id}),
            "undo": {"kind": "none"},
            "show": {
                "title": str(row["payee"]),
                "amount": refund.amount,
                "currency": currency,
                "payment": str(row["intent_id"]),
                "refund": refund.id,
            },
        },
        at=int(now.timestamp()),
    )


# ---------- Stripe ----------


@dataclass(frozen=True)
class Charge:
    id: str
    status: str
    livemode: bool


@dataclass(frozen=True)
class Refund:
    id: str
    status: str
    amount: int = 0
    reason: str | None = None


class Rail(Protocol):
    def pay(
        self, *, key: str, amount: int, currency: str, description: str, metadata: dict[str, str],
        idempotency_key: str, card: str = TEST_CARD,
    ) -> Charge: ...  # fmt: skip

    def capture(self, *, key: str, intent: str, idempotency_key: str) -> Charge: ...

    def cancel(self, *, key: str, intent: str, idempotency_key: str) -> Charge: ...

    def retrieve(self, *, key: str, intent: str) -> Charge: ...

    def refund(
        self,
        *,
        key: str,
        intent: str,
        amount: int,
        reason: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> Refund: ...

    def refunds_of(self, *, key: str, intent: str) -> list[Refund]: ...

    def find(self, *, key: str, pid: str) -> Charge | None: ...


def _said(e: Exception, fallback: str) -> str:
    text = getattr(e, "user_message", None)
    return cards.flat(text)[:160] if isinstance(text, str) and text.strip() else fallback


def _cents(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class StripeRail:
    """The official library (pinned in pyproject; its API version is the library's own), a client per call, short
    timeouts, two network retries that reuse the idempotency key. `client`: a factory tests pass instead."""

    def __init__(self, client: Callable[[str], Any] | None = None) -> None:
        self._client = client

    def _c(self, key: str) -> Any:
        if self._client is not None:
            return self._client(key)
        import stripe

        return stripe.StripeClient(key, max_network_retries=2, http_client=stripe.RequestsClient(timeout=20))

    @staticmethod
    def _call(fn: Callable[[], Any]) -> Any:
        import stripe

        try:
            return fn()
        except stripe.CardError as e:
            raise Declined(_said(e, "the test card was declined")) from None
        except (stripe.AuthenticationError, stripe.PermissionError, stripe.InvalidRequestError) as e:
            raise Refused(type(e).__name__) from None
        except stripe.StripeError as e:
            # network, Stripe's own error, an idempotency clash, a rate limit (it may be a retry's answer after the
            # first attempt went through): it may have happened
            raise Unknown(type(e).__name__) from None

    @staticmethod
    def _charge(pi: Any) -> Charge:
        return Charge(str(pi.id), str(pi.status), bool(pi.livemode))

    @staticmethod
    def _refund(r: Any) -> Refund:
        return Refund(str(r.id), str(r.status), _cents(getattr(r, "amount", 0)), getattr(r, "reason", None))

    def pay(
        self, *, key: str, amount: int, currency: str, description: str, metadata: dict[str, str],
        idempotency_key: str, card: str = TEST_CARD,
    ) -> Charge:  # fmt: skip
        if card not in TEST_CARDS.values():
            raise Refused("not a test card")
        params = {
            "amount": amount, "currency": currency, "confirm": True, "payment_method": card,
            "allowed_payment_method_types": ["card"], "error_on_requires_action": True,
            "description": description, "metadata": metadata,
        }  # fmt: skip
        pi = self._call(
            lambda: self._c(key).v1.payment_intents.create(
                params=params, options={"idempotency_key": idempotency_key}
            )
        )
        return self._charge(pi)

    def capture(self, *, key: str, intent: str, idempotency_key: str) -> Charge:
        pi = self._call(
            lambda: self._c(key).v1.payment_intents.capture(
                intent, options={"idempotency_key": idempotency_key}
            )
        )
        return self._charge(pi)

    def cancel(self, *, key: str, intent: str, idempotency_key: str) -> Charge:
        pi = self._call(
            lambda: self._c(key).v1.payment_intents.cancel(
                intent, params={"cancellation_reason": "requested_by_customer"},
                options={"idempotency_key": idempotency_key},
            )
        )  # fmt: skip
        return self._charge(pi)

    def retrieve(self, *, key: str, intent: str) -> Charge:
        return self._charge(self._call(lambda: self._c(key).v1.payment_intents.retrieve(intent)))

    def refund(
        self,
        *,
        key: str,
        intent: str,
        amount: int,
        reason: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> Refund:
        params = {"payment_intent": intent, "amount": amount, "reason": reason, "metadata": metadata}
        r = self._call(
            lambda: self._c(key).v1.refunds.create(
                params=params, options={"idempotency_key": idempotency_key}
            )
        )
        return self._refund(r)

    def refunds_of(self, *, key: str, intent: str) -> list[Refund]:
        got = self._call(
            lambda: self._c(key).v1.refunds.list(params={"payment_intent": intent, "limit": 100})
        )
        return [self._refund(r) for r in list(getattr(got, "data", None) or [])]

    def find(self, *, key: str, pid: str) -> Charge | None:
        """The PaymentIntent Atlas made for payment `pid`, by its own metadata (Stripe's search, which can lag about a
        minute behind a new one), or None. `pid` is a uuid (PID), so the query holds nothing else."""
        if not PID.fullmatch(pid):
            raise Refused("not a payment id")
        query = f"metadata['atlas_payment']:'{pid}'"
        found = self._call(
            lambda: self._c(key).v1.payment_intents.search(params={"query": query, "limit": 2})
        )
        data = list(getattr(found, "data", None) or [])
        if len(data) > 1:  # one row, one idempotency key: two would mean something Atlas can't explain
            raise Unknown("several")
        return self._charge(data[0]) if data else None


# ---------- Stripe's answer → the row ----------


def _apply(
    ledger: Ledger, owner: str, row: Mapping[str, Any], found: Charge, *, now: datetime, source: str,
    note: str | None = None, stripe_event: str | None = None,
) -> bool:  # fmt: skip
    """What Stripe holds for a payment that may still change (pending, unknown or held), applied once: True when the
    row now has a clear state (it may have had it already), False when Stripe's answer is still unclear. A live
    answer locks payments in this process and raises PaymentError (live_mode)."""
    pid, status = str(row["id"]), str(row["status"])
    intent = found.id if _INTENT.fullmatch(found.id) else None
    if found.livemode:
        _lock()
        if not ledger.settle(owner, pid, "unknown", intent, livemode=True):
            ledger.mark_live(owner, pid)  # a hold or a paid one: recorded all the same
        raise PaymentError("live_mode", LOCKED)
    if intent is None:
        return False
    if found.status == "requires_capture":
        if status in ("pending", "unknown"):
            # a hold Atlas never saw placed: the person gets the full window from now
            ledger.settle(owner, pid, "held", intent, capture_after=now + timedelta(seconds=HOLD_S), source=source,
                          note=note, stripe_event=stripe_event)  # fmt: skip
        return True
    if found.status == "succeeded":
        if status == "held":
            ledger.captured(owner, pid, source=source, note=note, stripe_event=stripe_event)
        elif status in ("pending", "unknown"):
            ledger.settle(owner, pid, "paid", intent, source=source, note=note, stripe_event=stripe_event)
        return True
    if found.status == "canceled":
        if status in OPEN:
            ledger.canceled(owner, pid, source=source, note=note or "The hold was released; no money moved.",
                            stripe_event=stripe_event)  # fmt: skip
        return True
    if found.status == "requires_payment_method" and status in ("pending", "unknown"):
        ledger.settle(owner, pid, "failed", intent, kind="declined", source=source, note=note,
                      stripe_event=stripe_event)  # fmt: skip
        return True
    return status not in ("pending", "unknown")


def reconcile(ledger: Ledger, rail: Rail, key: str, owner: str, *, now: datetime) -> None:
    """Before a new payment (and before acting on one that never got a clear answer): every earlier payment of the last
    REFUND_WINDOW_S still pending past IN_FLIGHT_S, or unknown, is looked up in Stripe by its own id in the metadata,
    never sent again (its card's grant is spent: a resend could charge long after the card closed), and settled from
    what Stripe holds. One still in flight or still unclear stops the new payment (PaymentError): never a second
    charge while the first may have gone through. Not found counts as failed only after SEARCH_LAG_S."""
    for row in ledger.unsettled(owner, now - timedelta(seconds=REFUND_WINDOW_S)):
        rid, age = str(row["id"]), (now - row["created_at"]).total_seconds()
        if row["status"] == "pending" and age < IN_FLIGHT_S:
            raise PaymentError("in_progress", IN_PROGRESS)
        try:
            found = rail.find(key=key, pid=rid)
        except (Declined, Refused, Unknown):
            raise PaymentError("unknown", UNCLEAR) from None
        if found is None:
            if age < SEARCH_LAG_S:
                raise PaymentError("unknown", UNCLEAR)
            ledger.settle(owner, rid, "failed", note="Stripe has no payment for it: nothing was charged.")
            continue
        if not _apply(ledger, owner, row, found, now=now, source="stripe", note="Checked with Stripe."):
            raise PaymentError("unknown", UNCLEAR)


def run_payment(
    ledger: Ledger,
    rail: Rail,
    owner: str,
    p: Payment,
    nonce: str,
    *,
    now: datetime,
    run_id: int | None = None,
) -> dict[str, Any]:
    """One approved payment (the caller already spent the grant whose nonce this is), charged at once (Stripe's
    automatic capture). Raises PaymentError."""
    key = test_key()
    reconcile(ledger, rail, key, owner, now=now)
    sha = nonce_sha(owner, nonce)
    pid, card = ledger.reserve(owner, p, sha, now, run_id)
    try:
        charge = rail.pay(
            key=key,
            amount=p.amount_cents,
            currency=p.currency,
            description=f"Atlas test payment to {p.payee}: {p.reason}",
            metadata={"atlas_payment": pid, "atlas_mode": "test"},
            idempotency_key=idempotency_key(sha),
            card=TEST_CARDS[card],
        )
    except Declined as e:
        ledger.settle(owner, pid, "failed", kind="declined", note=f"The test card was declined ({e}).")
        raise PaymentError("declined", f"Stripe declined the test payment ({e}). Nothing was paid.") from None
    except Refused as e:
        ledger.settle(owner, pid, "failed", note=f"Stripe refused the request ({e}).")
        raise PaymentError("stripe", f"Stripe refused the request ({e}). Nothing was paid.") from None
    except Unknown:
        ledger.settle(owner, pid, "unknown", note="Stripe didn't answer clearly.")
        raise PaymentError(
            "unknown",
            "Stripe didn't answer clearly, so this test payment may or may not have gone through. It counts toward "
            "today's limit, and Atlas checks it with Stripe before any new payment.",
        ) from None
    intent = charge.id if _INTENT.fullmatch(charge.id) else None
    if charge.livemode:
        _lock()
        ledger.settle(owner, pid, "unknown", intent, livemode=True)
        raise PaymentError("live_mode", LOCKED)
    if intent is None or charge.status != "succeeded":
        ledger.settle(owner, pid, "unknown", intent, note=f"Stripe said {cards.flat(charge.status)[:40]}.")
        raise PaymentError(
            "unknown",
            f"Stripe says the test payment is {cards.flat(charge.status)[:40]}; it counts toward today's limit.",
        )
    note = "Paid with Stripe's dispute test card." if card == "dispute" else None
    ledger.settle(owner, pid, "paid", intent, note=note)
    until = (now + timedelta(seconds=REFUND_WINDOW_S)).isoformat()
    chip = {"kind": "paid", "amount_cents": p.amount_cents, "currency": p.currency, "payee": p.payee,
            "payment_id": pid, "refund_until": until}  # fmt: skip
    return {"paid": True, "status": "paid", "test_mode": True, "amount": p.money(), "currency": p.currency,
            "payee": p.payee, "reason": p.reason, "payment_id": pid, "stripe_id": intent, "refund_until": until,
            "undo_token": pid, "chip": chip, "note": PAID_NOTE}  # fmt: skip


def _clear(ledger: Ledger, rail: Rail, key: str, owner: str, pid: str, *, now: datetime) -> dict[str, Any]:
    """The owner's payment `pid`, asked of Stripe first when it never got a clear answer. PaymentError when none."""
    if not PID.fullmatch(pid or ""):
        raise PaymentError("not_found", "No such payment.")
    row = ledger.load(owner, pid)
    if row is not None and row["status"] in (
        "pending",
        "unknown",
    ):  # it may have gone through: ask Stripe first
        reconcile(ledger, rail, key, owner, now=now)
        row = ledger.load(owner, pid)
    if row is None:
        raise PaymentError("not_found", "No such payment.")
    return row


def _sync_refunds(
    ledger: Ledger, rail: Rail, key: str, owner: str, row: dict[str, Any], *, now: datetime, note: str
) -> tuple[dict[str, Any], int]:
    """Stripe's own refunds of a payment that went through, recorded where the ledger missed one (a lost webhook or a
    write that failed after Stripe refunded): the row as it is now, and how many steps changed. Raises Declined,
    Refused or Unknown when Stripe can't say."""
    changed, pid = 0, str(row["id"])
    for r in rail.refunds_of(key=key, intent=str(row["intent_id"])):
        if not _REFUND.fullmatch(r.id) or not r.amount:
            continue
        if r.status in ("succeeded", "pending"):
            changed += ledger.refunded(owner, row, r, source="stripe", note=note, now=now)
        elif r.status in ("failed", "canceled"):
            changed += ledger.refund_failed(owner, pid, r, note="The refund failed; the money stays paid.")
        row = ledger.load(owner, pid) or row
    return row, changed


def capture_due(ledger: Ledger, rail: Rail, owner: str, *, now: datetime) -> dict[str, int]:
    """payments-tick, for payments made as holds before 2026-11-07's immediate charge: every hold whose time has come
    becomes the payment (Stripe's capture, once per payment by its idempotency key), and every hold whose cancel was
    asked for is canceled (retried with the same key; it is never captured). One Stripe can't answer now is tried
    again at the next run; one already captured or released (an expired hold) is settled from what Stripe holds. With
    no hold left it is one small read."""
    key = test_key()
    done = {"captured": 0, "canceled": 0, "settled": 0, "later": 0}
    rows = ledger.due(owner, now)
    # a live answer was ever recorded: nothing more is captured, in any process
    if rows and ledger.live(owner):
        _lock()
        raise PaymentError("live_mode", LOCKED)
    for row in rows:
        pid, intent = str(row["id"]), str(row["intent_id"])
        asked = (
            row.get("cancel_requested_at") is not None
        )  # the person's Cancel Stripe didn't answer: cancel again
        try:
            if asked:
                got = rail.cancel(key=key, intent=intent, idempotency_key=f"atlas-cancel-{pid}")
            else:
                got = rail.capture(key=key, intent=intent, idempotency_key=f"atlas-capture-{pid}")
        except Unknown:
            done["later"] += 1
            continue
        except (Declined, Refused):
            try:
                got = rail.retrieve(key=key, intent=intent)
            except (Declined, Refused, Unknown):
                done["later"] += 1
                continue
        if asked and got.status == "canceled" and not got.livemode:
            done["canceled"] += ledger.canceled(
                owner, pid, source="you", note="You canceled the hold; no money moved."
            )
        elif not asked and got.status == "succeeded" and not got.livemode:
            done["captured"] += ledger.captured(owner, pid, note="Paid after the 10-minute hold.")
        elif _apply(ledger, owner, row, got, now=now, source="stripe"):
            done["settled"] += 1
        else:
            done["later"] += 1
    return done


def run_refund(
    ledger: Ledger, rail: Rail, owner: str, pid: str, *, now: datetime, amount_cents: int | None = None,
    reason: str = "not_wanted", request: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The owner's Refund tap: all that is left, or part of it (`amount_cents`), for one of REFUND_REASONS, within
    REFUND_WINDOW_S. `request`: the app's id for this tap (a retry refunds once at most). Raises PaymentError."""
    if reason not in REFUND_REASONS:
        raise PaymentError("invalid", "reason: " + ", ".join(REFUND_REASONS))
    if amount_cents is not None and (isinstance(amount_cents, bool) or not isinstance(amount_cents, int)):
        raise PaymentError("invalid", "amount_cents: a whole number of cents")
    if request is not None:
        try:
            request = str(uuid.UUID(str(request)))
        except ValueError:
            raise PaymentError("invalid", "request: an id the app made") from None
    key = test_key()
    row = _clear(ledger, rail, key, owner, pid, now=now)
    if row["status"] in WENT_THROUGH and row["intent_id"]:
        try:  # what is left comes from Stripe's own refunds: a refund whose write was lost never goes out twice
            row, _ = _sync_refunds(ledger, rail, key, owner, row, now=now, note="Found in Stripe.")
        except (Declined, Refused, Unknown):
            raise PaymentError("unknown", "Stripe didn't answer clearly; try again in a moment.") from None
    total, done = int(row["amount_cents"]), int(row["refunded_cents"] or 0)
    currency = str(row["currency"])
    if row["status"] == "refunded":
        return {"refunded": True, "already": True, "payment_id": pid, "refund_id": row["refund_id"],
                "amount": money(total, currency), "left_cents": 0, "status": "refunded"}  # fmt: skip
    if row["status"] == "held":
        raise PaymentError("held", "It hasn't gone through yet; it can be refunded once it's paid.")
    if row["status"] not in WENT_THROUGH or not row["intent_id"]:
        raise PaymentError("not_paid", "Only a payment that went through can be refunded.")
    if row["dispute_status"] in DISPUTE_BLOCKS_REFUND:
        raise PaymentError(
            "disputed", "The bank is handling this payment, so it can't be refunded from Atlas now."
        )
    if now >= row["created_at"] + timedelta(seconds=REFUND_WINDOW_S):
        raise PaymentError(
            "too_late", "The 24-hour refund window has closed; refund it in the Stripe test dashboard."
        )
    left = total - done
    cents = left if amount_cents is None else amount_cents
    if not 1 <= cents <= left:
        raise PaymentError("invalid", f"A refund can be from $0.01 up to {money(left, currency)}.")
    stripe_reason, words = REFUND_REASONS[reason]
    try:
        r = rail.refund(key=key, intent=str(row["intent_id"]), amount=cents, reason=stripe_reason,
                        metadata={"atlas_payment": pid}, idempotency_key=refund_key(pid, request))  # fmt: skip
    except (Declined, Refused) as e:
        raise PaymentError("stripe", f"Stripe refused the refund ({e}).") from None
    except Unknown:
        raise PaymentError(
            "unknown", "Stripe didn't answer clearly; tap Refund again (the same tap refunds once at most)."
        ) from None
    if r.status not in ("succeeded", "pending") or not _REFUND.fullmatch(r.id):
        raise PaymentError("stripe", f"Stripe says the refund is {cards.flat(r.status)[:40]}.")
    r = replace(r, amount=r.amount or cents)
    note = words + (" (on its way)" if r.status == "pending" else "")
    ledger.refunded(owner, row, r, source="you", note=note, now=now)
    after = ledger.load(owner, pid) or row
    left_after = total - int(after["refunded_cents"] or 0)
    return {"refunded": True, "payment_id": pid, "refund_id": r.id, "amount": money(r.amount, currency),
            "amount_cents": r.amount, "left_cents": left_after, "status": after["status"]}  # fmt: skip


# ---------- Stripe's webhooks and the nightly check ----------

HANDLED = frozenset({
    "payment_intent.amount_capturable_updated", "payment_intent.succeeded", "payment_intent.canceled",
    "payment_intent.payment_failed", "refund.created", "refund.updated", "refund.failed", "charge.dispute.created",
    "charge.dispute.updated", "charge.dispute.closed",
})  # fmt: skip


def intent_of(event: Mapping[str, Any]) -> str | None:
    """The PaymentIntent a verified Stripe event is about (its own id, or the refund's or dispute's), or None."""
    obj = (event.get("data") or {}).get("object") if isinstance(event.get("data"), Mapping) else None
    if not isinstance(obj, Mapping):
        return None
    value = obj.get("id") if obj.get("object") == "payment_intent" else obj.get("payment_intent")
    return value if isinstance(value, str) and _INTENT.fullmatch(value) else None


def on_stripe_event(ledger: Ledger, owner: str, event: Mapping[str, Any], *, now: datetime) -> str:
    """One verified Stripe event (lifeos.api.payments checked its signature and found the owner) applied to the
    owner's payment, forward only: "applied", "same" (nothing new: a repeat, an older event, our own step) or
    "ignored". Live-mode events are never applied."""
    kind, eid = event.get("type"), event.get("id")
    if kind not in HANDLED or not isinstance(eid, str) or not _EVENT.fullmatch(eid) or event.get("livemode"):
        return "ignored"
    intent = intent_of(event)
    row = ledger.by_intent(owner, intent) if intent else None
    if row is None:
        return "ignored"
    obj = event["data"]["object"]
    pid = row["id"]
    if str(kind).startswith("payment_intent."):
        status = {"payment_intent.amount_capturable_updated": "requires_capture",
                  "payment_intent.succeeded": "succeeded", "payment_intent.canceled": "canceled",
                  "payment_intent.payment_failed": "requires_payment_method"}[str(kind)]  # fmt: skip
        if row["status"] not in OPEN:
            return "same"
        before = row["status"]
        note = "Stripe released the hold." if status == "canceled" else None
        _apply(ledger, owner, row, Charge(str(intent), status, False), now=now, source="stripe", note=note,
               stripe_event=eid)  # fmt: skip
        after = ledger.load(owner, pid) or row
        return "applied" if after["status"] != before else "same"
    if str(kind).startswith("refund."):
        rid, status = obj.get("id"), obj.get("status")
        if not isinstance(rid, str) or not _REFUND.fullmatch(rid):
            return "ignored"
        r = Refund(rid, str(status), _cents(obj.get("amount")), obj.get("reason"))
        if status in ("succeeded", "pending") and r.amount and row["status"] in WENT_THROUGH:
            done = ledger.refunded(owner, row, r, source="stripe", note="Refunded in Stripe.", now=now,
                                   stripe_event=eid)  # fmt: skip
            return "applied" if done else "same"
        if status in ("failed", "canceled"):
            why = obj.get("failure_reason")
            note = "The refund failed" + (f" ({cards.flat(str(why)).replace('_', ' ')[:40]})" if why else "")
            note += "; the money stays paid."
            done = ledger.refund_failed(owner, pid, r, note=note, stripe_event=eid)
            return "applied" if done else "same"
        return "same"
    did, status = obj.get("id"), obj.get("status")
    if not isinstance(did, str) or not _DISPUTE.fullmatch(did) or status not in DISPUTE_STATUSES:
        return "ignored"
    step = {"charge.dispute.created": "dispute_opened", "charge.dispute.updated": "dispute_updated",
            "charge.dispute.closed": "dispute_closed"}[str(kind)]  # fmt: skip
    note = DISPUTE_END.get(str(status)) or DISPUTE_WORDS.get(str(obj.get("reason")), DISPUTE_OTHER)
    done = ledger.disputed(owner, pid, did, str(status), step, amount=_cents(obj.get("amount")) or None, note=note,
                           stripe_event=eid)  # fmt: skip
    return "applied" if done else "same"


def check_all(ledger: Ledger, rail: Rail, owner: str, *, now: datetime) -> dict[str, int]:
    """The nightly check (payments-reconcile): every payment of the last CHECK_DAYS that may still change is compared
    with Stripe, for anything a webhook missed: a hold captured or released, a refund made in the dashboard, a refund
    that failed. Each change is a step on the payment's timeline from Stripe."""
    key = test_key()
    seen = {"checked": 0, "changed": 0, "unclear": 0}
    for row in ledger.recent(owner, now - timedelta(days=CHECK_DAYS)):
        seen["checked"] += 1
        pid, intent = str(row["id"]), row["intent_id"]
        try:
            if not intent:
                found = rail.find(key=key, pid=pid)
                if found is None:
                    if (now - row["created_at"]).total_seconds() >= SEARCH_LAG_S:
                        seen["changed"] += ledger.settle(owner, pid, "failed", source="stripe",
                                                         note="Stripe has no payment for it: nothing was charged.")  # fmt: skip
                    continue
            else:
                found = rail.retrieve(key=key, intent=str(intent))
            if row["status"] in OPEN:
                before = row["status"]
                if not _apply(
                    ledger, owner, row, found, now=now, source="stripe", note="Found by the nightly check."
                ):
                    seen["unclear"] += 1
                    continue
                row = ledger.load(owner, pid) or row
                seen["changed"] += row["status"] != before
            if row["status"] in WENT_THROUGH and row["intent_id"]:
                row, changed = _sync_refunds(
                    ledger, rail, key, owner, row, now=now, note="Found by the nightly check."
                )
                seen["changed"] += changed
        except (Declined, Refused, Unknown):
            seen["unclear"] += 1
    return seen


# ---------- what the app shows ----------


def actions_for(row: Mapping[str, Any], *, now: datetime) -> list[str]:
    """The taps that fit the payment's state: refund once it went through (within the window, never while the bank
    handles a dispute); report a problem and the evidence pack always. No cancel: payments are charged at once."""
    out = []
    if (
        row["status"] in ("paid", "partially_refunded")
        and row["intent_id"]
        and row["dispute_status"] not in DISPUTE_BLOCKS_REFUND
        and now < row["created_at"] + timedelta(seconds=REFUND_WINDOW_S)
    ):
        out.append("refund")
    return [*out, "problem", "evidence"]


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def shown(row: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """One payment as the app lists it (never the nonce, the run or anything Stripe sent but the ids)."""
    total, done = int(row["amount_cents"]), int(row["refunded_cents"] or 0)
    return {
        "id": str(row["id"]),
        "created_at": _iso(row["created_at"]),
        "amount_cents": total,
        "currency": row["currency"],
        "payee": row["payee"],
        "reason": row["reason"],
        "status": row["status"],
        "stripe_id": row["intent_id"],
        "capture_at": _iso(row["capture_after"]) if row["status"] == "held" else None,
        "captured_at": _iso(row["captured_at"]),
        "canceled_at": _iso(row["canceled_at"]),
        "refunded_cents": done,
        "refund_left_cents": total - done if row["status"] in WENT_THROUGH else 0,
        "refund_until": _iso(row["created_at"] + timedelta(seconds=REFUND_WINDOW_S)),
        "dispute": {
            "id": row["dispute_id"],
            "status": row["dispute_status"],
            "open": row["dispute_status"] in DISPUTE_BLOCKS_REFUND and row["dispute_status"] != "lost",
        }
        if row["dispute_id"]
        else None,
        "actions": actions_for(row, now=now),
        "test_mode": True,
    }


def shown_event(e: Mapping[str, Any]) -> dict[str, Any]:
    return {"at": _iso(e["at"]), "kind": e["kind"], "source": e["source"], "amount_cents": e["amount_cents"],
            "ref": e["ref"], "note": e["note"]}  # fmt: skip


def evidence(ledger: Ledger, owner: str, pid: str, *, now: datetime) -> dict[str, Any] | None:
    """The evidence pack: the payment as approved, its whole timeline and the signed receipts that name it, for a
    problem with the payee or a bank's dispute. Only this payment: nothing else of the person's."""
    row = ledger.load(owner, pid)
    if row is None:
        return None
    intent = row["intent_id"]
    return {
        "kind": "atlas-payment-evidence/v1",
        "made_at": now.isoformat(),
        "test_mode": True,
        "payment": shown(row, now=now) | {"actions": None},
        "approved": {
            "amount": money(int(row["amount_cents"]), str(row["currency"])),
            "payee": row["payee"],
            "reason": row["reason"],
            "how": (
                "Atlas showed a card with exactly this amount, payee and reason, and the account's owner tapped "
                "Approve. The server checked a signed approval (Ed25519) for exactly these words and used its "
                "one-time code once, so this approval paid this payment and nothing else."
            ),
        },
        "timeline": [shown_event(e) for e in ledger.events(owner, pid)],
        "receipts": ledger.receipts(owner, str(intent)) if intent else [],
        "verify": (
            "Each receipt carries its own hash and is part of the person's signed receipt chain. To check the whole "
            "chain against Atlas's signing key, export receipts in Settings, Permissions and run the verifier."
        ),
    }
