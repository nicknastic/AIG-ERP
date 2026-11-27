"""Web app: upload receipts and emails, review what was read, export for tax time."""

import hashlib
import io
import json
import mimetypes
import re
import zipfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import db, emails, extract, tax
from .categories import CATEGORIES, category_for

@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    yield


app = FastAPI(title="AIG expenses", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["categories"] = CATEGORIES
templates.env.globals["category_for"] = category_for

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
EMAIL_EXTS = {".eml", ".msg"}


def _media_type(filename: str, declared: str | None) -> str:
    guessed = mimetypes.guess_type(filename)[0]
    ext = Path(filename).suffix.lower()
    if ext in {".heic", ".heif"}:
        return "image/heic"
    return guessed or declared or "application/octet-stream"


def _safe_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[-80:]
    return stem or "file"


def _store(raw: bytes, filename: str, digest: str) -> str:
    name = f"{digest[:12]}_{_safe_name(filename)}"
    (db.receipts_dir() / name).write_bytes(raw)
    return name


def _from_extraction(fields: dict) -> dict:
    out = {k: fields.get(k) for k in (
        "vendor", "expense_date", "description", "currency", "subtotal", "gst", "pst", "tip",
        "total", "payment_method", "gst_number", "category_line", "confidence", "notes",
    )}
    out["currency"] = (out.get("currency") or "CAD").upper()
    for k in ("gst", "pst", "tip"):
        out[k] = out.get(k) or 0
    if out.get("category_line") not in {c.line for c in CATEGORIES}:
        out["category_line"] = "9270"
    if fields.get("is_receipt") is False:
        out["notes"] = ("Doesn't look like a receipt. " + (out.get("notes") or "")).strip()
        out["confidence"] = "low"
    out["raw_extraction"] = json.dumps(fields)
    return out


def ingest(raw: bytes, filename: str, declared_type: str | None) -> dict:
    """Store one uploaded file and create an expense from it. Returns a result row for the UI."""
    digest = hashlib.sha256(raw).hexdigest()
    existing = db.find_by_hash(digest)
    if existing:
        return {"file": filename, "status": "duplicate", "id": existing["id"]}

    ext = Path(filename).suffix.lower()
    stored = _store(raw, filename, digest)
    record: dict = {"source_file": stored, "source_hash": digest, "status": "review", "paid_by": "business"}
    error = None

    try:
        if ext in EMAIL_EXTS:
            parsed = emails.parse_msg(raw) if ext == ".msg" else emails.parse_eml(raw)
            record.update(source="email", email_subject=parsed["subject"][:500], email_from=parsed["from"][:300])
            record["description"] = parsed["subject"][:300]
            if extract.extraction_available():
                fields = extract.extract_from_email(
                    parsed["subject"], parsed["from"], parsed["body"],
                    [(d, t) for d, t, _ in parsed["attachments"]],
                )
                record.update(_from_extraction(fields))
        else:
            media = _media_type(filename, declared_type)
            record["source"] = "receipt"
            if extract.extraction_available():
                record.update(_from_extraction(extract.extract_from_file(raw, media)))
            elif media not in extract.IMAGE_TYPES | extract.HEIC_TYPES | {extract.PDF_TYPE}:
                raise ValueError(f"Unsupported file type: {media}")
    except (extract.ExtractionError, ValueError, json.JSONDecodeError) as e:
        error = str(e)
        record["notes"] = f"Could not read automatically: {error}"
        record["confidence"] = "low"

    if not extract.extraction_available() and not error:
        record["notes"] = "No Anthropic API key set, so fill in the details by hand."
    record.setdefault("source", "receipt")
    expense_id = db.insert_expense(record)
    return {"file": filename, "status": "error" if error else "added", "id": expense_id, "error": error}


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, year: str = "", status: str = ""):
    y = int(year) if year.isdigit() else None
    rows = db.list_expenses(year=y, status=status or None)
    settings = db.get_settings()
    reg = settings["gst_registered"] == "yes"
    items = [(r, tax.compute(r, reg)) for r in rows]
    return templates.TemplateResponse(request, "index.html", {
        "items": items, "years": db.years(), "year": y, "status": status,
        "settings": settings, "extraction": extract.extraction_available(),
        "review_count": sum(1 for r in rows if r["status"] != "confirmed"),
    })


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, files: list[UploadFile] = File(...)):
    results = []
    for f in files:
        raw = await f.read()
        if not raw:
            continue
        if len(raw) > MAX_UPLOAD_BYTES:
            results.append({"file": f.filename, "status": "error", "error": "File is larger than 25 MB"})
            continue
        results.append(ingest(raw, f.filename or "upload", f.content_type))
    return templates.TemplateResponse(request, "uploaded.html", {"results": results})


@app.post("/expenses/new")
def new_expense():
    expense_id = db.insert_expense({
        "source": "manual", "status": "review", "paid_by": "business",
        "expense_date": date.today().isoformat(),
    })
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)


@app.get("/expenses/{expense_id}", response_class=HTMLResponse)
def edit_expense(request: Request, expense_id: int):
    row = db.get_expense(expense_id)
    if not row:
        raise HTTPException(404)
    settings = db.get_settings()
    media = mimetypes.guess_type(row["source_file"] or "")[0] or ""
    return templates.TemplateResponse(request, "edit.html", {
        "e": row, "settings": settings, "media": media,
        "c": tax.compute(row, settings["gst_registered"] == "yes"),
    })


