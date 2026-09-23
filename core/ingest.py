"""Reading uploads, guessing document dates, and chunking long text."""

from __future__ import annotations

import io
import re
from datetime import date

SUPPORTED_TYPES = ["txt", "md", "pdf", "docx"]
CHUNK_CHARS = 20_000  # per extraction call; well inside model context windows

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def read_upload(name: str, data: bytes) -> str:
    """Extract plain text from a .txt, .md, .pdf or .docx upload."""
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    if ext == "docx":
        from docx import Document

        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts).strip()
    return data.decode("utf-8", errors="replace").strip()


def guess_doc_date(text: str) -> date | None:
    """Find the document's own date in its first lines (e.g. an email 'Date:' header)."""
    head = text[:1500]
    iso = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", head)
    if iso:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:
            pass
    named = re.search(r"\b([A-Z][a-z]{2,8})\.? (\d{1,2}),? (20\d\d)\b", head)
    if named and named[1][:3].lower() in _MONTHS:
        try:
            return date(int(named[3]), _MONTHS[named[1][:3].lower()], int(named[2]))
        except ValueError:
            pass
    return None


def guess_title(text: str, fallback: str = "Untitled document") -> str:
    subject = re.search(r"^Subject:\s*(.+)$", text, flags=re.MULTILINE)
    if subject:
        return re.sub(r"^((RE|FW|FWD):\s*)+", "", subject[1].strip(), flags=re.IGNORECASE)[:80]
    for line in text.splitlines():
        line = line.strip("#*[]-— \t")
        if len(line) >= 8:
            return line[:80]
    return fallback


def chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraph (then line) boundaries into chunks of at most max_chars."""
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], ""
    for block in re.split(r"(\n\s*\n)", text):
        pieces = [block] if len(block) <= max_chars else [
            block[i:i + max_chars] for i in range(0, len(block), max_chars)]
        for piece in pieces:
            if len(current) + len(piece) > max_chars and current.strip():
                chunks.append(current)
                current = ""
            current += piece
    if current.strip():
        chunks.append(current)
    return chunks
