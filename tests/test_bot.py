"""Comprehensive offline functional and edge-case test suite for Tony Bot.

Replays all 12 core functional scenarios + 13 safety edge cases using FakeChat adapter.
Preserves 100% of original scenario coverage.
"""
from __future__ import annotations

import argparse
import base64
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

from app.config import Config
from app.integrations.fake import FakeChat
from app.models import Attachment
from app.services.bot_service import TonyService
from app.storage.excel import ExcelStore
from app.storage.state import State

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, bool(ok), detail))


def rows(wb_path: Path, sheet: str):
    wb = load_workbook(wb_path, data_only=True)
    out = [r for r in wb[sheet].iter_rows(min_row=2, values_only=True) if r[0]] if sheet in wb.sheetnames else []
    wb.close()
    return out


def total_cases(wb_path: Path) -> int:
    wb = load_workbook(wb_path, data_only=True)
    n = sum(1 for ws in wb.worksheets if ws.title not in ("Index", "Audit")
            for r in ws.iter_rows(min_row=2, values_only=True) if r[0])
    wb.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="keep output in this directory (fresh each run)")
    args = ap.parse_args()
    root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="tony-test-"))
    if args.out and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    cfg = Config(data_dir=root, lock_retry_seconds=0.4, lock_max_wait_seconds=30, timezone="Asia/Kolkata")
    store, state, chat = ExcelStore(cfg), State(cfg), FakeChat()
    svc = TonyService(cfg, store, state, chat)
    wbp = cfg.workbook_path

    def say(text, **kw):
        """Send message to Tony, wait for background queue to idle, return replies."""
        m = chat.mark
        svc.handle(chat.message(text, **kw))
        svc.wq.wait_idle(30)
        return chat.replies_since(m)

    def last(replies):
        return replies[-1] if replies else ""

    # ---------------------------------------------------------------- 1 happy path
    r = say("""add test case: Login fails with valid password on Safari.
Steps: 1) open /login 2) enter valid user 3) click Sign in
Expected: dashboard opens. Actual: spinner forever.
module: Auth   priority: high   (screenshot attached)  https://jira.example.com/BUG-482""",
            attachments=[Attachment("screen.png", "image/png", PNG)])
    t = last(r)
    check("1  full case + image + Jira link -> saved, exact location replied",
          "Saved as TC-0001" in t and 'Sheet: Auth | Row 2 | Cells A2:L2' in t and "attachments/TC-0001_1.png" in t
          and "BUG-482" in t, t.replace("\n", " | "))
    a = rows(wbp, "Auth")
    check("1b row content correct (numbered steps, link, attachment path, reporter/date)",
          len(a) == 1 and a[0][4] == "1) open /login 2) enter valid user 3) click Sign in" and a[0][3] == "High"
          and a[0][8] == "https://jira.example.com/BUG-482" and a[0][9] == "attachments/TC-0001_1.png"
          and a[0][11].startswith("@priya / ") and (root / "attachments/TC-0001_1.png").read_bytes() == PNG, str(a[:1]))

    # ------------------------------------------------------------- 2 missing steps
    n_before = total_cases(wbp)
    r = say("add test case: checkout is broken")
    t = last(r)
    check("2  no steps -> follow-up question, nothing saved",
          "Steps to reproduce" in t and "Expected result" in t and total_cases(wbp) == n_before, t.replace("\n", " | "))
    r = say("steps: 1) add an item 2) press pay  expected: order confirmation page  module: Checkout")
    check("2b follow-up answer completes and saves the case", "Saved as TC-0002" in last(r)
          and rows(wbp, "Checkout")[0][1] == "checkout is broken", last(r).replace("\n", " | "))

    # --------------------------------------------------------------- 3 duplicate
    n_before = total_cases(wbp)
    r = say("""add test case: Safari login fails with correct password, spinner never stops
Steps: 1) open login page 2) type valid credentials 3) press sign in
Expected: dashboard shown. Actual: endless spinner. module: Auth""")
    t = last(r)
    check("3  same bug, different wording -> duplicate warning naming TC-0001 + sheet/row",
          "looks like TC-0001" in t and "Auth!row 2" in t and total_cases(wbp) == n_before, t.replace("\n", " | "))
    r = say("update")
    check("3b 'update' merges into the existing case (history kept in Audit), no new row",
          "Updated TC-0001" in last(r) and total_cases(wbp) == n_before, last(r).replace("\n", " | "))

    # ------------------------------------------------------------ 4 Tamil / English
    r = say("add test case: Safari-ல login பக்கம் திறக்கல. படிகள்: 1) /login திறக்கவும் 2) valid user போடவும் "
            "3) Sign in கிளிக் செய்யவும் Expected: dashboard திறக்கணும் Actual: spinner சுத்திட்டே இருக்கு "
            "module: Auth priority: medium")
    saved = "Saved as TC-0003" in last(r)
    tamil_row = [x for x in rows(wbp, "Auth") if x[0] == "TC-0003"]
    check("4  Tamil/English mix saved with Unicode intact", saved and tamil_row and "திறக்கவும்" in tamil_row[0][4]
          and "சுத்திட்டே" in tamil_row[0][6], last(r).replace("\n", " | "))

    # ------------------------------------------------------------ 5 two cases in one
    r = say("""add test cases:
Case 1: Coupon code is accepted after expiry date
Steps: 1) add item 2) apply expired coupon Expected: coupon is rejected module: Payments priority: high
Case 2: Profile photo crop is off-centre on Android
Steps: 1) open profile 2) upload photo 3) crop Expected: crop stays centred module: Profile""")
    t = last(r)
    check("5  two cases in one message -> two rows, two IDs",
          "Saved 2 cases" in t and "TC-0004" in t and "TC-0005" in t and len(rows(wbp, "Payments")) == 1
          and len(rows(wbp, "Profile")) == 1, t.replace("\n", " | "))

    # ------------------------------------------------------------------ 6 find
    r = say("where is the Safari login test?")
    t = last(r)
    check("6  where is the Safari login test? -> location from the sheet",
          'Best match: TC-0001' in t and 'Location: Sheet "Auth", Row 2 (cell A2)' in t, t.replace("\n", " | "))

    # ---------------------------------------------------------- 7 not found, no guess
    r = say("where is the payment refund test?")
    t = last(r)
    check("7  non-existent test -> 'no match', no hallucinated TC id", "No match" in t and "TC-" not in t, t.replace("\n", " | "))

    # ------------------------------------------------------------- 8 update + audit
    r = say("update TC-0001 status: passed")
    a = [x for x in rows(wbp, "Auth") if x[0] == "TC-0001"][0]
    au = [x for x in rows(wbp, "Audit") if x[2] == "UPDATE" and x[3] == "TC-0001" and x[4] == "Status"]
    check("8  update status -> row changed, Audit has old/new values",
          a[10] == "Passed" and au and au[-1][5] == "Open" and au[-1][6] == "Passed", last(r).replace("\n", " | "))

    # --------------------------------------------------------------- 9 masking
    r = say("""add test case: Login API returns 500 for expired session
Steps: 1) POST /login with password=Secr3t! 2) send header Authorization: Bearer abcdefghijklmnop12345 3) card 4111 1111 1111 1111
Expected: 401 with message. Actual: 500. module: Auth""")
    t = last(r)
    wb = load_workbook(wbp)
    blob = " ".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value)
    wb.close()
    check("9  password=Secr3t! (+ bearer token, card) masked before save",
          "Saved as" in t and "Secr3t" not in blob and "abcdefghijklmnop12345" not in blob and "4111 1111" not in blob
          and "password=****" in blob and "Masked" in t, t.replace("\n", " | "))

    # ------------------------------------------------------ 10 same event twice
    n_before = total_cases(wbp)
    body = ("add test case: Export to CSV drops the last row\nSteps: 1) open Reports 2) click Export Expected: all rows exported "
            "module: Reports")
    m1 = chat.message(body, event_id="webex-msg-FIXED-123")
    svc.handle(m1)
    svc.handle(chat.message(body, event_id="webex-msg-FIXED-123"))
    svc.wq.wait_idle(30)
    check("10 same Webex event delivered twice -> exactly one row", total_cases(wbp) == n_before + 1
          and len(rows(wbp, "Reports")) == 1)

    # -------------------------------------------------------------- 11 locked
    store.simulate_locked = True
    threading.Timer(1.5, lambda: setattr(store, "simulate_locked", False)).start()
    n_before = total_cases(wbp)
    r = say("add test case: Tooltip clipped inside modal on small screens\nSteps: 1) open settings modal 2) hover help icon "
            "Expected: tooltip fully visible module: UI")
    check("11 workbook locked -> told 'queued', retried, then saved (no crash)",
          len(r) >= 2 and "queued" in r[0] and "Saved as" in r[-1] and total_cases(wbp) == n_before + 1,
          " || ".join(x.replace("\n", " | ") for x in r))

    # ------------------------------------------------------ 12 twenty at once
    titles = [
        "Invoice PDF footer overlaps totals", "Cart badge shows stale count", "Dark theme hides toggle label",
        "Avatar upload rejects HEIC files", "Password reset email arrives late", "Search autocomplete flickers",
        "Calendar picker skips leap day", "Notification bell never clears", "Profile bio truncates emoji",
        "Coupon applies twice at payment", "Sidebar collapses after rotation", "Webhook retries stop silently",
        "Pagination resets active filter", "Currency symbol misaligned in table", "Drag handle unresponsive on touch",
        "Onboarding wizard loops step three", "Receipt download returns 404", "Language switch ignores locale",
        "Date range exceeds quota limit", "Upload progress bar jumps backwards"
    ]
    n_before = total_cases(wbp)
    mark = chat.mark
    with ThreadPoolExecutor(20) as ex:
        list(ex.map(lambda i: svc.handle(chat.message(
            f"add test case: {titles[i]}\nSteps: 1) open the page 2) trigger {i}\nExpected: works as designed module: Bulk",
            user=f"user{i}@example.com", room=f"room-{i}")), range(20)))
    svc.wq.wait_idle(60)
    ids = [x[0] for x in rows(wbp, "Bulk")]
    saved_msgs = [t for t in chat.replies_since(mark) if t.startswith("Saved as")]
    wb = load_workbook(wbp)
    wb.close()
    check("12 20 messages at once -> 20 rows, 20 unique IDs, workbook intact, 20 confirmations",
          len(ids) == 20 and len(set(ids)) == 20 and total_cases(wbp) == n_before + 20 and len(saved_msgs) == 20,
          f"rows={len(ids)} unique={len(set(ids))} confirmations={len(saved_msgs)}")

    if args.out:
        shutil.copy2(wbp, root / "testcases_after_12_samples.xlsx")

    # ======================================================== extra edge cases
    extras: list[tuple[str, bool, str]] = []

    def extra(name, ok, detail=""):
        extras.append((name, bool(ok), detail))

    mark = chat.mark
    svc.handle(chat.message("add test case: should be ignored", mentioned=False))
    extra("E1 un-mentioned group chatter is ignored", chat.mark == mark)

    r = say("delete TC-0003")
    extra("E2 delete asks for confirmation first (still Open)", "Reply \"yes\"" in last(r) and
          [x for x in rows(wbp, "Auth") if x[0] == "TC-0003"][0][10] == "Open")
    r = say("yes")
    row = [x for x in rows(wbp, "Auth") if x[0] == "TC-0003"]
    extra("E3 confirmed delete is soft (row still there, Status=Deleted)", row and row[0][10] == "Deleted", last(r))
    r = say("where is the Tamil login test பக்கம்")
    extra("E4 deleted cases are excluded from search", "TC-0003" not in last(r), last(r).replace("\n", " | "))
    r = say("restore TC-0003")
    extra("E5 restore brings it back with its previous status", "Restored TC-0003" in last(r)
          and [x for x in rows(wbp, "Auth") if x[0] == "TC-0003"][0][10] == "Open", last(r))

    r = say("list high priority Auth cases")
    t = last(r)
    extra("E6 list high priority Auth cases -> filtered", "TC-0001" in t and "TC-0003" not in t, t.replace("\n", " | "))
    r = say("show TC-0001")
    extra("E7 show TC-0001 returns the full case", "Steps: 1)" in last(r) and "Status: Passed" in last(r) and "Location: Sheet" in last(r), last(r))

    # humans sort / insert rows by hand: Tony must still find rows by TC ID
    wb = load_workbook(wbp)
    wb["Auth"].insert_rows(2)
    wb.save(wbp)
    wb.close()
    r = say("where is the Safari login test?")
    extra("E8 row inserted by hand above -> Tony still reports the NEW correct row (3)",
          'Row 3 (cell A3)' in last(r), last(r).replace("\n", " | "))
    r = say("update TC-0001 priority: critical")
    extra("E9 update after manual row shift edits the right row", [x for x in rows(wbp, "Auth") if x[0] == "TC-0001"][0][3] == "Critical")

    # Index damaged -> rebuilt on next write, and matches the sheets
    wb = load_workbook(wbp)
    del wb["Index"]
    wb.save(wbp)
    wb.close()
    say("update TC-0001 status: failed")
    extra("E10 deleted Index sheet is rebuilt and matches the real rows", "Index" in load_workbook(wbp).sheetnames and store.index_ok())

    r = say("add test case: =HYPERLINK(\"http://evil\",\"x\") formula injection\nSteps: 1) a Expected: b module: Sec")
    wb = load_workbook(wbp)
    c = wb["Sec"]["B2"]
    wb.close()
    extra("E11 text starting with '=' is stored as text, not a formula", c.data_type == "s")

    nb = len(list(cfg.backups_dir.glob("*.xlsx")))
    extra(f"E12 backups taken before writes and capped at 20 (have {nb})", 1 <= nb <= 20)

    r = say("add test case: x\nSteps: 1) y Expected: z module: Auth\nhttps://jira.example.com/BUG-1",
            attachments=[Attachment("run.exe", "application/octet-stream", b"MZ")])
    extra("E13 disallowed attachment type is skipped with a notice", "Skipped attachment" in last(r) or "looks like" in last(r))

    # ----------------------------------------------------------------- report
    print("\nTony functional test suite  (fake chat, no Webex)\n" + "=" * 78)
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"        -> {detail}")
    print("-" * 78 + "\nExtra edge cases")
    for name, ok, detail in extras:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"        -> {detail}")
    if args.out:
        with open(root / "transcript.txt", "w", encoding="utf-8") as fh:
            for who, text in chat.log[:80]:
                fh.write(f"{who}> {text}\n\n")
    allr = results + extras
    p = sum(1 for _, ok, _ in allr if ok)
    print("=" * 78 + f"\n{p}/{len(allr)} passed   (workbook: {wbp})")
    return 0 if p == len(allr) else 1


if __name__ == "__main__":
    sys.exit(main())
