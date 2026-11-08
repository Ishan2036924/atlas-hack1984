"""Agent mode's routes (AM-5 the start; AM-6 the read model and the controls).

POST /v1/agent-mode/runs {text}   a new request: 1 to 2,000 characters of the person's own words.
    201 {"id"}                    queued; the agents' worker pool is woken, so it starts within a second or two
    409 {"code": "busy", "id"}    a run is already going (one at a time per person): the app opens that one
    402 {"detail"}                the person's AI allowance, or today's run cap, is used up (the words /v1/chat shows)
    404 {"code": "not_available"} LIFEOS_AGENT_MODE is off for this person
    422                           empty, or over 2,000 characters
GET  /v1/agent-mode/runs/latest      {"run": RunView | null}: the newest run of the last seven days ("Last run: …")
GET  /v1/agent-mode/runs/{id}?rev=   the run's RunView, or {"unchanged": true} when `rev` is still the newest (one
                                     primary-key read); each poll says the person is looking (at most every 10 s)
POST /v1/agent-mode/runs/{id}/pause   a working run stops at its next step and parks; 409 while a card waits
POST /v1/agent-mode/runs/{id}/resume  a paused run goes on from where it stopped
POST /v1/agent-mode/runs/{id}/stop    what is done stays done; 409 for a run that ended another way
POST /v1/agent-mode/runs/{id}/continue: Keep going, on a run that stopped on a limit (capped): a new run, 201 {"id"}
POST /v1/agent-mode/runs/{id}/resolve {"action": "retry" | "skip"}: a failed run goes on from its checkpoint (nothing
                                     that finished runs twice), or without the step that failed; 409 otherwise
GET  /v1/agent-mode/runs/{id}/items/{n}  what one held item of the end card will send, exactly: {card} (an email's to,
                                     subject and body; an invite's guests and time), read from the checkpoint's own record of
                                     the call; 409 `no_card` when nothing waits for the person's OK. A pure read
POST /v1/agent-mode/runs/{id}/ask-aloud {"card_id"}  the phone face's spoken ask: the question built here from the end
                                     card the run waits on, read in the person's voice (talk.say) → {text, audio, type};
                                     409 `no_card` when that card isn't waiting, 409 when Atlas Shield would hide a name
GET  /v1/agent-mode/runs/{id}/undo   what "Undo…" on Done would do (AM-9): {items: [{step_id, title, effect: reverts | stays |
                                     cannot, note, link, never_mind}], count, email}. A pure read: no card, no push, no write.
POST /v1/agent-mode/runs/{id}/undo   K1's run undo for the `reverts` things only, newest first → {undone, skipped, kept,
                                     lines, left}; 409 `not_finished` for a run still going
Every one is 404 {"code": "not_available"} when the flag is off, 404 for a run that isn't the person's (never telling the
two apart), 409 {"code", "detail"} for a run in the wrong state. Every route is the person's own button, signed in: no
tool and no model can call them."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from lifeos import agentmode, chatmodes, speech
from lifeos.api import store, talk

NOT_AVAILABLE = {"code": "not_available", "detail": "not_available"}
NOT_FOUND = {"detail": "not found"}


class StartIn(BaseModel):
    text: str = Field(min_length=1, max_length=agentmode.REQUEST_MAX)


class AskAloudIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: str = Field(min_length=1, max_length=200)


NO_CARD = {"code": "no_card", "detail": "That card isn't waiting any more."}


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # nothing but the action: no run id, no owner

    action: Literal["retry", "skip"]


def router(limited: Callable[[str], Any]) -> APIRouter:
    r = APIRouter()

    @r.post("/v1/agent-mode/runs", status_code=201)
    async def start(body: StartIn, user: str = Depends(limited("agent_mode_start"))) -> Any:
        if not agentmode.enabled_for(user):  # off: nothing is read, written or spent
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        try:
            return {"id": await agentmode.begin(user, body.text)}
        except agentmode.Busy as busy:
            return JSONResponse({"code": "busy", "id": busy.run_id}, status_code=409)
        except agentmode.NotAvailable:
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        except ValueError as e:  # blank after trimming (the model's own limits are already checked)
            return JSONResponse({"detail": str(e)}, status_code=422)

    def answer(user: str, fn: Callable[[Any], Any]) -> Any:
        """`fn` as the person: its result, 404 for a run that isn't theirs, 409 for one in the wrong state."""
        try:
            return store.run(user, fn)
        except agentmode.Missing:
            return JSONResponse(NOT_FOUND, status_code=404)
        except agentmode.Conflict as c:
            return JSONResponse({"code": c.code, "detail": c.detail, **c.more}, status_code=409)

    @r.get("/v1/agent-mode/runs/latest")
    async def latest(user: str = Depends(limited("agent_mode_read"))) -> Any:
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        got = await asyncio.to_thread(answer, user, lambda c: agentmode.latest(c, user))
        return got if isinstance(got, JSONResponse) else {"run": got}

    @r.get("/v1/agent-mode/runs/{run_id}")
    async def read(
        run_id: int, rev: int | None = None, user: str = Depends(limited("agent_mode_read"))
    ) -> Any:
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        return await asyncio.to_thread(answer, user, lambda c: agentmode.poll(c, user, run_id, rev))

    async def control(user: str, run_id: int, fn: Callable[[Any], Any], *, wake: bool = False) -> Any:
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        got = await asyncio.to_thread(answer, user, fn)
        if isinstance(got, JSONResponse):
            return got
        if wake and got == "queued":  # committed: the worker pool takes it within a second or two
            await agentmode.wake(user, run_id)
        return {"id": run_id, "status": got}

    @r.post("/v1/agent-mode/runs/{run_id}/ask-aloud")
    async def ask_aloud(run_id: int, body: AskAloudIn, user: str = Depends(limited(talk.SPEAK_GROUP))) -> Any:
        """The phone face's spoken ask (the end card on screen at the same time): the question built here from the card
        the run waits on (agentmode.ask_aloud: never the app's words), read in the person's pick (Settings › Voice)
        through the same read as an answer (talk.say: Gradium first while it may, then OpenAI; held and booked). 409
        when that card isn't waiting, or Atlas Shield would hide a name in it (the app's device voice asks instead)."""
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        if not chatmodes.flag_on("voice_out", user):
            raise HTTPException(403, chatmodes.NOT_YET["voice_out"])

        def card(conn: Any) -> tuple[str, bool, speech.SpeechOut | None, str | None] | None:
            got = agentmode.view(conn, user, run_id).get("card") or {}
            if got.get("kind") != "end" or got.get("id") != body.card_id:
                return None
            line = agentmode.ask_aloud(got)
            if speech.shielded(conn, user, line):
                return line, True, None, None
            pick = speech.picked(conn, user)
            first = speech.gradium(conn, user, line) if pick == "natural" else None
            return line, False, first, speech.PICKS[pick]

        got = await asyncio.to_thread(answer, user, card)
        if isinstance(got, JSONResponse):
            return got
        if got is None:
            return JSONResponse(NO_CARD, status_code=409)
        line, kept, first, openai_voice = got
        if kept:
            raise HTTPException(409, talk.SHIELDED)
        spoken, _, _ = await talk.say(user, line, first, openai_voice)
        return {"text": line, "audio": base64.b64encode(spoken.audio).decode(), "type": spoken.mime}

    @r.get("/v1/agent-mode/runs/{run_id}/undo")
    async def undo_preview(run_id: int, user: str = Depends(limited("agent_mode_read"))) -> Any:
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        return await asyncio.to_thread(answer, user, lambda c: agentmode.undo_preview(c, user, run_id))

    @r.get("/v1/agent-mode/runs/{run_id}/items/{n}")
    async def held_item(run_id: int, n: int, user: str = Depends(limited("agent_mode_read"))) -> Any:
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        return await asyncio.to_thread(answer, user, lambda c: agentmode.held_item(c, user, run_id, n))

    @r.post("/v1/agent-mode/runs/{run_id}/undo")
    async def undo(run_id: int, user: str = Depends(limited("agent_mode_control"))) -> Any:
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        return await asyncio.to_thread(answer, user, lambda c: agentmode.undo(c, user, run_id))

    @r.post("/v1/agent-mode/runs/{run_id}/pause")
    async def pause(run_id: int, user: str = Depends(limited("agent_mode_control"))) -> Any:
        return await control(user, run_id, lambda c: agentmode.pause(c, user, run_id))

    @r.post("/v1/agent-mode/runs/{run_id}/resume")
    async def resume(run_id: int, user: str = Depends(limited("agent_mode_control"))) -> Any:
        return await control(user, run_id, lambda c: agentmode.resume(c, user, run_id), wake=True)

    @r.post("/v1/agent-mode/runs/{run_id}/stop")
    async def stop(run_id: int, user: str = Depends(limited("agent_mode_control"))) -> Any:
        return await control(user, run_id, lambda c: agentmode.stop(c, user, run_id))

    @r.post("/v1/agent-mode/runs/{run_id}/continue", status_code=201)
    async def keep_going(run_id: int, user: str = Depends(limited("agent_mode_start"))) -> Any:
        """Keep going: a run that stopped on a limit (the view's `capped`) goes on as a new run for the same request,
        told which steps are done (agentmode.keep_going; every check a new request has). 201 {"id"}; 409 for any other
        run (or another request going: {"code": "busy", "id"}); 404 for a run that isn't the person's."""
        if not agentmode.enabled_for(user):
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        try:
            got = await asyncio.to_thread(answer, user, lambda c: agentmode.keep_going(c, user, run_id))
        except agentmode.Busy as busy:
            return JSONResponse({"code": "busy", "id": busy.run_id}, status_code=409)
        except agentmode.NotAvailable:
            return JSONResponse(NOT_AVAILABLE, status_code=404)
        if isinstance(got, JSONResponse):
            return got
        await agentmode.wake(user, int(got))  # committed: the pool takes it within a second or two
        return {"id": int(got)}

    @r.post("/v1/agent-mode/runs/{run_id}/resolve")
    async def resolve(
        run_id: int, body: ResolveIn, user: str = Depends(limited("agent_mode_control"))
    ) -> Any:
        return await control(
            user, run_id, lambda c: agentmode.resolve(c, user, run_id, body.action), wake=True
        )

    return r
