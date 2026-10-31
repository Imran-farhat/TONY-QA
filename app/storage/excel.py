"""Excel storage engine for test case management.

Maintains Excel sheets as the source of truth with atomic writes,
index rebuilding, audit logging, and open-file lock protections.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config import Config
from app.models import CaseRecord, Draft

log = logging.getLogger("tony.storage.excel")

HEADERS = [
    "TC ID", "Title", "Module", "Priority", "Steps", "Expected", "Actual",
    "Environment", "Links", "Attachments", "Status", "Reporter / Date"
]
COL = {
    "tc_id": 1, "title": 2, "module": 3, "priority": 4, "steps": 5, "expected": 6, "actual": 7,
    "environment": 8, "links": 9, "attachments": 10, "status": 11, "reporter": 12
}
WIDTHS = [11, 42, 14, 10, 52, 30, 30, 18, 38, 34, 12, 24]
INDEX_HEADERS = ["TC ID", "Sheet", "Row", "Cell (click to jump)", "Title", "Status", "Module"]
AUDIT_HEADERS = ["Timestamp", "User", "Action", "TC ID", "Field", "Old value", "New value", "Source msg"]
RESERVED = {"index", "audit"}
TC_ID_RE = re.compile(r"^TC-(\d+)$")
FONT = "Arial"
MAX_CELL = 32000


class WorkbookLocked(Exception):
    """Workbook is open in Excel / locked by another process."""


class StoreError(Exception):
    """General storage error."""


def _sanitize_sheet(name: str) -> str:
    name = re.sub(r"[\[\]:*?/\\]", " ", name or "").strip().strip("'")
    name = re.sub(r"\s+", " ", name)[:31].strip()
    if not name:
        name = "General"
    if name.lower() in RESERVED:
        name = f"{name} tests"
    return name


def _set(cell, value):
    """Write text safely: never let user input become an executable Excel formula."""
    if isinstance(value, str):
        value = value[:MAX_CELL]
        cell.value = value
        cell.data_type = "s"
    else:
        cell.value = value
    return cell


class ExcelStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path: Path = cfg.workbook_path
        self.tz = ZoneInfo(cfg.timezone)
        self._tlock = threading.RLock()
        self._flock = FileLock(str(self.path) + ".lock", timeout=60)
        self._cache: Optional[tuple[tuple, list[CaseRecord]]] = None
        self.simulate_locked = False          # Test hook
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.attachments_dir.mkdir(parents=True, exist_ok=True)
        cfg.backups_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.transaction():
                pass                           # Creates workbook with Index + Audit

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_locked(self) -> bool:
        if self.simulate_locked:
            return True
        d, n = self.path.parent, self.path.name
        return (d / f"~${n}").exists() or (d / f".~lock.{n}#").exists()

    def _check_unlocked(self):
        if self.is_locked():
            raise WorkbookLocked(f"{self.path.name} is open in another program")

    @staticmethod
    def _module_sheets(wb: Workbook):
        return [ws for ws in wb.worksheets if ws.title.lower() not in RESERVED]

    def _style_header(self, ws, headers, widths=None):
        ws.append(headers)
        for i, _ in enumerate(headers, 1):
            c = ws.cell(row=1, column=i)
            c.font = Font(name=FONT, bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F3864")
            c.alignment = Alignment(vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(i)].width = (widths or [20] * len(headers))[i - 1]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

    def _ensure_support_sheets(self, wb: Workbook):
        if "Index" not in wb.sheetnames:
            ws = wb.create_sheet("Index", 0)
            self._style_header(ws, INDEX_HEADERS, [11, 18, 7, 20, 50, 12, 16])
        if "Audit" not in wb.sheetnames:
            ws = wb.create_sheet("Audit")
            self._style_header(ws, AUDIT_HEADERS, [22, 24, 10, 11, 14, 40, 40, 30])

    def _get_sheet(self, wb: Workbook, module: str):
        name = _sanitize_sheet(module)
        for ws in self._module_sheets(wb):
            if ws.title.lower() == name.lower():
                return ws
        if name == name.lower():
            name = name[:1].upper() + name[1:]
        pos = wb.sheetnames.index("Audit") if "Audit" in wb.sheetnames else len(wb.sheetnames)
        ws = wb.create_sheet(name, pos)
        self._style_header(ws, HEADERS, WIDTHS)
        return ws

    @staticmethod
    def _last_row(ws) -> int:
        r = ws.max_row
        while r > 1 and ws.cell(row=r, column=1).value in (None, ""):
            r -= 1
        return r

    def _load_or_create(self) -> Workbook:
        if not self.path.exists():
            wb = Workbook()
            wb.remove(wb.active)
            self._ensure_support_sheets(wb)
            return wb
        try:
            wb = load_workbook(self.path)
        except Exception as e:                       # Damaged file -> restore newest backup
            log.error("Workbook unreadable (%s); attempting restore from newest backup", e)
            backups = sorted(self.cfg.backups_dir.glob("*.xlsx"))
            if not backups:
                raise StoreError(f"Workbook is damaged and no backup exists: {e}")
            shutil.copy2(self.path, self.path.with_suffix(f".damaged-{int(self.now().timestamp())}"))
            shutil.copy2(backups[-1], self.path)
            wb = load_workbook(self.path)
        self._ensure_support_sheets(wb)
        return wb

    def _backup(self):
        if not self.path.exists():
            return
        stamp = self.now().strftime("%Y%m%d-%H%M%S-%f")
        dest = self.cfg.backups_dir / f"{self.path.stem}_{stamp}.xlsx"
        shutil.copy2(self.path, dest)
        old = sorted(self.cfg.backups_dir.glob(f"{self.path.stem}_*.xlsx"))
        for f in old[:-self.cfg.backups_keep]:
            f.unlink(missing_ok=True)

    def _atomic_save(self, wb: Workbook):
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        wb.save(tmp)
        self._backup()
        try:
            os.replace(tmp, self.path)
        except PermissionError as e:                 # Windows: file open in Excel
            tmp.unlink(missing_ok=True)
            raise WorkbookLocked(str(e))

    @contextmanager
    def transaction(self):
        """Exclusive, atomic read-modify-write context. Raises WorkbookLocked if file is locked."""
        self._check_unlocked()
        with self._tlock:
            try:
                with self._flock:
                    self._check_unlocked()
                    wb = self._load_or_create()
                    yield wb
                    self._rebuild_index(wb)
                    self._atomic_save(wb)
                    self._cache = None
            except Timeout:
                raise WorkbookLocked("another process holds the workbook lock")

    def _row_to_record(self, ws, r: int, vals) -> Optional[CaseRecord]:
        tc = str(vals[0]).strip() if vals[0] is not None else ""
        if not TC_ID_RE.match(tc):
            return None
        g = lambda i: "" if vals[i] is None else str(vals[i])
        return CaseRecord(tc, ws.title, r, g(1), g(2), g(3), g(4), g(5), g(6), g(7), g(8), g(9), g(10), g(11))

    def _scan(self, wb: Workbook) -> list[CaseRecord]:
        out = []
        for ws in self._module_sheets(wb):
            for r, vals in enumerate(ws.iter_rows(min_row=2, max_col=12, values_only=True), start=2):
                rec = self._row_to_record(ws, r, vals)
                if rec:
                    out.append(rec)
        return out

    def all_cases(self) -> list[CaseRecord]:
        """Scan sheets (cached by file mtime/size). Reads never block write queue."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return []
        key = (st.st_mtime_ns, st.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        for attempt in range(3):
            try:
                wb = load_workbook(self.path, read_only=False, data_only=True)
                break
            except PermissionError:
                if attempt == 2:
                    raise WorkbookLocked("cannot read workbook right now")
                threading.Event().wait(0.3)
            except Exception as e:
                if attempt == 2:
                    raise StoreError(str(e))
                threading.Event().wait(0.3)
        cases = self._scan(wb)
        wb.close()
        self._cache = (key, cases)
        return cases

    def get(self, tc_id: str) -> Optional[CaseRecord]:
        for c in self.all_cases():
            if c.tc_id == tc_id:
                return c
        return None

    def index_ok(self) -> bool:
        """True if Index sheet matches actual sheet layout."""
        wb = load_workbook(self.path, data_only=True)
        idx = {}
        if "Index" in wb.sheetnames:
            for r in wb["Index"].iter_rows(min_row=2, max_col=3, values_only=True):
                if r[0]:
                    idx[r[0]] = (r[1], r[2])
        real = {c.tc_id: (c.sheet, c.row) for c in self._scan(wb)}
        wb.close()
        return idx == real

    def _rebuild_index(self, wb: Workbook):
        ws = wb["Index"]
        if ws.max_row > 1:
            ws.delete_rows(2, ws.max_row)
        rows = sorted(self._scan(wb), key=lambda c: c.tc_id)
        for i, c in enumerate(rows, start=2):
            for col, v in enumerate([c.tc_id, c.sheet, c.row, f"A{c.row}", c.title, c.status, c.module], 1):
                cell = ws.cell(row=i, column=col)
                _set(cell, v)
                cell.font = Font(name=FONT)
            link = ws.cell(row=i, column=4)
            link.hyperlink = f"#'{c.sheet}'!A{c.row}"
            link.font = Font(name=FONT, color="0563C1", underline="single")

    def _audit(self, wb, user, action, tc_id, field="", old="", new="", source=""):
        ws = wb["Audit"]
        ws.append([self.now().strftime("%Y-%m-%d %H:%M:%S"), user, action, tc_id, field, "", "", source])
        r = ws.max_row
        for col, v in ((6, old), (7, new)):
            _set(ws.cell(row=r, column=col), v)
        for c in ws[r]:
            c.font = Font(name=FONT)
            c.alignment = Alignment(wrap_text=True, vertical="top")

    def _next_id(self, wb: Workbook) -> str:
        top = 0
        for c in self._scan(wb):
            top = max(top, int(c.tc_id[3:]))
        for name, col in (("Audit", 3), ("Index", 0)):
            if name in wb.sheetnames:
                for r in wb[name].iter_rows(min_row=2, values_only=True):
                    m = TC_ID_RE.match(str(r[col]).strip()) if r and r[col] else None
                    if m:
                        top = max(top, int(m.group(1)))
        return f"TC-{top + 1:04d}"

    def _find_row(self, wb: Workbook, tc_id: str):
        for ws in self._module_sheets(wb):
            for r in range(2, ws.max_row + 1):
                if ws.cell(row=r, column=1).value == tc_id:
                    return ws, r
        return None, 0

    def add_cases(self, drafts: list[Draft], user: str) -> list[dict]:
        """Write all drafts in ONE atomic transaction. Idempotent per draft.source_key."""
        results, cleanup = [], []
        with self.transaction() as wb:
            done = {}
            if "Audit" in wb.sheetnames:
                for r in wb["Audit"].iter_rows(min_row=2, values_only=True):
                    if r and r[2] == "ADD" and r[7]:
                        done[r[7]] = r[3]
            for d in drafts:
                if d.source_key and d.source_key in done:
                    ws, row = self._find_row(wb, done[d.source_key])
                    if ws:
                        results.append(self._result(done[d.source_key], ws.title, row, [], d, duplicate_event=True))
                        continue
                tc_id = self._next_id(wb)
                ws = self._get_sheet(wb, d.module or "General")
                row = self._last_row(ws) + 1
                files = self._store_attachments(tc_id, d.attachments, cleanup)
                date = self.now().strftime("%Y-%m-%d")
                reporter = f"{d.reporter or 'unknown'} / {date}"
                vals = [tc_id, d.title, ws.title, d.priority or "Medium", d.steps, d.expected, d.actual,
                        d.environment, "\n".join(d.links), "\n".join(files), d.status or "Open", reporter]
                for ci, v in enumerate(vals, 1):
                    cell = ws.cell(row=row, column=ci)
                    _set(cell, v)
                    cell.font = Font(name=FONT)
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
                if d.links:
                    ws.cell(row=row, column=COL["links"]).hyperlink = d.links[0]
                    ws.cell(row=row, column=COL["links"]).font = Font(name=FONT, color="0563C1", underline="single")
                if files:
                    ws.cell(row=row, column=COL["attachments"]).hyperlink = files[0]
                    ws.cell(row=row, column=COL["attachments"]).font = Font(name=FONT, color="0563C1", underline="single")
                self._audit(wb, d.reporter, "ADD", tc_id, "", "", d.title, d.source_key)
                results.append(self._result(tc_id, ws.title, row, files, d))
        for f in cleanup:
            Path(f).unlink(missing_ok=True)
        return results

    @staticmethod
    def _result(tc_id, sheet, row, files, d, duplicate_event=False) -> dict:
        return {"tc_id": tc_id, "sheet": sheet, "row": row, "range": f"A{row}:L{row}", "attachments": files,
                "links": list(d.links), "title": d.title, "replayed": duplicate_event,
                "defaults": {"module": not d.module, "priority": not d.priority}}

    def _store_attachments(self, tc_id: str, staged: list[str], cleanup: list, start: int = 1) -> list[str]:
        out = []
        for i, rel in enumerate(staged, start):
            src = self.cfg.data_dir / rel
            if not src.exists():
                continue
            ext = src.suffix or ".bin"
            dest_rel = f"attachments/{tc_id}_{i}{ext}"
            shutil.copy2(src, self.cfg.data_dir / dest_rel)
            cleanup.append(src)
            out.append(dest_rel)
        return out

    def update_case(self, tc_id: str, changes: dict, user: str, source: str = "",
                    new_attachments: Optional[list[str]] = None) -> dict:
        cleanup: list = []
        with self.transaction() as wb:
            ws, row = self._find_row(wb, tc_id)
            if not ws:
                raise StoreError(f"{tc_id} not found")
            rec = self._row_to_record(ws, row, [c.value for c in ws[row][:12]])
            if rec.deleted and changes.get("status") != "Open":
                raise StoreError(f"{tc_id} is deleted. Use 'restore {tc_id}' first.")
            diffs = []
            for key, new in changes.items():
                col = COL[key]
                old = ws.cell(row=row, column=col).value or ""
                if str(old) == new:
                    continue
                _set(ws.cell(row=row, column=col), new)
                self._audit(wb, user, "UPDATE", tc_id, HEADERS[col - 1], str(old), new, source)
                diffs.append((key, str(old), new))
            if new_attachments:
                existing = [x for x in str(ws.cell(row=row, column=COL["attachments"]).value or "").split("\n") if x]
                files = self._store_attachments(tc_id, new_attachments, cleanup, start=len(existing) + 1)
                if files:
                    allf = existing + files
                    _set(ws.cell(row=row, column=COL["attachments"]), "\n".join(allf))
                    self._audit(wb, user, "UPDATE", tc_id, "Attachments", "\n".join(existing), "\n".join(allf), source)
                    diffs.append(("attachments", "\n".join(existing), "\n".join(allf)))
            if "status" in changes:
                self._style_row(ws, row, deleted=(changes["status"] == "Deleted"))
        for f in cleanup:
            Path(f).unlink(missing_ok=True)
        return {"tc_id": tc_id, "sheet": ws.title, "row": row, "diffs": diffs}

    def soft_delete(self, tc_id: str, user: str, source: str = "") -> dict:
        with self.transaction() as wb:
            ws, row = self._find_row(wb, tc_id)
            if not ws:
                raise StoreError(f"{tc_id} not found")
            old = str(ws.cell(row=row, column=COL["status"]).value or "")
            if old.lower() == "deleted":
                return {"tc_id": tc_id, "sheet": ws.title, "row": row, "already": True}
            _set(ws.cell(row=row, column=COL["status"]), "Deleted")
            self._style_row(ws, row, deleted=True)
            self._audit(wb, user, "DELETE", tc_id, "Status", old, "Deleted", source)
        return {"tc_id": tc_id, "sheet": ws.title, "row": row, "already": False}

    def restore(self, tc_id: str, user: str, source: str = "") -> dict:
        with self.transaction() as wb:
            ws, row = self._find_row(wb, tc_id)
            if not ws:
                raise StoreError(f"{tc_id} not found")
            prev = "Open"
            for r in wb["Audit"].iter_rows(min_row=2, values_only=True):
                if r[3] == tc_id and r[2] == "DELETE" and r[5]:
                    prev = str(r[5])
            _set(ws.cell(row=row, column=COL["status"]), prev)
            self._style_row(ws, row, deleted=False)
            self._audit(wb, user, "RESTORE", tc_id, "Status", "Deleted", prev, source)
        return {"tc_id": tc_id, "sheet": ws.title, "row": row, "status": prev}

    @staticmethod
    def _style_row(ws, row: int, deleted: bool):
        for c in ws[row][:12]:
            if deleted:
                c.font = Font(name=FONT, strike=True, color="808080")
                c.fill = PatternFill("solid", fgColor="E7E6E6")
            else:
                link = c.hyperlink is not None
                c.font = Font(name=FONT, color="0563C1" if link else None, underline="single" if link else None)
                c.fill = PatternFill()

    def rebuild_index_now(self):
        with self.transaction():
            pass
