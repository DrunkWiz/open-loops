"""The user's workspace: documents, the commitment ledger, and change proposals.

A workspace is a plain JSON-serialisable dict, so it can live in Streamlit session
state (private to one browser session), be exported/imported, or be saved to a
local file when running on your own machine.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime
from pathlib import Path

VERSION = 1
TRACKED_FIELDS = ("task", "owner", "requester", "due_date", "due_text", "priority", "status")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_workspace(user_name: str = "Me", aliases: list[str] | None = None) -> dict:
    return {
        "version": VERSION,
        "user": {"name": user_name, "aliases": aliases or []},
        "documents": [],
        "commitments": [],
        "proposals": [],
    }


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def get_doc(ws: dict, doc_id: str) -> dict | None:
    return next((d for d in ws["documents"] if d["id"] == doc_id), None)


def get_commitment(ws: dict, cid: str) -> dict | None:
    return next((c for c in ws["commitments"] if c["id"] == cid), None)


def pending_proposals(ws: dict) -> list[dict]:
    return [p for p in ws["proposals"] if p["state"] == "pending"]


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
def add_document(ws: dict, title: str, doc_date: date, text: str, source: str = "paste") -> dict:
    doc = {
        "id": new_id("doc"),
        "title": title,
        "date": doc_date.isoformat(),
        "text": text,
        "source": source,
        "added_at": now(),
        "briefing": None,
        "extracted": [],  # everything the model found, including cancelled items
    }
    ws["documents"].append(doc)
    return doc


def _source(doc: dict, item: dict) -> dict:
    return {"doc_id": doc["id"], "doc_title": doc["title"], "doc_date": doc["date"],
            "quote": item.get("source_quote", ""), "verified": bool(item.get("verified"))}


def add_commitment(ws: dict, doc: dict, item: dict) -> dict:
    commitment = {
        "id": new_id("c"),
        **{k: item.get(k) for k in TRACKED_FIELDS},
        "change_note": item.get("change_note"),
        "sources": [_source(doc, item)],
        "history": [],
        "created_at": now(),
    }
    ws["commitments"].append(commitment)
    return commitment


def add_source(commitment: dict, doc: dict, item: dict) -> None:
    if all(s["doc_id"] != doc["id"] for s in commitment["sources"]):
        commitment["sources"].append(_source(doc, item))


def diff_fields(existing: dict, item: dict) -> dict[str, list]:
    """Fields where a newly extracted item differs from the ledger entry."""
    # Task wording isn't compared: rephrasing isn't a change, and the first wording is usually clearer.
    changes = {}
    for key in ("due_date", "owner", "status"):
        old, new = existing.get(key), item.get(key)
        if key == "due_date" and not new:
            continue  # a later mention without a date doesn't erase the date
        if key == "owner" and new == "Unassigned":
            continue
        if new != old:
            changes[key] = [old, new]
    return changes


def add_proposal(ws: dict, doc: dict, commitment: dict, item: dict, relation: str, reason: str) -> dict:
    proposal = {
        "id": new_id("p"),
        "state": "pending",
        "relation": relation,  # "update" | "cancelled"
        "commitment_id": commitment["id"],
        "doc_id": doc["id"],
        "item": item,
        "changes": diff_fields(commitment, item),
        "reason": reason,
        "created_at": now(),
    }
    ws["proposals"].append(proposal)
    return proposal


def resolve_proposal(ws: dict, proposal_id: str, accept: bool) -> None:
    """Accept: apply the change to the ledger entry. Ignore: keep the new item separately.

    Records what it did so `undo_proposal` can reverse it.
    """
    proposal = next(p for p in ws["proposals"] if p["id"] == proposal_id)
    if proposal["state"] != "pending":
        return
    doc = get_doc(ws, proposal["doc_id"])
    commitment = get_commitment(ws, proposal["commitment_id"])
    item = proposal["item"]
    undo: dict = {"before": copy.deepcopy(commitment) if commitment else None, "added_id": None}
    if accept and commitment and doc:
        for key, (old, new) in proposal["changes"].items():
            commitment[key] = new
            commitment["history"].append({
                "at": now(), "doc_id": doc["id"], "doc_title": doc["title"], "doc_date": doc["date"],
                "field": key, "old": old, "new": new,
            })
        if item.get("due_text") and "due_date" in proposal["changes"]:
            commitment["due_text"] = item["due_text"]
        commitment["change_note"] = proposal["reason"]
        add_source(commitment, doc, item)
    elif not accept and doc and item.get("status") != "cancelled":
        undo["added_id"] = add_commitment(ws, doc, item)["id"]
    undo["after"] = copy.deepcopy(commitment) if commitment else None
    proposal["undo"] = undo
    proposal["state"] = "accepted" if accept else "ignored"
    proposal["resolved_at"] = datetime.now().isoformat(timespec="microseconds")


def can_undo(ws: dict, proposal: dict) -> bool:
    """Undo is safe only if nothing has touched the affected commitments since."""
    undo = proposal.get("undo")
    if proposal["state"] == "pending" or not undo:
        return False
    if get_commitment(ws, proposal["commitment_id"]) != undo["after"]:
        return False
    if undo["added_id"]:
        added = get_commitment(ws, undo["added_id"])
        if not added or added["history"] or added["status"] != proposal["item"].get("status", "open"):
            return False
    return True


def undo_proposal(ws: dict, proposal_id: str) -> bool:
    """Put a resolved proposal back to pending, restoring the ledger as it was."""
    proposal = next((p for p in ws["proposals"] if p["id"] == proposal_id), None)
    if not proposal or not can_undo(ws, proposal):
        return False
    undo = proposal.pop("undo")
    proposal.pop("resolved_at", None)
    if undo["before"] is not None:
        ws["commitments"] = [undo["before"] if c["id"] == proposal["commitment_id"] else c
                             for c in ws["commitments"]]
    if undo["added_id"]:
        ws["commitments"] = [c for c in ws["commitments"] if c["id"] != undo["added_id"]]
    proposal["state"] = "pending"
    return True


EDITABLE_FIELDS = ("task", "owner", "requester", "due_date", "priority")


def edit_commitment(ws: dict, cid: str, updates: dict) -> list[str]:
    """Apply the user's corrections, recording each in the history. Returns the fields changed."""
    commitment = get_commitment(ws, cid)
    if not commitment:
        return []
    changed = []
    for key in EDITABLE_FIELDS:
        if key not in updates:
            continue
        new = updates[key]
        new = new.strip() or None if isinstance(new, str) else new
        if key in ("task", "owner") and not new:
            continue  # these can't be blank
        if new == commitment.get(key):
            continue
        commitment["history"].append({"at": now(), "doc_id": None, "doc_title": "You (edit)",
                                      "doc_date": date.today().isoformat(),
                                      "field": key, "old": commitment.get(key), "new": new})
        commitment[key] = new
        if key == "due_date":
            commitment["due_text"] = None  # the original wording no longer describes the date
        changed.append(key)
    return changed


