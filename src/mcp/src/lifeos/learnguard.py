"""The learning guard (the learning loop, 2026-11-07; plan .local/plans/learning-loop.md): a change Atlas learned on
its own may make an agent stricter, never freer, and never rewrites its instructions on its own. Pure functions, no
database, no model. Fail closed: what this module doesn't know is refused.

`problems(before, after)` compares two AgentSpecV2 and answers in plain words what Atlas may not apply by itself.
Applied without a tap only when `problems` == []:
  - the model (standard, fast, bulk: all already allowed for the agent),
  - evals added (more tests, never fewer),
  - anything that only tightens: fewer tools or apps, more ask-first tools, lower limits or budget, a narrower memory,
    fewer knowledge sources, peers or tables.
Refused (a proposal at most, for the person's tap):
  - ANY change to the instructions. Phrase matching can't tell "approval is not needed" or "Don't ask first" from a
    harmless rewording, so learned wording is never applied on its own (review MEDIUM 3),
  - a new tool, app, knowledge source, peer or table; an ask-first removed from a tool the agent keeps; a higher limit
    or budget; a changed or unknown limit (limit_problems); a wider memory,
  - any change to triggers, rules or when_unsure (the person's own words and the agent's reach are theirs to edit), and
    a new name, role, description, colour or template (not what learning is for),
  - any change to a field added to AgentSpecV2 later, until someone decides about it here.

A person's own edit or an "Explain and fix" they apply with a tap is theirs to make and never goes through here. The
judge (lifeos.judge) only scores."""

from __future__ import annotations

from typing import Any

from lifeos.agentspec import AgentSpecV2

MEMORY_ORDER = {"off": 0, "own": 1, "own_shared": 2}
FREE = ("model", "evals")  # what learning may change on its own (evals: only add)
PERSON = ("instructions",)  # learning may propose a change here, never apply it
SUBSETS = ("tools", "apps", "knowledge", "peers", "append_tables")  # may only shrink
SAME = ("triggers", "rules", "when_unsure", "name", "role", "description", "color", "template")
LOWER = (
    "steps",
    "seconds",
    "run_cap_usd",
)  # limits that may only go down; any other limit must stay as it is


def _names(values: list[Any]) -> set[str]:
    return {str(v) for v in values}


def limit_problems(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """What a change to the run limits would loosen: a LOWER limit raised, or any other limit changed (wait_hours is
    the person's call; a key added to RunLimits later is refused until someone decides about it here)."""
    out = []
    for k in sorted(set(before) | set(after)):
        if k in LOWER:
            if float(after.get(k, 0)) > float(before.get(k, 0)):
                out.append(f"raises the limit {k}")
        elif after.get(k) != before.get(k):
            out.append(f"changes the limit {k}")
    return out


def problems(before: AgentSpecV2, after: AgentSpecV2) -> list[str]:
    """What `after` changes that Atlas may not apply on its own, in plain words ([] when it may)."""
    b, a = before.model_dump(mode="json"), after.model_dump(mode="json")
    out: list[str] = []
    if a["instructions"] != b["instructions"]:
        out.append("changes its instructions (a proposal for the person's tap, never applied on its own)")
    for field in SUBSETS:
        added = sorted(_names(a[field]) - _names(b[field]))
        if added:
            out.append(f"adds to {field}: {', '.join(added)}")
    kept = _names(a["tools"]) & _names(b["tools"])
    dropped_asks = sorted((_names(b["ask_tools"]) - _names(a["ask_tools"])) & kept)
    if dropped_asks:
        out.append(f"stops asking first for: {', '.join(dropped_asks)}")
    out += limit_problems(b["limits"], a["limits"])
    if float(a["monthly_budget_usd"]) > float(b["monthly_budget_usd"]):
        out.append("lets it use more AI each month")
    if MEMORY_ORDER[a["memory_scope"]] > MEMORY_ORDER[b["memory_scope"]]:
        out.append("widens what the agent's memory can see")
    for field in SAME:
        if a[field] != b[field]:
            out.append(f"changes {field} (not something learning may change)")
    if any(e not in a["evals"] for e in b["evals"]):
        out.append("removes a test case")
    known = set(FREE) | set(PERSON) | set(SUBSETS) | set(SAME) | {"ask_tools", "limits", "monthly_budget_usd",
                                                                   "memory_scope"}  # fmt: skip
    for field in sorted(
        set(a) - known
    ):  # a field added to AgentSpecV2 later is refused until someone decides here
        if a[field] != b[field]:
            out.append(f"changes {field} (not reviewed for learning)")
    return out


def allowed(before: AgentSpecV2, after: AgentSpecV2) -> bool:
    return not problems(before, after)
