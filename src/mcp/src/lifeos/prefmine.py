"""Preferences from picks (the learning loop, 2026-11-07; plan .local/plans/learning-loop.md): "you picked the window
seat twice, so next time it suggests the window seat". The person's picks (lifeos.learnsignals, kind pick) are counted
per group; a clear habit becomes a proposed fact in Atlas's memory that waits for the person's review, never a fact
in use on its own.

A habit: in a group's last RECENT picks, one option was picked at least MIN_PICKS times and at least SHARE of the time.
The fact says it in plain words ("Usually picks window for flight seat"). It is saved through lifeos.memory.save with no
quote, so it waits in review like anything the person didn't say in their own words (memory.py: never recalled or
loaded until they approve it). Nothing is proposed while the person paused automatic memory (memory.save checks that
only for tracked saves, so this module checks first: review MEDIUM 2). A habit is proposed once: its mark
(learnsignals.mark_habit) stays even after the person says no, which deletes the memory row (review LOW 6); a save
refused for a sensitive topic or a secret is marked too, so it isn't tried every week. Preferences only change
defaults and suggestions: a pick never changes a tier, a rule or a card (lifeos.learnguard)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from psycopg import Connection

from lifeos import learnsignals, memory

RECENT = 6
MIN_PICKS = 2
SHARE = 2 / 3
LOOKBACK_DAYS = 90
PER_PASS = 5  # at most this many proposals a week per person (asking rarely)


def _words(slug: str) -> str:
    return " ".join(slug.replace("_", " ").replace("-", " ").split())


def fact_of(group: str, picked: str) -> str:
    return f"Usually picks {_words(picked)} for {_words(group)}"


def habits(picks: Sequence[dict[str, Any]]) -> list[tuple[str, str, int, int]]:
    """(group, option, times, out_of) for every clear habit in `picks` (oldest first), strongest first (pure)."""
    by_group: dict[str, list[str]] = defaultdict(list)
    for p in picks:
        a = p.get("attrs") or {}
        if isinstance(a.get("group"), str) and isinstance(a.get("picked"), str):
            by_group[a["group"]].append(a["picked"])
    out = []
    for group, seq in by_group.items():
        recent = seq[-RECENT:]
        option, times = Counter(recent).most_common(1)[0]
        if times >= MIN_PICKS and times / len(recent) >= SHARE:
            out.append((group, option, times, len(recent)))
    return sorted(out, key=lambda h: (-h[2] / h[3], -h[2], h[0]))


def propose(
    conn: Connection, user: str, *, now: datetime, save: Callable[..., dict[str, Any]] = memory.save
) -> dict[str, int]:
    """Turn the person's clear habits into facts waiting for their review. Returns counts only."""
    if memory.paused(conn, user):
        return {"habits": 0, "proposed": 0, "skipped": 0, "paused": 1}
    found = habits(learnsignals.picks(conn, user, now - timedelta(days=LOOKBACK_DAYS)))
    marked = learnsignals.habits_marked(conn, user)
    known = {memory.norm(t) for (t,) in conn.execute(
        "select text from public.memories where owner_id = %s and agent_id is null and kind = 'preference'", (user,)
    ).fetchall()}  # fmt: skip
    proposed = skipped = 0
    for group, option, _times, _of in found:
        if proposed >= PER_PASS:
            break
        text = fact_of(group, option)
        if (group, option) in marked or memory.norm(text) in known:
            continue
        try:
            save(conn, user, text=text, kind="preference", quote="", source="chat")
        except memory.Refused as e:
            skipped += 1
            if e.why != "paused":
                learnsignals.mark_habit(conn, user, group, option)  # a sensitive topic or a secret: not again
            continue
        except memory.MemoryOff:
            skipped += 1
            continue
        learnsignals.mark_habit(conn, user, group, option)
        known.add(memory.norm(text))
        proposed += 1
    return {"habits": len(found), "proposed": proposed, "skipped": skipped}
