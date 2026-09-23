"""Export dated commitments as an iCalendar (.ics) file of all-day events."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> list[str]:
    """RFC 5545 line folding at 75 octets."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > 75:
            out.append(current)
            current = " " + ch
        else:
            current += ch
    return out + [current]


def to_ics(commitments: list[dict], calendar_name: str = "Commitments") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Open Loops//Personal AI//EN",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_escape(calendar_name)}"]
    for c in commitments:
        if not c.get("due_date") or c.get("status") != "open":
            continue
        day = date.fromisoformat(c["due_date"])
        source = c["sources"][0] if c.get("sources") else {}
        description = f"Owner: {c['owner']}"
        if c.get("requester"):
            description += f"\nFor: {c['requester']}"
        if source:
            description += f"\nSource: {source.get('doc_title', '')}\n\"{source.get('quote', '')}\""
        lines += [
            "BEGIN:VEVENT",
            f"UID:{c['id']}@open-loops",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_escape(c['task'])}",
            f"DESCRIPTION:{_escape(description)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(folded for line in lines for folded in _fold(line)) + "\r\n"
