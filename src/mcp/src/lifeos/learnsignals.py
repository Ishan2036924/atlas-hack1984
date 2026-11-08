"""What a person tells Atlas about its work (the learning loop, 2026-11-07; plan .local/plans/learning-loop.md;
migration 20261217110000). Codes and numbers only, never the person's words:

  thumb      a thumb on an agent run (subject run): value 1 or -1; 0 clears it (a chat answer's thumb is
             answer_evals, lifeos.api.answers: not here)
  no_reason  why they said no to an Approve card (subject approval, which must be theirs and denied): a NO_REASONS code
  edit       how much of a draft they changed before approving it (subject approval): value = permille
             changed, 0 to 1000 (the app measures it on the device with edit_permille's rule; only the number comes)
  pick       which option they picked when Atlas offered a few (subject choice): attrs {"group": slug, "picked": slug},
             e.g. {"group": "flight_seat", "picked": "window"}. Server side only (Atlas's own choosers, none yet):
             POST /v1/signals takes ROUTE_KINDS, so nobody can fill the table with made-up picks (review HIGH 1)
  habit      prefmine's mark that it proposed "Usually picks X for Y" (subject choice, id habit:<group>:<picked>):
             a habit is proposed once, even after the person says no (which deletes the memory row; review LOW 6)

One row per person, kind and subject: a second signal about the same thing replaces the first (a thumb can flip).
Tier A (their own row, no receipt). Read by lifeos.judge (a run's signals) and lifeos.prefmine (the picks). Kept
DAYS_KEPT days (prune, in the weekly job, as the person)."""

from __future__ import annotations

import difflib
import json
import re
from datetime import datetime
from typing import Any

from psycopg import Connection

KINDS = ("thumb", "no_reason", "edit", "pick", "habit")
ROUTE_KINDS = ("thumb", "no_reason", "edit")  # what a person sends; picks and habit marks are the server's
SUBJECTS = {"thumb": ("run",), "no_reason": ("approval",), "edit": ("approval",), "pick": ("choice",),
            "habit": ("choice",)}  # fmt: skip
# Why the person said no, as the app's chips say it (fixed codes; the words are the app's)
NO_REASONS = ("wrong_person", "wrong_time", "wrong_words", "wrong_amount", "not_needed", "not_now", "not_safe",
              "other")  # fmt: skip
DAYS_KEPT = 180
PICKS_MAX = 500  # the newest picks prefmine reads in one pass
SUBJECT_ID = re.compile(r"^[A-Za-z0-9:_.-]{1,100}$")
SLUG = re.compile(r"^[a-z0-9_-]{1,40}$")
COLS = "kind, subject, subject_id, agent_id, value, reason, attrs, created_at, updated_at"


class Refused(ValueError):
    """A signal that can't be kept (plain words)."""


class NotFound(LookupError):
    """The thing the signal is about isn't this person's (or doesn't exist)."""


def edit_permille(before: str, after: str) -> int:
    """How much of a draft changed, 0 (untouched) to 1000 (rewritten): 1000 x (1 - difflib's similarity ratio), on
    whitespace-collapsed text. The app measures the same way on the device and sends only this number."""
    a, b = " ".join(str(before or "").split()), " ".join(str(after or "").split())
    if a == b:
        return 0
    return round(1000 * (1 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()))


def clean(
    kind: Any, subject: Any, subject_id: Any, *, value: Any = None, reason: Any = None, attrs: Any = None
) -> dict[str, Any]:
    """The signal as it may be stored, or Refused. Nothing free-form passes: codes, slugs and small numbers."""
    if kind not in KINDS:
        raise Refused(f"kind is one of {', '.join(KINDS)}")
    if subject not in SUBJECTS[kind]:
        raise Refused(f"a {kind} is about {' or '.join(SUBJECTS[kind])}")
    sid = str(subject_id or "")
    if not SUBJECT_ID.match(sid):
        raise Refused("subject_id is 1 to 100 letters, digits or : _ . -")
    if subject == "run":
        if not sid.isdigit() or len(sid) > 18:
            raise Refused("a run is named by its number")
        sid = str(int(sid))  # "007" and "7" are the same run (review LOW 8)
    out: dict[str, Any] = {"kind": kind, "subject": subject, "subject_id": sid, "value": None, "reason": None,
                           "attrs": {}}  # fmt: skip
    if kind == "thumb":
        if isinstance(value, bool) or value not in (1, -1, 0):
            raise Refused("a thumb is 1 (up), -1 (down) or 0 (clear)")
        out["value"] = int(value)
    elif kind == "no_reason":
        if reason not in NO_REASONS:
            raise Refused(f"reason is one of {', '.join(NO_REASONS)}")
        out["reason"] = reason
    elif kind == "edit":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
            raise Refused("an edit is a whole number from 0 to 1000 (permille changed)")
        out["value"] = value
    else:  # pick, habit
        a = attrs if isinstance(attrs, dict) else {}
        group, picked = a.get("group"), a.get("picked")
        if not (
            isinstance(group, str) and SLUG.match(group) and isinstance(picked, str) and SLUG.match(picked)
        ):
            raise Refused("a pick names its group and the picked option as short slugs (a-z, 0-9, _ -)")
        if set(a) - {"group", "picked"}:
            raise Refused("a pick carries only group and picked")
        if kind == "habit" and sid != habit_id(group, picked):
            raise Refused("a habit mark is named habit:<group>:<picked>")
        out["attrs"] = {"group": group, "picked": picked}
    return out


