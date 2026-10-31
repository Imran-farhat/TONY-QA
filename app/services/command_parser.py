"""Command parsing and structured test-case field extraction.

Deterministic rule-based parser that extracts test case fields and commands
without ever hallucinating missing steps or expected results.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Draft

TC_RE = re.compile(r"(?i)(?<![A-Za-z0-9])TC[-_ ]?(\d{1,6})(?![A-Za-z0-9])")


def norm_tc(text: str) -> str:
    m = TC_RE.search(text)
    return f"TC-{int(m.group(1)):04d}" if m else ""


@dataclass
class Command:
    kind: str            # help, add, show, update, delete, restore, list, find, unknown
    arg: str = ""
    tc_id: str = ""


_MENTION = re.compile(r"(?i)^\s*@?tony\b[\s,:\-]*")
_TCP = r"(TC[-_ ]?\d{1,6})"
_ADD = re.compile(
    r"(?is)^(?:please\s+)?(?:add|new|create|log|save|file|report)\s+(?:a\s+|an\s+|new\s+)?"
    r"(?:test[\s-]*cases?|tcs?|cases?|bugs?)\b\s*[:\-–]?\s*(.*)$"
)
_ADD2 = re.compile(r"(?is)^add\s*:\s*(.*)$")
_SHOW = re.compile(rf"(?is)^(?:show|get|display|open|view|details?(?:\s+of)?)\s+(?:me\s+)?(?:test[\s-]*case\s+)?{_TCP}\s*$")
_BARE_TC = re.compile(rf"(?is)^{_TCP}\s*\??$")
_UPDATE = re.compile(rf"(?is)^(?:update|edit|change|set)\s+(?:test[\s-]*case\s+)?{_TCP}\s*[:,\-]?\s*(.*)$")
_DELETE = re.compile(rf"(?is)^(?:delete|remove)\s+(?:test[\s-]*case\s+)?{_TCP}\s*$")
_RESTORE = re.compile(rf"(?is)^(?:restore|undelete|undo\s+delete)\s+(?:test[\s-]*case\s+)?{_TCP}\s*$")
_LIST = re.compile(r"(?is)^(?:list|show\s+(?:me\s+)?(?:all\s+)?|display\s+all|how\s+many)\b(.*)$")
_FIND = re.compile(r"(?is)^(?:where(?:'s|\s+is|\s+are|\s+can|\s+do|\s+did)?|find|search(?:\s+for)?|locate|look\s*up|which)\b(.*)$")
_HELP = re.compile(r"(?i)^(?:help|\?|hi|hello|hey|commands|what can you do)\b")


def strip_mention(text: str) -> str:
    return _MENTION.sub("", text.strip(), count=1).strip()


def parse_command(text: str) -> Command:
    t = strip_mention(text)
    if not t:
        return Command("help")
    if _HELP.match(t):
        return Command("help")
    if m := _ADD.match(t):
        return Command("add", m.group(1))
    if m := _ADD2.match(t):
        return Command("add", m.group(1))
    if m := _SHOW.match(t):
        return Command("show", tc_id=norm_tc(m.group(1)))
    if m := _BARE_TC.match(t):
        return Command("show", tc_id=norm_tc(m.group(1)))
    if m := _UPDATE.match(t):
        return Command("update", m.group(2), norm_tc(m.group(1)))
    if m := _DELETE.match(t):
        return Command("delete", tc_id=norm_tc(m.group(1)))
    if m := _RESTORE.match(t):
        return Command("restore", tc_id=norm_tc(m.group(1)))
    if m := _LIST.match(t):
        return Command("list", m.group(1))
    if m := _FIND.match(t):
        return Command("find", m.group(1).strip() or t)
    return Command("unknown", t)


_ALIASES = {
    "title": ["title", "summary"],
    "module": ["module", "component", "தொகுதி"],
    "priority": ["priority", "prio", "severity", "முன்னுரிமை"],
    "steps": ["steps to reproduce", "steps to repro", "repro steps", "steps", "step", "படிகள்"],
    "expected": ["expected result", "expected behaviour", "expected behavior", "expected", "expect",
                 "எதிர்பார்த்த முடிவு", "எதிர்பார்ப்பு"],
    "actual": ["actual result", "actual behaviour", "actual behavior", "actual", "observed",
               "உண்மையான முடிவு"],
    "environment": ["environment", "env", "browser", "device"],
    "status": ["status"],
    "links": ["links", "link", "url", "ticket", "jira"],
}
_ALIAS_TO_KEY = {a: k for k, v in _ALIASES.items() for a in v}
_ALT = "|".join(sorted((re.escape(a).replace(r"\ ", r"\s+") for a in _ALIAS_TO_KEY), key=len, reverse=True))
LABEL_RE = re.compile(rf"(?<![A-Za-z0-9_])({_ALT})(?:\s*[:=]\s*|\s+(?=1\s*[).]))", re.I)

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_TRAIL_URLS = re.compile(r"(?:\s+(?:https?://[^\s<>\"')\]]+))+\s*$")
_ATTACH_NOTE = re.compile(r"(?i)\(\s*(?:screenshots?|images?|attachments?|attached|ss|file)[^)]*\)")

PRIORITIES = [
    (r"\b(critical|blocker|urgent|p0|sev\s?1)\b", "Critical"),
    (r"\b(high|major|p1|sev\s?2)\b", "High"),
    (r"\b(medium|med|moderate|normal|p2|sev\s?3)\b", "Medium"),
    (r"\b(low|minor|trivial|p3|sev\s?4)\b", "Low"),
]
STATUSES = {
    "open": "Open", "in progress": "In Progress", "wip": "In Progress", "passed": "Passed",
    "pass": "Passed", "failed": "Failed", "fail": "Failed", "blocked": "Blocked",
    "retest": "Retest", "closed": "Closed", "fixed": "Fixed", "reopened": "Open"
}


def norm_priority(v: str) -> str:
    s = v.lower()
    for pat, val in PRIORITIES:
        if re.search(pat, s):
            return val
    return ""


def norm_status(v: str) -> str:
    return STATUSES.get(re.sub(r"[^a-z ]", "", v.lower()).strip(), "")


def split_labelled(text: str) -> tuple[str, dict[str, str]]:
    """Return (text before the first label, {field: raw value})."""
    ms = list(LABEL_RE.finditer(text))
    if not ms:
        return text, {}
    out: dict[str, str] = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        key = _ALIAS_TO_KEY[re.sub(r"\s+", " ", m.group(1).lower())]
        val = text[m.end():end]
        out[key] = (out[key] + "\n" + val) if key in out and key == "links" else out.get(key, val)
    return text[:ms[0].start()], out


def _strip_trailing_urls(v: str) -> tuple[str, list[str]]:
    m = _TRAIL_URLS.search(v)
    if not m:
        return v, []
    urls = [u.rstrip(".,;:!?") for u in URL_RE.findall(m.group(0))]
    return v[:m.start()], urls


def _clean(v: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", " ", v)).strip(" .;,\n\t")


def _one_line(v: str) -> str:
    v = v.strip()
    return _clean(re.split(r"\s{2,}|\n|\(|https?://", v)[0]) if v else ""


def normalize_steps(raw: str) -> str:
    s = raw.strip()
    if not s:
        return ""
    parts = [p for p in re.split(r"(?:(?<=\s)|^)\d{1,2}\s*[).](?=\s*\S)\s*", s) if p.strip()]
    numbered = re.search(r"(?:(?<=\s)|^)\d{1,2}\s*[).](?=\s*\S)", s) is not None
    if not numbered or not parts:
        lines = [re.sub(r"^\s*[-*•]\s+", "", ln) for ln in re.split(r"\n|;|->|→", s)]
        parts = [ln for ln in lines if ln.strip()]
    return " ".join(f"{i}) {_clean(p)}" for i, p in enumerate(parts, 1) if _clean(p))


_ENV_RE = re.compile(
    r"(?i)\b(safari|chrome|firefox|edge|opera|brave|ios|ipados|android|windows|macos|linux|ubuntu)"
    r"(?:\s+v?(\d{1,3}(?:\.\d+)*))?\b"
)


def detect_environment(text: str) -> str:
    """Only literal browser/OS mentions; never an assumption."""
    found: list[str] = []
    for m in _ENV_RE.finditer(text):
        name = {"ios": "iOS", "macos": "macOS", "ipados": "iPadOS"}.get(m.group(1).lower(), m.group(1).capitalize())
        label = f"{name} {m.group(2)}" if m.group(2) else name
        if not any(label.split()[0] == f.split()[0] and (len(f) >= len(label)) for f in found):
            found = [f for f in found if f.split()[0] != label.split()[0]] + [label]
    return " / ".join(found)


def parse_case(chunk: str) -> Draft:
    """Extract fields from ONE test case. Missing elements remain empty."""
    text = _ATTACH_NOTE.sub(" ", chunk).strip()
    leading, f = split_labelled(text)
    d = Draft()
    links: list[str] = []

    def take(key: str) -> str:
        v, urls = _strip_trailing_urls(f.get(key, ""))
        links.extend(urls)
        return v

    lead, urls = _strip_trailing_urls(leading)
    links.extend(urls)
    d.title = _clean(take("title") or lead)[:200]
    d.steps = normalize_steps(take("steps"))
    d.expected = _clean(take("expected"))
    d.actual = _clean(take("actual"))
    d.module = _one_line(take("module"))
    d.environment = _one_line(take("environment")) or detect_environment(f"{d.title} | {d.steps} | {d.actual}")
    d.priority = norm_priority(_one_line(take("priority")))
    d.status = norm_status(_one_line(take("status"))) or "Open"
    if "links" in f:
        links.extend(u.rstrip(".,;:!?") for u in URL_RE.findall(f["links"]))
    seen: list[str] = []
    for u in links:
        if u not in seen:
            seen.append(u)
    d.links = seen
    return d


def merge_reply(draft: Draft, text: str) -> Draft:
    """Fill gaps of a pending draft from a follow-up answer."""
    new = parse_case(text)
    labelled = bool(LABEL_RE.search(text))
    if not labelled:
        missing = [k for k in ("title", "steps", "expected") if not getattr(draft, k)]
        if len(missing) == 1 and text.strip():
            k = missing[0]
            setattr(draft, k, normalize_steps(text) if k == "steps" else _clean(text))
        return draft
    for k in ("title", "steps", "expected", "actual", "module", "environment", "priority"):
        if getattr(new, k) and not getattr(draft, k):
            setattr(draft, k, getattr(new, k))
    for u in new.links:
        if u not in draft.links:
            draft.links.append(u)
    return draft


CASE_MARK = re.compile(
    r"(?im)^[ \t]*(?:@?tony[ \t]+)?(?:(?:add|new|log|create)[ \t]+)?"
    r"(?:test[ \t]*cases?|cases?|tc)[ \t]*#?\d*[ \t]*[:\-–][ \t]*"
)


def split_cases(body: str) -> list[str]:
    ms = list(CASE_MARK.finditer(body))
    if not ms:
        return [body.strip()] if body.strip() else []
    chunks = [body[:ms[0].start()]]
    for i, m in enumerate(ms):
        chunks.append(body[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(body)])
    return [c.strip() for c in chunks if c.strip()]


UPDATABLE = ("title", "priority", "steps", "expected", "actual", "environment", "links", "status", "module")


def parse_update(arg: str) -> tuple[dict[str, str], list[str]]:
    """Return ({field: new value}, [problems])."""
    problems: list[str] = []
    changes: dict[str, str] = {}
    _, raw = split_labelled(arg)
    if not raw and norm_status(arg):
        raw = {"status": arg}
    for k, v in raw.items():
        v = v.strip()
        if k == "status":
            s = norm_status(_one_line(v))
            if s:
                changes[k] = s
            else:
                problems.append(f"Unknown status '{_one_line(v)}'. Use: Open, In Progress, Passed, Failed, Blocked, Retest, Closed.")
        elif k == "priority":
            p = norm_priority(_one_line(v))
            if p:
                changes[k] = p
            else:
                problems.append("Priority must be Critical, High, Medium or Low.")
        elif k == "steps":
            changes[k] = normalize_steps(v)
        elif k == "links":
            changes[k] = "\n".join(u.rstrip(".,;:!?") for u in URL_RE.findall(v))
        elif k == "module":
            problems.append("Changing the module (sheet) is not supported yet - delete and re-add the case instead.")
        else:
            changes[k] = _clean(v)
    changes = {k: v for k, v in changes.items() if v != ""}
    return changes, problems
