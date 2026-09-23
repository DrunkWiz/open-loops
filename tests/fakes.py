"""A scripted stand-in for core.llm.LLM, so the pipeline can be tested offline."""

from __future__ import annotations

from core.llm import Usage

KICKOFF_ITEMS = [
    {"task": "Complete new onboarding flow code", "owner": "Hamid", "requester": "Grace",
     "due_date": "2026-10-09", "due_text": "by Oct 9", "priority": "High", "status": "open", "change_note": None,
     "source_quote": "Hamid: new onboarding flow code complete by Oct 9."},
    {"task": "Complete offline sync integration code", "owner": "Hamid", "requester": "Grace",
     "due_date": "2026-10-12", "due_text": "by Oct 12", "priority": "High", "status": "open", "change_note": None,
     "source_quote": "Hamid: offline sync integration code complete by Oct 12."},
    {"task": "Send weekly status update to leadership", "owner": "Grace", "requester": "leadership",
     "due_date": "2026-09-18", "due_text": "every Friday, starting this Friday", "priority": "Medium",
     "status": "open", "change_note": None,
     "source_quote": "I'll send the weekly status update to leadership every Friday"},
]

UPDATE_ITEMS = [
    {"task": "Onboarding flow code complete", "owner": "Hamid", "requester": "Grace",
     "due_date": "2026-10-06", "due_text": "by Oct 6", "priority": "High", "status": "open",
     "change_note": "was Oct 9", "source_quote": "Hamid: onboarding flow code complete by Oct 6."},
    {"task": "Offline sync", "owner": "Hamid", "requester": None, "due_date": None, "due_text": None,
     "priority": "Medium", "status": "cancelled", "change_note": None,
     "source_quote": "Offline sync is ALSO cut from v3"},
    {"task": "Brief the customer success team on the scope change", "owner": "Grace", "requester": None,
     "due_date": "2026-09-25", "due_text": "by Friday", "priority": "High", "status": "open", "change_note": None,
     "source_quote": "I'll brief the customer success team on the scope change by Friday"},
]


class FakeLLM:
    model = "fake/model"
    temperature = 0.1
    thinking = True

    def __init__(self, *args, **kwargs):
        self.last_usage = Usage(0.1, 42, self.model)
        self.calls: list[str] = []

    def complete_json(self, system, user, schema, max_tokens=4096):
        self.calls.append(system[:40])
        usage = Usage(0.1, 42, self.model)
        if "commitment tracker" in system:  # change matching
            matches = []
            for line in user.splitlines():
                if not line.startswith("New item"):
                    continue
                index = int(line.split()[2].rstrip(":"))
                block = user.split(line, 1)[1].split("New item", 1)[0]
                ids = [part.split(":")[0] for part in block.split("- id ")[1:]]
                if "Onboarding" in line and ids:
                    matches.append({"new": index, "existing": ids[0], "relation": "update",
                                    "reason": "Deadline moved from Oct 9 to Oct 6 because QA needs more time."})
                elif "Offline sync" in line:
                    target = next((i for i in ids if "offline" in block.split(i, 1)[1].split("\n")[0].lower()), None)
                    matches.append({"new": index, "existing": target, "relation": "cancelled" if target else "none",
                                    "reason": "Offline sync was cut from v3."})
                else:
                    matches.append({"new": index, "existing": None, "relation": "none", "reason": ""})
            return {"matches": matches}, usage
        if "cut from v3" in user or "supersedes" in user:
            return {"items": UPDATE_ITEMS}, usage
        return {"items": KICKOFF_ITEMS}, usage

    def complete(self, system, user, max_tokens=4096, temperature=None, **extra):
        return "Hi Hamid, a quick nudge on the onboarding flow (due Oct 6). Thanks!", Usage(0.1, 42, self.model)

    def stream(self, system, user, max_tokens=4096):
        yield from ["### TL;DR\n", "Scope cut to make the Oct 20 date."]