def set_status(ws: dict, cid: str, status: str) -> None:
    commitment = get_commitment(ws, cid)
    if commitment and commitment["status"] != status:
        commitment["history"].append({"at": now(), "doc_id": None, "doc_title": "You",
                                      "doc_date": date.today().isoformat(),
                                      "field": "status", "old": commitment["status"], "new": status})
        commitment["status"] = status


def remove_document(ws: dict, doc_id: str) -> None:
    """Remove a document and any commitments that came only from it."""
    ws["documents"] = [d for d in ws["documents"] if d["id"] != doc_id]
    kept = []
    for c in ws["commitments"]:
        c["sources"] = [s for s in c["sources"] if s["doc_id"] != doc_id]
        if c["sources"]:
            kept.append(c)
    ws["commitments"] = kept
    ids = {c["id"] for c in kept}
    ws["proposals"] = [p for p in ws["proposals"]
                       if p["doc_id"] != doc_id and p["commitment_id"] in ids]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def to_json(ws: dict) -> str:
    return json.dumps(ws, indent=2, ensure_ascii=False)


def from_json(text: str) -> dict:
    ws = json.loads(text)
    if not isinstance(ws, dict) or ws.get("version") != VERSION:
        raise ValueError("Not a workspace file from this app")
    for key in ("documents", "commitments", "proposals"):
        if not isinstance(ws.get(key), list):
            raise ValueError(f"Workspace file is missing '{key}'")
    ws.setdefault("user", {"name": "Me", "aliases": []})
    return ws


def save_file(ws: dict, path: str | Path) -> None:
    Path(path).write_text(to_json(ws), encoding="utf-8")


def load_file(path: str | Path) -> dict | None:
    path = Path(path)
    return from_json(path.read_text(encoding="utf-8")) if path.exists() else None
