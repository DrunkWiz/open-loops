"""Live check of the free-text features against the real model: morning brief, follow-up draft,
and Q&A across documents. Uses the saved demo workspace (samples/demo_workspace.json).

Usage (needs NEBIUS_API_KEY in .env):
    python -m scripts.live_check
"""

from __future__ import annotations

import os
import sys
import unicodedata
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import workspace as wsx  # noqa: E402
from core.brief import brief_as_text, build_brief  # noqa: E402
from core.extract import User  # noqa: E402
from core.llm import FALLBACK_MODELS, LLM  # noqa: E402
from core.prompts import FOLLOWUP, MORNING, QA, today_line  # noqa: E402
from core.writer import followup_items, followup_listing, qa_context  # noqa: E402

# (question, words the answer should contain — any of them, case-insensitive)
QUESTIONS = [
    ("When is Hamid's onboarding flow due now, and did it change?", ["oct 6", "october 6", "10-06"]),
    ("Is offline sync still part of v3?", ["no", "cut", "v3.1"]),
    ("What did Dad say he would do for Grandma's party?", ["cake"]),
]


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> None:
    load_dotenv(ROOT / ".env")
    key = os.getenv("NEBIUS_API_KEY", "")
    if not key or key.startswith("your_"):
        sys.exit("Set NEBIUS_API_KEY in .env first.")
    ws = wsx.load_file(ROOT / "samples" / "demo_workspace.json")
    if not ws:
        sys.exit("Run `python -m scripts.build_demo` first.")
    user = User(ws["user"]["name"], ws["user"]["aliases"])
    as_of = date.fromisoformat(ws.get("demo", {}).get("as_of", "2026-09-23"))
    llm = LLM(key, FALLBACK_MODELS[0], temperature=0.3)
    results = []

    section("1. Morning brief (streamed)")
    brief = build_brief(ws, user, as_of)
    text = "".join(llm.stream(MORNING.format(user=user.name), brief_as_text(brief, user)))
    print(text, f"\n\n[{llm.last_usage.label() if llm.last_usage else ''}]")
    overdue = [c["task"] for c in brief["mine_overdue"]]
    ok = bool(text.strip()) and (not overdue or any(w in text.lower() for w in ("overdue", "late", "past due")))
    results.append(("Morning brief is non-empty and mentions overdue work", ok))

    section("2. Follow-up draft to Hamid")
    items = followup_items(ws, user, "hamid", as_of)
    listing, notes = followup_listing(ws, items)
    print("Items:\n" + listing + "\n" + "".join(f"note: {n}\n" for n in notes))
    draft, usage = llm.complete(FOLLOWUP.format(user=user.name, person="Hamid"), f"{today_line(as_of)}\n{listing}")
    print(draft, f"\n\n[{usage.label()}]")
    low = unicodedata.normalize("NFKC", draft).lower()
    ok = "hamid" in low and "onboarding" in low and "oct 9" not in low and "offline sync" not in low
    results.append(("Follow-up uses the latest plan (Oct 6, no cancelled offline sync)", ok))

    section("3. Ask across all documents (streamed)")
    context = qa_context(ws, [d["id"] for d in ws["documents"]], user, as_of)
    for question, expect in QUESTIONS:
        answer = "".join(llm.stream(QA, f"{today_line(as_of)} The user is {user.name}.\n\n{context}\n\n"
                                        f"Question: {question}"))
        print(f"\nQ: {question}\nA: {answer}\n[{llm.last_usage.label() if llm.last_usage else ''}]")
        plain = unicodedata.normalize("NFKC", answer).lower()  # models use non-breaking spaces
        results.append((f"Ask: {question}", any(w in plain for w in expect)))

    section("Summary")
    for label, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} passed")


if __name__ == "__main__":
    main()
