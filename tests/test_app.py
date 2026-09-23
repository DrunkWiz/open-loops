"""UI smoke tests: drive the real Streamlit script with a fake model (no network)."""

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
    demo = next(b for b in app.button if "demo" in b.label.lower())
    demo.click().run()  # prepares the demo
    app.run()  # runs it, then navigates to Changes
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
    next(b for b in app.button if "demo" in b.label.lower()).click().run()
    app.run()
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
    next(b for b in app.button if "demo" in b.label.lower()).click().run()
    app.run()
    nav(app, "ask")
    next(t for t in app.text_input if t.label.startswith("Ask")).set_value("When is onboarding due?")
    next(b for b in app.button if "Ask" in b.label).click().run()
    assert not app.exception
    assert app.session_state["qa"][0]["q"] == "When is onboarding due?"


DEMO_FILE = Path(__file__).parent.parent / "samples" / "demo_workspace.json"


@pytest.mark.skipif(not DEMO_FILE.exists(), reason="run `python -m scripts.build_demo` first")
def test_demo_loads_without_key(app):
    demo = next(b for b in app.button if "demo" in b.label.lower())
    assert "Load" in demo.label
    demo.click().run()
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
    next(b for b in app.button if "demo" in b.label.lower()).click().run()
    app.run()
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
    assert any("demo live" in b.label for b in at.button)
