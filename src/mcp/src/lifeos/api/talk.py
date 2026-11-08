"""Talk mode's paid voices (phase 5): Atlas's voice reading an answer (L2) and live conversation (L3). The device's own
voice (L1) is the app's, at $0, and needs nothing here. Which levels a person has: the capability registry's
voice_out levels (api/capabilities.py).

POST /v1/voice/speak {message_id, whole?}: the spoken line (whole: Read aloud's, every paragraph and item) of one of the person's own answers (speech.spoken_line: only the
  answer's own words), read by Atlas's voice. 403 while LIFEOS_VOICE_OUT is off for them; 404 for an answer that
  isn't theirs or isn't finished; 422 when it has no words; 409 when Atlas Shield would hide something in it (the
  device's voice reads it: the provider never hears what Shield kept from the model); 402 at a wall (their minutes of Atlas's voice today,
  then the month, day and pool: nothing is called). Reserve (committed) → the call, outside any transaction → the
  ledger row and the settle in one short transaction (a failure: its row and the release). Answers
  {text, audio (base64), type, minutes_left_today}. The person's pick (Settings › Voice, speech.picked) decides the
  voice: natural reads through Gradium first while it may (speech.gradium), else OpenAI, which also reads it once
  when Gradium fails; clear and warm are OpenAI's own voices.
POST /v1/voice/sample {voice}: one line (speech.SAMPLE: the server's own words around the assistant's name) in a
  pick's voice, so Settings can let a person hear each before choosing. The same flag, wall and booking as a read; each voice is read once per
  process and kept, so trying voices again costs nothing. Answers {text, audio, type, voice}.
POST /v1/voice/live {sdp}: a live conversation. 403 while LIFEOS_REALTIME is off for them (the provider is never
  asked and nothing is held); calls nobody ended are settled at their most first (realtime.sweep); 409 while one is
  on; then the hold for its most minutes and the session record (committed), then the call (realtime.session_config:
  talking only, no tools) and a watcher in this process that hangs up at its most, when the app goes quiet or when
  its usage reaches the hold, and settles it. Answers {id, sdp (OpenAI's answer), max_seconds, model}.
POST /v1/voice/live/{id}/beat: the app's sign of life every 15 s → {seconds_left}; 410 once it has ended. A beat on a
  call past its most that no watcher in this process carries (its own was lost to a restart) books it at its most and
  hangs it up, then answers 410 (Y1).
POST /v1/voice/live/{id}/end: the app's Stop → the call hung up and booked once → {ended, minutes_left_today}.
At its start lifeos-api sweeps every person's calls left open (realtime.sweep_everyone, in the background); the app
worker does the same on its timer (lifeos.cli _live_sweep), so a call nobody ended is booked and hung up even when its
person never starts another (Y1, 2026-10-26).

Tier C is never reached by voice at any level: nothing here approves, signs or runs a tool; the live model has no
tools; approvals stay a card, a tap and lifeos-signer's HMAC (tests/test_talk.py plants the opposite)."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lifeos import budget, chatmodes, config, db, llm, persona, realtime, speech
from lifeos.api import store

log = logging.getLogger(__name__)

SPEAK_GROUP = "voice"  # one model call each: the voice notes' own bucket (ten, then one every 20 s)
BEAT_GROUP = "voice_limits"  # beats and Stop: small writes (60, then one a second)
SPEAK_HOLD_S = 120  # a read's hold frees itself after this if the process dies mid-call
TICK_S = 5.0  # the watcher looks at a live call this often
END_WAIT_S = 15.0
NOT_HERE = "That answer isn't here to read aloud."
NO_WORDS = "That answer has no words to read aloud."
# a speech model named without a good price (llm.tts_token_usd): refused before any hold or call
SPEECH_OFF = "Atlas's voice is off right now. Your device's voice can still read answers aloud."
SHIELDED = (
    "Atlas Shield keeps private details in this answer from the AI, so your device's voice reads it instead."
)
SPEAK_FAILED = "Atlas's voice didn't answer. Your device's voice can read it instead."
ALREADY = "A live conversation is already on. End it first."
LIVE_FAILED = "Live conversation couldn't start. Try again in a moment."
BAD_OFFER = "That live connection request isn't one Atlas can use. Try again."
LIVE_ENDED = "This live conversation has ended."


class SpeakIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: int = Field(ge=1, le=2**62)
    whole: bool = False  # Read aloud on an answer: its whole words (speech.spoken_line whole); talk mode: its first part


class SampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: Literal["natural", "clear", "warm"]  # Settings › Personalize › Read aloud's picks (speech.PICKS)


class LiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=10, max_length=realtime.SDP_MAX)


def _minutes(left: Decimal | None) -> float | None:
    return None if left is None else float(left.quantize(Decimal("0.01"), ROUND_FLOOR))


def _forget(user: str) -> None:
    """The person's + menu shows fresh minutes on its next read."""
    from lifeos.api import capabilities

    capabilities.REGISTRY.forget(user)


