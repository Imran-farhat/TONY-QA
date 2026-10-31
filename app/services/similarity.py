"""Text similarity and tokenization for duplicate detection and natural language search."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import CaseRecord

STOP = set("""a an the of for to in on at by with and or but is are was were be been it this that these those
where what which who how when does do did can could would should will shall please tony find show me my i you we
us our your there here from as if then than so just any some test tests case cases tc testcase list search
locate lookup look up about into onto not no""".split()) - {"not", "no"}
STOP |= {"test", "case", "cases", "tc", "tests", "testcase"}

SYN = {
    "correct": "valid", "right": "valid", "proper": "valid", "good": "valid",
    "signin": "login", "signon": "login", "logon": "login", "logging": "login",
    "stuck": "spinner", "hangs": "spinner", "hang": "spinner", "loading": "spinner", "spinning": "spinner",
    "infinite": "forever", "never": "forever", "endless": "forever", "indefinitely": "forever",
    "broken": "fail", "fails": "fail", "failed": "fail", "failing": "fail", "failure": "fail",
    "error": "fail", "errors": "fail", "crash": "fail", "crashes": "fail", "crashed": "fail",
    "pwd": "password", "passwd": "password", "passwords": "password"
}

_TOKEN = re.compile(r"[A-Za-z0-9\u0B80-\u0BFF]+")


def _stem(w: str) -> str:
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"\blog[\s-]?in\b|\bsign[\s-]?in\b|\blog[\s-]?on\b", "login", t)
    t = re.sub(r"\blog[\s-]?out\b|\bsign[\s-]?out\b", "logout", t)
    out = []
    for w in _TOKEN.findall(t):
        if w in STOP:
            continue
        w = SYN.get(w, w)
        w = _stem(w)
        if w and w not in STOP:
            out.append(w)
    return out


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    return len(a) >= 5 and len(b) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.86


def _overlap(a: list[str], b: list[str]) -> tuple[float, float]:
    """(jaccard, overlap-coefficient) using typo-tolerant token equality."""
    if not a or not b:
        return 0.0, 0.0
    sa, sb = list(dict.fromkeys(a)), list(dict.fromkeys(b))
    inter = sum(1 for x in sa if any(_same(x, y) for y in sb))
    union = len(sa) + len(sb) - inter
    return inter / union, inter / min(len(sa), len(sb))


def similarity(title_a: str, rest_a: str, title_b: str, rest_b: str) -> float:
    """Duplicate score 0..1. Title dominates; steps/expected/actual add confirmation."""
    ta, tb = tokens(title_a), tokens(title_b)
    j, o = _overlap(ta, tb)
    title_sim = 0.5 * j + 0.5 * o
    fa, fb = ta + tokens(rest_a), tb + tokens(rest_b)
    jf, _ = _overlap(fa, fb)
    return min(1.0, 0.75 * title_sim + 0.25 * jf)


def duplicate_candidates(new_title: str, new_rest: str, cases: list[CaseRecord], threshold: float):
    res = []
    for c in cases:
        if c.deleted:
            continue
        s = similarity(new_title, new_rest, c.title, f"{c.steps} {c.expected} {c.actual}")
        if s >= threshold:
            res.append((c, s))
    return sorted(res, key=lambda x: -x[1])


def search(query: str, cases: list[CaseRecord], include_deleted: bool = False) -> list[tuple[CaseRecord, float]]:
    """Coverage score: how much of the query is found in each case (title counts most)."""
    q = list(dict.fromkeys(tokens(query)))
    if not q:
        return []
    out = []
    for c in cases:
        if c.deleted and not include_deleted:
            continue
        title = tokens(c.title)
        strong = title + tokens(c.module) + tokens(c.environment)
        weak = tokens(f"{c.steps} {c.expected} {c.actual} {c.links} {c.priority}")
        got = 0.0
        for w in q:
            if any(_same(w, t) for t in title):
                got += 1.0
            elif any(_same(w, t) for t in strong):
                got += 0.8
            elif any(_same(w, t) for t in weak):
                got += 0.5
        s = got / len(q)
        if s > 0:
            out.append((c, s))
    return sorted(out, key=lambda x: (-x[1], x[0].tc_id))

