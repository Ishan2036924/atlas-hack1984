"""Open Pseudonym at the model boundary (2026-11-07; plan .local/plans/pseudo-atlas.md): Ishan's open SDK
(open-pseudonym on PyPI, Apache-2.0) as a second layer after Atlas Shield, on every model request Atlas sends, with a
signed egress receipt for each one and "What the AI saw" for the person.

LIFEOS_PSEUDONYM: `off` (the default; any other value too) = today's app exactly: nothing here runs, no receipts, GET
/v1/pseudonym answers {"enabled": false}; `owner` = only OWNER_USER_ID; `all` = every user. It also needs
LIFEOS_PSEUDONYM_KEY (32 random bytes, `scripts/set-secret.py --generate LIFEOS_PSEUDONYM_KEY`, never typed or shown):
without it the switch counts as off (logged once), so a missing key never breaks a chat. The key never leaves the
server; from it (HKDF-SHA256, one label each) come the Ed25519 key that signs receipts, the key that seeds each run's
stand-ins and the key that seals the person's own words in "What the AI saw".

Where it runs (one boundary per path, right before the request leaves): Runtime B's every model call
(runtime.studio: the main chat, custom agents, their delegates and helpers, Agent mode), lifeos.llm.complete (the
workers' calls: titles, summaries, memory, research, drafts) and lifeos.llm.embed (vectors: secrets only, so recall
still works). Not here (the same limits Shield has, plus the Agent Builder's own requests): voice, images, places.

What a Boundary does with one request, after Shield has masked it as today:
  1. masks `input` (every message, text part, call's arguments and tool output), the `instructions` (Atlas's own
     fixed words come out the same; a plan or an agent's own words don't) and the `description`/`title` texts of the
     tools and the answer's shape, with the SDK's policy for the person's Shield level: secrets (cards, bank numbers, SSNs, passwords, keys, one-time codes) become {{CARD_1}} handles at every
     level; Private adds fake emails, phones and addresses; Strict adds fake names and companies. Dates, links, money
     and places stay (Atlas's tools need them); the SDK's `tag` action is never used, so its stand-ins can't be
     mistaken for Shield's [EMAIL_1] labels, which it leaves alone. A note for the model explains any handle.
  2. checks the request (fail closed: Stopped, nothing is sent, no retry; an error inside the SDK too): no value
     either layer hid this run (the SDK's vault, Shield's vault) may still be in it, and the SDK's detectors find
     nothing new in it. Each text is read once per vault version (each step resends the run).
  3. signs a receipt: the SDK's egress receipt (spec 11.2, schema-exact), one hash chain per turn (`session` =
     "<owner>/<turn>"), `dest` = "openai/<model>", `payload` = the digest of the request body exactly as the sender
     sends it (canonical JSON, effort fitted to the model; the sender adds only `stream` and `store`), the stand-ins by type (the SDK's entries and Shield's
     labels, under the SDK's type names) and the check's counts. Never a value. The first one of a turn also carries
     what the AI saw of the person's words (`seen`, masked) and those words themselves sealed (AES-256-GCM, AAD =
     owner/turn: no plain second copy of a private value in the database; only the API opens it, for its owner).
  4. puts its own stand-ins back in the answer, as it streams (a stand-in cut between pieces is held until whole),
     before Shield's unmask: fakes become the real values, except inside a link, in words and in tool calls alike (a
     page that steered the model can't get one sent to it, nor one tap away: P2-309's rule); a handle reads as a short
     label in words ("[a Visa card ending 1111]") and stays a {{handle}} in a tool call (a hidden secret is never echoed
     or put into an action, RULES S8).
Receipts are booked with the call's ledger row (lifeos.llm.record, in a savepoint: a missing table never costs a
ledger row) into public.pseudonym_receipts (owner-only, RLS). The stand-ins come from a vault seeded per person and
chat (or run), rebuilt every step from the run's own input and never stored, so the same run sees the same fakes at
every step (the prompt cache holds) and one person's stand-ins can never be put back into another's words."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import psycopg
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from open_pseudonym import Policy, Session, Signer, Vault
from open_pseudonym import egress as opn_egress
from open_pseudonym.jcs import digest
from open_pseudonym.jws import public_jwk
from psycopg import Connection
from psycopg.types.json import Jsonb

from lifeos import config, llm, shield
from lifeos.llm import ShieldStopped

log = logging.getLogger(__name__)

FLAG = "LIFEOS_PSEUDONYM"
KEY = "LIFEOS_PSEUDONYM_KEY"
OFF, OWNER, ALL = "off", "owner", "all"
KEY_MIN = 32  # characters of the key as saved (set-secret makes 43)
SEEN_MAX = 4000  # characters of the person's words kept for "What the AI saw" (each side)
MARKS_MAX = 200  # stand-ins marked in what the AI saw
RECEIPT_TYP = opn_egress.RECEIPT_TYP
SHIELD_DETECTOR = "atlas-shield@1"
# Shield's kinds under the SDK's type names (a receipt counts both layers' stand-ins in one vocabulary)
TYPE_OF: dict[str, str] = {
    "CARD": "CARD", "BANK": "BANK_ACCOUNT", "SSN": "SSN", "PASSWORD": "PASSWORD", "KEY": "API_KEY", "CODE": "OTP",
    "EMAIL": "EMAIL", "PHONE": "PHONE", "ADDRESS": "ADDRESS", "PERSON": "PERSON", "COMPANY": "ORG",
}  # fmt: skip
SECRETS: tuple[str, ...] = ("CARD", "IBAN", "ROUTING", "BANK_ACCOUNT", "SSN", "API_KEY", "PASSWORD", "OTP")
# what each Shield group adds to the SDK's policy (fakes; the SDK keeps the real value in its vault)
_FAKES: dict[str, tuple[str, ...]] = {
    "EMAIL": ("EMAIL",), "PHONE": ("PHONE",), "ADDRESS": ("ADDRESS", "IP_ADDRESS"), "PERSON": ("PERSON",),
    "COMPANY": ("ORG",),
}  # fmt: skip
STOPPED = "pseudonym: a private detail was still in the request"
# the model's note when the request holds a handle (after the run's items, never in the cached prefix)
HANDLE_NOTE = (
    "Atlas replaced some secrets in this conversation with handles in double braces. A handle is never filled into an "
    "action, so never put one in a tool call: if an action needs it, ask the user to type it in themselves."
)
_HANDLE = re.compile(r"\{\{[A-Z][A-Z0-9_]*_\d+\}\}")
# a word that is (or holds) a link: a scheme, //, www., or a host with a path; nothing is put back inside one
_SPACES = re.compile(
    "[" + re.escape("".join(sorted(shield._SPACE))) + "]+"
)  # Shield's spaces (as the app's \\s)
_LINKISH = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|//|\bwww\.|\b(?:https?|mailto|data|javascript|vbscript|file|tel|sms|intent|blob)\s*:"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?::\d+)?[/?#](?=[\w%=&./~?#-]))"
)


class Stopped(ShieldStopped):
    """The check found a value that should have been hidden (fail closed: nothing was sent). Kinds only."""

    def __init__(self, kinds: Iterable[str]) -> None:
        self.kinds = sorted(set(kinds))
        super().__init__(STOPPED)


# ---------- the switch and the keys ----------


def mode() -> str:
    value = config.get(FLAG, "").strip().lower()
    return value if value in (OWNER, ALL) else OFF


@dataclass(frozen=True)
class Keys:
    signer: Signer
    seed: bytes  # seeds each run's vault (HMAC with the person and the chat or run)
    seal: bytes  # AES-256-GCM key for the person's own words


def _derive(ikm: bytes, label: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=label.encode()).derive(ikm)


@lru_cache(maxsize=4)
def _keys_from(raw: str) -> Keys:
    ikm = raw.encode()
    return Keys(
        Signer.from_seed(_derive(ikm, "atlas-pseudonym-sign/v1")),
        _derive(ikm, "atlas-pseudonym-seed/v1"),
        _derive(ikm, "atlas-pseudonym-seal/v1"),
    )


_WARNED: list[bool] = []


def keys() -> Keys | None:
    """The server's keys, or None when LIFEOS_PSEUDONYM_KEY is missing or too short (logged once, never its value)."""
    raw = config.get(KEY, "").strip()
    if len(raw) < KEY_MIN:
        if not _WARNED:
            _WARNED.append(True)
            log.warning("pseudonym: %s is not set, so receipts are off", KEY)
        return None
    return _keys_from(raw)


