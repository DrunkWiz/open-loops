"""Analysing a new document against the ledger: extraction + change detection."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from . import workspace as wsx
from .extract import User, extract_commitments, normalize_name
from .llm import LLM, Usage

MAX_CANDIDATES = 6

MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "new": {"type": "integer"},
                    "existing": {"type": ["string", "null"]},
                    "relation": {"type": "string", "enum": ["same", "update", "cancelled", "none"]},
                    "reason": {"type": "string"},
                },
                "required": ["new", "existing", "relation", "reason"],
            },
        }
    },
    "required": ["matches"],
}

MATCH_PROMPT = """You maintain a personal commitment tracker. A NEW document has been added. For each \
new item, decide whether it refers to the same underlying commitment as one of its listed candidate \
existing items: the same person doing the same piece of work, even if worded differently or with a \
different deadline.

relation:
- "same": the same commitment with no meaningful change.
- "update": the same commitment, but its deadline, owner or scope changed.
- "cancelled": the new document says this commitment was cut, dropped, cancelled or moved out of scope.
- "none": none of the candidates is the same commitment. Be conservative: two different tasks for the \
same person are "none".

reason: one short sentence a busy person can read at a glance that says what changed AND why, using \
the new item's source text, e.g. "Deadline moved from Oct 9 to Oct 6 because QA needs more time." or \
"Moved to v3.1: Hamid confirmed it can't be stable in time." Don't restate field names like "status \
changed from open to cancelled", and never mention "candidate", "existing item" or "new item": the \
reader doesn't know those words. Take the reason from the change note or the context in the new \
document. If they give no reason, just state the change (e.g. "Deadline moved from Oct 13 to Oct 15.") \
rather than repeating it as a reason. Use only facts given here.

Return JSON: {"matches": [{"new": <index>, "existing": "<existing id or null>", "relation": "...", \
"reason": "..."}]} with exactly one entry per new item."""


def _similarity(a: str, b: str) -> float:
    words = lambda s: " ".join(sorted(set(re.findall(r"[a-z0-9]+", s.lower()))))  # noqa: E731
    return SequenceMatcher(None, words(a), words(b)).ratio()


def _first(name: str | None) -> str:
    return normalize_name(name or "").split(" ")[0] if name else ""


STOPWORDS = {"the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "by", "with", "from", "at", "is",
             "be", "complete", "completed", "code", "send", "get", "make", "do", "new", "update", "ready", "v3"}


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def candidates_for(item: dict, existing: list[dict]) -> list[dict]:
    """Existing commitments plausibly about the same thing: same owner, similar wording, or
    (for cancellations and unowned items, which often name only the feature) a shared keyword."""
    loose = item["status"] == "cancelled" or _first(item["owner"]) == "unassigned"
    scored = []
    for c in existing:
        same_owner = _first(c["owner"]) == _first(item["owner"]) and _first(item["owner"]) != "unassigned"
        score = _similarity(item["task"], c["task"])
        shared = len(_keywords(item["task"]) & _keywords(c["task"]))
        if same_owner or score >= 0.5 or (loose and shared):
            scored.append((score + (0.5 if same_owner else 0) + 0.2 * shared, c))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in scored[:MAX_CANDIDATES]]


def surrounding_text(quote: str, text: str, radius: int = 250) -> str:
    """The quote plus nearby lines, which often say *why* something changed."""
    anchor = quote.strip()[:40]
    at = text.find(anchor) if anchor else -1
    if at == -1:
        return quote
    start, end = max(0, at - radius), min(len(text), at + len(quote) + radius)
    return " ".join(text[start:end].split())


def _describe(c: dict) -> str:
    due = c.get("due_date") or c.get("due_text") or "no date"
    note = f' | change note: {c["change_note"]}' if c.get("change_note") else ""
    return f'{c["task"]} | owner: {c["owner"]} | due: {due} | status: {c["status"]}{note}'


@dataclass
class Analysis:
    items: list[dict]
    matches: dict[int, dict] = field(default_factory=dict)  # new index -> {existing, relation, reason}
    usage: Usage | None = None


def analyze(llm: LLM, text: str, doc_date: date, user: User, existing: list[dict],
            progress: Callable[[str], None] | None = None) -> Analysis:
    """Extract commitments and match them to the ledger. Pure: doesn't touch the workspace.

    `progress` receives short status messages; it may be called from a worker thread.
    """
    report = progress or (lambda message: None)
    extraction = extract_commitments(llm, text, doc_date, user, progress)
    analysis = Analysis(extraction.items, usage=extraction.usage)
    dated = sum(1 for i in extraction.items if i["due_date"])
    report(f"✅ Found {len(extraction.items)} commitment(s), {dated} with a date")
    active = [c for c in existing if c["status"] != "cancelled"]

    blocks, allowed = [], {}
    for i, item in enumerate(extraction.items):
        cands = candidates_for(item, active)
        if not cands:
            continue
        allowed[i] = {c["id"] for c in cands}
        lines = "\n".join(f'    - id {c["id"]}: {_describe(c)}' for c in cands)
        context = surrounding_text(item.get("source_quote", ""), text)
        source = f'\n  Context in the new document: "{context}"' if context else ""
        blocks.append(f"New item {i}: {_describe(item)}{source}\n  Candidates:\n{lines}")
    if not blocks:
        if active:
            report(f"🔍 Nothing here overlaps your {len(active)} tracked commitment(s)")
        return analysis

    report(f"🔍 Checking {len(blocks)} of them against your {len(active)} tracked commitment(s) for changes…")
    data, usage = llm.complete_json(MATCH_PROMPT, "\n\n".join(blocks), MATCH_SCHEMA)
    analysis.usage = usage if analysis.usage is None else analysis.usage + usage
    for m in data.get("matches", []) if isinstance(data, dict) else []:
        try:
            i = int(m.get("new"))
        except (TypeError, ValueError):
            continue
        relation = m.get("relation")
        if i in allowed and m.get("existing") in allowed[i] and relation in ("same", "update", "cancelled"):
            analysis.matches[i] = {"existing": m["existing"], "relation": relation,
                                   "reason": str(m.get("reason") or "").strip()}
    changed = sum(1 for m in analysis.matches.values() if m["relation"] != "same")
    report(f"🔁 {changed} possible change(s) to earlier plans" if changed else "🔁 No changes to earlier plans")
    return analysis


def apply_analysis(ws: dict, doc: dict, analysis: Analysis) -> dict:
    """Write an analysis into the workspace. Changes become proposals, never silent edits."""
    summary = {"added": [], "merged": [], "proposals": [], "cancelled_unmatched": 0}
    doc["extracted"] = analysis.items
    for i, item in enumerate(analysis.items):
        match = analysis.matches.get(i)
        existing = wsx.get_commitment(ws, match["existing"]) if match else None
        if not existing:
            if item["status"] == "cancelled":
                summary["cancelled_unmatched"] += 1  # nothing in the ledger to cancel
            else:
                summary["added"].append(wsx.add_commitment(ws, doc, item)["id"])
            continue
        if match["relation"] == "cancelled":
            item = {**item, "status": "cancelled"}
        changes = wsx.diff_fields(existing, item)
        if not changes:
            wsx.add_source(existing, doc, item)
            summary["merged"].append(existing["id"])
            continue
        relation = "cancelled" if item["status"] == "cancelled" else "update"
        reason = match["reason"] or "Updated in a newer document."
        summary["proposals"].append(wsx.add_proposal(ws, doc, existing, item, relation, reason)["id"])
    return summary
