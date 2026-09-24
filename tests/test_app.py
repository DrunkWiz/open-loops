"""UI smoke tests: drive the real Streamlit script with a fake model (no network)."""

import copy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.llm

from .fakes import FakeLLM

APP = str(Path(__file__).parent.parent / "app.py")


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(core.llm, "LLM", FakeLLM)
    monkeypatch.setattr(core.llm, "list_chat_models", lambda key: ["nvidia/nemotron-3-super-120b-a12b"])
    monkeypatch.setenv("NEBIUS_API_KEY", "")  # load_dotenv won't override an existing variable
    monkeypatch.delenv("WORKSPACE_FILE", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def set_key(at):
    next(t for t in at.sidebar.text_input if t.label == "Nebius API key").set_value("test-key").run()


def click(at, text):
    next(b for b in at.button if text in b.label).click().run()


def run_live_demo(at):
    click(at, "Run it live")
    at.run()  # the live demo runs on the rerun after the click


def nav(at, page):
    at.session_state["nav_next"] = page
    at.run()


def test_renders_without_key(app):
    assert not app.exception
    assert "Open Loops" in app.markdown[1].value
    next(b for b in app.button if "Analyze" in b.label).click().run()  # no key yet
    assert any("API key" in w.value for w in app.warning)


def test_every_page_renders_empty(app):
    for page in ["today", "loops", "changes", "ask", "docs", "add"]:
        nav(app, page)
        assert not app.exception, page


def test_demo_story_end_to_end(app):
    set_key(app)
    run_live_demo(app)
    assert not app.exception
    ws = app.session_state["ws"]
    assert len(ws["documents"]) == 3
    assert app.session_state["nav"] == "changes"
    pending = [p for p in ws["proposals"] if p["state"] == "pending"]
    assert len(pending) == 2

    accept = next(b for b in app.button if b.label == "✅ Accept")
    accept.click().run()
    assert not app.exception
    assert len([p for p in app.session_state["ws"]["proposals"] if p["state"] == "pending"]) == 1

    for page in ["today", "loops", "ask", "docs"]:
        nav(app, page)
        assert not app.exception, page


def test_add_document_from_example(app):
    set_key(app)
    app.selectbox(key="sample_pick").set_value("Mobile v3 kickoff plan (email)")
    next(b for b in app.button if b.label == "Load example").click().run()
    assert "Hamid" in app.session_state["doc_text"]
    assert app.session_state["user_name"] == "Grace"

    next(b for b in app.button if "Analyze" in b.label).click().run()
    assert not app.exception
    ws = app.session_state["ws"]
    assert len(ws["documents"]) == 1 and len(ws["commitments"]) == 3
    assert ws["documents"][0]["briefing"].startswith("### TL;DR")
    assert app.session_state["doc_text"] == ""  # input cleared after adding


def test_mark_done_and_followup(app):
    set_key(app)
    run_live_demo(app)
    nav(app, "loops")
    box = app.checkbox[0]
    box.check().run()
    assert not app.exception
    assert any(c["status"] == "done" for c in app.session_state["ws"]["commitments"])

    draft = next(b for b in app.button if b.label.startswith("Draft message"))
    draft.click().run()
    assert not app.exception
    assert app.session_state["followups"]


def test_ask_page(app):
    set_key(app)
    run_live_demo(app)
    nav(app, "ask")
    next(t for t in app.text_input if t.label.startswith("Ask")).set_value("When is onboarding due?")
    next(b for b in app.button if "Ask" in b.label).click().run()
    assert not app.exception
    assert app.session_state["qa"][0]["q"] == "When is onboarding due?"


DEMO_FILE = Path(__file__).parent.parent / "samples" / "demo_workspace.json"


@pytest.mark.skipif(not DEMO_FILE.exists(), reason="run `python -m scripts.build_demo` first")
def test_demo_loads_without_key(app):
    assert not any("Run it live" in b.label for b in app.button)  # no key: no live option
    click(app, "Show me Grace's week")
    assert not app.exception
    ws = app.session_state["ws"]
    assert len(ws["documents"]) == 3 and ws["commitments"]
    assert app.session_state["nav"] == "changes"
    assert app.session_state["user_name"] == "Grace"
    for page in ["today", "loops", "changes", "ask", "docs"]:
        nav(app, page)
        assert not app.exception, page


def test_quotes_are_html_escaped(app):
    set_key(app)
    run_live_demo(app)
    ws = app.session_state["ws"]
    ws["commitments"][0]["sources"][0]["quote"] = '<img src=x onerror="alert(1)">'
    nav(app, "loops")
    assert not app.exception
    rendered = " ".join(m.value for m in app.markdown)
    assert "<img" not in rendered and "&lt;img" in rendered


def test_server_key_is_never_sent_to_the_browser(monkeypatch):
    monkeypatch.setattr(core.llm, "LLM", FakeLLM)
    monkeypatch.setattr(core.llm, "list_chat_models", lambda key: ["nvidia/nemotron-3-super-120b-a12b"])
    monkeypatch.setenv("NEBIUS_API_KEY", "server-secret-123")
    monkeypatch.delenv("WORKSPACE_FILE", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert at.session_state["api_key"] == "server-secret-123"  # the app can use it...
    assert all(t.value != "server-secret-123" for t in at.text_input)  # ...but no widget holds it
    assert any("Run it live" in b.label for b in at.button)


@pytest.mark.skipif(not DEMO_FILE.exists(), reason="run `python -m scripts.build_demo` first")
def test_undo_on_changes_page(app):
    click(app, "Show me Grace's week")
    before = copy.deepcopy(app.session_state["ws"]["commitments"])
    click(app, "Accept")
    assert len([p for p in app.session_state["ws"]["proposals"] if p["state"] == "pending"]) == 3
    click(app, "Undo")
    assert not app.exception
    assert len([p for p in app.session_state["ws"]["proposals"] if p["state"] == "pending"]) == 4
    assert app.session_state["ws"]["commitments"] == before


def test_shared_key_limits(monkeypatch):
    monkeypatch.setattr(core.llm, "LLM", FakeLLM)
    monkeypatch.setattr(core.llm, "list_chat_models", lambda key: ["nvidia/nemotron-3-super-120b-a12b"])
    monkeypatch.setenv("NEBIUS_API_KEY", "server-secret-123")
    monkeypatch.setenv("MAX_AI_ACTIONS_PER_SESSION", "3")
    monkeypatch.setenv("MAX_DOC_CHARS", "500")
    monkeypatch.delenv("WORKSPACE_FILE", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert any("3 of 3" in c.value for c in at.sidebar.caption)

    # Too long for the shared key: refused before any AI call.
    at.session_state["doc_text"] = "x" * 600
    at.run()
    click(at, "Analyze")
    assert any("500" in w.value for w in at.warning)
    assert not at.session_state["ws"]["documents"]

    # A normal analysis with a briefing costs 2 of the 3 actions...
    at.session_state["doc_text"] = "Hamid: onboarding flow code complete by Oct 9."
    at.run()
    click(at, "Analyze")
    assert at.session_state["ai_used"] == 2

    # ...so a second one is refused.
    at.session_state["doc_text"] = "Jess: dark mode QA pass complete by Oct 13."
    at.run()
    click(at, "Analyze")
    assert any("free AI actions" in w.value for w in at.warning)
    assert len(at.session_state["ws"]["documents"]) == 1


def test_own_key_has_no_limits(monkeypatch):
    monkeypatch.setattr(core.llm, "LLM", FakeLLM)
    monkeypatch.setattr(core.llm, "list_chat_models", lambda key: ["m"])
    monkeypatch.setenv("NEBIUS_API_KEY", "server-secret-123")
    monkeypatch.setenv("MAX_AI_ACTIONS_PER_SESSION", "1")
    monkeypatch.delenv("WORKSPACE_FILE", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    next(t for t in at.sidebar.text_input if "own key" in t.label).set_value("visitor-key").run()
    assert at.session_state["using_shared_key"] is False
    for i in range(2):
        at.session_state["doc_text"] = f"Task {i}: Hamid ships it by Oct 9."
        at.run()
        click(at, "Analyze")
    assert len(at.session_state["ws"]["documents"]) == 2


def test_streamed_text_is_stored_unescaped(app, monkeypatch):
    def dollar_stream(self, system, user, max_tokens=4096):
        yield from ["Budget is ", "$220K", "."]

    monkeypatch.setattr(FakeLLM, "stream", dollar_stream)
    set_key(app)
    app.session_state["doc_text"] = "Budget for the quarter is $220K. Hamid ships by Oct 9."
    app.run()
    click(app, "Analyze")
    briefing = app.session_state["ws"]["documents"][0]["briefing"]
    assert briefing == "Budget is $220K."  # stored as written, escaped only when displayed


@pytest.mark.skipif(not DEMO_FILE.exists(), reason="run `python -m scripts.build_demo` first")
def test_stat_cards_open_filtered_views(app):
    click(app, "Show me Grace's week")
    ws = app.session_state["ws"]

    click(app, "Owed to me")
    assert app.session_state["nav"] == "loops" and app.session_state["f_who"] == "owed"

    click(app, "Overdue")
    assert app.session_state["f_overdue"] is True
    assert any("overdue only (1)" in b.label for b in app.button)  # Grace's one overdue item
    assert any("taco place" in m.value for m in app.markdown)

    click(app, "show all")
    assert app.session_state["f_overdue"] is False

    click(app, "Changes to review")
    assert app.session_state["nav"] == "changes"
    click(app, "Documents")
    assert app.session_state["nav"] == "docs"
    assert not app.exception
