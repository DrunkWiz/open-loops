"""Commitment extraction, source-quote verification, and who-owes-whom logic."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher

from .ingest import chunk_text
from .llm import LLM, Usage

PRIORITIES = ("High", "Medium", "Low")
STATUSES = ("open", "done", "cancelled")
SELF_WORDS = {"me", "i", "myself"}

COMMITMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "requester": {"type": ["string", "null"]},
                    "due_date": {"type": ["string", "null"]},
                    "due_text": {"type": ["string", "null"]},
                    "priority": {"type": "string", "enum": list(PRIORITIES)},
                    "status": {"type": "string", "enum": list(STATUSES)},
                    "change_note": {"type": ["string", "null"]},
                    "source_quote": {"type": "string"},
                },
                "required": ["task", "owner", "requester", "due_date", "due_text", "priority", "status",
                             "change_note", "source_quote"],
            },
        }
    },
    "required": ["items"],
}


@dataclass
class User:
    """Who the assistant works for; drives the Mine / Owed to me split."""

    name: str = "Me"
    aliases: list[str] = field(default_factory=list)

    def names(self) -> set[str]:
        out = {normalize_name(self.name)} | {normalize_name(a) for a in self.aliases}
        # First names ("Grace Liu" → "grace"), but not for labels like "Speaker 2".
        out |= {n.split()[0] for n in list(out) if len(n.split()) > 1 and not n.split()[-1].isdigit()}
        return {n for n in out if n} | SELF_WORDS

    def matches(self, person: str | None) -> bool:
        if not person:
            return False
        name = normalize_name(person)
        if name in self.names():
            return True
        # "Grace (PM)", "grace.liu", "Grace Liu" → compare first token too
        first = re.split(r"[\s.(_]", name)[0]
        return first in self.names()


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.]", " ", name.lower())).strip()


def calendar_lines(start: date, weeks: int = 10) -> str:
    """A compact calendar so the model can look dates up instead of doing weekday arithmetic."""
    monday = start - timedelta(days=start.weekday())
    rows = []
    for w in range(weeks):
        days = [monday + timedelta(days=7 * w + d) for d in range(7)]
        rows.append("  " + ", ".join(f"{d:%a} {d.isoformat()}" for d in days))
    return "\n".join(rows)


def extraction_prompt(doc_date: date, user: User) -> str:
    aliases = ", ".join(user.aliases) or "none"
    return f"""You extract commitments (action items) from a document for a personal AI assistant.

Calendar (use it to resolve dates; when a weekday and a day of the month are both given, e.g. "Friday the 23rd", pick the date where both match, even if it is in a later month):
{calendar_lines(doc_date)}

Context:
- The document is dated {doc_date:%A, %B %d, %Y}. Resolve relative deadlines ("tomorrow", "next \
Tuesday", "by Friday", "EOD") against that date and give due_date as YYYY-MM-DD. If a deadline cannot \
be pinned to one specific day, set due_date to null and keep the original words in due_text.
- The assistant's user is "{user.name}" (also known as: {aliases}). Whenever the user is the \
responsible person, set owner to exactly "{user.name}". When the user assigned or asked for the work, \
set requester to exactly "{user.name}".
- In chats and transcripts, "I"/"me" means whoever is speaking on that line. In notes or memos without \
speaker labels, "I"/"me" means the user.

Rules:
- A commitment is something a specific person will do, was asked to do, or volunteered to do. Skip \
vague ideas and general discussion.
- owner: the one person responsible, as named in the text, or "Unassigned".
- requester: who asked for it or will receive it (person, team or company), or null.
- status: "open" normally; "done" if the text says it is finished; "cancelled" if it was cut, dropped \
or moved out of scope.
- Also list every task, feature or deliverable the document says was cut, dropped, cancelled, postponed \
to a later release or moved out of scope, with status "cancelled" and its owner if known. These matter: \
they tell the user which earlier commitments no longer apply.
- Likewise list every task the document says was already done, sent or completed, with status "done", \
so the user's list can be updated.
- If the document says a deadline or plan changed, use the latest version and describe the old one in \
change_note (e.g. "was Oct 9"); otherwise change_note is null.
- source_quote: copy the shortest exact span (max 30 words) from the document that supports the item, \
character for character.
- Use only information in the document.

