"""Agent payments (the owner-only Stripe test pilot, lifeos.payments): the calm line under Atlas's answer once a test
payment went through, "Paid $12.00 to Blue Bottle in test mode", with Open in Payments (the receipt of a tier-C
payment, in the chat; the app asks for its state now, lifeos.api.payments; refunds live in Settings › Payments).

As with lifeos.memchip: the server takes it only from the `pay` tool's own structured result (key `chip`), never from
the model's words, its arguments or any other tool's result, and checks it again before it is stored on the answer; the
app checks it a third time (web lib/payChip). It carries words, a number and the payment's id, never a link. A chip
from a payment made as a hold (before 2026-11-07) also carries capture_at."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from lifeos import memchip, payments

KIND = "paid"
TOOL = payments.TOOL


def checked(value: Any) -> dict[str, Any] | None:
    """The chip `value` is, normalised, or None when it isn't exactly one: the amount in cents within the pilot's
    range, its currency, the payee in one line, the payment's id, until when Refund works and, for a hold, when it
    becomes the payment (capture_at)."""
    if not isinstance(value, Mapping) or value.get("kind") != KIND:
        return None
    cents, currency = value.get("amount_cents"), value.get("currency")
    payee, pid, until = (
        memchip.line(value.get("payee"), payments.PAYEE_MAX),
        value.get("payment_id"),
        value.get("refund_until"),
    )
    if (
        isinstance(cents, bool)
        or not isinstance(cents, int)
        or not payments.MIN_CENTS <= cents <= payments.MAX_CENTS
    ):
        return None
    if not isinstance(currency, str) or currency not in payments.CURRENCIES or not payee:
        return None
    if not isinstance(pid, str) or not payments.PID.fullmatch(pid):
        return None
    try:
        until_iso = datetime.fromisoformat(str(until)).isoformat()
        capture = value.get("capture_at")
        capture_iso = datetime.fromisoformat(str(capture)).isoformat() if capture is not None else None
    except ValueError:
        return None
    out = {"kind": KIND, "amount_cents": cents, "currency": currency, "payee": payee, "payment_id": pid,
           "refund_until": until_iso}  # fmt: skip
    return out | {"capture_at": capture_iso} if capture_iso else out


def from_result(name: str, output: Any, ok: bool) -> dict[str, Any] | None:
    """The chip `pay` made, from its own result; None for any other tool, a failure or a payment that didn't go
    through."""
    if (
        not ok
        or name != TOOL
        or not isinstance(output, Mapping)
        or output.get("error")
        or output.get("paid") is not True
    ):
        return None
    return checked(output.get("chip"))