class Watch:
    """One live call this process watches: it hangs up at the session's most, when `quiet()` (seconds since the app's
    last beat; None once the session has ended elsewhere) passes realtime.QUIET_S, when the usage the sideband
    reports reaches the hold, when the sideband shows the session's tools or words changed by a client
    (realtime.changed, Y3), or when stop() is called (the app's Stop), then settles once."""

    def __init__(
        self,
        sid: str,
        user: str,
        call_id: str,
        *,
        max_s: int,
        hold_usd: Decimal,
        model: str,
        calls: realtime.RealtimeCalls,
        quiet: Callable[[], Awaitable[float | None]],
        settle: Callable[[str, realtime.Usage], Awaitable[Any]],
        tick: float | None = None,
    ) -> None:
        self.sid, self.user, self.call_id = sid, user, call_id
        self.max_s, self.hold_usd, self.model = max_s, hold_usd, model
        self.calls, self.quiet, self.settle_with = calls, quiet, settle
        self.tick = TICK_S if tick is None else tick
        self.usage = realtime.Usage()
        self.reason: str | None = None
        self.booked: Any = None
        self.blind = False  # the sideband couldn't be read: the per-minute meter alone
        self._stop = asyncio.Event()
        self.finished = asyncio.Event()

    def stop(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
        self._stop.set()

    async def _read(self) -> None:
        try:
            async for event in self.calls.watch(self.call_id):
                # a client's own session.update: no longer talking only, so hang up
                if realtime.changed(event):
                    log.warning("live call hung up: its session's tools or words were changed")
                    self.stop("changed")
                    return
                got = realtime.event_usage(self.model, event)  # usage only: no other event is ever acted on
                if got is not None:
                    self.usage.add(got)
                    if self.usage.usd >= self.hold_usd:
                        self.stop("spent")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the sideband is advisory: the per-minute meter and the hangup stay
            self.blind = True

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        reader = asyncio.create_task(self._read())
        settle = True
        try:
            while not self._stop.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), self.tick)
                if self._stop.is_set():
                    break
                if loop.time() - started >= self.max_s:
                    self.stop("max")
                    break
                quiet = await self.quiet()
                if quiet is None:  # ended elsewhere (another process's Stop, a sweep): booked there
                    settle = False
                    self.stop("ended")
                    break
                if quiet >= realtime.QUIET_S:
                    self.stop("quiet")
        finally:
            reader.cancel()
            with suppress(Exception):
                await self.calls.hangup(self.call_id)
            if settle:
                with suppress(Exception):
                    self.booked = await self.settle_with(self.reason or "ended", self.usage)
            WATCHING.pop(self.sid, None)
            self.finished.set()


WATCHING: dict[str, Watch] = {}  # live calls this process watches, by session id
_TASKS: set[asyncio.Task[None]] = set()  # strong references until each watcher ends


def _serving() -> bool:
    """True in lifeos-api itself, whose lifespan opened this process's database pool (lifeos.db.use_pool) before this
    router's; an app a test builds runs without one (create_app(pool=False)), so it never sweeps a real database."""
    return getattr(db, "_pool", None) is not None