def _owner_check(conn: Connection, user: str, s: dict[str, Any]) -> str | None:
    """The subject must be the person's (RLS shows only theirs; the owner filter says so twice). Returns the agent a
    run or an approval belongs to, when there is one. NotFound otherwise; a no-reason needs a card they said no to."""
    sid = s["subject_id"]
    if s["subject"] == "run":
        row = conn.execute(
            "select agent_id from public.agent_runs where id = %s and owner_id = %s",
            (int(sid) if sid.isdigit() else -1, user),
        ).fetchone()
        if row is None:
            raise NotFound("no such run")
        return str(row[0]) if row[0] is not None else None  # research and Agent mode runs have no agent
    if s["subject"] == "approval":
        row = conn.execute(
            "select a.status, r.agent_id from public.approvals a left join public.agent_runs r "
            "on r.owner_id = a.owner_id and r.id = (case when a.run_id ~ '^[0-9]{1,18}$' then a.run_id::bigint end) "
            "where a.id = %s and a.owner_id = %s",
            (sid, user),
        ).fetchone()
        if row is None:
            raise NotFound("no such card")
        if s["kind"] == "no_reason" and row[0] != "denied":
            raise Refused("a reason for no goes with a card you said no to")
        return str(row[1]) if row[1] is not None else None
    return None  # a choice: an id the app made, nothing to look up


def record(conn: Connection, user: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Keep one signal (as the user): validate, check the subject is theirs, upsert. Refused / NotFound otherwise."""
    s = clean(raw.get("kind"), raw.get("subject"), raw.get("subject_id"), value=raw.get("value"),
              reason=raw.get("reason"), attrs=raw.get("attrs"))  # fmt: skip
    agent = _owner_check(conn, user, s)
    if s["kind"] == "thumb" and s["value"] == 0:
        conn.execute(
            "delete from public.learning_signals where owner_id = %s and kind = 'thumb' and subject = %s "
            "and subject_id = %s",
            (user, s["subject"], s["subject_id"]),
        )
        return {**s, "agent_id": agent, "value": None, "at": None}
    row = conn.execute(
        "insert into public.learning_signals (owner_id, kind, subject, subject_id, agent_id, value, reason, attrs) "
        "values (%s, %s, %s, %s, %s::uuid, %s, %s, %s::jsonb) "
        "on conflict (owner_id, kind, subject, subject_id) do update set value = excluded.value, "
        "reason = excluded.reason, attrs = excluded.attrs, agent_id = excluded.agent_id, updated_at = now() "
        f"returning {COLS}",
        (user, s["kind"], s["subject"], s["subject_id"], agent, s["value"], s["reason"], _json(s["attrs"])),
    ).fetchone()
    return item(row)


def _json(v: dict[str, Any]) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True)


def item(r: tuple[Any, ...]) -> dict[str, Any]:
    return {"kind": r[0], "subject": r[1], "subject_id": r[2], "agent_id": str(r[3]) if r[3] else None,
            "value": r[4], "reason": r[5], "attrs": dict(r[6] or {}), "at": r[8].isoformat()}  # fmt: skip


def for_run(conn: Connection, user: str, run_id: int) -> list[dict[str, Any]]:
    """The person's signals about one run: a thumb on the run, and the no-reasons and edits on its Approve cards."""
    rows = conn.execute(
        f"select {COLS} from public.learning_signals s where s.owner_id = %(u)s and ("
        "(s.subject = 'run' and s.subject_id = %(r)s) or (s.subject = 'approval' and s.subject_id in "
        "(select a.id from public.approvals a where a.owner_id = %(u)s and a.run_id = %(r)s)))",
        {"u": user, "r": str(int(run_id))},
    ).fetchall()
    return [item(r) for r in rows]


PICKS_SQL = (
    f"select {COLS} from (select {COLS} from public.learning_signals where owner_id = %s and kind = 'pick' "
    "and updated_at >= %s order by updated_at desc, subject_id limit %s) newest order by updated_at, subject_id"
)


def picks(conn: Connection, user: str, since: datetime) -> list[dict[str, Any]]:
    """The person's newest PICKS_MAX picks since `since`, oldest first (lifeos.prefmine reads them in order)."""
    return [item(r) for r in conn.execute(PICKS_SQL, (user, since, PICKS_MAX)).fetchall()]


def habit_id(group: str, picked: str) -> str:
    return f"habit:{group}:{picked}"


def habits_marked(conn: Connection, user: str) -> set[tuple[str, str]]:
    """The (group, picked) habits prefmine already proposed to this person (kept, waiting or said no to)."""
    rows = conn.execute(
        "select attrs ->> 'group', attrs ->> 'picked' from public.learning_signals where owner_id = %s "
        "and kind = 'habit'",
        (user,),
    ).fetchall()
    return {(str(g), str(p)) for g, p in rows}


def mark_habit(conn: Connection, user: str, group: str, picked: str) -> None:
    """Remember that this habit was proposed (server side only; never through the route)."""
    s = clean("habit", "choice", habit_id(group, picked), attrs={"group": group, "picked": picked})
    conn.execute(
        "insert into public.learning_signals (owner_id, kind, subject, subject_id, attrs) values (%s, 'habit', "
        "'choice', %s, %s::jsonb) on conflict (owner_id, kind, subject, subject_id) do update set updated_at = now()",
        (user, s["subject_id"], _json(s["attrs"])),
    )


PRUNE_SQL = (
    "delete from public.learning_signals where owner_id = %s and kind <> 'habit' "
    "and updated_at < now() - make_interval(days => %s)"
)


def prune(conn: Connection, user: str, *, days: int = DAYS_KEPT) -> int:
    """Signals older than `days` go (as the person); habit marks stay, so a habit the person said no to never comes
    back (one short row per habit proposed: at most PER_PASS a week). Returns how many went."""
    return conn.execute(PRUNE_SQL, (user, days)).rowcount or 0
