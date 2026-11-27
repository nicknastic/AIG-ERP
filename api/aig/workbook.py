"""The Excel workbook in OneDrive is the record of every expense.

Sheet "Expenses" holds one row per expense. The columns on the left are for
people; the grey columns on the right (from "ID") are what the app needs to
find receipt files again. Everything the app shows is read from this sheet,
so edits made directly in Excel to plain columns (date, vendor, amounts,
CRA line, status, notes...) are picked up. Calculated columns (expense type,
CRA category, totals in CAD, deductible) are rewritten every time.

A "Summary <year>" sheet per year is rebuilt on every save.
"""

import io
import json
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import tax
from .categories import category_for
from .store import ConflictError

SHEET = "Expenses"
HEADER_FILL = PatternFill("solid", fgColor="1F5F8B")
TECH_FILL = PatternFill("solid", fgColor="7F7F7F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
MONEY = "#,##0.00"

# (header, field, kind, width). kind: str, date, money, num, pct, int, derived
COLUMNS = [
    ("Date", "expense_date", "date", 11),
    ("Vendor", "vendor", "str", 24),
    ("What for", "description", "str", 30),
    ("Expense type", None, "derived", 28),
    ("CRA line", "category_line", "str", 9),
    ("CRA category", None, "derived", 30),
    ("Subtotal", "subtotal", "money", 11),
    ("GST/HST", "gst", "money", 10),
    ("PST", "pst", "money", 9),
    ("Tip", "tip", "money", 8),
    ("Total paid", "total", "money", 12),
    ("Currency", "currency", "str", 9),
    ("Exchange rate", "fx_rate", "num", 10),
    ("Total (CAD)", None, "derived", 12),
    ("Business use %", "business_pct", "pct", 10),
    ("Deductible (CAD)", None, "derived", 14),
    ("Paid by", "paid_by", "str", 12),
    ("Payment method", "payment_method", "str", 16),
    ("Status", "status", "str", 11),
    ("Added by", "added_by", "str", 22),
    ("Receipt", None, "derived", 44),
    ("Notes", "notes", "str", 40),
    # technical
    ("ID", "id", "int", 6),
    ("Source", "source", "str", 9),
    ("File id", "source_item", "str", 14),
    ("File path", "source_path", "str", 30),
    ("File link", "source_url", "str", 14),
    ("File hash", "source_hash", "str", 14),
    ("Email subject", "email_subject", "str", 20),
    ("Email from", "email_from", "str", 20),
    ("Supplier GST no.", "gst_number", "str", 14),
    ("Confidence", "confidence", "str", 10),
    ("Created", "created_at", "str", 18),
]
FIRST_TECH = "ID"
FIELDS = [f for _, f, k, _ in COLUMNS if f]

DEFAULTS = {
    "currency": "CAD", "fx_rate": 1.0, "gst": 0.0, "pst": 0.0, "tip": 0.0, "business_pct": 100,
    "category_line": "9270", "status": "review", "paid_by": "business", "source": "receipt",
}


def blank_row(**values) -> dict:
    row = {f: None for f in FIELDS}
    row.update(DEFAULTS)
    row.update(values)
    return row


def _to_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _to_date(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s[:10]


def _parse(kind: str, v):
    if kind == "date":
        return _to_date(v)
    if kind == "money":
        return _to_float(v)
    if kind == "num":
        return _to_float(v)
    if kind == "pct":
        f = _to_float(v)
        if f is None:
            return 100
        return int(round(f * 100)) if f <= 1 else int(round(f))
    if kind == "int":
        f = _to_float(v)
        return int(f) if f is not None else None
    s = _to_str(v)
    return s


def rows_from_bytes(data: bytes) -> list[dict]:
    wb = load_workbook(io.BytesIO(data), data_only=True)
    if SHEET not in wb.sheetnames:
        return []
    ws = wb[SHEET]
    header = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(header) if h}
    rows = []
    for values in ws.iter_rows(min_row=2, values_only=True):
        if not any(v not in (None, "") for v in values):
            continue
        row = blank_row()
        for h, field, kind, _ in COLUMNS:
            if field and h in idx and idx[h] < len(values):
                parsed = _parse(kind, values[idx[h]])
                if parsed is not None or kind not in ("money", "num") or field in ("subtotal", "total"):
                    row[field] = parsed
        for k in ("gst", "pst", "tip"):
            row[k] = row[k] or 0.0
        row["fx_rate"] = row["fx_rate"] or 1.0
        row["currency"] = (row["currency"] or "CAD").upper()
        row["category_line"] = category_for(row["category_line"]).line
        status = (row["status"] or "").lower()
        row["status"] = "confirmed" if status.startswith("confirm") else "review"
        rows.append(row)
    # Rows added by hand in Excel get an ID so the app can address them.
    next_id = max([r["id"] or 0 for r in rows] + [0]) + 1
    for r in rows:
        if not r["id"]:
            r["id"], next_id = next_id, next_id + 1
    return rows


def _header(ws, cols) -> None:
    ws.append([c[0] for c in cols])
    tech = False
    for i, (h, _, _, w) in enumerate(cols, start=1):
        tech = tech or h == FIRST_TECH
        cell = ws.cell(row=1, column=i)
        cell.fill, cell.font = (TECH_FILL if tech else HEADER_FILL), HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "B2"


def build(rows: list[dict], settings: dict) -> bytes:
    reg = settings.get("gst_registered") == "yes"
    rows = sorted(rows, key=lambda r: (r["expense_date"] or "9999", r["id"]))
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    _header(ws, COLUMNS)
    for r in rows:
        c = tax.compute(r, reg)
        cat = category_for(r["category_line"])
        derived = {
            "Expense type": cat.type, "CRA category": cat.name, "Total (CAD)": c.total_cad,
            "Deductible (CAD)": c.deductible_cad,
            "Receipt": (r["source_path"] or "").rsplit("/", 1)[-1] or None,
        }
        out = []
        for h, field, kind, _ in COLUMNS:
            if field is None:
                out.append(derived[h])
            elif kind == "date" and r[field]:
                try:
                    out.append(date.fromisoformat(r[field]))
                except ValueError:
                    out.append(r[field])
            elif kind == "pct":
                out.append((r[field] if r[field] is not None else 100) / 100)
            elif field == "status":
                out.append("Confirmed" if r["status"] == "confirmed" else "To review")
            else:
                out.append(r[field])
        ws.append(out)
        n = ws.max_row
        for i, (h, field, kind, _) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=n, column=i)
            if kind == "money" or h in ("Total (CAD)", "Deductible (CAD)"):
                cell.number_format = MONEY
            elif kind == "pct":
                cell.number_format = "0%"
            elif kind == "date":
                cell.number_format = "yyyy-mm-dd"
            if h == "Receipt" and r["source_url"]:
                cell.hyperlink = r["source_url"]
                cell.font = Font(color="1F5F8B", underline="single")
    ws.auto_filter.ref = ws.dimensions

    years = sorted({int(r["expense_date"][:4]) for r in rows
                    if r["expense_date"] and r["expense_date"][:4].isdigit()}, reverse=True)
    for y in years:
        _summary_sheet(wb, [r for r in rows if (r["expense_date"] or "").startswith(str(y))], settings, y)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _summary_sheet(wb, rows, settings, year) -> None:
    a, b = settings["partner_a_name"], settings["partner_b_name"]
    s = tax.summarize(rows, settings, year)
    ws = wb.create_sheet(f"Summary {year}")
    cols = [("Expense type", None, None, 30), ("CRA line", None, None, 9), ("CRA category", None, None, 34),
            ("Receipts", None, None, 10), ("Paid (CAD)", None, None, 14), ("Deductible (CAD)", None, None, 16),
            (f"{a} {settings['partner_a_pct']}%", None, None, 14), (f"{b} {settings['partner_b_pct']}%", None, None, 14)]
    _header(ws, cols)
    for lt in s.lines:
        ws.append([category_for(lt.line).type, lt.line, lt.name, lt.count, lt.total_cad,
                   lt.deductible_cad, lt.partner_a, lt.partner_b])
    ws.append(["Total business expenses", "9368", "", sum(l.count for l in s.lines), "",
               s.total_deductible, s.partner_a, s.partner_b])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    if s.capital:
        ws.append([])
        ws.append(["Equipment (claimed through CCA, not in the total above)", "9936", s.capital.name,
                   s.capital.count, s.capital.total_cad, s.capital.deductible_cad])
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=8):
        for cell in row:
            cell.number_format = MONEY
    ws.append([])
    ws.append(["Meals and entertainment count at 50%. Each partner reports their share on their own T2125."])
    if s.needs_review:
        ws.append([f"{s.needs_review} expense(s) still marked 'To review'."])


