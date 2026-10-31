"""Tony service orchestrator: maps incoming chat commands to Excel operations and replies."""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from app.config import Config
from app.integrations.base import ChatAdapter
from app.models import CaseRecord, Draft, IncomingMessage
from app.services import similarity as textsim
from app.services.command_parser import (
    PRIORITIES,
    STATUSES,
    TC_RE,
    Command,
    merge_reply,
    norm_tc,
    parse_case,
    parse_command,
    parse_update,
    split_cases,
    strip_mention,
)
from app.services.security import mask_text
from app.storage.excel import ExcelStore, StoreError, WorkbookLocked
from app.storage.queue import Job, WriteQueue
from app.storage.state import State

log = logging.getLogger("tony.services.bot_service")

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf", ".txt", ".log", ".json", ".csv", ".mp4"}

_CONFIRM = re.compile(r"(?i)^(?:yes|y|yep|yeah|confirm(?:ed)?(?:\s+delete)?|ok(?:ay)?|sure|do\s+it)\s*[.!]*$")
_CANCEL = re.compile(r"(?i)^(?:no|n|nope|cancel|stop|abort|skip|never\s*mind|discard)\s*[.!]*$")
_NEW = re.compile(r"(?i)^(?:new|save(?:\s+(?:it\s+)?(?:as\s+)?new)?|add(?:\s+(?:it\s+)?(?:as\s+)?new)?|keep\s+both|separate|save\s+anyway)\s*[.!]*$")
_UPD = re.compile(r"(?i)^(?:update(?:\s+(?:it|that|that\s+one|existing|the\s+existing(?:\s+one)?|TC[-_ ]?\d+))?|merge)\s*[.!]*$")

HELP = """Hi, I'm Tony. I keep your team's test cases in Excel and tell you exactly where each one lives.

Save:    @tony add test case: <title>. Steps: 1) ... 2) ... Expected: ... Actual: ... module: Auth priority: high
         (attach a screenshot and/or paste a link; several cases in one message are fine: "Case 1: ... Case 2: ...")
Find:    @tony where is the Safari login test?
Show:    @tony show TC-0042
List:    @tony list high priority Auth cases   (also: open / failed / passed ...)
Update:  @tony update TC-0042 status: passed   (also priority, title, steps, expected, actual, environment, links)
Delete:  @tony delete TC-0042   (soft delete, I'll ask you to confirm)   |   @tony restore TC-0042

I never guess steps or expected results: if they are missing I'll ask. Passwords, tokens and card numbers are masked before saving."""


def short_link(url: str) -> str:
    u = urlparse(url)
    seg = [s for s in u.path.split("/") if s]
    return seg[-1] if seg else (u.netloc or url)


def reporter_name(msg: IncomingMessage) -> str:
    n = msg.user_name or msg.user_id
    if "@" in n and not n.startswith("@"):
        return "@" + n.split("@")[0]
    return n if n.startswith("@") else "@" + re.sub(r"\s+", ".", n.strip().lower())