def set_up() -> bool:
    """Whether this server ever had the feature (its key is set, whatever the switch says now): receipts age out."""
    return len(config.get(KEY, "").strip()) >= KEY_MIN


def enabled_for(user: str | None) -> bool:
    """On for this user: `all`, or `owner` and this is OWNER_USER_ID, and the key is set. Never for no user."""
    if not user:
        return False
    m = mode()
    if m == OFF:
        return False
    if m == OWNER and (not (owner := config.get("OWNER_USER_ID", "").strip()) or user != owner):
        return False
    return keys() is not None


def public_key() -> dict[str, str] | None:
    """The receipts' public key (a JWK with its `kid`), for checking them anywhere; None without the key."""
    k = keys()
    if k is None:
        return None
    return {**public_jwk(k.signer.public_key), "kid": k.signer.kid}


# ---------- the policy ----------


def policy_for(kinds: Iterable[str] | None) -> Policy:
    """The SDK's policy for a Shield level's kinds (None: Shield is off for the person, secrets only). Secrets are
    handles at every level; contact details and names are faked only where the person's level hides them; everything
    else is kept. Never `tag` (its [TYPE_n] labels would look like Shield's)."""
    rules: dict[str, dict[str, str]] = {t: {"action": "handle"} for t in SECRETS}
    for kind in sorted(set(kinds or ()) & set(_FAKES)):
        rules.update({t: {"action": "fake"} for t in _FAKES[kind]})
    fakes = "+".join(sorted(t for t, r in rules.items() if r["action"] == "fake")) or "secrets"
    return Policy.from_dict(
        {"type": "policy", "id": f"atlas:{fakes}", "description": "Atlas Shield's level, second layer",
         "rules": rules, "default": {"action": "keep"}, "allow": []}
    )  # fmt: skip


# ---------- one turn's boundary ----------


