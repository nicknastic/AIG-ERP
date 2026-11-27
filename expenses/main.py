"""Web app: snap receipts from a phone, review what was read, keep the Excel file and export for tax time."""

import hashlib
import io
import json
import mimetypes
import zipfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates

from . import auth, db, emails, excel, extract, onedrive, storage, tax
from .categories import CATEGORIES, category_for


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    yield


app = FastAPI(title="AIG expenses", lifespan=lifespan)
app.include_router(auth.router)


@app.middleware("http")
async def require_login(request: Request, call_next):
    blocked = auth.gate(request)
    return blocked if blocked is not None else await call_next(request)


# Added last so it runs first and the session is available to require_login.
app.add_middleware(SessionMiddleware, secret_key=auth.session_secret(), https_only=auth.on_azure(),
                   same_site="lax", max_age=60 * 60 * 24 * 60, session_cookie="aig_session")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["categories"] = CATEGORIES
templates.env.globals["category_for"] = category_for

# Last problem writing the Excel file, shown at the top of every page until a write succeeds.
_excel_status: dict[str, str | None] = {"warning": None}
templates.env.globals["excel_warning"] = lambda: _excel_status["warning"]
templates.env.globals["login_enabled"] = auth.enabled


def refresh_excel(*years) -> None:
    warning = None
    for y in {int(str(y)[:4]) for y in years if y and str(y)[:4].isdigit()}:
        warning = excel.write_safely(y) or warning
    _excel_status["warning"] = warning


def _year(date_str) -> int | None:
    return int(date_str[:4]) if date_str and date_str[:4].isdigit() else None

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
EMAIL_EXTS = {".eml", ".msg"}


def _media_type(filename: str, declared: str | None) -> str:
    guessed = mimetypes.guess_type(filename)[0]
    ext = Path(filename).suffix.lower()
    if ext in {".heic", ".heif"}:
        return "image/heic"
    return guessed or declared or "application/octet-stream"


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

    ext = Path(filename).suffix.lower() or mimetypes.guess_extension(declared_type or "") or ".jpg"
    data = raw
    if ext not in EMAIL_EXTS:
        data, media = storage.normalize_image(raw, _media_type(filename, declared_type))
        if media == "image/jpeg":
            ext = ".jpg"
    record: dict = {"source_hash": digest, "status": "review", "paid_by": "business"}
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
            record["source"] = "receipt"
            if extract.extraction_available():
                record.update(_from_extraction(extract.extract_from_file(data, media)))
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
    _save_file(expense_id, data, ext)
    refresh_excel(record.get("expense_date"))
    return {"file": filename, "status": "error" if error else "added", "id": expense_id, "error": error}


def _save_file(expense_id: int, data: bytes, ext: str) -> None:
    """Save the receipt; if OneDrive is unreachable keep it on the server so nothing is lost."""
    try:
        storage.save_receipt(expense_id, data, ext)
    except storage.StorageError as e:
        fallback = db.DATA_DIR / "unsynced" / f"{expense_id}{ext}"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_bytes(data)
        row = db.get_expense(expense_id)
        db.update_expense(expense_id, {
            "source_file": str(fallback), "source_path": str(fallback),
            "notes": ((row["notes"] or "") + f" Not saved to OneDrive yet ({e}); it will move there on the next save.").strip(),
        })


def _move_file(expense_id: int) -> None:
    try:
        storage.file_into_place(expense_id)
    except storage.StorageError as e:
        _excel_status["warning"] = f"Couldn't move the receipt file: {e}"


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
async def upload(request: Request, files: list[UploadFile] = File(...), next: str = Form("")):
    results = []
    for f in files:
        raw = await f.read()
        if not raw:
            continue
        if len(raw) > MAX_UPLOAD_BYTES:
            results.append({"file": f.filename, "status": "error", "error": "File is larger than 25 MB"})
            continue
        results.append(ingest(raw, f.filename or "upload", f.content_type))
    if next == "capture" and len(results) == 1 and results[0].get("id"):
        return RedirectResponse(f"/expenses/{results[0]['id']}?snapped={results[0]['status']}", status_code=303)
    return templates.TemplateResponse(request, "uploaded.html", {"results": results})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/snap", response_class=HTMLResponse)
def snap(request: Request):
    recent = db.list_expenses()[:5]
    return templates.TemplateResponse(request, "snap.html", {
        "settings": db.get_settings(), "recent": recent, "extraction": extract.extraction_available(),
    })


@app.get("/manifest.webmanifest")
def manifest():
    name = f"{db.get_settings()['business_name']} receipts"
    return Response(json.dumps({
        "name": name, "short_name": "Receipts", "start_url": "/snap", "display": "standalone",
        "background_color": "#f7f7f5", "theme_color": "#1f5f8b",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }), media_type="application/manifest+json")


ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180"><rect width="180" height="180" rx="36" '
            'fill="#1f5f8b"/><path d="M55 40h70v100l-12-8-12 8-11-8-12 8-11-8-12 8z" fill="#fff"/>'
            '<path d="M70 70h40M70 88h40M70 106h25" stroke="#1f5f8b" stroke-width="7" stroke-linecap="round"/></svg>')


@app.get("/icon.svg")
def icon():
    return Response(ICON_SVG, media_type="image/svg+xml")


@app.get("/apple-touch-icon.png")
def apple_icon():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (180, 180), "#1f5f8b")
    d = ImageDraw.Draw(img)
    d.polygon([(55, 40), (125, 40), (125, 140), (113, 132), (101, 140), (90, 132), (78, 140), (67, 132), (55, 140)], fill="white")
    for y, x2 in ((70, 110), (88, 110), (106, 95)):
        d.line([(70, y), (x2, y)], fill="#1f5f8b", width=7)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.post("/expenses/new")