async def _sweep_left() -> None:
    """Every person's live calls left open, swept once as this process starts (it watches none of them: whoever did
    is gone), booked at their most and hung up; in the background, so it never holds up the start."""
    try:
        hang = await asyncio.to_thread(realtime.sweep_everyone, store.run, lambda: frozenset(WATCHING))
        if hang:
            await realtime.hang_up(realtime.calls(), hang)
    except Exception as e:  # noqa: BLE001 - class only; the app worker's timer sweeps them too
        log.warning("live calls left open not swept at start (%s)", type(e).__name__)


async def _at_start(app: Any) -> AsyncIterator[None]:
    if _serving():
        task = asyncio.create_task(_sweep_left())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    yield


SAMPLES_MAX = 64  # samples kept per process (each about 150 KB): the oldest goes first
_SAMPLES: dict[tuple[str, str, str], speech.Spoken] = {}  # (provider, voice, line) → its sample, read once


async def say(
    user: str, line: str, first: speech.SpeechOut | None, openai_voice: str | None
) -> tuple[speech.Spoken, speech.SpeechOut, Decimal | None]:
    """Read `line` for `user`: the price known first, the hold (committed), then the call outside any transaction
    (Gradium first when `first` is it; if it fails, its failed row and OpenAI once, in the same hold), then the ledger
    row and the settle in one short transaction. Returns the audio, the voice that read it and the minutes left today.
    A refusal or a failure is an HTTPException (402 at a wall, 503 with no price, 502 when nothing could read it)."""
    try:
        llm.tts_token_usd()  # the model's price is known before anything is held or called (never booked wrong)
    except config.ConfigError:
        raise HTTPException(503, SPEECH_OFF) from None
    usd = speech.estimate_usd(line)
    now = datetime.now(UTC)
    hold = await asyncio.to_thread(
        store.run,
        user,
        lambda conn: budget.reserve_talk(
            conn, user, budget.TALK_ATLAS, want_usd=usd, least_usd=usd, hold_s=SPEAK_HOLD_S, now=now
        ),
    )  # BudgetExceeded → 402, nothing held, nothing called
    voice: speech.SpeechOut = first or speech.provider(openai_voice)
    t = time.monotonic()
    try:
        try:
            spoken = await asyncio.to_thread(voice.speak, line)
        except llm.LLMError as e:  # Unusable too (a Gradium 200 with nothing to play costs nothing)
            if first is None:
                raise
            # Gradium couldn't read it: its failed row (no cost), then OpenAI once, in the same hold
            ms, err = speech.elapsed_ms(t), e
            await asyncio.to_thread(store.run, user, lambda conn: speech.failed(conn, user, first, err, ms))
            voice, t = speech.provider(openai_voice), time.monotonic()
            spoken = await asyncio.to_thread(voice.speak, line)
    except speech.Unusable as e:  # Y5: a billed 200 with nothing to play is booked as paid, never $0
        ms, seen = speech.elapsed_ms(t), e.spoken

        def paid(conn: Any) -> None:
            speech.book(conn, user, line, voice, seen, ms)  # its usage, else the line's estimate
            budget.settle(conn, user, hold)  # the ledger row and the settle commit together

        await asyncio.to_thread(store.run, user, paid)
        raise HTTPException(502, SPEAK_FAILED) from None
    except llm.LLMError as e:
        ms, err = speech.elapsed_ms(t), e

        def release(conn: Any) -> None:
            speech.failed(conn, user, voice, err, ms)
            budget.settle(conn, user, hold)

        await asyncio.to_thread(store.run, user, release)
        raise HTTPException(502, SPEAK_FAILED) from None
    ms = speech.elapsed_ms(t)

    def book(conn: Any) -> Decimal | None:
        speech.book(conn, user, line, voice, spoken, ms)
        budget.settle(conn, user, hold)  # the ledger row and the settle commit together
        return budget.talk_minutes_left(conn, user, budget.TALK_ATLAS)

    left = await asyncio.to_thread(store.run, user, book)
    return spoken, voice, left