@dataclass(frozen=True)
class Receipt:
    """One signed egress receipt as it is stored (never a value: the token, its digests and counts; the turn's first
    one also `seen` and the person's words sealed)."""

    turn: str
    seq: int
    at: int
    model: str
    token: str
    payload: str
    stand_ins: Mapping[str, int]
    seen: str | None = None
    real_sealed: str | None = None
    marks: tuple[
        tuple[int, int, str], ...
    ] = ()  # where `seen` holds a stand-in: (start, end, type), never a value

    def to_dict(self) -> dict[str, Any]:
        """As it rides on a ledger payload (JSON: a booking the ledger refused is kept on the run's checkpoint)."""
        return {"turn": self.turn, "seq": self.seq, "at": self.at, "model": self.model, "token": self.token,
                "payload": self.payload, "stand_ins": dict(self.stand_ins), "seen": self.seen,
                "real_sealed": self.real_sealed, "marks": [list(m) for m in self.marks]}  # fmt: skip

    @classmethod
    def of(cls, value: Receipt | Mapping[str, Any]) -> Receipt:
        if isinstance(value, Receipt):
            return value
        marks = tuple((int(a), int(b), str(k)) for a, b, k in value.get("marks") or ())
        return cls(str(value["turn"]), int(value["seq"]), int(value["at"]), str(value["model"]), str(value["token"]),
                   str(value["payload"]), dict(value.get("stand_ins") or {}), value.get("seen"),
                   value.get("real_sealed"), marks)  # fmt: skip


@dataclass
class _Texts:
    """The strings of a request the check reads: `input` (where Shield's values may be) and the instructions when this
    boundary masked them."""

    input: list[str] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)