def new_expense():
    expense_id = db.insert_expense({
        "source": "manual", "status": "review", "paid_by": "business",
        "expense_date": date.today().isoformat(),
    })
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)


@app.get("/expenses/{expense_id}", response_class=HTMLResponse)
def edit_expense(request: Request, expense_id: int, snapped: str = ""):
    row = db.get_expense(expense_id)
    if not row:
        raise HTTPException(404)
    settings = db.get_settings()
    name = row["source_path"] or row["source_file"] or ""
    media = mimetypes.guess_type(name)[0] or ""
    if name.lower().endswith((".heic", ".heif")):
        media = "image/heic"
    return templates.TemplateResponse(request, "edit.html", {
        "e": row, "settings": settings, "media": media, "snapped": snapped,
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
    before = db.get_expense(expense_id)
    if not before:
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
    _move_file(expense_id)
    refresh_excel(before["expense_date"], data["expense_date"])
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
        storage.delete_receipt(row)
        refresh_excel(row["expense_date"])
    return RedirectResponse("/", status_code=303)


@app.get("/expenses/{expense_id}/receipt")
def receipt_file(expense_id: int):
    row = db.get_expense(expense_id)
    data = storage.read_receipt(row) if row else None
    if data is None:
        raise HTTPException(404)
    name = row["source_path"] or row["source_file"]
    return Response(data, media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=3600"})


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
            content = storage.read_receipt(r)
            if content is not None:
                z.writestr(f"receipts/{Path(r['source_path'] or r['source_file']).name}", content)
        z.writestr(excel.workbook_name(y), excel.to_bytes(y))
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="expenses-{y}-package.zip"'},
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", error: str = ""):
    return templates.TemplateResponse(request, "settings.html", {
        "settings": db.get_settings(), "saved": saved, "error": error,
        "extraction": extract.extraction_available(),
        "receipts_path": storage.receipts_location(), "excel_path": storage.excel_location(),
        "onedrive_connected": onedrive.connected(), "onedrive_account": onedrive.account(),
        "login_enabled": auth.enabled(), "using_onedrive": storage.using_onedrive(),
        "default_od_receipts": storage.DEFAULT_ONEDRIVE_RECEIPTS, "default_od_excel": storage.DEFAULT_ONEDRIVE_EXCEL,
    })


def _check_folder(value: str, use_onedrive: bool) -> str | None:
    """Return an error message if the folder can't be used."""
    if not value:
        return None
    if use_onedrive:
        try:
            onedrive.check_folder(value.replace("\\", "/"))
        except onedrive.OneDriveError as e:
            return f"Can't write to OneDrive folder {value}: {e}"
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        return f"Use a full folder path, for example C:\\Users\\Nick\\OneDrive\\Receipts or /Users/nick/Receipts. Got: {value}"
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return f"Can't write to {p}: {e.strerror or e}"
    return None


@app.post("/settings")
def save_settings_route(
    business_name: str = Form(...), partner_a_name: str = Form(...), partner_a_pct: float = Form(...),
    partner_b_name: str = Form(...), partner_b_pct: float = Form(...), gst_registered: str = Form("no"),
    receipts_folder: str = Form(""), excel_folder: str = Form(""), storage_choice: str = Form("", alias="storage"),
):
    if round(partner_a_pct + partner_b_pct, 4) != 100:
        raise HTTPException(400, "Partner shares must add up to 100%")
    receipts_folder, excel_folder = receipts_folder.strip().strip('"'), excel_folder.strip().strip('"')
    current = db.get_settings()
    storage_choice = storage_choice or current["storage"]
    if storage_choice == "onedrive" and not onedrive.connected():
        storage_choice = "local"
    if auth.on_azure() and storage_choice != "onedrive" and (receipts_folder or excel_folder):
        return RedirectResponse("/settings?" + urlencode(
            {"error": "On Azure, connect OneDrive first: folders on the server aren't kept safe."}), status_code=303)
    for folder in (receipts_folder, excel_folder):
        err = _check_folder(folder, storage_choice == "onedrive")
        if err:
            return RedirectResponse("/settings?" + urlencode({"error": err}), status_code=303)
    old_location = storage.receipts_location()
    db.save_settings({
        "storage": storage_choice,
        "receipts_folder": receipts_folder, "excel_folder": excel_folder,
        "business_name": business_name.strip(), "partner_a_name": partner_a_name.strip(),
        "partner_a_pct": f"{partner_a_pct:g}", "partner_b_name": partner_b_name.strip(),
        "partner_b_pct": f"{partner_b_pct:g}", "gst_registered": "yes" if gst_registered == "yes" else "no",
    })
    moved = 0
    if storage.receipts_location() != old_location:
        # Bring existing receipts along so everything lives in the new folder.
        for r in db.list_expenses():
            if r["source_file"]:
                _move_file(r["id"])
                moved += 1
    refresh_excel(*db.years())
    msg = "Saved." + (f" Moved {moved} receipt(s) to the new folder." if moved else "")
    return RedirectResponse("/settings?" + urlencode({"saved": msg}), status_code=303)


@app.post("/excel/rebuild")
def rebuild_excel():
    refresh_excel(*(db.years() or [date.today().year]))
    msg = _excel_status["warning"] or f"Excel updated in {storage.excel_location()}"
    return RedirectResponse("/settings?" + urlencode({"saved": msg}), status_code=303)
