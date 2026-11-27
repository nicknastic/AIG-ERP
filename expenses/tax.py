"""Turn stored expenses into T2125 numbers and per-partner shares."""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field

from .categories import CATEGORIES, category_for

CCA_LINE = "9936"


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Computed:
    total_cad: float
    gst_cad: float
    pst_cad: float
    cost_cad: float          # what the business is out of pocket, after recoverable GST
    deductible_cad: float    # after business-use % and the 50% meals limit
    itc_cad: float           # GST/HST input tax credit claimable on the GST return


def compute(row, gst_registered: bool) -> Computed:
    fx = _num(row["fx_rate"]) or 1.0
    total = _num(row["total"]) * fx
    gst = _num(row["gst"]) * fx
    pst = _num(row["pst"]) * fx
    cat = category_for(row["category_line"])
    biz = max(0, min(100, int(_num(row["business_pct"])))) / 100
    limit = cat.deductible_pct / 100
    cost = total - gst if gst_registered else total
    deductible = cost * biz * limit
    itc = gst * biz * limit if gst_registered else 0.0
    return Computed(
        round(total, 2), round(gst, 2), round(pst, 2), round(cost, 2), round(deductible, 2), round(itc, 2)
    )


@dataclass
class LineTotal:
    line: str
    name: str
    count: int = 0
    total_cad: float = 0.0
    deductible_cad: float = 0.0
    itc_cad: float = 0.0
    partner_a: float = 0.0
    partner_b: float = 0.0


@dataclass
class Summary:
    year: int | None
    lines: list[LineTotal] = field(default_factory=list)
    capital: LineTotal | None = None
    total_deductible: float = 0.0
    total_itc: float = 0.0
    partner_a: float = 0.0
    partner_b: float = 0.0
    needs_review: int = 0
    missing_date: int = 0
    foreign_no_rate: int = 0
    missing_gst_number: int = 0


def summarize(rows, settings: dict, year: int | None) -> Summary:
    reg = settings.get("gst_registered", "yes") == "yes"
    pa = _num(settings.get("partner_a_pct")) / 100
    pb = _num(settings.get("partner_b_pct")) / 100
    by_line: dict[str, LineTotal] = {}
    s = Summary(year=year)
    for row in rows:
        if row["status"] != "confirmed":
            s.needs_review += 1
        if not row["expense_date"]:
            s.missing_date += 1
        if (row["currency"] or "CAD").upper() != "CAD" and _num(row["fx_rate"]) == 1.0:
            s.foreign_no_rate += 1
        c = compute(row, reg)
        # CRA requires the supplier's GST number to claim an ITC on purchases of $30 or more.
        if reg and c.gst_cad > 0 and c.total_cad >= 30 and not (row["gst_number"] or "").strip():
            s.missing_gst_number += 1
        cat = category_for(row["category_line"])
        lt = by_line.setdefault(cat.line, LineTotal(cat.line, cat.name))
        lt.count += 1
        lt.total_cad += c.total_cad
        lt.deductible_cad += c.deductible_cad
        lt.itc_cad += c.itc_cad
    order = [c.line for c in CATEGORIES]
    for line in sorted(by_line, key=order.index):
        lt = by_line[line]
        lt.total_cad = round(lt.total_cad, 2)
        lt.deductible_cad = round(lt.deductible_cad, 2)
        lt.itc_cad = round(lt.itc_cad, 2)
        s.total_itc += lt.itc_cad
        if line == CCA_LINE:
            # Capital purchases are depreciated through CCA (Area A of T2125), not expensed.
            s.capital = lt
            continue
        lt.partner_a = round(lt.deductible_cad * pa, 2)
        lt.partner_b = round(lt.deductible_cad * pb, 2)
        s.lines.append(lt)
        s.total_deductible += lt.deductible_cad
        s.partner_a += lt.partner_a
        s.partner_b += lt.partner_b
    s.total_deductible = round(s.total_deductible, 2)
    s.total_itc = round(s.total_itc, 2)
    s.partner_a = round(s.partner_a, 2)
    s.partner_b = round(s.partner_b, 2)
    return s


LEDGER_COLUMNS = [
    "id", "date", "vendor", "description", "expense_type", "t2125_line", "category", "currency", "fx_rate",
    "subtotal", "gst_hst", "pst", "tip", "total", "total_cad", "business_use_pct",
    "deductible_cad", "itc_cad", "supplier_gst_number", "payment_method", "paid_by",
    "source", "receipt_file", "status", "notes",
]


def ledger_csv(rows, settings: dict) -> str:
    reg = settings.get("gst_registered", "yes") == "yes"
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(LEDGER_COLUMNS)
    for row in sorted(rows, key=lambda r: (r["expense_date"] or "", r["id"])):
        c = compute(row, reg)
        cat = category_for(row["category_line"])
        w.writerow([
            row["id"], row["expense_date"] or "", row["vendor"] or "", row["description"] or "",
            cat.type, cat.line, cat.name, row["currency"], row["fx_rate"], row["subtotal"] if row["subtotal"] is not None else "",
            row["gst"], row["pst"], row["tip"], row["total"] if row["total"] is not None else "", c.total_cad,
            row["business_pct"], c.deductible_cad, c.itc_cad, row["gst_number"] or "",
            row["payment_method"] or "", row["paid_by"] or "", row["source"], row["source_file"] or "",
            row["status"], row["notes"] or "",
        ])
    return out.getvalue()


def summary_csv(s: Summary, settings: dict) -> str:
    a, b = settings.get("partner_a_name", "Partner A"), settings.get("partner_b_name", "Partner B")
    reg = settings.get("gst_registered", "no") == "yes"
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Expense type", "T2125 line", "Category", "Receipts", "Total paid (CAD)", "Deductible (CAD)",
                f"{a} share", f"{b} share"] + (["GST/HST ITCs"] if reg else []))
    for lt in s.lines:
        w.writerow([category_for(lt.line).type, lt.line, lt.name, lt.count, lt.total_cad, lt.deductible_cad,
                    lt.partner_a, lt.partner_b] + ([lt.itc_cad] if reg else []))
    w.writerow(["Total business expenses", "9368", "", sum(l.count for l in s.lines), "",
                s.total_deductible, s.partner_a, s.partner_b])
    if s.capital:
        w.writerow([])
        w.writerow(["Equipment", CCA_LINE, "Capital purchases (CCA calculated separately, not included above)",
                    s.capital.count, s.capital.total_cad, s.capital.deductible_cad])
    if reg:
        w.writerow([])
        w.writerow(["Total GST/HST input tax credits (for the GST/HST return)", "", "", "", "", "", "", "", s.total_itc])
    return out.getvalue()
