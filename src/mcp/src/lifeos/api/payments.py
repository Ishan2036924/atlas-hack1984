"""Agent payments, the owner-only Stripe test pilot and its payments section (lifeos.payments). The app only: the model
has no tool for any of these. lifeos-api holds no Stripe key (D74: keys live where they're used), so the tap that reaches
Stripe (Refund) is relayed to the agent runner, the one process with STRIPE_TEST_KEY, with the person's own
session token; the runner checks that token itself and acts only on that person's own payment
(lifeos.agents_service /payments/*). Reads, a reported problem, the evidence pack and the limits are the person's own
rows, as the person (RLS).

GET  /v1/payments                      → {enabled, test_mode, limits, items: [Payment ≤50]}
GET  /v1/payments/limits               → Limits (the person's, within the code's maximums, and the next test card)
PUT  /v1/payments/limits               {per_payment_cents, per_day_cents, per_day_count, test_card, confirm_raise?}
     → Limits. Lowering is instant; raising any of them needs confirm_raise=true (the app's own "yes" dialog): 409
     {code: confirm_raise} otherwise.
GET  /v1/payments/{id}                 → {payment, events: [Event]} (the timeline)
POST /v1/payments/{id}/refund          {amount_cents?, reason?, request?} → {refunded, refund_id, amount, left_cents,
     status[, already]} (all that is left or part of it; relayed)
POST /v1/payments/{id}/problem         {reason, note?} → {payment, events}   (a step on the timeline, nothing sent)
GET  /v1/payments/{id}/evidence        → the evidence pack (JSON, a download)
POST /v1/stripe/webhook                Stripe's signed events (no sign-in; below)

Every route but the webhook answers 404 while payments are off for this user (LIFEOS_PAYMENTS, owner only) or for
someone else's payment; 409 when the payment's state doesn't allow the tap; 502/503 when Stripe or the runner can't do
it now.

The webhook (the payments section's item 2): the body is read raw (at most WEBHOOK_MAX_BYTES, counted while
streaming) after a per-address rate limit, and its Stripe-Signature checked with STRIPE_WEBHOOK_SECRET (Stripe's own
library: HMAC-SHA256 over the timestamp and the raw body, WEBHOOK_TOLERANCE_S) before anything in it is read. Only
then: a test-mode event of a handled type about a PaymentIntent Atlas made (public.payment_owner_by_intent, the one
lookup without a person) for someone payments are on for is applied, as that person, forward only
(lifeos.payments.on_stripe_event). Anything else is acknowledged (200) and dropped, so Stripe doesn't retry it; a
database that can't be reached answers 503, so Stripe tries again (for up to three days)."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lifeos import config, db, payments
from lifeos.api import edge, store
from lifeos.runtime import remote

# the runner's worst case: a Stripe lookup and a refund, each 20 s with two retries and their backoff
RUNNER_TIMEOUT_S = 150.0
SECRET = "STRIPE_WEBHOOK_SECRET"
WEBHOOK_MAX_BYTES = 256 * 1024
WEBHOOK_TOLERANCE_S = 300  # Stripe's default: an event older than 5 minutes (a replay) is refused


class RefundBody(BaseModel):
    amount_cents: int | None = Field(default=None, ge=1, le=payments.MAX_CENTS)  # None: all that is left
    reason: str = "not_wanted"
    request: uuid.UUID | None = None  # the app's id for this tap: a retry refunds once at most


class ProblemBody(BaseModel):
    reason: str
    note: str | None = Field(default=None, max_length=payments.NOTE_MAX)


class LimitsBody(BaseModel):
    per_payment_cents: int
    per_day_cents: int
    per_day_count: int
    test_card: str = "visa"
    confirm_raise: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _on(user: str) -> None:
    if not payments.enabled_for(user):
        raise HTTPException(404, "not found")


def _ledger(user: str) -> payments.Ledger:
    return payments.Ledger(lambda fn: store.run(user, fn))


async def _relay(path: str, body: dict[str, Any], what: str) -> dict[str, Any]:
    try:
        async with remote.client(remote.forwarded(), timeout=RUNNER_TIMEOUT_S) as http:
            r = await http.post(path, json=body)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"the {what} couldn't start ({type(e).__name__}); try again") from None
    try:
        got = r.json()
    except ValueError:
        got = {}
    if r.status_code == 200 and isinstance(got, dict):
        return got
    detail = got.get("detail") if isinstance(got, dict) else None
    raise HTTPException(r.status_code if r.status_code in (404, 409, 422, 502, 503) else 502,
                        detail if isinstance(detail, dict) else f"the {what} didn't go through")  # fmt: skip


def detail(user: str, pid: str) -> dict[str, Any]:
    ledger, now = _ledger(user), _now()
    row = ledger.load(user, pid)
    if row is None:
        raise HTTPException(404, "not found")
    return {
        "payment": payments.shown(row, now=now),
        "events": [payments.shown_event(e) for e in ledger.events(user, pid)],
    }


def _refused(why: str) -> None:
    """A dropped webhook call leaves one line in the service log: a class, never the body or the signature."""
    print(f"stripe webhook: {why}", file=sys.stderr)


def mount(app: FastAPI, limited: Callable[[str], Any], users: edge.UserLimit) -> None:
    @app.get("/v1/payments")
    def listing(user: str = Depends(limited("payments_read"))) -> dict[str, Any]:
        _on(user)
        ledger, now = _ledger(user), _now()
        return {
            "enabled": True,
            "test_mode": True,
            "limits": ledger.limits(user).shown(),
            "items": [payments.shown(r, now=now) for r in ledger.listing(user)],
        }

    @app.get("/v1/payments/limits")
    def limits(user: str = Depends(limited("payments_read"))) -> dict[str, Any]:
        _on(user)
        return _ledger(user).limits(user).shown()

    @app.put("/v1/payments/limits")
    def set_limits(body: LimitsBody, user: str = Depends(limited("payments_write"))) -> dict[str, Any]:
        _on(user)
        try:
            new = payments.limits_of(body.model_dump())
        except payments.PaymentError as e:
            raise HTTPException(422, str(e)) from None
        ledger = _ledger(user)
        if new.raised_from(ledger.limits(user)) and not body.confirm_raise:
            raise HTTPException(409, {"code": "confirm_raise", "message": "Raising a limit needs your yes."})
        ledger.set_limits(user, new)
        return ledger.limits(user).shown()

    @app.get("/v1/payments/{payment_id}")
    def one(payment_id: uuid.UUID, user: str = Depends(limited("payments_read"))) -> dict[str, Any]:
        _on(user)
        return detail(user, str(payment_id))

    @app.post("/v1/payments/{payment_id}/refund")
    async def refund(
        payment_id: uuid.UUID, body: RefundBody | None = None, user: str = Depends(limited("payments"))
    ) -> dict[str, Any]:
        _on(user)
        b = body or RefundBody()
        if b.reason not in payments.REFUND_REASONS:
            raise HTTPException(422, "reason: " + ", ".join(payments.REFUND_REASONS))
        out = {"user": user, "payment_id": str(payment_id), "amount_cents": b.amount_cents, "reason": b.reason,
               "request": str(b.request) if b.request else None}  # fmt: skip
        return await _relay("/payments/refund", out, "refund")

    @app.post("/v1/payments/{payment_id}/problem")
    def problem(
        payment_id: uuid.UUID, body: ProblemBody, user: str = Depends(limited("payments_write"))
    ) -> dict[str, Any]:
        _on(user)
        if body.reason not in payments.PROBLEMS:
            raise HTTPException(422, "reason: " + ", ".join(payments.PROBLEMS))
        note = " ".join((body.note or "").split())[: payments.NOTE_MAX] or None
        if not _ledger(user).problem(user, str(payment_id), body.reason, note, _now()):
            raise HTTPException(404, "not found")
        return detail(user, str(payment_id))

    @app.get("/v1/payments/{payment_id}/evidence")
    def evidence(payment_id: uuid.UUID, user: str = Depends(limited("payments_read"))) -> JSONResponse:
        _on(user)
        pack = payments.evidence(_ledger(user), user, str(payment_id), now=_now())
        if pack is None:
            raise HTTPException(404, "not found")
        name = f"atlas-payment-{str(payment_id)[:8]}-evidence.json"
        return JSONResponse(
            json.loads(json.dumps(pack, default=str)),
            headers={"content-disposition": f'attachment; filename="{name}"', "cache-control": "no-store"},
        )

    @app.post("/v1/stripe/webhook", include_in_schema=False)
    async def stripe_webhook(request: Request) -> JSONResponse:
        address = edge.client_address(request.scope)
        if not await users.aallow(f"ip:{address}", "stripe_webhook"):
            raise HTTPException(
                429, "too many requests", {"retry-after": str(edge.UserLimit.retry_after("stripe_webhook"))}
            )
        secret = config.get(SECRET, "").strip()
        if config.get(payments.FLAG, "").strip().lower() != "owner" or not secret.startswith("whsec_"):
            raise HTTPException(404, "not found")
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > WEBHOOK_MAX_BYTES):
            _refused("too_big")
            raise HTTPException(413, "too big")
        raw = bytearray()
        async for part in request.stream():
            raw += part
            if len(raw) > WEBHOOK_MAX_BYTES:
                _refused("too_big")
                raise HTTPException(413, "too big")
        if not verified(bytes(raw), request.headers.get("stripe-signature"), secret):
            _refused("bad_signature")
            raise HTTPException(400, "bad signature")
        try:
            event = json.loads(bytes(raw))
        except ValueError:
            _refused("not_json")
            return JSONResponse({"received": True})
        if not isinstance(event, dict):
            return JSONResponse({"received": True})
        try:
            said = await asyncio.to_thread(apply, event)
        except (db.DbError, OSError) as e:  # Stripe tries again
            _refused(f"db ({type(e).__name__})")
            raise HTTPException(503, "try again") from None
        return JSONResponse({"received": True, "result": said})


def verified(raw: bytes, header: str | None, secret: str) -> bool:
    """Stripe's own check of the Stripe-Signature header over the raw body (the library's WebhookSignature, the
    timestamp within WEBHOOK_TOLERANCE_S). Never raises; a malformed header is simply not verified."""
    import stripe

    try:
        stripe.WebhookSignature.verify_header(raw, header, secret, tolerance=WEBHOOK_TOLERANCE_S)
    except (stripe.SignatureVerificationError, ValueError, IndexError, TypeError):
        return False
    return True


def apply(event: dict[str, Any]) -> str:
    """A verified event applied for the payment's owner, or why not (a word for the log, never the event)."""
    import psycopg

    if event.get("livemode") is not False:
        return "ignored"
    if event.get("type") not in payments.HANDLED:
        return "ignored"
    intent = payments.intent_of(event)
    if intent is None:
        return "ignored"
    try:
        with db.connect() as conn:
            got = conn.execute("select public.payment_owner_by_intent(%s)", (intent,)).fetchone()
    except psycopg.Error as e:
        raise db.DbError(f"owner lookup failed: {type(e).__name__}") from None
    owner = str(got[0]) if got and got[0] else None
    if owner is None or not payments.enabled_for(owner):
        return "ignored"
    try:
        return payments.on_stripe_event(_ledger(owner), owner, event, now=_now())
    except psycopg.Error as e:
        raise db.DbError(f"apply failed: {type(e).__name__}") from None
    except payments.PaymentError:
        return "refused"
