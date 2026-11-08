"""Open Pseudonym's "What the AI saw" (lifeos.pseudonym), as the signed-in user (role lifeos_tools + RLS):

GET /v1/pseudonym              → {"enabled": false} while LIFEOS_PSEUDONYM is off for this user (the app shows no row);
                                 else {"enabled": true, "key": the receipts' public key (JWK + kid)}
GET /v1/pseudonym/turns        → {"turns": [{id, at, job, model, calls, words, stand_ins}], "more"}: newest first, ?before=
                                 <the last one's `at`> for the next page; no words, no tokens
GET /v1/pseudonym/turns/{id}   → {id, at, job, seen, real, marks, words, stand_ins, ok, calls: [{seq, at, model, payload,
                                 stand_ins, ok, why, token}]}: what the AI saw beside the person's own words (opened on
                                 the server for its owner only), and each request's receipt checked
Both lists 404 while the switch is off for this user, and a turn that isn't theirs is a 404 too (RLS and the owner
filter: another person's turn id finds nothing)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from lifeos import pseudonym
from lifeos.api import store

OFF = "Open Pseudonym isn't on for this account"


def mount(app: FastAPI, limited: Callable[[str], Any]) -> None:
    @app.get("/v1/pseudonym")
    def pseudonym_get(user: str = Depends(limited("pseudonym"))) -> dict[str, Any]:
        if not pseudonym.enabled_for(user):
            return {"enabled": False}
        return {"enabled": True, "key": pseudonym.public_key()}

    @app.get("/v1/pseudonym/turns")
    def pseudonym_turns(
        before: datetime | None = None, limit: int = 20, user: str = Depends(limited("pseudonym"))
    ) -> dict[str, Any]:
        if not pseudonym.enabled_for(user):
            raise HTTPException(404, OFF)
        limit = max(1, min(limit, pseudonym.TURNS_MAX))
        got = store.run(user, lambda conn: pseudonym.turns(conn, user, before=before, limit=limit + 1))
        return {"turns": got[:limit], "more": len(got) > limit}

    @app.get("/v1/pseudonym/turns/{turn}")
    def pseudonym_turn(turn: uuid.UUID, user: str = Depends(limited("pseudonym"))) -> dict[str, Any]:
        if not pseudonym.enabled_for(user):
            raise HTTPException(404, OFF)
        got = store.run(user, lambda conn: pseudonym.turn_view(conn, user, str(turn)))
        if got is None:
            raise HTTPException(404, "no such turn")
        return got
