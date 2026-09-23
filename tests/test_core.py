from datetime import date
from pathlib import Path

from core import workspace as wsx
from core.brief import build_brief, relative_due
from core.calendar import to_ics
from core.changes import analyze, apply_analysis
from core.extract import User, direction, normalize_item, verify_quote
from core.ingest import chunk_text, guess_doc_date, guess_title
from core.llm import _visible_prefix, clean, extract_json

from .fakes import FakeLLM

SAMPLES = Path(__file__).parent.parent / "samples"
GRACE = User("Grace", ["Grace Liu"])


# --- llm helpers ------------------------------------------------------------ #
def test_clean_strips_reasoning_and_citations():
    assert clean("<think>hmm</think>\n### TL;DR\nDone.【from document】") == "### TL;DR\nDone."
    assert clean("reasoning here</think>Answer") == "Answer"


def test_visible_prefix_holds_back_partial_think_tag():
    assert _visible_prefix("Hello <thi") == "Hello"
    assert _visible_prefix("<think>secret") == ""
    assert _visible_prefix("<think>x</think>Shown") == "Shown"


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"items": []}\n```') == {"items": []}
    assert extract_json('[1, 2]') == [1, 2]


# --- ingest ----------------------------------------------------------------- #
def test_guess_doc_date_and_title():
    text = (SAMPLES / "conflicting_updates_email.txt").read_text(encoding="utf-8")
    assert guess_doc_date(text) == date(2026, 9, 22)
    assert guess_title(text).startswith("Mobile app v3")
    assert guess_doc_date("no dates here") is None


def test_chunk_text_respects_limit_and_keeps_everything():
    text = "\n\n".join(f"Paragraph {i} " + "x" * 300 for i in range(100))
    chunks = chunk_text(text, max_chars=2000)
    assert len(chunks) > 1
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == text


# --- extraction helpers ----------------------------------------------------- #
def test_verify_quote():
    doc = "Hamid: onboarding flow code complete by Oct 6. (Previously I said Oct 9 — it's Oct 6 now.)"
    assert verify_quote("Hamid: onboarding flow code complete by Oct 6.", doc)
    assert verify_quote("hamid: onboarding flow   code complete ... it's Oct 6 now", doc)
    assert not verify_quote("Hamid will ship offline sync on Oct 12", doc)
    assert not verify_quote("", doc)


def test_normalize_item_validates_fields():
    item = normalize_item({"task": " Do it ", "owner": "", "due_date": "Oct 5", "priority": "urgent",
                           "status": "maybe", "requester": "null"})
    assert item == {"task": "Do it", "owner": "Unassigned", "requester": None, "due_date": None,
                    "due_text": None, "priority": "Medium", "status": "open", "change_note": None,
                    "source_quote": ""}
    assert normalize_item({"task": ""}) is None


def test_user_matching_and_direction():
    assert GRACE.matches("grace") and GRACE.matches("Grace Liu") and GRACE.matches("grace.liu")
    assert not GRACE.matches("Hamid") and not GRACE.matches(None)
    speaker = User("Speaker 2")
    assert speaker.matches("Speaker 2") and not speaker.matches("Speaker 1")
    assert direction({"owner": "Grace", "requester": None}, GRACE) == "mine"
    assert direction({"owner": "Hamid", "requester": "Grace Liu"}, GRACE) == "owed"
    assert direction({"owner": "Hamid", "requester": "Jess"}, GRACE) == "others"


# --- pipeline: extraction, change detection, proposals ---------------------- #
def build_story():
    ws = wsx.new_workspace("Grace", ["Grace Liu"])
    llm = FakeLLM()
    kickoff_text = (SAMPLES / "mobile_v3_kickoff_email.txt").read_text(encoding="utf-8")
    update_text = (SAMPLES / "conflicting_updates_email.txt").read_text(encoding="utf-8")

    doc1 = wsx.add_document(ws, "Kickoff", date(2026, 9, 15), kickoff_text)
    first = apply_analysis(ws, doc1, analyze(llm, kickoff_text, date(2026, 9, 15), GRACE, ws["commitments"]))
    doc2 = wsx.add_document(ws, "Scope change", date(2026, 9, 22), update_text)
    second = apply_analysis(ws, doc2, analyze(llm, update_text, date(2026, 9, 22), GRACE, ws["commitments"]))
    return ws, first, second


def test_first_document_adds_verified_commitments():
    ws, first, _ = build_story()
    assert len(first["added"]) == 3
    onboarding = wsx.get_commitment(ws, first["added"][0])
    assert onboarding["sources"][0]["verified"] is True
    assert onboarding["due_date"] == "2026-10-09"


def test_second_document_proposes_changes_instead_of_editing():
    ws, first, second = build_story()
    assert len(second["proposals"]) == 2  # deadline change + cancellation
    assert len(second["added"]) == 1  # the new CS briefing task
    onboarding = wsx.get_commitment(ws, first["added"][0])
    assert onboarding["due_date"] == "2026-10-09"  # unchanged until accepted

    by_relation = {p["relation"]: p for p in wsx.pending_proposals(ws)}
    assert by_relation["update"]["changes"]["due_date"] == ["2026-10-09", "2026-10-06"]
    assert by_relation["cancelled"]["changes"]["status"] == ["open", "cancelled"]


def test_accepting_and_ignoring_proposals():
    ws, first, _ = build_story()
    update = next(p for p in wsx.pending_proposals(ws) if p["relation"] == "update")
    cancel = next(p for p in wsx.pending_proposals(ws) if p["relation"] == "cancelled")

    wsx.resolve_proposal(ws, update["id"], accept=True)
    onboarding = wsx.get_commitment(ws, first["added"][0])
    assert onboarding["due_date"] == "2026-10-06"
    assert onboarding["history"][0]["old"] == "2026-10-09"
    assert len(onboarding["sources"]) == 2

    before = len(ws["commitments"])
    wsx.resolve_proposal(ws, cancel["id"], accept=False)  # ignoring a cancellation adds nothing
    assert len(ws["commitments"]) == before
    assert wsx.get_commitment(ws, first["added"][1])["status"] == "open"
    assert not wsx.pending_proposals(ws)


def test_unmatched_cancellation_is_not_added():
    ws = wsx.new_workspace("Grace")
    text = (SAMPLES / "conflicting_updates_email.txt").read_text(encoding="utf-8")
    doc = wsx.add_document(ws, "Scope change", date(2026, 9, 22), text)
    summary = apply_analysis(ws, doc, analyze(FakeLLM(), text, date(2026, 9, 22), GRACE, []))
    assert summary["cancelled_unmatched"] == 1
    assert all(c["status"] != "cancelled" for c in ws["commitments"])


# --- brief, calendar, persistence ------------------------------------------- #
def test_brief_sections():
    ws, _, _ = build_story()
    brief = build_brief(ws, GRACE, date(2026, 9, 23))
    assert [c["task"] for c in brief["mine_overdue"]] == ["Send weekly status update to leadership"]
    assert [c["task"] for c in brief["mine_upcoming"]] == ["Brief the customer success team on the scope change"]
    assert brief["counts"]["pending"] == 2
    assert brief["counts"]["owed"] == 2  # Hamid's two items, requested by Grace


def test_relative_due_labels():
    item = {"due_date": "2026-09-25", "due_text": "by Friday"}
    assert relative_due(item, date(2026, 9, 23)) == "Fri Sep 25 · in 2 days"
    assert relative_due(item, date(2026, 9, 25)).endswith("today")
    assert relative_due(item, date(2026, 9, 26)).endswith("1 day overdue")
    assert relative_due({"due_date": None, "due_text": "EOD"}, date(2026, 9, 23)) == "EOD"


def test_ics_export():
    ws, _, _ = build_story()
    ics = to_ics(ws["commitments"])
    assert ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR")
    assert ics.count("BEGIN:VEVENT") == 4
    assert "DTSTART;VALUE=DATE:20261009" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_workspace_roundtrip_and_remove(tmp_path):
    ws, _, _ = build_story()
    path = tmp_path / "ws.json"
    wsx.save_file(ws, path)
    loaded = wsx.load_file(path)
    assert loaded == ws

    doc2 = ws["documents"][1]["id"]
    wsx.remove_document(loaded, doc2)
    assert len(loaded["documents"]) == 1
    assert not loaded["proposals"]
    assert len(loaded["commitments"]) == 3


def test_cancellations_match_by_feature_keyword():
    from core.changes import candidates_for

    existing = [{"id": "a", "task": "Apple Watch companion app feature-complete", "owner": "Wen", "status": "open"},
                {"id": "b", "task": "Complete offline sync integration code", "owner": "Hamid", "status": "open"}]
    assert [c["id"] for c in candidates_for(
        {"task": "Apple Watch support", "owner": "Unassigned", "status": "cancelled"}, existing)] == ["a"]
    assert candidates_for({"task": "Order the cake", "owner": "Dad", "status": "open"}, existing) == []


def test_stream_hides_citation_markers():
    assert _visible_prefix("Due Oct 6 【Mobile v3") == "Due Oct 6"
    assert _visible_prefix("Due Oct 6 【Mobile v3 (email)】.") == "Due Oct 6."


def test_undo_restores_accept_and_ignore():
    import copy as _copy

    ws, first, _ = build_story()
    before = _copy.deepcopy(ws["commitments"])
    update = next(p for p in wsx.pending_proposals(ws) if p["relation"] == "update")
    wsx.resolve_proposal(ws, update["id"], accept=True)
    assert wsx.get_commitment(ws, first["added"][0])["due_date"] == "2026-10-06"
    assert wsx.undo_proposal(ws, update["id"])
    assert ws["commitments"] == before and update["state"] == "pending"

    # Ignoring adds the new item separately; undo removes it again.
    wsx.resolve_proposal(ws, update["id"], accept=False)
    assert len(ws["commitments"]) == len(before) + 1
    assert wsx.undo_proposal(ws, update["id"])
    assert ws["commitments"] == before


def test_undo_blocked_after_later_edits():
    ws, first, _ = build_story()
    update = next(p for p in wsx.pending_proposals(ws) if p["relation"] == "update")
    wsx.resolve_proposal(ws, update["id"], accept=True)
    wsx.set_status(ws, first["added"][0], "done")  # the user changed it afterwards
    assert not wsx.can_undo(ws, update)
    assert not wsx.undo_proposal(ws, update["id"])
    assert wsx.get_commitment(ws, first["added"][0])["status"] == "done"
