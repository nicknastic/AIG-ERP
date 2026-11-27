"""Keep an Excel workbook per tax year in the Excel folder chosen in Settings.

The workbook is rebuilt from the database every time an expense is added,
changed or deleted, so it always matches what the app shows. Edit expenses in
the app, not in the workbook: changes made directly in Excel are overwritten.
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import db, tax
from .categories import category_for

HEADER_FILL = PatternFill("solid", fgColor="1F5F8B")
HEADER_FONT = Font(bold=True, color="FFFFFF")
MONEY = '#,##0.00'


def workbook_path(year: int) -> Path:
    name = db.get_settings().get("business_name", "AIG").strip() or "AIG"
    safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip() or "AIG"
    return db.excel_dir() / f"{safe} Expenses {year}.xlsx"


def _header(ws, headers: list[str], widths: list[int]) -> None:
    ws.append(headers)
    for i, w in enumerate(widths, start=1):
        cell = ws.cell(row=1, column=i)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def build(year: int) -> Workbook:
    settings = db.get_settings()
    reg = settings.get("gst_registered") == "yes"
    rows = sorted(db.list_expenses(year=year), key=lambda r: (r["expense_date"] or "", r["id"]))
    a, b = settings["partner_a_name"], settings["partner_b_name"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Expenses"
    _header(ws, [
        "Date", "Vendor", "What for", "Expense type", "CRA line", "CRA category", "Total paid",
        "GST/HST", "PST", "Tip", "Currency", "Total (CAD)", "Business use %", "Deductible (CAD)",
        "Paid by", "Payment method", "Status", "Receipt", "Notes",
    ], [11, 24, 30, 26, 9, 30, 12, 10, 10, 8, 9, 12, 10, 14, 12, 16, 11, 40, 40])
    for r in rows:
        c = tax.compute(r, reg)
        cat = category_for(r["category_line"])
        ws.append([
            r["expense_date"] or "", r["vendor"] or "", r["description"] or "", cat.type, cat.line,
            cat.name, r["total"], r["gst"], r["pst"], r["tip"], r["currency"], c.total_cad,
            r["business_pct"] / 100, c.deductible_cad, r["paid_by"] or "", r["payment_method"] or "",
            "Confirmed" if r["status"] == "confirmed" else "To review",
            Path(r["source_file"]).name if r["source_file"] else "", r["notes"] or "",
        ])
        row = ws.max_row
        for col in (7, 8, 9, 10, 12, 14):
            ws.cell(row=row, column=col).number_format = MONEY
        ws.cell(row=row, column=13).number_format = "0%"
        if r["source_file"] and Path(r["source_file"]).is_file():
            cell = ws.cell(row=row, column=18)
            cell.hyperlink = Path(r["source_file"]).resolve().as_uri()
            cell.font = Font(color="1F5F8B", underline="single")
    ws.auto_filter.ref = ws.dimensions

    s = tax.summarize(rows, settings, year)
    ws2 = wb.create_sheet("Summary")
    _header(ws2, ["Expense type", "CRA line", "CRA category", "Receipts", "Paid (CAD)", "Deductible (CAD)",
                  f"{a} {settings['partner_a_pct']}%", f"{b} {settings['partner_b_pct']}%"],
            [30, 9, 34, 10, 14, 16, 14, 14])
    for lt in s.lines:
        ws2.append([category_for(lt.line).type, lt.line, lt.name, lt.count, lt.total_cad,
                    lt.deductible_cad, lt.partner_a, lt.partner_b])
    ws2.append(["Total business expenses", "9368", "", sum(l.count for l in s.lines), "",
                s.total_deductible, s.partner_a, s.partner_b])
    for cell in ws2[ws2.max_row]:
        cell.font = Font(bold=True)
    if s.capital:
        ws2.append([])
        ws2.append(["Equipment (claimed through CCA, not in the total above)", "9936", s.capital.name,
                    s.capital.count, s.capital.total_cad, s.capital.deductible_cad])
    for row in ws2.iter_rows(min_row=2, min_col=5, max_col=8):
        for cell in row:
            cell.number_format = MONEY
    ws2.append([])
    ws2.append(["Meals and entertainment count at 50%. Each partner reports their share on their own T2125."])
    if s.needs_review:
        ws2.append([f"{s.needs_review} expense(s) still need review in the app."])
    return wb


def write(year: int) -> Path:
    path = workbook_path(year)
    tmp = path.with_name(path.stem + ".tmp.xlsx")
    try:
        build(year).save(tmp)
        tmp.replace(path)  # raises PermissionError on Windows if the file is open in Excel
    finally:
        tmp.unlink(missing_ok=True)
    return path


def write_safely(year: int | None) -> str | None:
    """Update the workbook; return a warning for the user instead of failing the request."""
    if not year:
        return None
    try:
        write(year)
        return None
    except PermissionError:
        return ("Couldn't update the Excel file because it is open. Close it in Excel, "
                "then use 'Update Excel now' in Settings.")
    except OSError as e:
        return f"Couldn't update the Excel file: {e}"
