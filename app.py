"""
Open Loops — your AI chief of staff for every promise you make, and every promise made to you.
Built for the Nebius x NVIDIA Global AI Hackathon (Personal AI Track).

Feed it anything you already have (emails, meeting notes, Slack threads, family group chats,
voice-memo transcripts) and it keeps ONE ledger of commitments across all of them: who owes
what to whom, by when, with the exact source sentence, and it flags when a newer document
changes an older plan. Runs on open models (NVIDIA Nemotron first) via Nebius Token Factory.
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import os
import queue
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, AuthenticationError, NotFoundError, RateLimitError

from core import workspace as wsx
from core.brief import brief_as_text, build_brief, due, relative_due, sort_by_due
from core.calendar import to_ics
from core.changes import analyze, apply_analysis
from core.extract import User, direction
from core.ingest import SUPPORTED_TYPES, guess_doc_date, guess_title, read_upload
from core.llm import FALLBACK_MODELS, LLM, list_chat_models
from core.prompts import BRIEFING, FOLLOWUP, MORNING, QA, today_line
from core.writer import briefing_input, followup_items, followup_listing, qa_context

load_dotenv()

APP_DIR = Path(__file__).parent
SAMPLES_DIR = APP_DIR / "samples"
CATALOG = json.loads((SAMPLES_DIR / "catalog.json").read_text(encoding="utf-8"))
SAMPLES = {s["title"]: s for s in CATALOG["samples"]}
DEMO_FILE = SAMPLES_DIR / "demo_workspace.json"  # built by `python -m scripts.build_demo`
WORKSPACE_FILE = os.getenv("WORKSPACE_FILE")  # optional: persist to a local JSON file

PAGES = ["add", "today", "loops", "changes", "ask", "docs"]
DIRECTION_LABEL = {"mine": "I owe", "owed": "Owed to me", "others": "Others"}
PRIORITY_ICON = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}
SUGGESTED_QUESTIONS = [
    "What do I owe people this week?",
    "Who am I waiting on, and for what?",
    "What changed in the plan recently?",
    "What's still undecided?",
]

st.set_page_config(page_title="Open Loops", page_icon="🔁", layout="wide")

st.markdown(
    """