class Ledger:
    """Read-modify-write access to the workbook, retrying if someone else saved in between."""

    def __init__(self, store, settings: dict):
        self.store, self.settings = store, settings

    @property
    def path(self) -> str:
        name = "".join(ch for ch in self.settings.get("business_name", "AIG") if ch.isalnum() or ch in " -_").strip()
        return f"{self.settings['excel_folder'].strip('/')}/{name or 'AIG'} Expenses.xlsx"

    def load(self) -> tuple[list[dict], str | None]:
        found = self.store.read(self.path)
        if not found:
            return [], None
        data, etag = found
        return rows_from_bytes(data), etag

    def rows(self) -> list[dict]:
        return self.load()[0]

    def get(self, expense_id: int) -> dict | None:
        return next((r for r in self.rows() if r["id"] == expense_id), None)

    def save(self, rows: list[dict], etag: str | None) -> None:
        data = build(rows, self.settings)
        if etag is None:
            self.store.write(self.path, data, must_not_exist=True)
        else:
            self.store.write(self.path, data, if_match=etag)

    def mutate(self, fn, attempts: int = 4):
        """fn(rows) changes rows in place and returns a result."""
        for i in range(attempts):
            rows, etag = self.load()
            result = fn(rows)
            try:
                self.save(rows, etag)
                return result
            except ConflictError:
                if i == attempts - 1:
                    raise
        return None

    def rewrite(self) -> None:
        self.mutate(lambda rows: None)


def next_id(rows: list[dict]) -> int:
    return max([r["id"] or 0 for r in rows] + [0]) + 1


def dumps(row: dict) -> str:
    return json.dumps(row)