class TonyService:
    def __init__(self, cfg: Config, store: ExcelStore, state: State, adapter: ChatAdapter):
        self.cfg, self.store, self.state, self.chat = cfg, store, state, adapter
        self.wq = WriteQueue(store.is_locked, cfg.lock_retry_seconds, cfg.lock_max_wait_seconds)
        self.last_event_ts: float = 0.0
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)

    def handle(self, msg: IncomingMessage) -> None:
        try:
            self._handle(msg)
        except WorkbookLocked:
            self._send(msg, "The workbook is busy right now and I couldn't read it. Please try again in a minute.")
        except Exception:
            log.exception("unhandled error for event %s", msg.event_id)
            self._send(msg, "Sorry, something went wrong on my side. Nothing was saved. Please try again; "
                            "if it keeps happening tell the admin (event " + msg.event_id[-8:] + ").")

    def _send(self, msg_or_room, text: str):
        room = msg_or_room.room_id if hasattr(msg_or_room, "room_id") else msg_or_room
        try:
            self.chat.send(room, text)
        except Exception:
            log.exception("send failed")

    def _handle(self, msg: IncomingMessage):
        if not msg.addressed:
            return
        if not self.state.claim_event(msg.event_id):
            log.info("duplicate event %s ignored", msg.event_id)
            return
        self.last_event_ts = time.time()
        text, n_masked = mask_text(msg.text or "", self.cfg.mask_emails)
        text = strip_mention(text)
        cmd = parse_command(text)
        pend = self.state.get_pending(msg.room_id, msg.user_id)
        if pend and self._handle_pending(msg, pend, text, cmd, n_masked):
            return
        k = cmd.kind
        if k == "help":
            self._send(msg, HELP)
        elif k == "add":
            self._cmd_add(msg, cmd, n_masked)
        elif k == "show":
            self._cmd_show(msg, cmd)
        elif k == "update":
            self._cmd_update(msg, cmd, n_masked)
        elif k == "delete":
            self._cmd_delete(msg, cmd)
        elif k == "restore":
            self._cmd_restore(msg, cmd)
        elif k == "list":
            self._cmd_list(msg, cmd)
        elif k == "find":
            self._cmd_find(msg, cmd)
        else:
            self._send(msg, "I didn't catch that. To save a case, start with `add test case:` and give me the steps and "
                            "expected result. To look one up, ask `where is ...`. Say `help` for everything I can do.")

    def _stage(self, msg: IncomingMessage) -> tuple[list[str], list[str]]:
        paths, rejected = [], []
        tag = hashlib.sha1(msg.event_id.encode()).hexdigest()[:10]
        for i, a in enumerate(msg.attachments, 1):
            ext = Path(a.filename or "").suffix.lower() or (mimetypes.guess_extension(a.content_type or "") or "")
            ext = ".jpg" if ext == ".jpe" else ext
            if ext not in ALLOWED_EXT:
                rejected.append(f"{a.filename or 'file'} (type not allowed)")
                continue
            if len(a.data) > self.cfg.max_attachment_mb * 1024 * 1024:
                rejected.append(f"{a.filename} (over {self.cfg.max_attachment_mb} MB)")
                continue
            rel = f"attachments/_staging/{tag}_{i}{ext}"
            (self.cfg.data_dir / rel).write_bytes(a.data)
            paths.append(rel)
        return paths, rejected

    def _unstage(self, paths: list[str]):
        for p in paths:
            (self.cfg.data_dir / p).unlink(missing_ok=True)

    def _loc(self, c) -> str:
        sheet, row = (c["sheet"], c["row"]) if isinstance(c, dict) else (c.sheet, c.row)
        return f'Sheet "{sheet}", Row {row}'

    def _queued_note(self, msg, what: str):
        def f():
            self._send(msg, f"The workbook is open in another program (Excel), so I've queued {what} and will keep retrying. "
                            f"I'll confirm here the moment it's saved.")
        return f

    def _submit(self, msg, what, run, on_done, after=None):
        def done(res):
            on_done(res)
            if after:
                after()

        def err(e):
            if isinstance(e, StoreError):
                self._send(msg, f"I couldn't do that: {e}")
            elif isinstance(e, WorkbookLocked):
                self._send(msg, "The workbook stayed locked for too long, so I gave up on this request. Nothing was changed. "
                                "Close Excel and send it again.")
            else:
                self._send(msg, "Sorry, the save failed and nothing was changed. Please try again.")
            if after:
                after()
        self.wq.submit(Job(run=run, on_done=done, on_error=err, on_queued=self._queued_note(msg, what)))

    def _cmd_add(self, msg: IncomingMessage, cmd: Command, n_masked: int):
        chunks = split_cases(cmd.arg)
        if not chunks:
            self._send(msg, "Tell me the case, e.g.\n@tony add test case: Login fails on Safari. Steps: 1) open /login "
                            "2) enter valid user 3) click Sign in. Expected: dashboard opens. Actual: spinner forever. "
                            "module: Auth priority: high")
            return
        staged, rejected = self._stage(msg)
        drafts: list[Draft] = []
        for i, ch in enumerate(chunks, 1):
            d = parse_case(ch)
            d.reporter = reporter_name(msg)
            d.source_key = f"{msg.event_id}#{i}"
            drafts.append(d)
        drafts[0].attachments = staged
        notes = []
        if n_masked:
            notes.append(f"Masked {n_masked} sensitive value(s) (passwords/tokens/cards) before saving.")
        if rejected:
            notes.append("Skipped attachment(s): " + ", ".join(rejected) + ".")
        if len(chunks) > 1 and staged:
            notes.append("Attachments were linked to the first case.")
        self._process(msg, drafts, notes)

    def _process(self, msg, drafts: list[Draft], notes: list[str]):
        ready: list[Draft] = []
        question = None
        cases = None
        for i, d in enumerate(drafts):
            miss = d.missing()
            if miss:
                label = f'"{d.title}"' if d.title else "this case"
                question = (f"I can't save {label} yet, I need:\n  - " + "\n  - ".join(miss) +
                            "\nReply with e.g.  steps: 1) ... 2) ...  expected: ...   (I never guess these; "
                            "say `cancel` to drop it.)")
                self.state.set_pending(msg.room_id, msg.user_id, {"kind": "followup", "draft": d.to_json(),
                                                                  "rest": [x.to_json() for x in drafts[i + 1:]]})
                break
            cases = cases if cases is not None else self.store.all_cases()
            rest_txt = f"{d.steps} {d.expected} {d.actual}"
            dups = textsim.duplicate_candidates(d.title, rest_txt, cases, self.cfg.duplicate_threshold)
            if dups:
                c, s = dups[0]
                question = (f'This looks like {c.tc_id} in {c.sheet}!row {c.row} ("{c.title}"), {round(s * 100)}% similar.\n'
                            f'Reply "new" to save it as a new case, "update" to merge these details into {c.tc_id}, '
                            f'or "cancel".')
                self.state.set_pending(msg.room_id, msg.user_id, {"kind": "dupe", "draft": d.to_json(), "match": c.tc_id,
                                                                  "rest": [x.to_json() for x in drafts[i + 1:]]})
                break
            ready.append(d)
        after = (lambda: self._send(msg, question)) if question else None
        if ready:
            self._submit(msg, f"{len(ready)} case(s)", lambda: self.store.add_cases(ready, ready[0].reporter),
                         lambda res: self._send(msg, self._fmt_saved(res, notes)), after)
        elif question:
            if notes:
                question += "\n" + " ".join(notes)
            self._send(msg, question)

    def _fmt_saved(self, results: list[dict], notes: list[str]) -> str:
        blocks = []
        for r in results:
            lines = [f'Saved as {r["tc_id"]}' + (" (this message was already saved earlier)" if r["replayed"] else "")]
            if len(results) > 1:
                lines[0] += f' "{r["title"]}"'
            lines.append(f'File: {self.cfg.workbook_name} | Sheet: {r["sheet"]} | Row {r["row"]} | Cells {r["range"]}')
            extra = []
            for a in r["attachments"]:
                extra.append(f'{"Screenshot" if Path(a).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp") else "Attachment"}: {a}')
            if r["links"]:
                extra.append("Link: " + ", ".join(short_link(u) for u in r["links"]))
            if extra:
                lines.append("   ".join(extra))
            d = []
            if r["defaults"]["module"]:
                d.append("no module given, filed under General")
            if r["defaults"]["priority"]:
                d.append("priority defaulted to Medium")
            if d:
                lines.append("Note: " + "; ".join(d) + ". Tell me `update " + r["tc_id"] + " priority: ...` to change.")
            blocks.append("\n".join(lines))
        head = f"Saved {len(results)} cases:\n\n" if len(results) > 1 else ""
        tail = ("\n" + " ".join(notes)) if notes else ""
        wb = f"\nWorkbook: {self.cfg.workbook_url}" if self.cfg.workbook_url else ""
        return head + "\n\n".join(blocks) + tail + wb

    def _handle_pending(self, msg, pend: dict, text: str, cmd: Command, n_masked: int) -> bool:
        kind = pend["kind"]
        room, user = msg.room_id, msg.user_id
        if kind == "delete":
            if _CONFIRM.match(text):
                self.state.clear_pending(room, user)
                self._do_delete(msg, pend["tc_id"])
                return True
            if _CANCEL.match(text):
                self.state.clear_pending(room, user)
                self._send(msg, f"OK, {pend['tc_id']} is untouched.")
                return True
            if cmd.kind != "unknown":
                self.state.clear_pending(room, user)
                return False
            self._send(msg, f'Delete {pend["tc_id"]}? Reply "yes" to confirm or "cancel".')
            return True

        draft = Draft.from_json(pend["draft"])
        rest = [Draft.from_json(x) for x in pend["rest"]]
        if _CANCEL.match(text):
            self.state.clear_pending(room, user)
            self._unstage(draft.attachments)
            self._send(msg, "Dropped it, nothing saved." + (f" Moving on to the next case ({len(rest)} left)." if rest else ""))
            if rest:
                self._process(msg, rest, [])
            return True

        if kind == "dupe":
            if _NEW.match(text):
                self.state.clear_pending(room, user)
                self._submit(msg, "the new case", lambda: self.store.add_cases([draft], draft.reporter),
                             lambda res: self._send(msg, self._fmt_saved(res, [])),
                             (lambda: self._process(msg, rest, [])) if rest else None)
                return True
            if _UPD.match(text):
                self.state.clear_pending(room, user)
                self._merge_into_existing(msg, pend["match"], draft, rest)
                return True
            if cmd.kind != "unknown":
                self.state.clear_pending(room, user)
                self._unstage(draft.attachments)
                return False
            self._send(msg, f'Reply "new" (save as a new case), "update" (merge into {pend["match"]}) or "cancel".')
            return True

        if kind == "followup":
            if cmd.kind not in ("unknown",) and not LABEL_ONLY(text):
                self.state.clear_pending(room, user)
                self._unstage(draft.attachments)
                return False
            staged, rejected = self._stage(msg)
            draft.attachments += staged
            merge_reply(draft, text)
            if draft.missing():
                self.state.set_pending(room, user, {"kind": "followup", "draft": draft.to_json(),
                                                    "rest": [x.to_json() for x in rest]})
                self._send(msg, "Thanks, still missing: " + ", ".join(draft.missing()) +
                           ".\nReply like  steps: 1) ... 2) ...  expected: ...   or `cancel`.")
                return True
            self.state.clear_pending(room, user)
            notes = [f"Masked {n_masked} sensitive value(s) before saving."] if n_masked else []
            self._process(msg, [draft] + rest, notes)
            return True
        return False

    def _merge_into_existing(self, msg, tc_id: str, d: Draft, rest: list[Draft]):
        existing = self.store.get(tc_id)
        if not existing:
            self._send(msg, f"{tc_id} no longer exists; save as new with `add test case:` again.")
            return
        changes = {}
        for k in ("priority", "steps", "expected", "actual", "environment"):
            v = getattr(d, k)
            if v and v != getattr(existing, k):
                changes[k] = v
        links = [x for x in existing.links.split("\n") if x]
        merged = links + [u for u in d.links if u not in links]
        if merged != links:
            changes["links"] = "\n".join(merged)

        def done(res):
            if not res["diffs"]:
                self._send(msg, f"{tc_id} already had all of that, nothing changed.")
            else:
                self._send(msg, self._fmt_updated(res))
        self._submit(msg, f"the update to {tc_id}",
                     lambda: self.store.update_case(tc_id, changes, d.reporter, d.source_key, d.attachments),
                     done, (lambda: self._process(msg, rest, [])) if rest else None)

    def _fmt_full(self, c: CaseRecord) -> str:
        lines = [f'{c.tc_id}  "{c.title}"',
                 f'Location: {self._loc(c)} (cells {c.cell_range})',
                 f"Module: {c.module} | Priority: {c.priority} | Status: {c.status}"]
        for label, v in (("Steps", c.steps), ("Expected", c.expected), ("Actual", c.actual),
                         ("Environment", c.environment), ("Links", c.links.replace("\n", ", ")),
                         ("Attachments", c.attachments.replace("\n", ", ")), ("Reporter / Date", c.reporter)):
            if v:
                lines.append(f"{label}: {v}")
        return "\n".join(lines)

    def _cmd_show(self, msg, cmd):
        c = self.store.get(cmd.tc_id)
        self._send(msg, self._fmt_full(c) if c else f"I can't find {cmd.tc_id} in the workbook. (I don't guess.)")

    def _cmd_find(self, msg, cmd):
        if m := TC_RE.search(cmd.arg):
            c = self.store.get(norm_tc(m.group(0)))
            self._send(msg, (f'{c.tc_id} "{c.title}"\nLocation: {self._loc(c)} (cell A{c.row})' +
                             (f"\nStatus: {c.status}" if c.status else "")) if c
                       else f"I can't find {norm_tc(m.group(0))} in the workbook.")
            return
        q = cmd.arg.strip(" ?.!")
        if not textsim.tokens(q):
            self._send(msg, "What should I look for? e.g. `where is the Safari login test?`")
            return
        cases = self.store.all_cases()
        hits = [(c, s) for c, s in textsim.search(q, cases) if s >= 0.4]
        if not hits or hits[0][1] < self.cfg.search_threshold:
            live = sum(1 for c in cases if not c.deleted)
            self._send(msg, f'No match for "{q}" in the workbook ({live} cases searched). I won\'t guess. '
                            f'Try other keywords, or say `list` to browse.')
            return
        best, _ = hits[0]
        lines = [f'Best match: {best.tc_id} "{best.title}"',
                 f'Location: {self._loc(best)} (cell A{best.row})   Status: {best.status or "-"}']
        for c, s in hits[1:4]:
            lines.append(f"Also similar: {c.tc_id} ({self._loc(c)}), {round(s * 100)}% match")
        if self.cfg.workbook_url:
            lines.append(f"Open: {self.cfg.workbook_url}")
        self._send(msg, "\n".join(lines))

    def _cmd_list(self, msg, cmd):
        arg = cmd.arg
        low = arg.lower()
        cases = self.store.all_cases()
        pri = next((v for pat, v in PRIORITIES if re.search(pat, low)), "")
        stat = ""
        for k, v in sorted(STATUSES.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", low):
                stat = v
                break
        if re.search(r"\bdeleted\b", low):
            stat = "Deleted"
        mod = next((s for s in {c.sheet for c in cases} if re.search(rf"(?<![a-z0-9]){re.escape(s.lower())}(?![a-z0-9])", low)), "")
        sel = [c for c in cases if c.deleted] if stat == "Deleted" else [c for c in cases if not c.deleted]
        if pri:
            sel = [c for c in sel if c.priority.lower() == pri.lower()]
        if stat and stat != "Deleted":
            sel = [c for c in sel if c.status.lower() == stat.lower()]
        if mod:
            sel = [c for c in sel if c.sheet == mod]
        leftover = re.sub(r"(?i)\b(list|all|show|me|cases?|test|tests|priority|status|module|the|of|in|with|how|many|are|there|"
                          r"deleted)\b", " ", arg)
        for w in [pri, stat, mod] + list(STATUSES) + [p for p in ("critical", "high", "medium", "low")]:
            if w:
                leftover = re.sub(rf"(?i)(?<![a-z]){re.escape(w)}(?![a-z])", " ", leftover)
        if textsim.tokens(leftover):
            keep = {c.tc_id for c, s in textsim.search(leftover, sel) if s >= self.cfg.search_threshold}
            sel = [c for c in sel if c.tc_id in keep]
        desc = " ".join(x for x in [pri and f"{pri} priority", stat and f"status {stat}", mod and f'module "{mod}"'] if x)
        if not sel:
            self._send(msg, f"No cases found{(' for ' + desc) if desc else ''}.")
            return
        sel.sort(key=lambda c: c.tc_id)
        lines = [f"{len(sel)} case(s)" + (f" ({desc})" if desc else "") + ":"]
        for c in sel[:20]:
            lines.append(f'{c.tc_id} [{c.priority or "-"} | {c.status or "-"}] {c.title}  -> {c.sheet}!A{c.row}')
        if len(sel) > 20:
            lines.append(f"...and {len(sel) - 20} more. Narrow it down, e.g. `list high priority Auth cases`.")
        self._send(msg, "\n".join(lines))

    def _fmt_updated(self, res: dict) -> str:
        parts = []
        for k, old, new in res["diffs"]:
            if k in ("status", "priority"):
                parts.append(f"{k} {old or '-'} -> {new}")
            else:
                parts.append(f"{k} changed")
        return (f'Updated {res["tc_id"]} ({self._loc(res)}): ' + "; ".join(parts) +
                ". Previous values are kept in the Audit sheet.")

    def _cmd_update(self, msg, cmd, n_masked):
        changes, problems = parse_update(cmd.arg)
        staged, rejected = self._stage(msg)
        if not changes and not staged:
            self._send(msg, ("\n".join(problems) + "\n" if problems else "") +
                       f"Tell me what to change, e.g. `update {cmd.tc_id} status: passed`.")
            return
        c = self.store.get(cmd.tc_id)
        if not c:
            self._unstage(staged)
            self._send(msg, f"I can't find {cmd.tc_id} in the workbook.")
            return
        pre = ("\n".join(problems) + "\n") if problems else ""

        def done(res):
            tail = f"\nMasked {n_masked} sensitive value(s)." if n_masked else ""
            if not res["diffs"]:
                self._send(msg, f"{pre}{cmd.tc_id} already has those values, nothing changed.")
            else:
                self._send(msg, pre + self._fmt_updated(res) + tail)
        self._submit(msg, f"the update to {cmd.tc_id}",
                     lambda: self.store.update_case(cmd.tc_id, changes, reporter_name(msg), msg.event_id, staged), done)

    def _cmd_delete(self, msg, cmd):
        c = self.store.get(cmd.tc_id)
        if not c:
            self._send(msg, f"I can't find {cmd.tc_id} in the workbook.")
            return
        if c.deleted:
            self._send(msg, f"{c.tc_id} is already deleted. Say `restore {c.tc_id}` to bring it back.")
            return
        self.state.set_pending(msg.room_id, msg.user_id, {"kind": "delete", "tc_id": c.tc_id})
        self._send(msg, f'Delete {c.tc_id} "{c.title}" ({self._loc(c)})?\nThis is a soft delete: the row stays in the workbook '
                        f'marked Deleted and can be restored with `restore {c.tc_id}`.\nReply "yes" to confirm or "cancel".')

    def _do_delete(self, msg, tc_id: str):
        def done(res):
            if res.get("already"):
                self._send(msg, f"{tc_id} was already deleted.")
            else:
                self._send(msg, f'Deleted {tc_id} ({self._loc(res)}). The row is kept, marked Deleted and greyed out; '
                                f'say `restore {tc_id}` to undo.')
        self._submit(msg, f"the delete of {tc_id}", lambda: self.store.soft_delete(tc_id, reporter_name(msg), msg.event_id), done)

    def _cmd_restore(self, msg, cmd):
        c = self.store.get(cmd.tc_id)
        if not c:
            self._send(msg, f"I can't find {cmd.tc_id} in the workbook.")
            return
        if not c.deleted:
            self._send(msg, f"{c.tc_id} isn't deleted.")
            return
        self._submit(msg, f"the restore of {cmd.tc_id}", lambda: self.store.restore(cmd.tc_id, reporter_name(msg), msg.event_id),
                     lambda res: self._send(msg, f'Restored {res["tc_id"]} ({self._loc(res)}), status back to {res["status"]}.'))


def LABEL_ONLY(text: str) -> bool:
    """True if a follow-up answer looks like labelled fields (so 'steps: ...' is not mistaken for a command)."""
    from app.services.command_parser import LABEL_RE
    return bool(LABEL_RE.search(text))

