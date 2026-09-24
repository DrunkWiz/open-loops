<div align="center">

<!-- Replace with your banner: ![Open Loops](assets/banner.png) -->
# 🔁 Open Loops
### Your AI chief of staff for every promise you make, and every promise made to you

**Built for the Nebius x NVIDIA Global AI Hackathon (Personal AI Track)**

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B)
![Nebius](https://img.shields.io/badge/Inference-Nebius%20Token%20Factory-2E2EFF)
![NVIDIA Nemotron](https://img.shields.io/badge/Model-NVIDIA%20Nemotron-76B900)
![License](https://img.shields.io/badge/License-MIT-green)

</div>

---

## 🚩 The problem

Your commitments don't live in one place. A deadline is set in a meeting, moved in an email a week later, and a family plan is agreed in a group chat while all that happens. Things slip because **nothing tracks the promises across all of those sources**.

A chatbot doesn't solve this. You paste one document, get a summary, and it forgets. Meeting tools like Otter, Fireflies and Granola only see calls. Neither will tell you that *Tuesday's email quietly moved the deadline you agreed on Friday.*

## 💡 What Open Loops does

Feed it the text you already have: emails, meeting notes, auto-generated transcripts, Slack threads, family group chats, voice-memo brain dumps, or PDF and Word files. It keeps **one ledger of commitments across all of them**:

| | Feature | Why it matters |
|---|---|---|
| 📌 | **I owe / Owed to me / Others** | Tell it who you are once, and every item is sorted by who owes what to whom. |
| 🔁 | **Cross-document change detection** | When a newer document moves a deadline, reassigns work or cancels something, you get a **proposal** with the reason, to accept or ignore, and you can undo either. Nothing is edited silently. |
| ✅ | **Verified sources** | Every commitment keeps the exact sentence it came from. The app checks that quote against the original, word for word, and flags any that don't match. |
| 📅 | **Real dates** | "By Friday", "next Tuesday" and "tomorrow" become real dates based on when each document was written. Anything vague stays marked as undated, never guessed. |
| ☀️ | **Morning brief** | What's overdue, due today and due this week, and who you're waiting on, in one screen. |
| ✉️ | **Act on it** | Draft follow-up messages per person, export dated items to your calendar (`.ics`), or download a checklist. |
| 💬 | **Ask across everything** | "What did I promise Rachel?" Answers come only from your documents, and newer documents win when they disagree. |

### Try the demo in 30 seconds
**No API key needed:** click **▶ Load the 3-document demo** to explore results saved from a real Nemotron run. With a key, the same button runs the demo live (about a minute with reasoning on). It plays one week in the life of Grace, a product lead:
1. **Tue Sep 15:** kickoff email. Hamid owes the onboarding flow by Oct 9, and offline sync is "definitely in".
2. **Mon Sep 21:** family group chat planning Grandma's 80th. Grace calls the caterer, and Dad orders the cake.
3. **Tue Sep 22:** an email that changes the plan.

Open **🔁 Changes** and the app flags the plan changes: *Hamid's deadline moved Oct 9 → Oct 6*, *offline sync was cut*, *Apple Watch moved to v3.1*. Each one is shown with its source quote, for you to accept or ignore. **☀️ Today** then shows Grace's work *and* family commitments together.

## ⚙️ How it works

```
 Any text            ┌──────────────────────────── Nebius Token Factory ─────────────────────────────┐
 (email, chat,  ──▶  │ 1. Extract   JSON-schema output: task, owner, requester, real due date,        │
  transcript,        │              status, change note, exact source quote                           │
  PDF, DOCX)         │ 2. Match     compare only against commitments with the same owner or similar   │
                     │              wording → same / update / cancelled / none                         │
                     │ 3. Write     streamed briefing, morning brief, follow-ups, grounded Q&A         │
                     └──────────────────────────── NVIDIA Nemotron (default) ─────────────────────────┘
                                                    │
        ┌───────────────────────────────────────────▼──────────────────────────────────────┐
        │ Plain Python (deterministic, unit-tested)                                          │
        │ quote verification · who-owes-whom · overdue maths · proposals · calendar export   │
        └────────────────────────────────────────────────────────────────────────────────────┘
```

- **The model extracts and compares; plain Python decides.** Quote checks, date maths, the Mine / Owed / Others split and the morning brief are ordinary code, so they're predictable and tested.
- **Structured output:** Nebius JSON-schema mode where the model supports it, with automatic fallback to JSON mode.
- **Speed:** extraction runs in the background while the briefing streams in. Every answer shows its latency and token count.
- **Long documents** are split into chunks and nothing is dropped. Briefings of long documents summarise each part first, then combine the notes.
- **Models:** the sidebar lists the chat models Nebius serves right now, with **NVIDIA Nemotron 3 Super** first. Reasoning traces are removed before display.

## 📏 Evaluation

`evals/run_evals.py` runs the real pipeline on the sample documents and scores it against hand-written expectations in `evals/cases.json`, for example *"Hamid's onboarding is due Oct 6, not the old Oct 9"*, *"'by Friday' from a Tuesday is Sep 25"*, *"offline sync must not stay open"*, and *"don't claim Hannah approved a logo"*. It also reports **source grounding**: the share of extracted items whose quote appears word for word in the document.

```bash
python -m evals.run_evals
```

**Latest run, NVIDIA Nemotron 3 Super on Nebius (reasoning on): 34/34 checks passed, and 46/46 source quotes found word for word in the original documents.** The checks include a second chain of documents the prompts were never tuned on: a founder's voice memo followed by an email thread that moves a deadline earlier, reassigns a task, cancels one and marks one done. The full report is in [`evals/RESULTS.md`](evals/RESULTS.md). Model outputs vary between runs, so rerun the evals after changing prompts.

`python -m scripts.live_check` checks the writing features against the live model: the morning brief, a follow-up draft (it must use the latest plan, not a cancelled task or an old date) and Ask across documents. Latest run: 5/5.

The core logic has 34 offline tests (`pytest`), including UI tests that click through the real Streamlit app with a stand-in for the model.

## 🔒 Privacy

- Runs on **open-weight models** through Nebius Token Factory. Your documents aren't stored in a consumer chatbot's history.
- On a shared deployment, **each visitor's workspace lives only in their own browser session**. Use *Export workspace* to keep it.
- Running locally, you can set `WORKSPACE_FILE` to save your workspace to a local JSON file.
- Because the endpoint is OpenAI-compatible, you can point it at a model you host yourself.

## 🚀 Run it locally

**Prerequisites:** Python 3.10+ and a Nebius Token Factory API key ([tokenfactory.nebius.com](https://tokenfactory.nebius.com)).

```bash
git clone https://github.com/<your-username>/open-loops.git
cd open-loops
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env  — then add your key
streamlit run app.py
```

Run the tests with `pip install -r requirements-dev.txt` and then `pytest`.

## ☁️ Deploy a public demo (Streamlit Community Cloud)

1. Push this repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), click **Create app** and pick the repo, with `app.py` as the entry point.
3. *(Optional)* Under **Advanced settings → Secrets**, add `NEBIUS_API_KEY = "..."` (the quotes are required) so visitors can use it without their own key. The key stays on the server and is never sent to the browser.
4. Don't set `WORKSPACE_FILE` on a public deployment.

**Protecting your credits.** When visitors use the app's own key, each one gets a limited number of AI actions per session (analyzing a document, a morning brief, a follow-up draft, a question), there is a daily cap across all visitors, and very long documents are refused. Visitors who paste their own key have no limits. Tune the limits with these optional secrets:

| Secret | Default | Meaning |
|---|---|---|
| `MAX_AI_ACTIONS_PER_SESSION` | `15` | AI actions per visitor session on the app's key |
| `MAX_AI_ACTIONS_PER_DAY` | `300` | AI actions per day across all visitors on the app's key |
| `MAX_DOC_CHARS` | `40000` | Longest document accepted on the app's key |

## 🗂️ Project structure

```
app.py               Streamlit UI (Add · Today · Commitments · Changes · Ask · Documents)
core/
  llm.py             Nebius client, model discovery, streaming, JSON-schema output
  extract.py         commitment extraction, quote verification, who-owes-whom
  changes.py         cross-document matching → proposals
  workspace.py       ledger data model, proposals, persistence
  brief.py           morning brief (deterministic)
  writer.py          model inputs for briefings, Q&A, follow-ups
  ingest.py          .txt/.md/.pdf/.docx reading, date guessing, chunking
  calendar.py        .ics export
  prompts.py         prompts
samples/             10 messy real-world documents, demo story (catalog.json),
                     saved demo results (demo_workspace.json)
scripts/             build_demo.py (regenerate saved demo) · live_check.py (live feature check)
evals/               scored evaluation against hand-written expectations
tests/               offline unit + UI tests (fake model)
```

## 🛣️ What's next

- Connect directly to Gmail, Slack and calendars, instead of pasting text in
- Voice-memo upload with speech-to-text
- Reminders pushed to your phone when something you're waiting on goes overdue

## 📄 License

MIT. See [LICENSE](LICENSE).

---

<div align="center">Built for the <b>Nebius x NVIDIA Global AI Hackathon</b> · Powered by NVIDIA Nemotron on Nebius Token Factory</div>