class Boundary:
    """One turn (a chat turn, an agent run, one worker call): its SDK session, its receipts' chain, and the stand-ins
    it put in, to take back out of the answer. `scope` names the chat or run, so the same one gets the same fakes at
    every step and after a resume; `allow`: what the person keeps visible to the AI (Shield's always-visible list and
    their own names), never faked."""

    def __init__(
        self,
        owner: str,
        *,
        keys: Keys,
        kinds: Iterable[str] | None = None,
        allow: Iterable[str] = (),
        scope: str | None = None,
        clock: Any = time.time,
    ) -> None:
        self.owner, self.turn = owner, str(uuid.uuid4())
        self._keys, self._clock = keys, clock
        seed = hmac.new(keys.seed, f"{owner}\n{scope or self.turn}".encode(), hashlib.sha256).digest()
        self.session = Session(
            policy_for(kinds), Vault(seed=seed), allow=[a for a in allow if a],
            session_id=f"{owner}/{self.turn}", clock=clock,
        )  # fmt: skip
        self._seq, self._prev = 0, None
        self._pending: list[Receipt] = []
        self._seen = False
        self._cache: dict[str, str] = {}  # text → masked, within this turn (each step resends the whole run)
        self._clean: dict[str, int] = {}  # masked text → the vault version the check found nothing in it at
        self._ids: dict[
            str, tuple[int, frozenset[str]]
        ] = {}  # masked text → (vault version, entries it shows)

    # ----- out -----

    def outgoing(
        self,
        body: Mapping[str, Any],
        *,
        shield_vault: shield.Vault | None = None,
        real: str | None = None,
        notes: bool = True,
    ) -> dict[str, Any]:
        """The request as it may leave (a new dict; `body` is never changed), checked and receipted: `input`, the
        `instructions` (Atlas's own fixed words come out the same; a plan, an agent's own words or rules don't) and the
        descriptions in `tools` and the answer's shape (`text`) are masked and read by the check. `real`: the person's
        words in the turn's last user item, before any masking (the first call that carries it records "What the AI
        saw"); `notes`: the model's note on handles (never for embeddings: their input is only the texts). Raises
        Stopped when the check finds a value that should have been hidden, or when masking or the check itself fails
        (fail closed: nothing is sent, no retry)."""
        try:
            return self._outgoing(body, shield_vault, real, notes)
        except Stopped:
            raise
        except Exception as e:  # noqa: BLE001 - class only; a request nobody could check never leaves
            log.warning("pseudonym: request not checked (%s)", type(e).__name__)
            raise Stopped(["CHECK"]) from None

    def _outgoing(
        self, body: Mapping[str, Any], shield_vault: shield.Vault | None, real: str | None, notes: bool
    ) -> dict[str, Any]:
        # what the sender sends, bar `stream` and `store`: the receipt's digest is of exactly this
        out = llm._fitted(dict(body))
        for attempt in range(2):
            texts = _Texts()
            out["input"] = self._mask_input(body.get("input"), texts)
            if isinstance(body.get("instructions"), str):
                out["instructions"] = self._mask_text(body["instructions"])
                texts.instructions.append(out["instructions"])
            for key in ("tools", "text"):
                if key in body:
                    out[key] = self._mask_descriptions(body[key], texts)
            note = self._notes(texts) if notes else ""
            if note and isinstance(out["input"], list):
                out["input"] = [*out["input"], {"role": "developer", "content": note}]
            elif note and isinstance(out["input"], str):
                out["input"] = f"{out['input']}\n\n{note}"
            found = self._check(texts, shield_vault)
            if not found:
                break
            if attempt == 0:
                # a name learned late in the run may sit earlier without a cue: once more with everything learned
                self._cache.clear()
                continue
            log.warning("pseudonym: request stopped, %s still in it", ",".join(sorted(set(found))))
            raise Stopped(found)
        self._sign(out, texts, real)
        return out

    def _mask_text(self, text: str) -> str:
        if not text:
            return text
        got = self._cache.get(text)
        if got is None:
            got = self.session.mask(text).text
            self._cache[text] = got
        return got

    def _mask_json(self, raw: str) -> str:
        """A call's arguments or a tool's output: JSON value by value (the same bytes when nothing changed), else as
        plain text."""
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return self._mask_text(raw)
        changed = [False]

        def walk(v: Any) -> Any:
            if isinstance(v, str):
                m = self._mask_text(v)
                changed[0] = changed[0] or m != v
                return m
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            return v

        out = walk(data)
        return json.dumps(out, default=str) if changed[0] else raw

    def _mask_input(self, value: Any, texts: _Texts) -> Any:
        if isinstance(value, str):
            masked = self._mask_text(value)
            texts.input.append(masked)
            return masked
        if not isinstance(value, list):
            return value
        out: list[Any] = []
        for item in value:
            if isinstance(item, str):  # an embeddings request: the texts themselves
                out.append(self._mask_text(item))
                texts.input.append(out[-1])
                continue
            if not isinstance(item, dict):
                out.append(item)
                continue
            it = dict(item)
            kind = it.get("type")
            if kind == "function_call":
                it["arguments"] = self._mask_json(str(it.get("arguments") or "{}"))
                texts.input.append(it["arguments"])
            elif kind == "function_call_output":
                it["output"] = self._mask_json(str(it.get("output") or ""))
                texts.input.append(it["output"])
            elif isinstance(it.get("content"), str):
                it["content"] = self._mask_text(it["content"])
                texts.input.append(it["content"])
            elif isinstance(it.get("content"), list):
                parts = []
                for p in it["content"]:
                    if isinstance(p, dict) and isinstance(p.get("text"), str):
                        p = {**p, "text": self._mask_text(p["text"])}
                        texts.input.append(p["text"])
                    parts.append(p)
                it["content"] = parts
            out.append(it)
        return out

    def _mask_descriptions(self, value: Any, texts: _Texts) -> Any:
        """Tool definitions or an answer's shape: each `description` and `title` text masked (an agent's own words can
        sit there), names and structure as they are. The same objects when nothing changed."""
        if isinstance(value, list):
            out = [self._mask_descriptions(v, texts) for v in value]
            return value if all(a is b for a, b in zip(out, value, strict=True)) else out
        if not isinstance(value, dict):
            return value
        got: dict[str, Any] = {}
        for k, v in value.items():
            if k in ("description", "title") and isinstance(v, str):
                got[k] = self._mask_text(v)
                texts.instructions.append(got[k])
            else:
                got[k] = self._mask_descriptions(v, texts)
        return value if all(got[k] is value[k] for k in value) else got

    def _notes(self, texts: _Texts) -> str:
        """The model's note for the handles this request holds (their kind and last digits, never a value)."""
        lines: list[str] = []
        held = {m.group(0) for t in [*texts.input, *texts.instructions] for m in _HANDLE.finditer(t)}
        for e in self.session.vault.entries:
            if e.action == "handle" and e.stand_in in held:
                lines.append(f"{e.stand_in} is {e.meta.get('description') or 'a secret'}.")
        return "\n".join([HANDLE_NOTE, *lines]) if lines else ""

    def _check(self, texts: _Texts, shield_vault: shield.Vault | None) -> list[str]:
        """What is still in the request that should not be, by type: a value the SDK's vault knows, a fresh find of
        the SDK's detectors, a value Shield hid this run (Shield's kind). Empty when the request may go."""
        # open-pseudonym 0.1.1 learns a name's parts from an email it fakes ("dana" of dana.reyes@...) even where the
        # policy keeps names, then counts that kept name as a leak ("tell Dana hi" at the Contacts level): a part is a
        # leak here only when names are hidden
        s = self.session
        parts = s.policy.rule_for("PERSON").action != "keep"
        found: list[str] = []
        version = s.vault.version
        for text in dict.fromkeys([*texts.input, *texts.instructions]):
            if self._clean.get(text) == version:  # read already, and nothing was learned since
                continue
            got = [
                lk.type
                for lk in opn_egress.check(s.vault, text, s.restorer())
                if parts or lk.entry_id != "part"
            ]
            got += [sp.type for sp in s.detect(text)
                    if s.policy.rule_for(sp.type).action != "keep" and not s.vault.is_stand_in(sp.text)]  # fmt: skip
            if got:
                found += got
            else:
                self._clean[text] = version
        if shield_vault is not None:
            found += _shield_left(texts.input, shield_vault)
        return found

    def _sign(self, body: Mapping[str, Any], texts: _Texts, real: str | None) -> None:
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        joined = "\n".join([*texts.input, *texts.instructions])
        counts: dict[str, int] = {}
        for eid in set().union(
            *[self._entries_in(t) for t in dict.fromkeys([*texts.input, *texts.instructions])]
        ):
            e = self.session.vault.by_id(eid)
            kind = e.type if e is not None else "PERSON"
            counts[kind] = counts.get(kind, 0) + 1
        for kind, n in shield_counts(joined).items():
            counts[kind] = counts.get(kind, 0) + n
        self._seq += 1
        now = int(self._clock())
        doc = {
            "opn": "0.1", "type": "egress", "session": self.session.id, "seq": self._seq, "iat": now,
            "prev": self._prev, "dest": f"openai/{body.get('model') or ''}", "payload": digest(payload.encode()),
            "policy": self.session.policy.digest, "stand_ins": dict(sorted(counts.items())),
            "check": {"known_values": sum(1 for e in self.session.vault.entries if e.action != "keep"), "found": 0},
            "detectors": [*self.session.detector_names(), SHIELD_DETECTOR],
        }  # fmt: skip
        token = self._keys.signer.sign(doc, RECEIPT_TYP)
        self._prev = digest(token.encode("ascii"))
        seen = sealed = None
        marks: tuple[tuple[int, int, str], ...] = ()
        if real is not None and not self._seen:
            masked = last_user_text(body.get("input"))
            if masked is not None:
                self._seen = True
                seen, sealed = masked[:SEEN_MAX], seal(self._keys, self.owner, self.turn, real[:SEEN_MAX])
                marks = self._marks(seen)
        self._pending.append(
            Receipt(self.turn, self._seq, now, str(body.get("model") or ""), token, doc["payload"],
                    doc["stand_ins"], seen, sealed, marks)
        )  # fmt: skip

    def _entries_in(self, text: str) -> frozenset[str]:
        """The SDK's entries whose stand-ins `text` holds (cached per text and vault version)."""
        version = self.session.vault.version
        hit = self._ids.get(text)
        if hit is None or hit[0] != version:
            hit = (
                version,
                frozenset(m.entry_id for m in self.session.restorer().matches(text) if m.entry_id),
            )
            self._ids[text] = hit
        return hit[1]

    def _marks(self, text: str) -> tuple[tuple[int, int, str], ...]:
        """Where `text` holds a stand-in of either layer, by the SDK's type name, in order (at most MARKS_MAX)."""
        out: list[tuple[int, int, str]] = []
        for m in self.session.restorer().matches(text):
            e = self.session.vault.by_id(m.entry_id) if m.entry_id else None
            out.append((m.start, m.end, e.type if e is not None else "PERSON"))
        out += [
            (m.start(), m.end(), TYPE_OF.get(m.group(1), m.group(1))) for m in shield._TOKEN.finditer(text)
        ]
        return tuple(sorted(set(out)))[:MARKS_MAX]

    def take(self) -> list[dict[str, Any]]:
        """The receipts signed since the last take, as the next ledger payload carries them (Receipt.to_dict)."""
        got, self._pending = self._pending, []
        return [r.to_dict() for r in got]

    # ----- back -----

    def _active(self) -> bool:
        return bool(self.session.vault.entries)

    def restore_text(self, text: str) -> str:
        """Words: every fake back except inside a link (one tap would send the real value to the link's site, P2-309's
        rule); a handle as its short label, never its value."""
        return self._put_back(text)[0] if text and self._active() else text

    def _put_back(
        self,
        text: str,
        start: int = 0,
        end: int | None = None,
        *,
        keep: bool = False,
        final: bool = True,
        long_first: bool = False,
    ) -> tuple[str, int]:
        """`text[start:end]` with the stand-ins put back, none inside a link; `keep`: handles stay as they are (a tool
        call), else each reads as its label; `long_first`: the text begins inside a word longer than shield.WORD_MAX (a
        streamed window), which counts as a link. The end moves back so a stand-in is never cut in half; (words, end)."""
        s = self.session
        stop = len(text) if end is None else end
        reveal = (lambda eid: e.stand_in if (e := s.vault.by_id(eid)) is not None else "") if keep else None
        links = link_spans(text, final=final)
        if long_first and (first := word_spans(text, final=final)):  # the text begins inside a cut word
            links.append(first[0])
        parts, at = [], start
        for m in s.restorer().matches(text, 0, reveal):
            if m.end <= start:
                continue
            if m.start < stop < m.end:
                stop = max(start, m.start)
            if m.start >= stop:
                break
            if m.start < at or any(a < m.end and m.start < b for a, b in links):
                continue
            parts += [text[at : m.start], m.replacement]
            at = m.end
        parts.append(text[at:stop])
        return "".join(parts), stop

    def restore_args(self, raw: Any) -> Any:
        """A tool call's arguments: fakes back except inside a link, handles kept (never filled into an action). JSON
        stays JSON (the same bytes when nothing changed)."""
        if not self._active() or not isinstance(raw, str):
            return raw
        try:
            data = json.loads(raw)
        except ValueError:
            return self._restore_outside_links(raw)

        def walk(v: Any) -> Any:
            if isinstance(v, str):
                return self._restore_outside_links(v)
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            return v

        back = walk(data)
        return raw if back == data else json.dumps(back, ensure_ascii=False)

    def _restore_outside_links(self, text: str) -> str:
        return self._put_back(text, keep=True)[0]

    def restore_response(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """A whole Responses API answer with its message words and calls' arguments put back (a copy)."""
        out = dict(data)
        if not self._active():
            return out
        if isinstance(out.get("output"), list):
            out["output"] = [self._restore_item(i) for i in out["output"]]
        if isinstance(out.get("output_text"), str):
            out["output_text"] = self.restore_text(out["output_text"])
        return out

    def _restore_item(self, item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        it = dict(item)
        if it.get("type") == "message" and isinstance(it.get("content"), list):
            it["content"] = [
                {**p, "text": self.restore_text(p["text"])} if isinstance(p, dict) and isinstance(p.get("text"), str)
                else p for p in it["content"]
            ]  # fmt: skip
        elif it.get("type") == "function_call" and isinstance(it.get("arguments"), str):
            it["arguments"] = self.restore_args(it["arguments"])
        return it

    async def restore_events(self, events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """A streamed answer's events with the stand-ins put back: words per output item and content part (a stand-in
        cut between pieces is held until whole, and what is held comes out before that part's `done`), each finished
        item and the final response whole. Argument pieces are dropped (Runtime B reads a call's arguments from its
        finished item); everything else passes as it is."""
        streams: dict[tuple[Any, Any], Any] = {}
        async for event in events:
            if not self._active():
                yield event
                continue
            kind = event.get("type")
            key = (event.get("item_id"), event.get("content_index"))
            if kind == "response.output_text.delta":
                st = streams.get(key)
                if st is None:
                    st = streams[key] = _Words(self)
                words = st.feed(str(event.get("delta") or ""))
                if words:
                    yield {**event, "delta": words}
            elif kind == "response.output_text.done":
                st = streams.pop(key, None)
                rest = st.flush() if st is not None else ""
                if rest:
                    yield {k: v for k, v in event.items() if k != "text"} | {"type": "response.output_text.delta",
                                                                              "delta": rest}  # fmt: skip
                yield {**event, "text": self.restore_text(str(event.get("text") or ""))}
            elif kind == "response.function_call_arguments.delta":
                continue
            elif kind == "response.function_call_arguments.done":
                yield {**event, "arguments": self.restore_args(event.get("arguments"))}
            elif isinstance(event.get("response"), dict):
                # the final response: words still held (a part that never said `done`) go out first, once
                for (item_id, index), st in streams.items():
                    rest = st.flush()
                    if rest:
                        yield {"type": "response.output_text.delta", "item_id": item_id, "content_index": index,
                               "delta": rest}  # fmt: skip
                streams.clear()
                yield {**event, "response": self.restore_response(event["response"])}
            elif isinstance(event.get("item"), dict):
                yield {**event, "item": self._restore_item(event["item"])}
            else:
                yield event


class _Words:
    """One streamed part's words with the stand-ins put back. The last word (Shield's word: spaces next to an em dash
    join two words, as house style drops the dash with them, and spaces at the very end may still join) and a stand-in
    that may still be growing are held until whole; flush lets out the rest. Each piece reads only from the start of
    the word that is still open, so the cost of a long answer grows with its length, not its square. Its pieces joined
    equal the whole part restored."""

    def __init__(self, b: Boundary) -> None:
        self._b, self._text, self._done = b, "", 0

    def feed(self, piece: str) -> str:
        self._text += piece
        base, long = word_start(self._text, self._done)
        window = self._text[base:]
        spans = word_spans(window, final=False)
        cut = min(
            len(window) - (self._b.session.restorer().longest + 1), spans[-1][0] if spans else len(window)
        )
        if base + cut <= self._done:
            return ""
        words, end = self._b._put_back(window, self._done - base, cut, final=False, long_first=long)
        self._done = base + end
        return words

    def flush(self) -> str:
        base, long = word_start(self._text, self._done)
        words, end = self._b._put_back(self._text[base:], self._done - base, long_first=long)
        self._done = base + end
        return words


def _joins(text: str, a: int, b: int, final: bool) -> bool:
    """Whether the spaces text[a:b] join the words on each side (shield._after): only spaces and tabs, next to an em
    dash, or at the very end while more may come."""
    if any(c not in " \t" for c in text[a:b]):
        return False
    after = text[b] if b < len(text) else None
    return (a > 0 and text[a - 1] == "\u2014") or after == "\u2014" or (after is None and not final)


def word_spans(text: str, *, final: bool = True) -> list[tuple[int, int]]:
    """Shield's words in `text` (lifeos.shield._after): runs of anything but its spaces, where spaces next to an em
    dash, or (while more may come) at the end, join rather than part."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SPACES.finditer(text):
        if _joins(text, m.start(), m.end(), final):
            continue
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def word_start(text: str, at: int) -> tuple[int, bool]:
    """Where the word holding position `at` begins (after the last spaces before it that part words), and whether it
    is longer than shield.WORD_MAX: the look back stops there, and such a word counts as a link, as Shield's cut word
    does."""
    i = min(at, len(text))
    stop = max(0, i - shield.WORD_MAX)
    while i > stop:
        j = i
        while j > stop and text[j - 1] not in shield._SPACE:
            j -= 1
        k = j
        while k > stop and text[k - 1] in shield._SPACE:
            k -= 1
        if k == j:  # no spaces before it in reach
            return stop, stop > 0
        if not _joins(text, k, j, True):
            return j, False
        i = k
    return stop, stop > 0


def link_spans(text: str, *, final: bool = True) -> list[tuple[int, int]]:
    """Where `text` holds a link: each of Shield's words (word_spans) that is or holds one, or that is longer than
    shield.WORD_MAX (Shield cuts such a word and shows nothing inside it)."""
    return [
        (a, b)
        for a, b in word_spans(text, final=final)
        if b - a > shield.WORD_MAX or _LINKISH.search(text, a, b)
    ]


def for_run(owner: str, guard: shield.Shield | None, *, scope: str | None = None) -> Boundary | None:
    """This person's boundary for one turn, or None while the switch is off for them (then nothing here runs)."""
    if not enabled_for(owner):
        return None
    k = keys()
    if k is None:
        return None
    allow = sorted(guard.visible.whole) if guard is not None else []
    return Boundary(owner, keys=k, kinds=guard.kinds if guard is not None else None, allow=allow, scope=scope)


# ---------- the two layers in one vocabulary ----------


def shield_counts(text: str) -> dict[str, int]:
    """Distinct Shield labels in `text` by the SDK's type name ([EMAIL_1] twice counts once)."""
    seen = {m.group(0): m.group(1) for m in shield._TOKEN.finditer(text)}
    out: dict[str, int] = {}
    for kind in seen.values():
        t = TYPE_OF.get(kind, kind)
        out[t] = out.get(t, 0) + 1
    return out


def _shield_left(texts: Sequence[str], vault: shield.Vault) -> list[str]:
    """Shield's kinds of any value it hid this run that is still in these texts, as Shield finds a repeat (whole
    words, 3 characters or more, as written): Shield's own rule, so a value it hides is never a false alarm here."""
    known = {v: k for v, k in vault.seen.items() if len(v) >= 3}
    if not known:
        return []
    alts = "|".join(re.escape(v) for v in sorted(known, key=len, reverse=True))
    pattern = re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])")
    return [known[m.group(0)] for t in texts for m in pattern.finditer(t)]


def last_user_text(value: Any) -> str | None:
    """The words of the last user item in a request's input (its text parts joined), or the input itself as text."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    for item in reversed(value):
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(str(p.get("text")) for p in content if isinstance(p, dict) and p.get("text"))
    return None


# ---------- sealing the person's own words ----------


def seal(k: Keys, owner: str, turn: str, text: str) -> str:
    """`text` under AES-256-GCM, bound to the person and the turn (base64url of nonce + ciphertext)."""
    nonce = os.urandom(12)
    ct = AESGCM(k.seal).encrypt(nonce, text.encode("utf-8"), f"{owner}/{turn}".encode())
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii").rstrip("=")


def unseal(k: Keys, owner: str, turn: str, sealed: str) -> str | None:
    """The words back, or None when they can't be opened (another key, another person or turn, changed bytes)."""
    try:
        raw = base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4))
        return AESGCM(k.seal).decrypt(raw[:12], raw[12:], f"{owner}/{turn}".encode()).decode("utf-8")
    except Exception:  # noqa: BLE001 - class only; a row that won't open shows as "can't be shown"
        return None