def _opt_float(v: str) -> float | None:
    v = (v or "").replace("$", "").replace(",", "").strip()
    if not v:
        return None
    return float(v)


@app.post("/expenses/{expense_id}")
def save_expense(
    expense_id: int,
    vendor: str = Form(""), expense_date: str = Form(""), description: str = Form(""),
    currency: str = Form("CAD"), fx_rate: str = Form("1"), subtotal: str = Form(""),
    gst: str = Form("0"), pst: str = Form("0"), tip: str = Form("0"), total: str = Form(""),
    payment_method: str = Form(""), paid_by: str = Form("business"),
    category_line: str = Form("9270"), business_pct: int = Form(100), gst_number: str = Form(""),
    notes: str = Form(""), action: str = Form("save"),
):
    if not db.get_expense(expense_id):
        raise HTTPException(404)
    try:
        data = {
            "vendor": vendor.strip() or None,
            "expense_date": expense_date or None,
            "description": description.strip() or None,
            "currency": (currency or "CAD").upper()[:3],
            "fx_rate": _opt_float(fx_rate) or 1.0,
            "subtotal": _opt_float(subtotal),
            "gst": _opt_float(gst) or 0,
            "pst": _opt_float(pst) or 0,
            "tip": _opt_float(tip) or 0,
            "total": _opt_float(total),
            "payment_method": payment_method.strip() or None,
            "paid_by": paid_by,
            "category_line": category_for(category_line).line,
            "business_pct": max(0, min(100, business_pct)),
            "gst_number": gst_number.strip() or None,
            "notes": notes.strip() or None,
        }
    except ValueError:
        raise HTTPException(400, "One of the amounts is not a number")
    if action == "confirm":
        data["status"] = "confirmed"
    elif action == "unconfirm":
        data["status"] = "review"
    db.update_expense(expense_id, data)
    if action == "confirm":
        nxt = next((r for r in db.list_expenses(status="review") if r["id"] != expense_id), None)
        if nxt:
            return RedirectResponse(f"/expenses/{nxt['id']}", status_code=303)
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)


@app.post("/expenses/{expense_id}/delete")
def remove_expense(expense_id: int):
    row = db.get_expense(expense_id)
    if row:
        db.delete_expense(expense_id)
        if row["source_file"]:
            others = db.find_by_hash(row["source_hash"] or "")
            if not others:
                (db.receipts_dir() / row["source_file"]).unlink(missing_ok=True)
    return RedirectResponse("/", status_code=303)


@app.get("/receipts/{name}")
def receipt_file(name: str):
    path = (db.receipts_dir() / name).resolve()
    if path.parent != db.receipts_dir().resolve() or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/summary", response_class=HTMLResponse)
def summary(request: Request, year: str = ""):
    years = db.years()
    y = int(year) if year.isdigit() else (years[0] if years else date.today().year)
    settings = db.get_settings()
    s = tax.summarize(db.list_expenses(year=y), settings, y)
    return templates.TemplateResponse(request, "summary.html", {"s": s, "years": years, "year": y, "settings": settings})


def _csv(body: str, filename: str) -> Response:
    return Response(
        "﻿" + body,  # BOM so Excel opens it as UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/ledger.csv")
def export_ledger(year: str = ""):
    y = int(year) if year.isdigit() else None
    return _csv(tax.ledger_csv(db.list_expenses(year=y), db.get_settings()), f"expenses-{y or 'all'}.csv")


@app.get("/export/summary.csv")
def export_summary(year: str):
    y = int(year)
    settings = db.get_settings()
    s = tax.summarize(db.list_expenses(year=y), settings, y)
    return _csv(tax.summary_csv(s, settings), f"t2125-summary-{y}.csv")


@app.get("/export/package.zip")
def export_package(year: str):
    """Everything an accountant needs for one year: summary, ledger and every receipt."""
    y = int(year)
    settings = db.get_settings()
    rows = db.list_expenses(year=y)
    s = tax.summarize(rows, settings, y)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"t2125-summary-{y}.csv", "﻿" + tax.summary_csv(s, settings))
        z.writestr(f"expenses-{y}.csv", "﻿" + tax.ledger_csv(rows, settings))
        for r in rows:
            if r["source_file"]:
                p = db.receipts_dir() / r["source_file"]
                if p.is_file():
                    z.write(p, f"receipts/{r['expense_date'] or 'undated'}_{r['id']}_{r['source_file']}")
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="expenses-{y}-package.zip"'},
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = ""):
    return templates.TemplateResponse(request, "settings.html", {
        "settings": db.get_settings(), "saved": bool(saved), "extraction": extract.extraction_available(),
    })


@app.post("/settings")
def save_settings_route(
    business_name: str = Form(...), partner_a_name: str = Form(...), partner_a_pct: float = Form(...),
    partner_b_name: str = Form(...), partner_b_pct: float = Form(...), gst_registered: str = Form("no"),
):
    if round(partner_a_pct + partner_b_pct, 4) != 100:
        raise HTTPException(400, "Partner shares must add up to 100%")
    db.save_settings({
        "business_name": business_name.strip(), "partner_a_name": partner_a_name.strip(),
        "partner_a_pct": f"{partner_a_pct:g}", "partner_b_name": partner_b_name.strip(),
        "partner_b_pct": f"{partner_b_pct:g}", "gst_registered": "yes" if gst_registered == "yes" else "no",
    })
    return RedirectResponse("/settings?saved=1", status_code=303)
