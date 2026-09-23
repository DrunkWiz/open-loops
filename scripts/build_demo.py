"""Run the 3-document demo for real and save the result, so the app can load it without an API key.

Usage (needs NEBIUS_API_KEY in .env):
    python -m scripts.build_demo

Writes samples/demo_workspace.json. Rerun it after changing prompts or the demo documents.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import workspace as wsx  # noqa: E402
from core.changes import analyze, apply_analysis  # noqa: E402
from core.extract import User  # noqa: E402
from core.llm import FALLBACK_MODELS, LLM  # noqa: E402
from core.prompts import BRIEFING  # noqa: E402
from core.writer import briefing_input  # noqa: E402

SAMPLES = ROOT / "samples"
OUT = SAMPLES / "demo_workspace.json"


def main() -> None:
    load_dotenv(ROOT / ".env")
    key = os.getenv("NEBIUS_API_KEY", "")
    if not key or key.startswith("your_"):
        sys.exit("Set NEBIUS_API_KEY in .env first.")
    catalog = json.loads((SAMPLES / "catalog.json").read_text(encoding="utf-8"))
    story = catalog["demo_story"]
    by_file = {s["file"]: s for s in catalog["samples"]}
    model = FALLBACK_MODELS[0]
    extract_llm = LLM(key, model, temperature=0.1)
    write_llm = LLM(key, model, temperature=0.3)

    user = User(story["user"], story["aliases"])
    ws = wsx.new_workspace(user.name, user.aliases)
    for file in story["files"]:
        sample = by_file[file]
        doc_date = date.fromisoformat(sample["date"])
        text = (SAMPLES / file).read_text(encoding="utf-8")
        print(f"Analyzing {sample['title']}…", flush=True)
        analysis = analyze(extract_llm, text, doc_date, user, ws["commitments"])
        doc = wsx.add_document(ws, sample["title"], doc_date, text, source="example")
        summary = apply_analysis(ws, doc, analysis)
        briefing, _ = write_llm.complete(BRIEFING, briefing_input(write_llm, doc["title"], doc["date"], text))
        doc["briefing"] = briefing
        print(f"  {len(summary['added'])} new, {len(summary['merged'])} merged, "
              f"{len(summary['proposals'])} proposals · {analysis.usage.label() if analysis.usage else ''}")

    ws["demo"] = {"generated": date.today().isoformat(), "model": model, "as_of": story["as_of"]}
    wsx.save_file(ws, OUT)
    print(f"Saved {OUT.relative_to(ROOT)}: {len(ws['commitments'])} commitments, "
          f"{len(wsx.pending_proposals(ws))} pending changes.")


if __name__ == "__main__":
    main()
