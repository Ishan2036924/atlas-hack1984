"""Signed receipts (K4, spec section 6): one record per action, in a per-person hash chain, with the signer's
signature over chain heads. This module builds a receipt, appends it (under an advisory lock, so two actions at once
can't fork a chain), verifies a chain, signs and checks a seal, and exports and verifies an export offline.

  hash   = sha256('lifeos-receipt/v1\\n' + canonical JSON of the receipt without `hash`)   (canonical: lifeos.approvals)
  prev   = the previous receipt's hash, 64 zeros for the first
  a seal = Ed25519 by lifeos-signer over 'lifeos-receipt-head/v1\\n<owner>\\n<seq>\\n<head>\\n<at>'

The runtime writes receipts (as the person, RLS); lifeos-signer writes seals after recomputing the chain itself. A
receipt stores a Shield-masked summary of the call and the digest a card carries, never the values; for the tools
a permission can name it also keeps `args.show` (what Settings > Permissions shows: the subject or the event's title,
its start, who it reached, how many rows), each value masked on its own, so the screen never parses the summary."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from psycopg import Connection
from psycopg.types.json import Jsonb

from lifeos import approvals
from lifeos.consent import mandates, schemas

GENESIS = "0" * 64
HASH_TAG = "lifeos-receipt/v1"
SEAL_TAG = "lifeos-receipt-head/v1"
EXPORT_MAX = 5000
PAGE_MAX = 200
SUMMARY_MAX = 600
SHOW_TITLE_MAX = 160
SHOW_TO_MAX = 20
SHOW_ADDRESS_MAX = 254
_SHOW_TITLE = {
    "send_email": "subject",
    "outlook_send_email": "subject",
    "calendar_invite": "title",
    "pay": "payee",
}
_INTENT = re.compile(r"^pi_[A-Za-z0-9]{6,250}$")  # a Stripe PaymentIntent id (lifeos.payments, test pilot)
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?)?$")
_FIELDS = ("owner", "seq", "prev", "at", "actor", "run", "conversation", "tool", "tier", "authority", "args", "inputs", "result", "undo")  # fmt: skip


def hash_of(doc: Mapping[str, Any]) -> str:
    """The receipt's hash: over every field but `hash` itself (the previous receipt's hash is one of them)."""
    body = {k: v for k, v in doc.items() if k != "hash"}
    return hashlib.sha256(f"{HASH_TAG}\n{approvals.canonical(body)}".encode()).hexdigest()


def result_sha(result: Any) -> str:
    """A digest of what the tool answered (never the answer): sorted keys, anything odd as text."""
    text = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def never_ran(output: Any) -> bool:
    """The server refused before the tool did anything (a missing or refused approval, a bad identity): nothing
    happened, so there is nothing to receipt."""
    err = output.get("error") if isinstance(output, Mapping) else None
    code = err.get("code") if isinstance(err, Mapping) else None
    return code in ("approval", "identity") or (isinstance(err, str) and err.startswith("approval required"))


def show_of(
    tool: str, args: Mapping[str, Any], mask: Callable[[str, int], str], *, output: Any = None
) -> dict[str, Any] | None:
    """What Settings > Permissions shows of one call, taken from the call's own fields (never from the summary): the
    thing itself (an email's subject, an event's title, a payment's payee), an event's start as given (only an ISO date
    or time), who it reached (every recipient field), how many rows. Each text goes through `mask` (the run's Shield) on
    its own; a body or notes never do. A test payment (lifeos.payments) also keeps its amount in cents, its currency and,
    from the tool's own answer (`output`), Stripe's id for it. None for a tool no permission can name."""
    out: dict[str, Any] = {}
    key = _SHOW_TITLE.get(tool)
    title = args.get(key) if key else None
    if isinstance(title, str) and title.strip():
        out["title"] = mask(title.strip(), SHOW_TITLE_MAX)[:SHOW_TITLE_MAX]
    when = args.get("when") if tool == "calendar_invite" else None
    if isinstance(when, str) and _WHEN.match(when.strip()):
        out["when"] = when.strip()
    to: list[str] = []
    for f in mandates.RECIPIENT_FIELDS.get(tool, ()):
        value = args.get(f)
        items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        to += [mask(str(x).strip(), SHOW_ADDRESS_MAX)[:SHOW_ADDRESS_MAX] for x in items if str(x).strip()]
    if to:
        out["to"] = to[:SHOW_TO_MAX]
    if tool == "rows_bulk_add" and isinstance(args.get("rows"), list):
        out["count"] = len(args["rows"])
    if tool == "pay":
        cents, currency = args.get("amount_cents"), args.get("currency")
        if isinstance(cents, int) and not isinstance(cents, bool) and 0 <= cents <= 99_999_999:
            out["amount"] = cents
        if isinstance(currency, str) and re.fullmatch(r"[a-z]{3}", currency):
            out["currency"] = currency
        intent = output.get("stripe_id") if isinstance(output, Mapping) else None
        if isinstance(intent, str) and _INTENT.match(intent):
            out["payment"] = intent
    return out or None


def build(
    owner: str, seq: int, prev: str, *, at: int, actor: Mapping[str, str], run: int | None, conversation: str | None,
    tool: str, tier: str, authority: Mapping[str, str], args_summary: str, args_sha: str,
    inputs: Sequence[Mapping[str, Any]], ok: bool, result_sha_: str, undo: Mapping[str, Any],
    show: Mapping[str, Any] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The receipt.v1 document, hash included. Raises schemas.SchemaError if what the runtime gave doesn't fit."""
    doc: dict[str, Any] = {
        "v": 1, "owner": owner, "seq": int(seq), "prev": prev, "at": int(at), "actor": dict(actor), "run": run,
        "conversation": conversation, "tool": tool, "tier": tier, "authority": dict(authority),
        "args": {"summary": args_summary[:SUMMARY_MAX], "sha": args_sha}, "inputs": [dict(i) for i in inputs][:16],
        "result": {"ok": bool(ok), "sha": result_sha_}, "undo": dict(undo),
    }  # fmt: skip
    if show:
        doc["args"]["show"] = dict(show)
    doc["hash"] = hash_of(doc)
    schemas.validate("receipt", doc)
    return doc


# ---------- appending (the runtime, as the person) ----------


def append(conn: Connection, owner: str, fields: Mapping[str, Any], *, at: int) -> dict[str, Any]:
    """The next receipt of this person's chain. `fields` are build()'s keywords beyond owner, seq, prev and at. Runs
    under a per-person advisory lock held to the end of the transaction, so seq and prev never fork."""
    conn.execute("select pg_advisory_xact_lock(hashtextextended('receipts:' || %s, 0))", (owner,))
    last = conn.execute(
        "select seq, hash from public.receipts where owner_id = %s order by seq desc limit 1", (owner,)
    ).fetchone()
    seq, prev = (int(last[0]) + 1, str(last[1])) if last else (1, GENESIS)
    doc = build(owner, seq, prev, at=at, **fields)
    conn.execute(
        "insert into public.receipts (owner_id, seq, prev_hash, hash, at, run_id, conversation_id, tool, tier, "
        "authority_kind, authority_ref, ok, body) values (%s, %s, %s, %s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            owner, seq, prev, doc["hash"], doc["at"], doc["run"], doc["conversation"], doc["tool"], doc["tier"],
            doc["authority"]["kind"], doc["authority"].get("ref"), doc["result"]["ok"], Jsonb(doc),
        ),
    )  # fmt: skip
    return doc


# ---------- verifying ----------


@dataclass
class Verdict:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    checked: int = 0
    sealed_to: int = 0  # the newest seq a valid seal covers
    unsealed: int = 0  # receipts after that seal

    def bad(self, why: str) -> None:
        self.ok = False
        if len(self.problems) < 20:
            self.problems.append(why)


def verify_chain(
    docs: Sequence[Mapping[str, Any]], *, owner: str | None = None, start_prev: str | None = None
) -> Verdict:
    """Whether these receipts, in order, are an unbroken piece of one person's chain: each fits the schema, its hash is
    the hash of its own fields, it points at the one before it, seq counts up by one, and the owner never changes.
    `start_prev`: the hash the first receipt must point at (None: the first one is taken as it is, which is right for
    an export that starts mid-chain; GENESIS for one that starts at seq 1)."""
    v = Verdict()
    prev_hash, prev_seq = start_prev, None
    for d in docs:
        v.checked += 1
        seq = d.get("seq")
        try:
            schemas.validate("receipt", d)
        except schemas.SchemaError:
            v.bad(f"receipt {seq}: not a receipt")
            return v
        if d["hash"] != hash_of(d):
            v.bad(f"receipt {seq}: its content does not match its hash")
        if owner is not None and d["owner"] != owner:
            v.bad(f"receipt {seq}: belongs to someone else")
        if prev_seq is not None and seq != prev_seq + 1:
            v.bad(f"receipt {seq}: follows {prev_seq}, so one is missing")
        if prev_hash is not None and d["prev"] != prev_hash:
            v.bad(f"receipt {seq}: does not point at the one before it")
        if (seq == 1) != (d["prev"] == GENESIS):
            v.bad(f"receipt {seq}: the start of the chain is wrong")
        prev_hash, prev_seq = d["hash"], seq
    return v


def seal_message(owner: str, seq: int, head: str, at: int) -> bytes:
    return f"{SEAL_TAG}\n{owner}\n{int(seq)}\n{head}\n{int(at)}".encode()


def seal_sign(signing_key: str, owner: str, seq: int, head: str, at: int) -> dict[str, Any]:
    """lifeos-signer only. The seal.v1 document."""
    key = Ed25519PrivateKey.from_private_bytes(approvals._b64d(signing_key, 32))
    sig = approvals._b64e(key.sign(seal_message(owner, seq, head, at)))
    return {"v": 1, "owner": owner, "seq": int(seq), "head": head, "at": int(at), "sig": sig}


def seal_verify(seal: Mapping[str, Any], public: str) -> bool:
    """Whether the seal is the signer's signature over exactly (owner, seq, head, at). Never raises."""
    try:
        key = Ed25519PublicKey.from_public_bytes(approvals._b64d(public, 32))
        key.verify(
            approvals._b64d(str(seal["sig"]), 64),
            seal_message(str(seal["owner"]), int(seal["seq"]), str(seal["head"]), int(seal["at"])),
        )
    except (InvalidSignature, approvals.ApprovalError, ValueError, KeyError, TypeError):
        return False
    return True


def verify_export(export: Mapping[str, Any], public: str | None = None) -> Verdict:
    """An export checked on its own: the chain inside it, then every seal against the signer's public key (the export
    carries it, or pass the one you trust) and against the receipt it names. `sealed_to` is the newest receipt a valid
    seal vouches for; receipts after it are listed as unsealed, not as wrong."""
    v = Verdict()
    try:
        schemas.validate("export", export)
    except schemas.SchemaError as e:
        v.bad(str(e)[:200])
        return v
    receipts = list(export["receipts"])
    owner, first = export["owner"], export["from_seq"]
    chain = verify_chain(receipts, owner=owner, start_prev=GENESIS if first == 1 else None)
    v.ok, v.problems, v.checked = chain.ok, chain.problems, chain.checked
    if receipts and receipts[0]["seq"] != first:
        v.bad("the export starts at a different receipt than it says")
    if receipts and receipts[-1]["seq"] != export["to_seq"]:
        v.bad("the export ends at a different receipt than it says")
    key = public or export["verify_key"]
    if public is not None and public != export["verify_key"]:
        v.bad("the export names a different signing key than the one you trust")
    by_seq = {r["seq"]: r for r in receipts}
    for s in export["seals"]:
        if s["owner"] != owner or not seal_verify(s, key):
            v.bad(f"the seal at {s['seq']} is not the signer's")
            continue
        r = by_seq.get(s["seq"])
        if r is None:
            continue  # a seal for a receipt outside this export: its signature is fine, nothing here to compare
        if r["hash"] != s["head"]:
            v.bad(f"receipt {s['seq']} is not the one the signer sealed")
        elif chain.ok:
            v.sealed_to = max(v.sealed_to, s["seq"])
    v.unsealed = sum(1 for r in receipts if r["seq"] > v.sealed_to)
    return v


# ---------- reading (the person's own rows) ----------

_BODY = "body"


def _docs(rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    return [dict(r[0]) for r in rows]


def listing(
    conn: Connection, owner: str, *, run_id: int | None = None, conversation: str | None = None,
    since: tuple[int, int] | None = None, mandate: str | None = None, after_seq: int = 0, limit: int = 50,
) -> list[dict[str, Any]]:  # fmt: skip
    """The person's receipts, oldest first after `after_seq`: a run's, a chat's, a chat turn's (`since` = (from, to)
    unix seconds, inclusive) or one permission's (`mandate`: the calls it let through), at most PAGE_MAX."""
    where, params = ["owner_id = %s", "seq > %s"], [owner, int(after_seq)]
    if run_id is not None:
        where.append("run_id = %s")
        params.append(int(run_id))
    if conversation is not None:
        where.append("conversation_id = %s")
        params.append(conversation)
    if since is not None:
        where.append("at >= to_timestamp(%s) and at <= to_timestamp(%s)")
        params += [int(since[0]), int(since[1])]
    if mandate is not None:
        where.append("authority_kind = 'mandate' and authority_ref = %s")
        params.append(mandate)
    params.append(max(1, min(int(limit), PAGE_MAX)))
    rows = conn.execute(
        f"select body from public.receipts where {' and '.join(where)} order by seq limit %s", tuple(params)
    ).fetchall()
    return _docs(rows)


def chain_from(conn: Connection, owner: str, seq: int, limit: int) -> list[dict[str, Any]]:
    """The documents of this person's chain from `seq` on, in order (lifeos-signer re-derives a seal from these)."""
    rows = conn.execute(
        "select body from public.receipts where owner_id = %s and seq >= %s order by seq limit %s",
        (owner, max(1, int(seq)), max(1, int(limit))),
    ).fetchall()
    return _docs(rows)


def head(conn: Connection, owner: str) -> tuple[int, str] | None:
    row = conn.execute(
        "select seq, hash from public.receipts where owner_id = %s order by seq desc limit 1", (owner,)
    ).fetchone()
    return (int(row[0]), str(row[1])) if row else None


def seals(conn: Connection, owner: str, *, after_seq: int = 0, limit: int = PAGE_MAX) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select seq, head, extract(epoch from at)::bigint, sig from public.receipt_seals where owner_id = %s and seq > %s "
        "order by seq limit %s",
        (owner, int(after_seq), max(1, min(int(limit), EXPORT_MAX))),
    ).fetchall()
    return [
        {"v": 1, "owner": owner, "seq": int(r[0]), "head": str(r[1]), "at": int(r[2]), "sig": str(r[3])}
        for r in rows
    ]


def last_seal(conn: Connection, owner: str) -> dict[str, Any] | None:
    got = conn.execute(
        "select seq, head, extract(epoch from at)::bigint, sig from public.receipt_seals where owner_id = %s "
        "order by seq desc limit 1",
        (owner,),
    ).fetchone()
    return (
        {
            "v": 1,
            "owner": owner,
            "seq": int(got[0]),
            "head": str(got[1]),
            "at": int(got[2]),
            "sig": str(got[3]),
        }
        if got
        else None
    )


def export(conn: Connection, owner: str, *, from_seq: int, public: str, now: int) -> dict[str, Any]:
    """The person's receipts from `from_seq` (at most EXPORT_MAX) with the seals that cover them, ready to be checked
    offline (verify_export) with the signer's public key."""
    rows = conn.execute(
        "select body from public.receipts where owner_id = %s and seq >= %s order by seq limit %s",
        (owner, max(1, int(from_seq)), EXPORT_MAX),
    ).fetchall()
    docs = _docs(rows)
    first = docs[0]["seq"] if docs else max(1, int(from_seq))
    last = docs[-1]["seq"] if docs else 0
    return {
        "v": 1, "owner": owner, "from_seq": first, "to_seq": last, "receipts": docs,
        "seals": [s for s in seals(conn, owner, after_seq=max(0, first - 1)) if s["seq"] <= last],
        "verify_key": public, "exported_at": int(now),
    }  # fmt: skip


def insert_seal(conn: Connection, owner: str, seal: Mapping[str, Any]) -> bool:
    """lifeos-signer, as the person: keep one signed head. False when that seq is already sealed."""
    cur = conn.execute(
        "insert into public.receipt_seals (owner_id, seq, head, at, sig) values (%s, %s, %s, to_timestamp(%s), %s) "
        "on conflict (owner_id, seq) do nothing",
        (owner, seal["seq"], seal["head"], seal["at"], seal["sig"]),
    )
    return cur.rowcount == 1


RETENTION_FLOOR_DAYS = (
    30  # the traces' shortest window (lifeos.traces.KEEP_DAYS_DEFAULT); the database holds the floor too
)


def prune(conn: Connection, *, days: int) -> int:
    """Age this person's receipts out with their traces (the nightly job passes lifeos.traces.keep_days): removes the
    receipts and seals that come before the newest seal on a receipt older than `days`, so history is only dropped
    after the signer vouched for it, and the seal that anchors what is left stays (an export from there verifies mid-
    chain). A chain nobody sealed is kept whole. Returns how many receipts went. As the person (public.receipts_prune
    reads auth.uid(): it can never name someone else)."""
    row = conn.execute("select public.receipts_prune(%s)", (max(RETENTION_FLOOR_DAYS, int(days)),)).fetchone()
    return int(row[0] or 0) if row else 0


def enabled() -> bool:
    """LIFEOS_RECEIPTS=0 keeps no receipts (the rest of the consent layer is unaffected)."""
    from lifeos import config

    return config.get("LIFEOS_RECEIPTS", "1").strip() != "0"


def sink(adb: Any, user: str) -> Any:
    """The runtime's way to keep a receipt: one short transaction as the person (`adb`), None when switched off."""
    if not enabled():
        return None

    async def write(fields: dict[str, Any], at: int) -> None:
        await adb(lambda c: append(c, user, fields, at=at))

    return write