Return JSON of the form {{"items": [...]}} where each item has the keys task, owner, requester, \
due_date, due_text, priority (High, Medium or Low), status, change_note, source_quote."""


def _clean_str(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value if value and value.lower() not in {"null", "none", "n/a"} else None


def _iso_date(value) -> str | None:
    value = _clean_str(value)
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def normalize_item(raw: dict) -> dict | None:
    task = _clean_str(raw.get("task"))
    if not task:
        return None
    priority = (_clean_str(raw.get("priority")) or "Medium").title()
    status = (_clean_str(raw.get("status")) or "open").lower()
    return {
        "task": task[0].upper() + task[1:],
        "owner": _clean_str(raw.get("owner")) or "Unassigned",
        "requester": _clean_str(raw.get("requester")),
        "due_date": _iso_date(raw.get("due_date")),
        "due_text": _clean_str(raw.get("due_text")),
        "priority": priority if priority in PRIORITIES else "Medium",
        "status": status if status in STATUSES else "open",
        "change_note": _clean_str(raw.get("change_note")),
        # Drop list markers the model copies along with the line ("- ", "* ", "2. ").
        "source_quote": re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", _clean_str(raw.get("source_quote")) or ""),
    }


def _norm_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"}))
    return re.sub(r"\s+", " ", text).strip()


def verify_quote(quote: str, document: str) -> bool:
    """True if the quote (allowing '...' elisions and minor typos) appears in the document."""
    if not quote:
        return False
    doc = _norm_text(document)
    parts = [p.strip(" \"'") for p in re.split(r"\.\.\.|…", _norm_text(quote))]
    parts = [p for p in parts if len(p) >= 4]
    if not parts:
        return False
    for part in parts:
        if part in doc:
            continue
        matcher = SequenceMatcher(None, doc, part, autojunk=False)
        best = matcher.find_longest_match(0, len(doc), 0, len(part))
        if best.size / len(part) < 0.85:
            return False
    return True


def task_key(item: dict) -> tuple[str, str]:
    return normalize_name(item["owner"]), re.sub(r"\W+", " ", item["task"].lower()).strip()


@dataclass
class Extraction:
    items: list[dict]
    usage: Usage | None


def extract_commitments(llm: LLM, text: str, doc_date: date, user: User,
                        progress: Callable[[str], None] | None = None) -> Extraction:
    """Extract commitments from a document (chunked if long), with verified source quotes.

    `progress` receives short status messages; it may be called from a worker thread.
    """
    report = progress or (lambda message: None)
    system = extraction_prompt(doc_date, user)
    items, seen, usage = [], set(), None
    chunks = chunk_text(text)
    for n, chunk in enumerate(chunks, start=1):
        part = f" (part {n} of {len(chunks)})" if len(chunks) > 1 else ""
        report(f"🧠 {llm.model.split('/')[-1]} is reading the document{part} and extracting commitments…")
        data, chunk_usage = llm.complete_json(system, f"<document>\n{chunk}\n</document>", COMMITMENT_SCHEMA)
        usage = chunk_usage if usage is None else usage + chunk_usage
        raw_items = data.get("items", []) if isinstance(data, dict) else data
        for raw in raw_items if isinstance(raw_items, list) else []:
            item = normalize_item(raw) if isinstance(raw, dict) else None
            if not item or task_key(item) in seen:
                continue
            seen.add(task_key(item))
            if user.matches(item["owner"]):
                item["owner"] = user.name
            if user.matches(item["requester"]):
                item["requester"] = user.name
            item["verified"] = verify_quote(item["source_quote"], text)
            items.append(item)
    return Extraction(items, usage)


def direction(item: dict, user: User) -> str:
    """'mine' (user owes it), 'owed' (someone owes the user), or 'others'."""
    if user.matches(item.get("owner")):
        return "mine"
    if user.matches(item.get("requester")):
        return "owed"
    return "others"