<style>
.block-container {padding-top: 2.5rem; max-width: 1200px;}
.hero {padding: 1.4rem 1.6rem; border-radius: 16px; margin-bottom: 1.75rem;
       background: linear-gradient(120deg, #0B1F0A 0%, #1B3A12 55%, #3F6B12 100%); color: #F4FBEA;}
.hero h1 {margin: 0; padding: 0; font-size: 2.1rem; color: #FFFFFF;}
.hero p {margin: .35rem 0 0 0; font-size: 1.02rem; opacity: .92;}
.hero code {background: rgba(255,255,255,.12); color: #DDF5B8;}
.hero .tag {display: inline-block; font-size: .75rem; padding: .15rem .6rem; border-radius: 999px;
            background: rgba(118,185,0,.25); border: 1px solid rgba(118,185,0,.6); margin-bottom: .6rem;}
.metric {border: 1px solid rgba(128,128,128,.25); border-radius: 12px; padding: .9rem 1.1rem;}
.metric .n {font-size: 1.6rem; font-weight: 700; line-height: 1.1;}
.metric .l {font-size: .8rem; opacity: .7;}
.metric.alert .n {color: #D9480F;}
.quote {border-left: 3px solid #76B900; padding: .2rem .7rem; margin: .3rem 0; font-style: italic; opacity: .9;}
</style>
""",
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_initial_workspace() -> dict:
    if WORKSPACE_FILE:
        try:
            loaded = wsx.load_file(WORKSPACE_FILE)
            if loaded:
                return loaded
        except (OSError, ValueError):
            pass
    return wsx.new_workspace()


ss = st.session_state
if "ws" not in ss:
    ss.ws = load_initial_workspace()
    ss.user_name = ss.ws["user"]["name"]
    ss.user_aliases = ", ".join(ss.ws["user"]["aliases"])
ss.setdefault("nav", "add")
ss.setdefault("as_of", date.today())
ss.setdefault("last_result", None)
ss.setdefault("qa", [])
ss.setdefault("morning", None)
ss.setdefault("followups", {})
ss.setdefault("doc_text", "")
ss.setdefault("doc_title", "")
ss.setdefault("doc_date", date.today())
ss.setdefault("flash", None)
if "nav_next" in ss:  # navigation requested by a button in the previous run
    ss.nav = ss.pop("nav_next")
if ss.pop("clear_input", False):  # after a document is added; must run before the widgets render
    ss.doc_text, ss.doc_title = "", ""

ws: dict = ss.ws


def persist() -> None:
    if WORKSPACE_FILE:
        try:
            wsx.save_file(ws, WORKSPACE_FILE)
        except OSError as e:
            st.toast(f"Couldn't save workspace file: {e}", icon="⚠️")


def current_user() -> User:
    return User(ws["user"]["name"] or "Me", list(ws["user"]["aliases"]))


def go(page: str) -> None:
    ss.nav_next = page


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def md(text: str | None) -> str:
    """Escape $ so Streamlit doesn't render currency as LaTeX math."""
    return (text or "").replace("$", r"\$")


def quote_html(text: str | None) -> str:
    """A source quote as a styled block. Escaped: quotes come from user documents and model output."""
    return f'<div class="quote">“{html.escape(text or "") or "—"}”</div>'


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "no date"
    d = date.fromisoformat(iso)
    return f"{d:%a %b} {d.day}"


def friendly_error(e: Exception) -> str:
    if isinstance(e, AuthenticationError):
        return "🔑 Nebius rejected the API key. Check the key in the sidebar."
    if isinstance(e, RateLimitError):
        return "⏳ Nebius rate limit reached. Wait a few seconds and try again."
    if isinstance(e, APIConnectionError):
        return "🌐 Couldn't reach Nebius Token Factory. Check your internet connection."
    if isinstance(e, NotFoundError):
        return "🤖 That model isn't available on Nebius any more. Pick another in the sidebar."
    if isinstance(e, APIStatusError):
        return f"⚠️ Nebius API error ({e.status_code}): {e.message}"
    if isinstance(e, ValueError):
        return "🧩 The model returned an unexpected format. Try again, or pick another model."
    return f"⚠️ Unexpected error: {e}"


def configured_api_key() -> tuple[str, str | None]:
    """(key, problem) from .env or Streamlit secrets, ignoring the .env.example placeholder.

    Also accepts the key inside a secrets section (e.g. under [general]). `problem` explains
    why secrets exist but couldn't be used, so a misconfigured deployment isn't silent.
    """
    key = os.getenv("NEBIUS_API_KEY", "").strip()
    problem = None
    if not key:
        try:
            secrets = st.secrets
            key = str(secrets.get("NEBIUS_API_KEY", "") or "").strip()
            if not key:  # look one level down, e.g. [general] NEBIUS_API_KEY = "..."
                for value in secrets.values():
                    if hasattr(value, "get") and value.get("NEBIUS_API_KEY"):
                        key = str(value.get("NEBIUS_API_KEY")).strip()
                        break
        except Exception as e:  # noqa: BLE001
            # Streamlit raises the same "not found" error for a missing file (normal locally) and for
            # invalid TOML (usually the key isn't in quotes); only the second is worth a warning.
            if "pars" in str(e).lower():
                problem = ("Streamlit secrets couldn't be read, so the app's key isn't loaded. Use this "
                           'format, with the key in double quotes: `NEBIUS_API_KEY = "your-key"`')
    if key.startswith("your_"):
        key = ""
    return key, problem


@st.cache_data(ttl=3600, show_spinner=False)
def cached_models(api_key: str) -> list[str]:
    return list_chat_models(api_key)  # exceptions aren't cached


def need_key() -> bool:
    if not ss.get("api_key"):
        st.warning("🔑 Add your **Nebius API key** in the sidebar first.")
        return True
    return False


def make_llm(temperature: float | None = None) -> LLM:
    return LLM(ss.api_key, ss.model, ss.temperature if temperature is None else temperature,
               thinking=not ss.get("fast_mode", False))


def stream_md(llm: LLM, system: str, user_msg: str, on_tick=None) -> str:
    """Stream a reply into the page, with a 'thinking' note until the first words arrive.

    `on_tick` is called on every chunk (including while the model is still reasoning), so the
    caller can update other parts of the page, e.g. background progress.
    """
    note = st.empty()
    note.caption("🧠 Thinking it through…" if llm.thinking else "⚡ Writing…")

    raw: list[str] = []

    def chunks():
        cleared = False
        for piece in llm.stream(system, user_msg):
            if on_tick:
                on_tick()
            if not piece:
                continue
            if not cleared:
                note.empty()
                cleared = True
            raw.append(piece)
            yield md(piece)
        note.empty()

    st.write_stream(chunks())
    # Return the unescaped text: callers store it and escape again (with md) when they display it.
    return "".join(raw)


# --------------------------------------------------------------------------- #
# Protecting a shared key (the deployment's own key, e.g. from Streamlit secrets)
# --------------------------------------------------------------------------- #
def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


MAX_AI_ACTIONS_PER_SESSION = env_int("MAX_AI_ACTIONS_PER_SESSION", 15)
MAX_AI_ACTIONS_PER_DAY = env_int("MAX_AI_ACTIONS_PER_DAY", 300)
MAX_DOC_CHARS_SHARED = env_int("MAX_DOC_CHARS", 40_000)


@st.cache_resource
def daily_usage() -> dict:
    """Process-wide counter shared by every visitor, so refreshing the page can't reset it."""
    return {"day": date.today().isoformat(), "count": 0, "lock": threading.Lock()}


def ai_actions_left() -> int:
    return max(0, MAX_AI_ACTIONS_PER_SESSION - ss.get("ai_used", 0))


def allow_ai(cost: int = 1, warn: bool = True) -> bool:
    """Spend `cost` AI actions. Always allowed with the visitor's own key; capped with the shared key."""
    if not ss.get("using_shared_key"):
        return True
    if not warn:
        st_warning = lambda message: None  # noqa: E731
    else:
        st_warning = st.warning
    if cost > ai_actions_left():
        st_warning(f"You've used this session's {MAX_AI_ACTIONS_PER_SESSION} free AI actions on the demo key. "
                   "Paste your own Nebius key in the sidebar to keep going. The saved demo still works.")
        return False
    usage = daily_usage()
    with usage["lock"]:
        today = date.today().isoformat()
        if usage["day"] != today:
            usage["day"], usage["count"] = today, 0
        if usage["count"] + cost > MAX_AI_ACTIONS_PER_DAY:
            st_warning("The demo key has reached today's limit. Paste your own Nebius key in the sidebar, "
                       "or explore the saved demo.")
            return False
        usage["count"] += cost
    ss.ai_used = ss.get("ai_used", 0) + cost
    return True


# --------------------------------------------------------------------------- #
# Callbacks (run before the next render, so they may set widget state)
# --------------------------------------------------------------------------- #
def load_sample() -> None:
    sample = SAMPLES.get(ss.get("sample_pick"))
    if not sample:
        return
    ss.doc_text = (SAMPLES_DIR / sample["file"]).read_text(encoding="utf-8")
    ss.doc_title = sample["title"]
    ss.doc_date = date.fromisoformat(sample["date"])
    if not ss.ws["commitments"]:  # only adopt the sample's persona on an empty workspace
        ss.user_name = sample["user"]
        ss.user_aliases = ", ".join(sample["aliases"])
        ss.as_of = date.fromisoformat(sample["date"])


def on_upload() -> None:
    upload = ss.get("uploader")
    if upload is None:
        return
    try:
        text = read_upload(upload.name, upload.getvalue())
    except Exception as e:  # noqa: BLE001
        ss.flash = ("error", f"Couldn't read {upload.name}: {e}")
        return
    if not text.strip():
        ss.flash = ("warning", f"No text found in {upload.name} (is it a scanned PDF?).")
        return
    ss.doc_text = text
    ss.doc_title = guess_title(text, fallback=upload.name.rsplit(".", 1)[0])
    ss.doc_date = guess_doc_date(text) or date.today()


def toggle_done(cid: str, key: str) -> None:
    wsx.set_status(ss.ws, cid, "done" if ss[key] else "open")
    persist_now()


def resolve(pid: str, accept: bool) -> None:
    wsx.resolve_proposal(ss.ws, pid, accept)
    persist_now()
    st.toast(("Accepted" if accept else "Ignored") + ". You can undo it under Resolved.", icon="✅")


def undo(pid: str) -> None:
    if wsx.undo_proposal(ss.ws, pid):
        persist_now()
        st.toast("Undone: the change is back up for review.", icon="↩️")
    else:
        st.toast("Couldn't undo: that commitment was edited afterwards.", icon="⚠️")


def persist_now() -> None:
    if WORKSPACE_FILE:
        try:
            wsx.save_file(ss.ws, WORKSPACE_FILE)
        except OSError:
            pass


def clear_views() -> None:
    ss.last_result, ss.qa, ss.morning, ss.followups = None, [], None, {}


def reset_workspace() -> None:
    ss.ws = wsx.new_workspace(ss.user_name, [a.strip() for a in ss.user_aliases.split(",") if a.strip()])
    clear_views()
    persist_now()


def import_workspace() -> None:
    upload = ss.get("ws_import")
    if upload is None:
        return
    try:
        ss.ws = wsx.from_json(upload.getvalue().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        ss.flash = ("error", f"Couldn't import workspace: {e}")
        return
    ss.user_name = ss.ws["user"]["name"]
    ss.user_aliases = ", ".join(ss.ws["user"]["aliases"])
    clear_views()
    ss.flash = ("success", "Workspace imported.")
    persist_now()


def prepare_demo() -> None:
    """Live demo with a key; otherwise (or when out of free actions) fall back to the saved results."""
    if not ss.get("api_key"):
        load_saved_demo()
        return
    if not allow_ai(3, warn=False):
        load_saved_demo()
        ss.flash = ("warning", "Not enough free AI actions left for a live run, so the saved demo results "
                               "were loaded instead. Paste your own key in the sidebar to run it live.")
        return
    story = CATALOG["demo_story"]
    ss.user_name = story["user"]
    ss.user_aliases = ", ".join(story["aliases"])
    ss.as_of = date.fromisoformat(story["as_of"])
    ss.ws = wsx.new_workspace(story["user"], story["aliases"])
    clear_views()
    ss.run_demo = True


def load_saved_demo() -> None:
    """Load the demo results saved from a real model run: instant, and no API key needed."""
    try:
        demo = wsx.from_json(DEMO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        ss.flash = ("error", f"Couldn't load the saved demo ({e}). Add an API key to run it live.")
        return
    story = CATALOG["demo_story"]
    ss.ws = demo
    ss.user_name = demo["user"]["name"]
    ss.user_aliases = ", ".join(demo["user"]["aliases"])
    ss.as_of = date.fromisoformat(demo.get("demo", {}).get("as_of", story["as_of"]))
    clear_views()
    meta = demo.get("demo", {})
    ss.flash = ("success", f"Loaded the demo as **Grace**: saved results from a real run of "
                           f"`{meta.get('model', 'the model')}` on Nebius. Start with the changes her latest "
                           "email made to the plan." + ("" if ss.get("api_key") else
                                                        " Add an API key in the sidebar to run it live."))
    go("changes")
    persist_now()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### 🔁 Open Loops")
    # A key from .env / secrets is never put in a widget: password boxes can be revealed in the browser.
    server_key, key_problem = configured_api_key()
    if key_problem:
        st.warning(key_problem)
    if server_key:
        own_key = st.text_input("Use your own key instead (optional)", type="password",
                                help="Get one at tokenfactory.nebius.com. With your own key there are no "
                                     "usage limits.").strip()
        ss.api_key = own_key or server_key
        ss.using_shared_key = not own_key
        if ss.using_shared_key:
            st.caption(f"🔑 Using this app's Nebius key · **{ai_actions_left()} of "
                       f"{MAX_AI_ACTIONS_PER_SESSION}** free AI actions left this session")
        else:
            st.caption("🔑 Using your own key · no limits")
    else:
        ss.using_shared_key = False
        ss.api_key = st.text_input("Nebius API key", type="password",
                                   help="Get one at tokenfactory.nebius.com, or set NEBIUS_API_KEY in .env "
                                        "or Streamlit secrets.").strip()

    models = FALLBACK_MODELS
    if ss.api_key:
        try:
            models = cached_models(ss.api_key) or FALLBACK_MODELS
        except Exception as e:  # noqa: BLE001
            st.error(friendly_error(e))
    ss.model = st.selectbox("Model", models, help="Live list from Nebius Token Factory, NVIDIA Nemotron first.")
    ss.temperature = st.slider("Creativity (temperature)", 0.0, 1.0, 0.3, 0.05,
                               help="Extraction always runs at a low temperature for accuracy.")
    st.toggle("⚡ Fast mode", key="fast_mode",
              help="Skip the model's reasoning step: about 10x faster, but it can miss items. "
                   "Leave off for the most accurate results.")

    st.divider()
    st.markdown("**👤 You are**")
    st.text_input("Your name", key="user_name", help="Used to split commitments into *I owe* and *Owed to me*.")
    st.text_input("Other names you go by", key="user_aliases", placeholder="e.g. Nate, N. Quek, nathan.q",
                  help="Comma-separated. How your name appears in emails, chats and transcripts.")
    ws["user"] = {"name": ss.user_name.strip() or "Me",
                  "aliases": [a.strip() for a in ss.user_aliases.split(",") if a.strip()]}
    st.date_input("📅 As of", key="as_of", help="The day your brief is calculated for. Handy for demos.")

    st.divider()
    st.markdown("**💾 Workspace**")
    st.caption("Private to this browser session" + (f" · autosaves to `{WORKSPACE_FILE}`" if WORKSPACE_FILE else
                                                     ". Export it to keep it."))
    st.download_button("⬇️ Export workspace", wsx.to_json(ws), file_name="open-loops-workspace.json",
                       mime="application/json", width="stretch", disabled=not ws["documents"])
    st.file_uploader("Import workspace", type=["json"], key="ws_import", on_change=import_workspace)
    with st.popover("🗑️ Start over", width="stretch"):
        st.write("Delete all documents and commitments in this workspace?")
        st.button("Yes, start over", on_click=reset_workspace, type="primary")
    st.caption("Inference: Nebius Token Factory · open-weight models, no consumer chatbot history.")

user = current_user()

# --------------------------------------------------------------------------- #
# Header + metrics
# --------------------------------------------------------------------------- #
st.markdown(
    f"""
<div class="hero">
  <span class="tag">🏆 Nebius x NVIDIA Global AI Hackathon · Personal AI Track</span>
  <h1>🔁 Open Loops</h1>
  <p>Your AI chief of staff for every promise you make, and every promise made to you,
  across emails, meetings, chats and notes.</p>
  <p style="font-size:.85rem;opacity:.75">Running <code>{ss.model}</code> on Nebius Token Factory</p>
</div>
""",
    unsafe_allow_html=True,
)

brief = build_brief(ws, user, ss.as_of)
counts = brief["counts"]
metrics = [
    ("Open loops", counts["open"], False),
    ("I owe", counts["mine"], False),
    ("Owed to me", counts["owed"], False),
    ("Overdue", counts["overdue"], counts["overdue"] > 0),
    ("Changes to review", counts["pending"], counts["pending"] > 0),
    ("Documents", counts["documents"], False),
]
for col, (label, n, alert) in zip(st.columns(len(metrics), gap="medium"), metrics):
    col.markdown(f'<div class="metric{" alert" if alert else ""}"><div class="n">{n}</div>'
                 f'<div class="l">{label}</div></div>', unsafe_allow_html=True)

if ss.flash:
    kind, message = ss.flash
    ss.flash = None
    getattr(st, kind)(message)

pending_label = f"🔁 Changes ({counts['pending']})" if counts["pending"] else "🔁 Changes"
nav_labels = {"add": "➕ Add", "today": "☀️ Today", "loops": "📌 Commitments", "changes": pending_label,
              "ask": "💬 Ask", "docs": "📚 Documents"}
st.space("medium")
st.segmented_control("Navigate", PAGES, key="nav", format_func=nav_labels.get, required=True,
                     label_visibility="collapsed", width="stretch")
st.space("medium")


# --------------------------------------------------------------------------- #
# Shared renderers
# --------------------------------------------------------------------------- #
def usage_caption(label: str | None) -> None:
    if label:
        st.caption(label)


def commitment_card(c: dict, prefix: str, show_direction: bool = True) -> None:
    status = c["status"]
    with st.container(border=True):
        left, right = st.columns([0.05, 0.95], vertical_alignment="top")
        key = f"{prefix}_{c['id']}_{status}"
        left.checkbox("Done", value=status == "done", key=key, label_visibility="collapsed",
                      on_change=toggle_done, args=(c["id"], key), disabled=status == "cancelled")
        with right:
            task = md(c["task"])
            st.markdown(f"~~{task}~~" if status != "open" else f"**{task}**")
            meta = f"{PRIORITY_ICON.get(c['priority'], '⚪')} {c['priority']} · 👤 {md(c['owner'])}"
            if c.get("requester"):
                meta += f" → {md(c['requester'])}"
            meta += f" · 📅 {relative_due(c, ss.as_of)}"
            st.caption(meta)
            with st.container(horizontal=True, gap="small"):
                d = due(c)
                if status == "cancelled":
                    st.badge("Dropped", color="gray")
                elif status == "done":
                    st.badge("Done", color="green")
                elif d and d < ss.as_of:
                    st.badge("Overdue", icon="🔥", color="red")
                if show_direction:
                    kind = direction(c, user)
                    st.badge(DIRECTION_LABEL[kind], color={"mine": "blue", "owed": "violet", "others": "gray"}[kind])
                if c.get("change_note"):
                    st.badge(md(c["change_note"])[:60], icon="🔁", color="orange")
                verified = all(s["verified"] for s in c["sources"])
                st.badge("Source verified" if verified else "Check source", icon="✅" if verified else "⚠️",
                         color="green" if verified else "orange")
                with st.popover(f"📎 {len(c['sources'])} source{'s' if len(c['sources']) != 1 else ''}"):
                    for s in c["sources"]:
                        st.markdown(f"**{md(s['doc_title'])}** · {fmt_date(s['doc_date'])}")
                        st.markdown(quote_html(s["quote"]), unsafe_allow_html=True)
                        if not s["verified"]:
                            st.caption("⚠️ This quote couldn't be matched word-for-word in the document.")
                    if c["history"]:
                        st.markdown("**History**")
                        for h in c["history"]:
                            old = fmt_date(h["old"]) if h["field"] == "due_date" else h["old"]
                            new = fmt_date(h["new"]) if h["field"] == "due_date" else h["new"]
                            st.caption(f"{fmt_date(h['doc_date'])} · {h['field'].replace('_', ' ')}: "
                                       f"{md(str(old))} → {md(str(new))} ({md(h['doc_title'])})")


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def run_analysis(title: str, doc_date: date, text: str, with_briefing: bool) -> None:
    """Extract + match in a background thread while the briefing streams in."""
    extract_llm, brief_llm = make_llm(0.1), make_llm()
    existing = [dict(c) for c in ws["commitments"]]
    started = time.perf_counter()
    steps: list[str] = [f"📄 Reading *{md(title)}* ({len(text):,} characters)"]
    messages: queue.Queue[str] = queue.Queue()  # filled from the worker thread, shown by this one
    status = st.status("Analyzing your document…", expanded=True)
    status.write(steps[0])

    def show_progress() -> None:
        while True:
            try:
                step = messages.get_nowait()
            except queue.Empty:
                return
            steps.append(step)
            status.write(step)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(analyze, extract_llm, text, doc_date, user, existing, messages.put)
        briefing, brief_usage = None, None
        if with_briefing:
            st.markdown("#### 📋 Briefing")
            try:
                source = briefing_input(brief_llm, title, doc_date.isoformat(), text)
                briefing = stream_md(brief_llm, BRIEFING, source, on_tick=show_progress)
                brief_usage = brief_llm.last_usage
            except Exception as e:  # noqa: BLE001
                st.warning(f"Briefing skipped: {friendly_error(e)}")
        while not future.done():  # extraction may still be running after the briefing finishes
            show_progress()
            time.sleep(0.2)
        show_progress()
        try:
            analysis = future.result()
        except Exception as e:  # noqa: BLE001
            status.update(label="Analysis failed", state="error")
            st.error(friendly_error(e))
            return
    elapsed = time.perf_counter() - started
    status.update(label=f"Done in {elapsed:.0f}s", state="complete", expanded=False)
    doc = wsx.add_document(ws, title, doc_date, text)
    doc["briefing"] = briefing if isinstance(briefing, str) else None
    summary = apply_analysis(ws, doc, analysis)
    persist()
    ss.last_result = {"doc_id": doc["id"], "summary": summary, "steps": steps, "seconds": elapsed,
                      "usage": [u.label() for u in (brief_usage, analysis.usage) if u]}
    ss.morning = None
    ss.clear_input = True
    st.rerun()


def run_demo_story() -> None:
    story = CATALOG["demo_story"]
    catalog = {s["file"]: s for s in CATALOG["samples"]}
    llm = make_llm(0.1)
    with st.status("Running the demo as Grace…", expanded=True) as status:
        for file in story["files"]:
            sample = catalog[file]
            st.write(f"📄 **{sample['title']}** ({fmt_date(sample['date'])})")
            text = (SAMPLES_DIR / file).read_text(encoding="utf-8")
            try:
                analysis = analyze(llm, text, date.fromisoformat(sample["date"]), current_user(),
                                   [dict(c) for c in ws["commitments"]], progress=st.caption)
            except Exception as e:  # noqa: BLE001
                status.update(label="Demo stopped", state="error")
                st.error(friendly_error(e))
                return
            doc = wsx.add_document(ws, sample["title"], date.fromisoformat(sample["date"]), text, source="example")
            summary = apply_analysis(ws, doc, analysis)
            st.write(f"→ {len(summary['added'])} new, {len(summary['merged'])} already tracked, "
                     f"{len(summary['proposals'])} change(s) flagged · "
                     f"{analysis.usage.label() if analysis.usage else ''}")
        status.update(label="Demo ready", state="complete")
    persist()
    ss.flash = ("success", "Demo loaded as **Grace**. Start with the changes her latest email made to the plan.")
    go("changes")
    st.rerun()


def page_add() -> None:
    if ss.pop("run_demo", False):
        run_demo_story()
        return

    story_help = ("Grace's week: a project kickoff email, a family chat, then an email that changes the "
                  "plan. Shows cross-document change detection. Replaces the current workspace.")
    first_visit = not ws["documents"]
    if first_visit:
        with st.container(border=True):
            st.markdown("### 👋 New here? See it in 10 seconds")
            st.markdown("Follow **Grace**, a product lead, through one week: a project kickoff email, her "
                        "family's group chat about Grandma's 80th, and an email that quietly changes the plan. "
                        "Open Loops tracks every promise across all three and flags what changed.")
            b1, b2, _ = st.columns([2, 2, 3], vertical_alignment="center")
            b1.button("▶ Show me Grace's week", on_click=load_saved_demo, type="primary", width="stretch",
                      help="Instant: results saved from a real run of NVIDIA Nemotron on Nebius. No key needed.",
                      disabled=not DEMO_FILE.exists())
            if ss.api_key:
                b2.button("Run it live (≈1 min)", on_click=prepare_demo, width="stretch",
                          help=story_help + " Analyzes all three documents with the model right now.")
            st.caption("Or add your own document below: an email, meeting notes, a chat export, a PDF.")
        st.space("small")

    with st.container(border=True):
        st.markdown("**Try an example document**")
        c1, c2, c3 = st.columns([3, 1, 2], vertical_alignment="bottom")
        c1.selectbox("Example document", list(SAMPLES), key="sample_pick")
        c2.button("Load example", on_click=load_sample, width="stretch")
        if not first_visit:
            if ss.api_key:
                c3.button("▶ Run the 3-document demo live", on_click=prepare_demo, type="primary",
                          width="stretch", help=story_help + " Takes about a minute with reasoning on.")
            else:
                c3.button("▶ Load the 3-document demo", on_click=load_saved_demo, type="primary",
                          width="stretch", help=story_help + " No API key needed: shows results saved from a "
                                                             "real run.")
            if ss.api_key and DEMO_FILE.exists():
                st.button("or load the saved demo results instantly", on_click=load_saved_demo, type="tertiary")
        if not ss.api_key:
            st.caption("No API key? The demo works without one. Add a key in the sidebar to analyze your own "
                       "documents.")

    st.space("medium")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.text_area("Paste an email, meeting transcript, chat export or notes", key="doc_text", height=300,
                     placeholder="Paste anything with plans, promises or deadlines in it…")
    with right:
        st.file_uploader("…or upload a file", type=SUPPORTED_TYPES, key="uploader", on_change=on_upload,
                         help="Text, Markdown, PDF or Word. Scanned PDFs without a text layer aren't supported.")
        st.text_input("Title", key="doc_title", placeholder="e.g. Q4 planning sync")
        st.date_input("Document date", key="doc_date",
                      help="When it was written. Used to turn 'next Tuesday' into a real date.")
        with_briefing = st.toggle("Also write a briefing", value=True)

    st.space("small")
    if st.button("🔍 Analyze and add to my loops", type="primary", width="stretch"):
        text = ss.doc_text.strip()
        if need_key():
            return
        if not text:
            st.warning("📝 Paste some text, upload a file, or load an example first.")
            return
        if ss.get("using_shared_key") and len(text) > MAX_DOC_CHARS_SHARED:
            st.warning(f"This document is {len(text):,} characters. The demo key handles up to "
                       f"{MAX_DOC_CHARS_SHARED:,}: trim it, or paste your own Nebius key in the sidebar "
                       "for longer documents.")
            return
        if not allow_ai(2 if with_briefing else 1):
            return
        title = ss.doc_title.strip() or guess_title(text)
        run_analysis(title, ss.doc_date, text, with_briefing)

    result = ss.last_result
    doc = wsx.get_doc(ws, result["doc_id"]) if result else None
    if not doc:
        return
    summary = result["summary"]
    st.divider()
    st.markdown(f"### ✅ Added *{md(doc['title'])}*")
    parts = [f"**{len(summary['added'])}** new commitments"]
    if summary["merged"]:
        parts.append(f"**{len(summary['merged'])}** already tracked (linked the new source)")
    if summary["proposals"]:
        parts.append(f"**{len(summary['proposals'])}** changes to earlier plans need your review")
    st.markdown(" · ".join(parts))
    if result.get("steps"):
        with st.expander(f"How it was analyzed · {result['seconds']:.0f}s"):
            for step in result["steps"]:
                st.markdown(step)
    for label in result["usage"]:
        st.caption(label)
    if summary["proposals"]:
        st.button(f"🔁 Review {len(summary['proposals'])} change(s)", on_click=go, args=("changes",), type="primary")
    if doc.get("briefing"):
        with st.expander("📋 Briefing", expanded=True):
            st.markdown(md(doc["briefing"]))
    new_items = [c for c in (wsx.get_commitment(ws, cid) for cid in summary["added"]) if c]
    if new_items:
        st.markdown("#### New commitments")
        for c in sort_by_due(new_items):
            commitment_card(c, "new")


def page_today() -> None:
    if not ws["commitments"]:
        st.info("Nothing tracked yet. Add a document, or run the 3-document demo from **➕ Add**.")
        return
    st.markdown(f"### ☀️ {ss.as_of:%A, %B} {ss.as_of.day}")

    key = (ss.as_of.isoformat(), json.dumps(counts), len(ws["commitments"]))
    if ss.morning and ss.morning["key"] == key:
        with st.container(border=True):
            st.markdown(md(ss.morning["text"]))
            usage_caption(ss.morning["usage"])
    elif st.button("✨ Write my morning brief", type="primary"):
        if need_key() or not allow_ai():
            return
        llm = make_llm()
        with st.container(border=True):
            try:
                text = stream_md(llm, MORNING.format(user=user.name), brief_as_text(brief, user))
            except Exception as e:  # noqa: BLE001
                st.error(friendly_error(e))
                return
            ss.morning = {"key": key, "text": text, "usage": llm.last_usage.label() if llm.last_usage else None}
            usage_caption(ss.morning["usage"])

    if brief["pending"]:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.markdown(f"🔁 **{len(brief['pending'])} plan change(s)** from newer documents need your decision.")
            c2.button("Review", on_click=go, args=("changes",), width="stretch")

    st.space("small")
    col_me, col_them = st.columns(2, gap="large")
    with col_me:
        st.markdown("#### What I owe")
        sections = [("🔥 Overdue", brief["mine_overdue"]), ("📍 Today", brief["mine_today"]),
                    ("🗓️ Next 7 days", brief["mine_upcoming"])]
        if not any(items for _, items in sections):
            st.caption("Nothing due in the next 7 days. 🎉")
        for label, items in sections:
            if items:
                st.markdown(f"**{label}**")
                for c in items:
                    commitment_card(c, "today_me", show_direction=False)
        if brief["mine_undated"]:
            st.caption(f"+ {len(brief['mine_undated'])} open item(s) with no date: see 📌 Commitments.")
    with col_them:
        st.markdown("#### Waiting on others")
        if not brief["owed_overdue"] and not brief["owed_due_soon"]:
            st.caption("No one owes you anything in the next 7 days.")
        if brief["owed_overdue"]:
            st.markdown("**🔥 Overdue: time to chase**")
            for c in brief["owed_overdue"]:
                commitment_card(c, "today_owed_late", show_direction=False)
        if brief["owed_due_soon"]:
            st.markdown("**⏳ Due soon**")
            for c in brief["owed_due_soon"]:
                commitment_card(c, "today_owed", show_direction=False)


def timeline_buckets(items: list[dict]) -> dict[str, list[dict]]:
    week_end = ss.as_of + timedelta(days=6 - ss.as_of.weekday())
    buckets: dict[str, list[dict]] = {"🔥 Overdue": [], "📍 This week": [], "🗓️ Next week": [], "🔭 Later": [],
                                      "❔ No date": []}
    for c in items:
        d = due(c)
        if not d:
            buckets["❔ No date"].append(c)
        elif d < ss.as_of and c["status"] == "open":
            buckets["🔥 Overdue"].append(c)
        elif d <= week_end:
            buckets["📍 This week"].append(c)
        elif d <= week_end + timedelta(days=7):
            buckets["🗓️ Next week"].append(c)
        else:
            buckets["🔭 Later"].append(c)
    return buckets


def page_loops() -> None:
    if not ws["commitments"]:
        st.info("No commitments yet. Add a document from **➕ Add**.")
        return
    c1, c2, c3 = st.columns([2, 2, 1], gap="medium")
    st.space("xsmall")
    who = c1.segmented_control("Whose", ["mine", "owed", "others", "all"], default="all", key="f_who",
                               format_func=lambda k: {**DIRECTION_LABEL, "all": "Everyone"}[k]) or "all"
    state = c2.segmented_control("Status", ["open", "done", "cancelled", "all"], default="open", key="f_state",
                                 format_func=lambda k: {"open": "Open", "done": "Done", "cancelled": "Dropped",
                                                        "all": "All"}[k]) or "open"
    view = c3.segmented_control("View", ["list", "timeline"], default="list", key="f_view",
                                format_func=lambda k: {"list": "List", "timeline": "Timeline"}[k]) or "list"

    items = sort_by_due([c for c in ws["commitments"]
                         if (who == "all" or direction(c, user) == who) and (state == "all" or c["status"] == state)])
    if not items:
        st.caption("Nothing matches these filters.")
    elif view == "list":
        for c in items:
            commitment_card(c, "loops")
    else:
        for label, bucket in timeline_buckets(items).items():
            if bucket:
                st.markdown(f"#### {label} · {len(bucket)}")
                for c in bucket:
                    commitment_card(c, "timeline")

    st.space("medium")
    st.divider()
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### 📤 Take it with you")
        dated = [c for c in items if c.get("due_date") and c["status"] == "open"]
        st.download_button(f"📅 Add {len(dated)} dated item(s) to my calendar (.ics)", to_ics(dated, "Open Loops"),
                           file_name="open-loops.ics", mime="text/calendar", disabled=not dated, width="stretch")
        checklist = "\n".join(
            f"- [{'x' if c['status'] == 'done' else ' '}] {c['task']} ({c['owner']}, {relative_due(c, ss.as_of)})"
            for c in items)
        st.download_button("⬇️ Download as a Markdown checklist", checklist, file_name="open-loops.md",
                           mime="text/markdown", disabled=not items, width="stretch")
    with right:
        st.markdown("#### ✉️ Draft a follow-up")
        people = sorted({c["owner"].split(" ")[0] for c in ws["commitments"]
                         if c["status"] == "open" and direction(c, user) != "mine" and c["owner"] != "Unassigned"})
        if not people:
            st.caption("No one else has open items right now.")
            return
        person = st.selectbox("Who to nudge", people)
        chosen = followup_items(ws, user, person.lower(), ss.as_of)
        st.caption(f"{len(chosen)} open item(s) for {person}")
        if st.button(f"Draft message to {person}", width="stretch"):
            if need_key() or not allow_ai():
                return
            llm = make_llm()
            listing, notes = followup_listing(ws, chosen)
            for note in notes:
                st.caption(f"ℹ️ {note}")
            try:
                text, usage = llm.complete(FOLLOWUP.format(user=user.name, person=person),
                                           f"{today_line(ss.as_of)}\n{listing}")
            except Exception as e:  # noqa: BLE001
                st.error(friendly_error(e))
                return
            ss.followups[person] = (text, usage.label())
        if person in ss.followups:
            text, label = ss.followups[person]
            st.code(text, language=None, wrap_lines=True)
            st.caption(f"Copy with the button in the corner · {label}")


def page_changes() -> None:
    pending = wsx.pending_proposals(ws)
    if not pending:
        st.success("No plan changes waiting for review.")
    else:
        st.markdown(f"### {len(pending)} change(s) from newer documents")
        st.caption("Nothing changes without your OK. **Accept** updates the tracked commitment; "
                   "**Ignore** keeps the old one and tracks the new item separately.")
    for p in pending:
        c = wsx.get_commitment(ws, p["commitment_id"])
        doc = wsx.get_doc(ws, p["doc_id"])
        if not c or not doc:
            continue
        with st.container(border=True):
            icon = "❌" if p["relation"] == "cancelled" else "🔁"
            st.markdown(f"{icon} **{md(c['task'])}** · 👤 {md(c['owner'])}")
            st.markdown(md(p["reason"]))
            for fieldname, (old, new) in p["changes"].items():
                if fieldname == "due_date":
                    old, new = fmt_date(old), fmt_date(new)
                st.markdown(f"- {fieldname.replace('_', ' ').capitalize()}: ~~{md(str(old))}~~ → **{md(str(new))}**")
            if p["item"].get("source_quote"):
                st.markdown(quote_html(p["item"]["source_quote"]), unsafe_allow_html=True)
            first = c["sources"][0] if c["sources"] else None
            st.caption(f"From *{md(doc['title'])}* · {fmt_date(doc['date'])}"
                       + (f". Previously from *{md(first['doc_title'])}* · {fmt_date(first['doc_date'])}"
                          if first else ""))
            b1, b2, _ = st.columns([1, 1, 4])
            b1.button("✅ Accept", key=f"acc_{p['id']}", on_click=resolve, args=(p["id"], True), type="primary",
                      width="stretch")
            b2.button("Ignore", key=f"ign_{p['id']}", on_click=resolve, args=(p["id"], False), width="stretch")

    resolved = sorted((p for p in ws["proposals"] if p["state"] != "pending"),
                      key=lambda p: p.get("resolved_at", ""), reverse=True)
    if resolved:
        st.space("small")
        st.markdown(f"#### Resolved · {len(resolved)}")
        for p in resolved:
            c = wsx.get_commitment(ws, p["commitment_id"])
            mark = "✅ Accepted" if p["state"] == "accepted" else "↩️ Ignored"
            undoable = wsx.can_undo(ws, p)
            with st.container(border=True):
                left, right = st.columns([5, 1], vertical_alignment="center")
                left.markdown(f"{mark} · **{md(c['task'] if c else p['item']['task'])}**")
                left.caption(md(p["reason"]))
                right.button("↶ Undo", key=f"undo_{p['id']}", on_click=undo, args=(p["id"],), width="stretch",
                             disabled=not undoable,
                             help="Put this change back up for review." if undoable else
                             "Can't undo: this commitment has been edited since.")


def page_ask() -> None:
    if not ws["documents"]:
        st.info("Add a document first, then ask anything across everything you've added.")
        return
    titles = {d["id"]: f"{d['title']} · {fmt_date(d['date'])}" for d in ws["documents"]}
    scope = st.multiselect("Search in", list(titles), default=list(titles), format_func=titles.get)
    picked = st.pills("Suggestions", SUGGESTED_QUESTIONS, key="qa_pill", label_visibility="collapsed")
    with st.form("ask", clear_on_submit=True, border=False):
        c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
        question = c1.text_input("Ask across your documents", value=picked or "",
                                 placeholder="e.g. What did I promise Rachel?")
        asked = c2.form_submit_button("💬 Ask", type="primary", width="stretch")

    fresh = None
    if asked:
        if need_key():
            return
        if not question.strip():
            st.warning("Type a question first.")
        elif not scope:
            st.warning("Pick at least one document to search.")
        elif allow_ai():
            llm = make_llm()
            context = qa_context(ws, scope, user, ss.as_of)
            with st.chat_message("user"):
                st.markdown(md(question))
            with st.chat_message("assistant"):
                try:
                    answer = stream_md(llm, QA, f"{today_line(ss.as_of)} The user is {user.name}.\n\n"
                                                f"{context}\n\nQuestion: {question}")
                except Exception as e:  # noqa: BLE001
                    st.error(friendly_error(e))
                    return
                label = llm.last_usage.label() if llm.last_usage else None
                usage_caption(label)
            fresh = {"q": question, "a": answer, "usage": label}
    for turn in ss.qa:
        with st.chat_message("user"):
            st.markdown(md(turn["q"]))
        with st.chat_message("assistant"):
            st.markdown(md(turn["a"]))
            usage_caption(turn["usage"])
    if fresh:
        ss.qa.insert(0, fresh)


def page_docs() -> None:
    if not ws["documents"]:
        st.info("No documents yet.")
        return
    for doc in sorted(ws["documents"], key=lambda d: d["date"], reverse=True):
        tracked = [c for c in ws["commitments"] if any(s["doc_id"] == doc["id"] for s in c["sources"])]
        with st.expander(f"📄 {doc['title']} · {fmt_date(doc['date'])} · {len(tracked)} commitment(s)"):
            if doc.get("briefing"):
                st.markdown(md(doc["briefing"]))
            elif st.button("📋 Write a briefing", key=f"brief_{doc['id']}"):
                if need_key() or not allow_ai():
                    return
                llm = make_llm()
                try:
                    source = briefing_input(llm, doc["title"], doc["date"], doc["text"])
                    doc["briefing"] = stream_md(llm, BRIEFING, source)
                    persist()
                    usage_caption(llm.last_usage.label() if llm.last_usage else None)
                except Exception as e:  # noqa: BLE001
                    st.error(friendly_error(e))
            cancelled = [i for i in doc.get("extracted", []) if i["status"] == "cancelled"]
            if cancelled:
                st.caption("Dropped in this document: " + "; ".join(md(i["task"]) for i in cancelled))
            st.text_area("Original text", doc["text"], height=200, disabled=True, key=f"text_{doc['id']}")
            if st.button("Remove this document", key=f"rm_{doc['id']}"):
                wsx.remove_document(ws, doc["id"])
                persist()
                st.rerun()


{"add": page_add, "today": page_today, "loops": page_loops, "changes": page_changes, "ask": page_ask,
 "docs": page_docs}[ss.nav or "add"]()

st.divider()
st.caption(f"Built for the Nebius x NVIDIA Global AI Hackathon · Powered by `{ss.model}` on Nebius Token Factory")
