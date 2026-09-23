"""Building model inputs for the free-text features."""

from __future__ import annotations

from datetime import date

from .brief import open_items, relative_due, sort_by_due
from .extract import User, direction
from .ingest import chunk_text
from .llm import LLM
from .prompts import CHUNK_NOTES, document_block
from .workspace import pending_proposals

QA_CONTEXT_CHARS = 60_000


def briefing_input(llm: LLM, title: str, doc_date: str, text: str) -> str:
    """Whole document if it fits; otherwise per-chunk notes (map-reduce) so nothing is dropped."""
    chunks = chunk_text(text)
    if len(chunks) == 1:
        return document_block(title, doc_date, text)
    notes = []
    for i, chunk in enumerate(chunks, start=1):
        part, _ = llm.complete(CHUNK_NOTES, chunk)
        notes.append(f"## Part {i} of {len(chunks)}\n{part}")
    return document_block(f"{title} (notes from {len(chunks)} parts)", doc_date, "\n\n".join(notes))


def ledger_block(ws: dict, user: User, as_of) -> str:
    rows = []
    for c in sort_by_due(ws["commitments"]):
        who = {"mine": "user owes", "owed": "owed to user", "others": "others"}[direction(c, user)]
        rows.append(f"- [{c['status']}] {c['task']} — owner {c['owner']}, {who}, due "
                    f"{relative_due(c, as_of)}; source: {c['sources'][0]['doc_title'] if c['sources'] else '?'}")
    return "<commitment_tracker>\n" + ("\n".join(rows) or "(empty)") + "\n</commitment_tracker>"


def qa_context(ws: dict, doc_ids: list[str], user: User, as_of) -> str:
    """Selected documents (newest first, within a size budget) plus the commitment ledger."""
    docs = sorted((d for d in ws["documents"] if d["id"] in doc_ids), key=lambda d: d["date"], reverse=True)
    parts, used = [], 0
    for d in docs:
        room = QA_CONTEXT_CHARS - used
        if room <= 500:
            break
        text = d["text"] if len(d["text"]) <= room else d["text"][:room] + "\n[…truncated]"
        parts.append(document_block(d["title"], d["date"], text))
        used += len(text)
    return "\n\n".join(parts + [ledger_block(ws, user, as_of)])


def followup_items(ws: dict, user: User, person_first: str, as_of) -> list[dict]:
    """Open items a given person owes (to the user or in shared work), for a follow-up message."""
    items = [c for c in open_items(ws)
             if c["owner"].lower().split(" ")[0] == person_first and direction(c, user) != "mine"]
    return sort_by_due(items)


def followup_listing(ws: dict, items: list[dict]) -> tuple[str, list[str]]:
    """Bullet list for a follow-up message, using the latest known plan.

    Changes still awaiting review are applied here (a message shouldn't chase a cancelled task
    or an old deadline). Returns the listing and notes about what was adjusted.
    """
    pending = {p["commitment_id"]: p for p in pending_proposals(ws)}
    lines, notes = [], []
    for c in items:
        proposal = pending.get(c["id"])
        if proposal and proposal["changes"].get("status", [None, None])[1] == "cancelled":
            notes.append(f"Left out “{c['task']}”: a newer document cancels it (change not reviewed yet).")
            continue
        due_iso = c.get("due_date")
        if proposal and "due_date" in proposal["changes"]:
            due_iso = proposal["changes"]["due_date"][1]
            notes.append(f"Used the newer date for “{c['task']}” (change not reviewed yet).")
        if due_iso:
            d = date.fromisoformat(due_iso)
            when = f"due {d:%a %b} {d.day}"
        else:
            when = f"due {c['due_text']}" if c.get("due_text") else "no date set"
        lines.append(f"- {c['task']} ({when})")
    return "\n".join(lines), notes
