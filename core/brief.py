"""Morning brief: a deterministic view of the ledger as of a given day."""

from __future__ import annotations

from datetime import date, timedelta

from .extract import User, direction
from .workspace import pending_proposals


def due(c: dict) -> date | None:
    try:
        return date.fromisoformat(c["due_date"]) if c.get("due_date") else None
    except ValueError:
        return None


def relative_due(c: dict, as_of: date) -> str:
    d = due(c)
    if not d:
        return c.get("due_text") or "no date"
    days = (d - as_of).days
    label = f"{d:%a %b} {d.day}"
    if days < 0:
        return f"{label} · {-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return f"{label} · today"
    if days == 1:
        return f"{label} · tomorrow"
    return f"{label} · in {days} days"


def open_items(ws: dict) -> list[dict]:
    return [c for c in ws["commitments"] if c["status"] == "open"]


def sort_by_due(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda c: (due(c) is None, due(c) or date.max, c["task"].lower()))


def build_brief(ws: dict, user: User, as_of: date, horizon_days: int = 7) -> dict:
    """Sections of the morning brief. Pure function of the workspace and the date."""
    horizon = as_of + timedelta(days=horizon_days)
    mine = [c for c in open_items(ws) if direction(c, user) == "mine"]
    owed = [c for c in open_items(ws) if direction(c, user) == "owed"]

    def split(items: list[dict]) -> tuple[list, list, list]:
        overdue = [c for c in items if due(c) and due(c) < as_of]
        today = [c for c in items if due(c) == as_of]
        upcoming = [c for c in items if due(c) and as_of < due(c) <= horizon]
        return sort_by_due(overdue), today, sort_by_due(upcoming)

    mine_overdue, mine_today, mine_upcoming = split(mine)
    owed_overdue, owed_today, owed_upcoming = split(owed)
    recent_cutoff = (as_of - timedelta(days=3)).isoformat()
    changed = [c for c in ws["commitments"]
               if any(h["doc_date"] and h["doc_date"] >= recent_cutoff for h in c["history"])]
    return {
        "as_of": as_of,
        "mine_overdue": mine_overdue,
        "mine_today": mine_today,
        "mine_upcoming": mine_upcoming,
        "mine_undated": [c for c in mine if not due(c)],
        "owed_overdue": owed_overdue,
        "owed_due_soon": sort_by_due(owed_today + owed_upcoming),
        "pending": pending_proposals(ws),
        "changed": changed,
        "counts": {
            "open": len(open_items(ws)),
            "mine": len(mine),
            "owed": len(owed),
            "overdue": len(mine_overdue) + len(owed_overdue),
            "pending": len(pending_proposals(ws)),
            "documents": len(ws["documents"]),
        },
    }


def brief_as_text(brief: dict, user: User) -> str:
    """Compact plain-text version of the brief, used as model input for the narrative."""
    as_of = brief["as_of"]

    def lines(items: list[dict], with_owner: bool = False) -> str:
        if not items:
            return "  (none)"
        return "\n".join(
            f"  - {c['task']}" + (f" [{c['owner']}]" if with_owner else "") + f" — {relative_due(c, as_of)}"
            for c in items)

    return f"""Today is {as_of:%A, %B %d, %Y}. The user is {user.name}.
The user's overdue items:
{lines(brief['mine_overdue'])}
Due today:
{lines(brief['mine_today'])}
Due in the next 7 days:
{lines(brief['mine_upcoming'])}
Others owe the user, overdue:
{lines(brief['owed_overdue'], True)}
Others owe the user, due soon:
{lines(brief['owed_due_soon'], True)}
Plan changes awaiting the user's review: {len(brief['pending'])}"""
