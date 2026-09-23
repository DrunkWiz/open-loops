"""Score Open Loops against hand-labelled expectations on the sample documents.

Usage (needs NEBIUS_API_KEY in .env or the environment):
    python -m evals.run_evals                      # default model
    python -m evals.run_evals --model MiniMaxAI/MiniMax-M3

Writes a Markdown report to evals/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import workspace as wsx  # noqa: E402
from core.changes import analyze, apply_analysis  # noqa: E402
from core.extract import User, extract_commitments  # noqa: E402
from core.llm import FALLBACK_MODELS, LLM  # noqa: E402
from core.prompts import QA, document_block, today_line  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
CASES = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))


def read(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def mentions(task: str, words: list[str]) -> bool:
    return any(w.lower() in task.lower() for w in words)


def first_name(name: str) -> str:
    return re.split(r"[\s._(]", name.strip().lower())[0]


def owner_matches(owner: str, expected: str | None) -> bool:
    return expected is None or first_name(owner) == first_name(expected)


def check_extraction(items: list[dict], check: dict) -> tuple[bool, str]:
    if "not_open" in check:
        bad = [i for i in items if i["status"] == "open" and mentions(i["task"], check["not_open"])
               and not mentions(i["task"], check.get("unless_any", []))]
        return not bad, f"no open item about {check['not_open'][0]}" + (f" (found: {bad[0]['task']})" if bad else "")
    found = [i for i in items if mentions(i["task"], check["task_any"]) and owner_matches(i["owner"], check.get("owner"))]
    label = f"{check.get('owner', 'someone')}: {check['task_any'][0]}" + (f" due {check['due']}" if "due" in check else "")
    if not found:
        return False, f"{label} (not found)"
    if "due" in check and all(i["due_date"] != check["due"] for i in found):
        return False, f"{label} (got {', '.join(str(i['due_date']) for i in found)})"
    return True, label


def run(model: str, api_key: str, thinking: bool = True) -> str:
    llm = LLM(api_key, model, temperature=0.1, thinking=thinking)
    lines, passed, total, quotes, verified = [], 0, 0, 0, 0
    started = time.perf_counter()

    lines.append("## Commitment extraction\n")
    for case in CASES["extraction"]:
        text = read(case["sample"])
        result = extract_commitments(llm, text, date.fromisoformat(case["date"]), User(case["user"]))
        quotes += len(result.items)
        verified += sum(1 for i in result.items if i["verified"])
        lines.append(f"**{case['sample']}** ({len(result.items)} items)\n")
        for check in case["checks"]:
            ok, label = check_extraction(result.items, check)
            passed, total = passed + ok, total + 1
            lines.append(f"- {'✅' if ok else '❌'} {label}" + (f" — *{check['why']}*" if check.get("why") else ""))
        lines.append("")

    lines.append("## Cross-document change detection\n")
    for case in CASES["changes"]:
        user = User(case["user"], case.get("aliases", []))
        ws = wsx.new_workspace(user.name, user.aliases)
        for name, doc_date in case["sequence"]:
            text = read(name)
            doc = wsx.add_document(ws, name, date.fromisoformat(doc_date), text)
            apply_analysis(ws, doc, analyze(llm, text, date.fromisoformat(doc_date), user, ws["commitments"]))
        proposals = wsx.pending_proposals(ws)
        lines.append(f"**{' → '.join(n for n, _ in case['sequence'])}** ({len(proposals)} proposals)\n")
        for check in case["checks"]:
            hits = [p for p in proposals if p["relation"] == check["relation"]
                    and mentions(wsx.get_commitment(ws, p["commitment_id"])["task"], check["task_any"])]
            if "new_due" in check:
                hits = [p for p in hits if p["changes"].get("due_date", [None, None])[1] == check["new_due"]]
            ok = bool(hits)
            passed, total = passed + ok, total + 1
            lines.append(f"- {'✅' if ok else '❌'} {check['relation']}: {check['task_any'][0]} — *{check['why']}*")
        lines.append("")

    lines.append("## Grounded Q&A\n")
    for case in CASES["qa"]:
        context = document_block(case["sample"], case["date"], read(case["sample"]))
        answer, _ = llm.complete(QA, f"{today_line(date.fromisoformat(case['date']))}\n\n{context}\n\n"
                                     f"Question: {case['question']}", temperature=0.1)
        low = answer.lower()
        ok = any(w in low for w in case["expect_any"]) and not any(w in low for w in case["reject_any"])
        passed, total = passed + ok, total + 1
        lines.append(f"- {'✅' if ok else '❌'} *{case['question']}* → {answer.strip().splitlines()[0][:160]}")
    lines.append("")

    minutes = (time.perf_counter() - started) / 60
    grounding = f"{verified}/{quotes} ({verified / quotes:.0%})" if quotes else "n/a"
    header = [
        "# Evaluation results\n",
        f"- **Model:** `{model}` on Nebius Token Factory ({'reasoning on' if thinking else 'fast mode'})",
        f"- **Run:** {date.today().isoformat()}, {minutes:.1f} min",
        f"- **Score:** **{passed}/{total} checks passed ({passed / total:.0%})**",
        f"- **Source grounding:** {grounding} extracted items had a quote found verbatim in the document\n",
        "Checks are hand-written expectations for the files in `samples/` (see `evals/cases.json`).\n",
    ]
    return "\n".join(header + lines)


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=FALLBACK_MODELS[0])
    parser.add_argument("--fast", action="store_true", help="skip the model's reasoning step")
    args = parser.parse_args()
    key = os.getenv("NEBIUS_API_KEY", "")
    if not key or key.startswith("your_"):
        sys.exit("Set NEBIUS_API_KEY in .env first.")
    report = run(args.model, key, thinking=not args.fast)
    out = Path(__file__).parent / ("RESULTS_fast.md" if args.fast else "RESULTS.md")
    out.write_text(report, encoding="utf-8")
    print(report.split("## ")[0])
    print(f"Full report: {out}")


if __name__ == "__main__":
    main()