def router(limited: Callable[[str], Any]) -> APIRouter:
    r = APIRouter(lifespan=_at_start)  # FastAPI runs it inside the app's own lifespan (after the pool opens)

    @r.post("/v1/voice/speak")
    async def speak(body: SpeakIn, user: str = Depends(limited(SPEAK_GROUP))) -> dict[str, Any]:
        if not chatmodes.flag_on("voice_out", user):
            raise HTTPException(403, chatmodes.NOT_YET["voice_out"])

        def answer(conn: Any) -> tuple[str, bool, speech.SpeechOut | None, str | None]:
            row = conn.execute(
                "select role, text, status from public.messages where id = %s and owner_id = %s",
                (body.message_id, user),
            ).fetchone()
            if row is None or row[0] != "assistant" or row[2] == "streaming":
                raise HTTPException(404, NOT_HERE)
            line = speech.spoken_line(str(row[1] or ""), whole=body.whole)
            kept = bool(line) and speech.shielded(conn, user, line)
            if not line or kept:
                return line, kept, None, None
            # the person's pick (Settings › Voice): natural = Gradium first while it may read this line for them
            # (lifeos.speech.gradium), else OpenAI; clear and warm = OpenAI's own voices
            pick = speech.picked(conn, user)
            first = speech.gradium(conn, user, line) if pick == "natural" else None
            return line, kept, first, speech.PICKS[pick]

        line, kept, first, openai_voice = await asyncio.to_thread(store.run, user, answer)
        if not line:
            raise HTTPException(422, NO_WORDS)
        if kept:  # Atlas Shield: the provider never hears what Shield kept from the model
            raise HTTPException(409, SHIELDED)
        spoken, _, left = await say(user, line, first, openai_voice)
        _forget(user)
        return {
            "text": line,
            "audio": base64.b64encode(spoken.audio).decode(),
            "type": spoken.mime,
            "minutes_left_today": _minutes(left),
        }

    @r.post("/v1/voice/sample")
    async def sample(body: SampleIn, user: str = Depends(limited(SPEAK_GROUP))) -> dict[str, Any]:
        if not chatmodes.flag_on("voice_out", user):
            raise HTTPException(403, chatmodes.NOT_YET["voice_out"])

        def pick(conn: Any) -> tuple[str, speech.SpeechOut | None]:
            # the voice says the assistant's own name; one Atlas Shield would hide falls back to the default's
            line = speech.sample_line(persona.assistant_name(conn, user))
            if speech.shielded(conn, user, line):
                line = speech.sample_line(persona.DEFAULT_NAME)
            return line, speech.gradium(conn, user, line) if body.voice == "natural" else None

        line, first = await asyncio.to_thread(store.run, user, pick)
        openai_voice = speech.PICKS[body.voice]
        who = (
            ("gradium", first.voice)
            if isinstance(first, speech.GradiumSpeech)
            else ("openai", speech.openai_name(openai_voice))
        )
        key = (*who, line)
        got = _SAMPLES.get(key)
        if (
            got is None
        ):  # read once per voice and line per process, then kept: trying voices again costs nothing
            got, used, _ = await say(user, line, first, openai_voice)
            if (
                used is first or first is None
            ):  # a Gradium sample that fell back to OpenAI isn't kept as Gradium's
                if len(_SAMPLES) >= SAMPLES_MAX:
                    _SAMPLES.pop(next(iter(_SAMPLES), key), None)  # the oldest goes: the cache stays small
                _SAMPLES[key] = got
        return {
            "text": line,
            "audio": base64.b64encode(got.audio).decode(),
            "type": got.mime,
            "voice": body.voice,
        }

    @r.post("/v1/voice/live")
    async def live(body: LiveIn, user: str = Depends(limited(SPEAK_GROUP))) -> dict[str, Any]:
        if not chatmodes.flag_on("realtime", user):  # a flagged-off person never reaches the provider
            raise HTTPException(403, chatmodes.NOT_YET["realtime"])
        if not body.sdp.startswith("v=0"):
            raise HTTPException(422, BAD_OFFER)
        calls = realtime.calls()
        watched = frozenset(WATCHING)
        hang = await asyncio.to_thread(store.run, user, lambda conn: realtime.sweep(conn, user, watched))
        for call_id in hang:
            with suppress(Exception):
                await calls.hangup(call_id)
        model_name = realtime.model()
        rate = budget.TALK_LIVE.usd_per_min
        now = datetime.now(UTC)

        def begin(conn: Any) -> tuple[budget.Hold, realtime.Session]:
            if realtime.open_one(conn, user):
                raise HTTPException(409, ALREADY)
            hold = budget.reserve_talk(conn, user, budget.TALK_LIVE, want_usd=rate * realtime.MAX_MIN,
                                       least_usd=rate, hold_s=realtime.MAX_MIN * 60 + realtime.GRACE_S,
                                       now=now)  # fmt: skip
            return hold, realtime.open_session(conn, user, hold, model_name)

        hold, sess = await asyncio.to_thread(store.run, user, begin)  # BudgetExceeded → 402, nothing called
        try:
            answer, call_id = await calls.connect(body.sdp, realtime.session_config(model_name))
        except llm.LLMError:
            await asyncio.to_thread(
                store.run, user, lambda conn: realtime.settle(conn, user, sess.id, "failed")
            )  # the record closed, a failed row booked at nothing, the hold released
            raise HTTPException(502, LIVE_FAILED) from None
        await asyncio.to_thread(
            store.run, user, lambda conn: realtime.mark_call(conn, user, sess.id, call_id)
        )

        async def quiet() -> float | None:
            return await asyncio.to_thread(
                store.run, user, lambda conn: realtime.quiet_s(conn, user, sess.id)
            )

        async def settle(reason: str, usage: realtime.Usage) -> Any:
            got = await asyncio.to_thread(
                store.run, user, lambda conn: realtime.settle(conn, user, sess.id, reason, usage)
            )
            _forget(user)
            return got

        watch = Watch(sess.id, user, call_id, max_s=sess.max_s, hold_usd=hold.usd, model=model_name, calls=calls,
                      quiet=quiet, settle=settle)  # fmt: skip
        WATCHING[sess.id] = watch
        task = asyncio.create_task(watch.run())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return {"id": sess.id, "sdp": answer, "max_seconds": sess.max_s, "model": model_name}

    @r.post("/v1/voice/live/{sid}/beat")
    async def live_beat(sid: uuid.UUID, user: str = Depends(limited(BEAT_GROUP))) -> dict[str, Any]:
        key = str(sid)
        left = await asyncio.to_thread(store.run, user, lambda conn: realtime.beat(conn, user, key))
        if left is None:
            raise HTTPException(410, LIVE_ENDED)
        # past its most and nobody here watches it (a restart lost its watcher): booked at its most, hung up
        if left <= 0 and key not in WATCHING:
            call_id = await asyncio.to_thread(
                store.run, user, lambda conn: realtime.end(conn, user, key, "max")
            )
            if call_id:
                await realtime.hang_up(realtime.calls(), [call_id])
            _forget(user)
            raise HTTPException(410, LIVE_ENDED)
        return {"seconds_left": left}

    @r.post("/v1/voice/live/{sid}/end")
    async def live_end(sid: uuid.UUID, user: str = Depends(limited(BEAT_GROUP))) -> dict[str, Any]:
        key = str(sid)
        watch = WATCHING.get(key)
        if watch is not None and watch.user == user:
            watch.stop("ended")
            with suppress(TimeoutError):
                await asyncio.wait_for(watch.finished.wait(), END_WAIT_S)
        else:  # no watcher here (another process, or a restart): book what it ran, then hang up
            call_id = await asyncio.to_thread(store.run, user, lambda conn: realtime.end(conn, user, key))
            if call_id:
                with suppress(Exception):
                    await realtime.calls().hangup(call_id)
        left = await asyncio.to_thread(
            store.run, user, lambda conn: budget.talk_minutes_left(conn, user, budget.TALK_LIVE)
        )
        _forget(user)
        return {"ended": True, "minutes_left_today": _minutes(left)}

    return r