# ---------- storing and reading (as the person, RLS) ----------

_COLUMNS = "owner_id, turn, seq, at, job, model, run_id, conversation_id, payload, stand_ins, token, seen, real_sealed, marks"


def store(
    conn: Connection,
    owner: str,
    receipts: Sequence[Receipt | Mapping[str, Any]],
    *,
    job: str,
    run_id: str | None = None,
    conversation_id: str | None = None,
) -> int:
    """Book receipts beside their ledger row, in a savepoint: a database without the table (or any other error) costs
    these receipts only, logged by class. A receipt booked once is never booked twice. Returns how many went in."""
    if not receipts:
        return 0
    try:
        with conn.transaction():
            n = 0
            for r in map(Receipt.of, receipts):
                n += conn.execute(
                    f"insert into public.pseudonym_receipts ({_COLUMNS}) values (%s, %s, %s, to_timestamp(%s), %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (owner_id, turn, seq) do nothing",
                    (owner, r.turn, r.seq, r.at, job[:60] or "chat", r.model[:100] or "unknown", run_id,
                     conversation_id, r.payload, Jsonb(dict(r.stand_ins)), r.token, r.seen, r.real_sealed,
                     None if r.seen is None else Jsonb([list(m) for m in r.marks])),
                ).rowcount  # fmt: skip
            return n
    except psycopg.Error as e:
        log.warning("pseudonym: receipts not booked (%s)", type(e).__name__)
        return 0


def prune(conn: Connection, owner: str, *, days: int) -> int:
    """Delete this person's receipts older than `days` (the nightly retention job, the traces' window)."""
    return conn.execute(
        "delete from public.pseudonym_receipts where owner_id = %s and at < now() - make_interval(days => %s)",
        (owner, max(1, int(days))),
    ).rowcount


# ---------- "What the AI saw" (lifeos.api.pseudonym) ----------

TURNS_MAX = 50


def words_hidden(seen: str | None, marks: Any) -> dict[str, int]:
    """What was hidden in the person's own words, by type: each distinct stand-in the marks point at, counted once
    (the same thing "What the AI saw" highlights; the requests' own counts also hold the chat so far)."""
    if not seen:
        return {}
    found: set[tuple[str, str]] = set()
    for mark in marks or []:
        try:
            a, b, kind = int(mark[0]), int(mark[1]), str(mark[2])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= a < b <= len(seen):
            found.add((seen[a:b], kind))
    out: dict[str, int] = {}
    for _, kind in found:
        out[kind] = out.get(kind, 0) + 1
    return dict(sorted(out.items()))


def turns(conn: Connection, owner: str, *, before: Any = None, limit: int = 20) -> list[dict[str, Any]]:
    """The person's recent turns that recorded what the AI saw, newest first: when, the job, the model, how many
    requests it sent, what was hidden in the person's words (`words`) and the stand-ins of its last request (each
    resends the run, so the last holds the most). No words, no tokens: a list is cheap to open."""
    rows = conn.execute(
        "select r.turn, r.at, r.job, r.model, r.seen, r.marks, "
        "(select count(*) from public.pseudonym_receipts c where c.owner_id = r.owner_id and c.turn = r.turn), "
        "(select c.stand_ins from public.pseudonym_receipts c where c.owner_id = r.owner_id and c.turn = r.turn "
        " order by c.seq desc limit 1) "
        "from public.pseudonym_receipts r where r.owner_id = %s and r.seen is not null "
        "and (%s::timestamptz is null or r.at < %s::timestamptz) order by r.at desc limit %s",
        (owner, before, before, max(1, min(int(limit), TURNS_MAX + 1))),
    ).fetchall()
    return [
        {
            "id": str(t),
            "at": at.isoformat(),
            "job": job,
            "model": model,
            "calls": int(n),
            "stand_ins": dict(s or {}),
            "words": words_hidden(seen, marks),
        }
        for t, at, job, model, seen, marks, n, s in rows
    ]


def check_turn(owner: str, turn: str, rows: Sequence[Mapping[str, Any]], public: Any) -> list[str | None]:
    """Each receipt of a turn checked (in seq order): None when it holds up, else a short reason. It holds up when its
    signature verifies with the server's public key, it names this person and turn, its place in the chain is right
    (seq 1, 2, 3... each `prev` the digest of the one before) and every column the app shows equals what was signed."""
    from open_pseudonym.jws import SignatureError, verify

    out: list[str | None] = []
    prev: str | None = None
    for i, r in enumerate(rows, start=1):
        token = str(r["token"])
        try:
            body = verify(token, public, RECEIPT_TYP)
        except SignatureError:
            out.append("signature")
        else:
            at = r["at"]
            if body.get("session") != f"{owner}/{turn}" or body.get("seq") != r["seq"] or r["seq"] != i:
                out.append("place")
            elif body.get("prev") != prev:
                out.append("chain")
            elif (
                body.get("payload") != r["payload"]
                or body.get("stand_ins") != dict(r["stand_ins"] or {})
                or body.get("dest") != f"openai/{r['model']}"
                or body.get("iat") != int(at.timestamp() if hasattr(at, "timestamp") else at)
                or (body.get("check") or {}).get("found") != 0
            ):
                out.append("changed")
            else:
                out.append(None)
        prev = digest(token.encode("ascii", errors="replace"))
    return out


def turn_view(conn: Connection, owner: str, turn: str) -> dict[str, Any] | None:
    """One turn for its owner: what the AI saw beside the person's own words (opened here, for them only), the
    stand-ins marked, and every request's receipt checked. None when there is no such turn of theirs."""
    rows = conn.execute(
        "select seq, at, job, model, payload, stand_ins, token, seen, real_sealed, marks from "
        "public.pseudonym_receipts where owner_id = %s and turn = %s order by seq",
        (owner, turn),
    ).fetchall()
    if not rows:
        return None
    cols = ("seq", "at", "job", "model", "payload", "stand_ins", "token", "seen", "real_sealed", "marks")
    recs = [dict(zip(cols, r, strict=True)) for r in rows]
    k = keys()
    first = next((r for r in recs if r["seen"] is not None), None)
    real = unseal(k, owner, turn, first["real_sealed"]) if k is not None and first is not None else None
    problems = check_turn(owner, turn, recs, k.signer.public_key) if k is not None else ["key"] * len(recs)
    calls = [
        {"seq": r["seq"], "at": r["at"].isoformat(), "model": r["model"], "payload": r["payload"],
         "stand_ins": dict(r["stand_ins"] or {}), "ok": why is None, "why": why, "token": r["token"]}
        for r, why in zip(recs, problems, strict=True)
    ]  # fmt: skip
    return {
        "id": turn, "at": recs[0]["at"].isoformat(), "job": recs[0]["job"],
        "seen": first["seen"] if first is not None else None, "real": real,
        "marks": list(first["marks"] or []) if first is not None else [],
        "words": words_hidden(first["seen"], first["marks"]) if first is not None else {},
        "stand_ins": dict(recs[-1]["stand_ins"] or {}), "ok": all(c["ok"] for c in calls), "calls": calls,
    }  # fmt: skip
