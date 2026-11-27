"""JSON API behind the Static Web App. Every route is under /api."""

import hashlib
import io
import json
import mimetypes
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from . import emails, extract, images, settings as settings_mod, tax
from .auth import current_user
from .categories import CATEGORIES, category_for
from .store import ConflictError, StoreError, get_store
from .workbook import Ledger, blank_row, build, next_id

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
EMAIL_EXTS = {".eml", ".msg"}

app = FastAPI(title="AIG expenses API", docs_url=None, redoc_url=None, openapi_url=None)
api = APIRouter(prefix="/api", dependencies=[Depends(current_user)])


@app.middleware("http")
async def ensure_api_prefix(request: Request, call_next):
    # Azure Functions may hand over the path with or without its /api prefix.
    if not request.scope["path"].startswith("/api"):
        request.scope["path"] = "/api" + request.scope["path"]
    return await call_next(request)


@app.exception_handler(StoreError)
async def store_error(_request, exc: StoreError):
    msg = "Someone else saved at the same moment. Please try again." if isinstance(exc, ConflictError) else str(exc)
    return JSONResponse({"detail": msg}, status_code=503)


def ledger() -> Ledger:
    return Ledger(get_store(), settings_mod.load())


def _public(row: dict, reg: bool) -> dict:
    c = tax.compute(row, reg)
    cat = category_for(row["category_line"])
    out = {k: v for k, v in row.items() if k not in ("source_item", "source_hash")}
    out.update(type=cat.type, cra_name=cat.name, total_cad=c.total_cad, deductible_cad=c.deductible_cad,
               itc_cad=c.itc_cad, has_receipt=bool(row["source_item"]),
               receipt_name=(row["source_path"] or "").rsplit("/", 1)[-1] or None)
    return out


# ---------- receipt files ----------

def _clean(s: str, limit: int) -> str:
    s = re.sub(r'[<>:"/\\|?*#%\x00-\x1f]+', " ", s or "")
    return re.sub(r"\s+", " ", s).strip(" .")[:limit].strip()


def receipt_path(row: dict, folder: str, ext: str) -> str:
    year = (row["expense_date"] or "")[:4]
    name = " ".join([row["expense_date"] or "undated", _clean(row["vendor"] or "", 40) or "Unknown vendor", "-",
                     _clean(category_for(row["category_line"]).type, 40), "-", str(row["id"])])
    return f"{folder}/{year if year.isdigit() else 'Undated'}/{name}{ext}"


def _ext(row: dict) -> str:
    return Path(row["source_path"] or "").suffix.lower() or ".jpg"


def put_receipt(row: dict, data: bytes, ext: str, folder: str) -> None:
    info = get_store().write(receipt_path(row, folder, ext), data)
    row.update(source_item=info.id, source_path=info.path, source_url=info.url)


def refile_receipt(row: dict, folder: str) -> None:
    """Rename/move a receipt after its details (or the folder setting) change."""
    if not row["source_item"]:
        return
    wanted = receipt_path(row, folder, _ext(row))
    if row["source_path"] == wanted:
        return
    store = get_store()
    data = store.read_item(row["source_item"])
    if data is None:
        return
    old = row["source_item"]
    put_receipt(row, data, _ext(row), folder)
    if row["source_item"] != old:
        store.delete_item(old)


# ---------- reading receipts ----------

def _media_type(filename: str, declared: str | None) -> str:
    ext = Path(filename).suffix.lower()
    if ext in {".heic", ".heif"}:
        return "image/heic"
    return mimetypes.guess_type(filename)[0] or declared or "application/octet-stream"


def _from_extraction(fields: dict) -> dict:
    out = {k: fields.get(k) for k in (
        "vendor", "expense_date", "description", "currency", "subtotal", "gst", "pst", "tip",
        "total", "payment_method", "gst_number", "category_line", "confidence", "notes")}
    out["currency"] = (out.get("currency") or "CAD").upper()
    for k in ("gst", "pst", "tip"):
        out[k] = out.get(k) or 0
    out["category_line"] = category_for(out.get("category_line")).line
    if fields.get("is_receipt") is False:
        out["notes"] = ("Doesn't look like a receipt. " + (out.get("notes") or "")).strip()
        out["confidence"] = "low"
    return out


def ingest(raw: bytes, filename: str, declared_type: str | None, user: dict) -> dict:
    digest = hashlib.sha256(raw).hexdigest()
    lg = ledger()
    rows = lg.rows()
    dup = next((r for r in rows if r["source_hash"] == digest), None)
    if dup:
        return {"file": filename, "status": "duplicate", "id": dup["id"]}

    ext = Path(filename).suffix.lower() or mimetypes.guess_extension(declared_type or "") or ".jpg"
    data, fields, error = raw, {}, None
    fields["source"] = "receipt"
    try:
        if ext in EMAIL_EXTS:
            parsed = emails.parse_msg(raw) if ext == ".msg" else emails.parse_eml(raw)
            fields.update(source="email", email_subject=parsed["subject"][:500], email_from=parsed["from"][:300],
                          description=parsed["subject"][:300])
            if extract.extraction_available():
                fields.update(_from_extraction(extract.extract_from_email(
                    parsed["subject"], parsed["from"], parsed["body"], [(d, t) for d, t, _ in parsed["attachments"]])))
        else:
            data, media = images.normalize(raw, _media_type(filename, declared_type))
            if media == "image/jpeg":
                ext = ".jpg"
            if media not in extract.IMAGE_TYPES | extract.HEIC_TYPES | {extract.PDF_TYPE}:
                raise ValueError(f"Unsupported file type: {media}")
            if extract.extraction_available():
                fields.update(_from_extraction(extract.extract_from_file(data, media)))
    except (extract.ExtractionError, ValueError, json.JSONDecodeError) as e:
        error = str(e)
        fields.update(notes=f"Could not read automatically: {error}", confidence="low")
    if not extract.extraction_available() and not error:
        fields["notes"] = "Automatic reading is off, so fill in the details by hand."

    row = blank_row(**fields, source_hash=digest, added_by=user["email"],
                    created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    row["id"] = next_id(rows)
    put_receipt(row, data, ".jpg" if ext == ".jpeg" else ext, lg.settings["receipts_folder"])

    def add(current):
        if any(r["id"] == row["id"] for r in current):
            row["id"] = next_id(current)
        current.append(row)
        return row["id"]

    expense_id = lg.mutate(add)
    refile(lg, expense_id)  # only does something if another upload took the planned ID
    return {"file": filename, "status": "error" if error else "added", "id": expense_id, "error": error}


FILE_FIELDS = ("source_item", "source_path", "source_url")


def refile(lg: Ledger, expense_id: int) -> None:
    """Move the expense's receipt to its proper name, then record the new location in the workbook."""
    row = lg.get(expense_id)
    if not row or not row["source_item"]:
        return
    before = {k: row[k] for k in FILE_FIELDS}
    refile_receipt(row, lg.settings["receipts_folder"])
    after = {k: row[k] for k in FILE_FIELDS}
    if after != before:
        def record(rows):
            for r in rows:
                if r["id"] == expense_id:
                    r.update(after)
        lg.mutate(record)


# ---------- routes ----------

@api.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@api.get("/config")
def config():
    s = settings_mod.load()
    store = get_store()
    return {
        "settings": s,
        "categories": [{"line": c.line, "type": c.type, "name": c.name, "deductible_pct": c.deductible_pct}
                       for c in sorted(CATEGORIES, key=lambda c: c.type)],
        "extraction": extract.extraction_available(),
        "storage": store.kind,
        "receipts_location": store.describe(s["receipts_folder"]),
        "excel_location": store.describe(Ledger(store, s).path),
    }


@api.post("/settings")
def save_settings(values: dict = Body(...)):
    try:
        a, b = float(values.get("partner_a_pct", 50)), float(values.get("partner_b_pct", 50))
    except (TypeError, ValueError):
        raise HTTPException(400, "Partner shares must be numbers")
    if round(a + b, 4) != 100:
        raise HTTPException(400, "Partner shares must add up to 100%")
    old = settings_mod.load()
    new = dict(old)
    new.update({k: str(v).strip() for k, v in values.items() if k in settings_mod.DEFAULTS})
    new["partner_a_pct"], new["partner_b_pct"] = f"{a:g}", f"{b:g}"
    new["gst_registered"] = "yes" if new.get("gst_registered") == "yes" else "no"
    for k in ("receipts_folder", "excel_folder"):
        new[k] = (new[k] or settings_mod.DEFAULTS[k]).replace("\\", "/").strip("/")
        if new[k] != old[k]:
            try:
                get_store().check_folder(new[k])
            except StoreError as e:
                raise HTTPException(400, f"Can't use the folder '{new[k]}': {e}")
    store = get_store()
    old_ledger, new_ledger = Ledger(store, old), Ledger(store, new)
    moved = 0
    if new_ledger.path != old_ledger.path:
        # Move the workbook: write it at the new place, then remove the old one.
        rows = old_ledger.rows()
        if store.read(new_ledger.path):
            raise HTTPException(400, f"There is already a workbook at {new_ledger.path}.")
        store.write(new_ledger.path, build(rows, new), must_not_exist=True)
        store.delete_path(old_ledger.path)
    settings_mod.save(new)
    new_ledger.rewrite()  # partner names/split appear in the summary sheets
    if new["receipts_folder"] != old["receipts_folder"]:
        for r in new_ledger.rows():
            if r["source_item"]:
                refile(new_ledger, r["id"])
                moved += 1
    return {"settings": new, "moved": moved}


@api.get("/expenses")
def list_expenses(year: str = "", status: str = ""):
    lg = ledger()
    reg = lg.settings["gst_registered"] == "yes"
    rows = lg.rows()
    years = sorted({int(r["expense_date"][:4]) for r in rows
                    if r["expense_date"] and r["expense_date"][:4].isdigit()}, reverse=True)
    if year.isdigit():
        rows = [r for r in rows if (r["expense_date"] or "").startswith(year)]
    if status:
        rows = [r for r in rows if r["status"] == status]
    rows.sort(key=lambda r: (r["expense_date"] or "", r["id"]), reverse=True)
    return {"expenses": [_public(r, reg) for r in rows], "years": years}


@api.post("/upload")
async def upload(files: list[UploadFile] = File(...), user: dict = Depends(current_user)):
    results = []
    for f in files:
        raw = await f.read()
        if not raw:
            continue
        if len(raw) > MAX_UPLOAD_BYTES:
            results.append({"file": f.filename, "status": "error", "error": "File is larger than 25 MB"})
            continue
        results.append(ingest(raw, f.filename or "upload.jpg", f.content_type, user))
    return {"results": results}


@api.post("/expenses")
def new_expense(user: dict = Depends(current_user)):
    lg = ledger()
    row = blank_row(source="manual", expense_date=date.today().isoformat(), added_by=user["email"],
                    created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))

    def add(rows):
        row["id"] = next_id(rows)
        rows.append(row)
        return row["id"]
    return {"id": lg.mutate(add)}


@api.get("/expenses/{expense_id}")
def get_expense(expense_id: int):
    lg = ledger()
    rows = lg.rows()
    row = next((r for r in rows if r["id"] == expense_id), None)
    if not row:
        raise HTTPException(404, "Expense not found")
    reg = lg.settings["gst_registered"] == "yes"
    review = [r["id"] for r in sorted(rows, key=lambda r: (r["expense_date"] or "", r["id"]), reverse=True)
              if r["status"] != "confirmed" and r["id"] != expense_id]
    return {"expense": _public(row, reg), "next_review": review[0] if review else None}


def _num(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except ValueError:
        raise HTTPException(400, f"'{v}' is not a number")


@api.post("/expenses/{expense_id}")
def update_expense(expense_id: int, body: dict = Body(...)):
    lg = ledger()
    changes = {
        "vendor": (body.get("vendor") or "").strip() or None,
        "expense_date": body.get("expense_date") or None,
        "description": (body.get("description") or "").strip() or None,
        "currency": ((body.get("currency") or "CAD").strip().upper() or "CAD")[:3],
        "fx_rate": _num(body.get("fx_rate"), 1.0) or 1.0,
        "subtotal": _num(body.get("subtotal")),
        "gst": _num(body.get("gst"), 0.0),
        "pst": _num(body.get("pst"), 0.0),
        "tip": _num(body.get("tip"), 0.0),
        "total": _num(body.get("total")),
        "payment_method": (body.get("payment_method") or "").strip() or None,
        "paid_by": body.get("paid_by") or "business",
        "category_line": category_for(body.get("category_line")).line,
        "business_pct": int(max(0, min(100, _num(body.get("business_pct"), 100)))),
        "gst_number": (body.get("gst_number") or "").strip() or None,
        "notes": (body.get("notes") or "").strip() or None,
    }
    action = body.get("action", "save")
    if action == "confirm":
        changes["status"] = "confirmed"
    elif action == "unconfirm":
        changes["status"] = "review"

    def apply(rows):
        for r in rows:
            if r["id"] == expense_id:
                r.update(changes)
                return True
        return False
    if not lg.mutate(apply):
        raise HTTPException(404, "Expense not found")
    refile(lg, expense_id)
    return get_expense(expense_id)


@api.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int):
    lg = ledger()
    removed = {}

    def drop(rows):
        for i, r in enumerate(rows):
            if r["id"] == expense_id:
                removed.update(rows.pop(i))
                return True
        return False
    if lg.mutate(drop) and removed.get("source_item"):
        get_store().delete_item(removed["source_item"])
    return {"ok": True}


@api.get("/expenses/{expense_id}/receipt")
def receipt(expense_id: int):
    row = ledger().get(expense_id)
    data = get_store().read_item(row["source_item"]) if row and row["source_item"] else None
    if data is None:
        raise HTTPException(404, "No receipt file")
    return Response(data, media_type=mimetypes.guess_type(row["source_path"] or "")[0] or "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=3600"})


@api.get("/summary")
def summary(year: str = ""):
    lg = ledger()
    rows = lg.rows()
    years = sorted({int(r["expense_date"][:4]) for r in rows
                    if r["expense_date"] and r["expense_date"][:4].isdigit()}, reverse=True)
    y = int(year) if year.isdigit() else (years[0] if years else date.today().year)
    s = tax.summarize([r for r in rows if (r["expense_date"] or "").startswith(str(y))], lg.settings, y)
    line = lambda lt: {"line": lt.line, "type": category_for(lt.line).type, "name": lt.name, "count": lt.count,
                       "total_cad": lt.total_cad, "deductible_cad": lt.deductible_cad,
                       "partner_a": lt.partner_a, "partner_b": lt.partner_b, "itc_cad": lt.itc_cad}
    return {
        "year": y, "years": years, "settings": lg.settings,
        "lines": [line(lt) for lt in s.lines], "capital": line(s.capital) if s.capital else None,
        "total_deductible": s.total_deductible, "partner_a": s.partner_a, "partner_b": s.partner_b,
        "total_itc": s.total_itc, "needs_review": s.needs_review, "missing_date": s.missing_date,
        "foreign_no_rate": s.foreign_no_rate, "missing_gst_number": s.missing_gst_number,
    }


def _csv(body: str, filename: str) -> Response:
    return Response("﻿" + body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@api.get("/export/ledger.csv")
def export_ledger(year: str = ""):
    lg = ledger()
    rows = [r for r in lg.rows() if not year.isdigit() or (r["expense_date"] or "").startswith(year)]
    return _csv(tax.ledger_csv(rows, lg.settings), f"expenses-{year or 'all'}.csv")


@api.get("/export/summary.csv")
def export_summary(year: str):
    lg = ledger()
    rows = [r for r in lg.rows() if (r["expense_date"] or "").startswith(year)]
    return _csv(tax.summary_csv(tax.summarize(rows, lg.settings, int(year)), lg.settings),
                f"t2125-summary-{year}.csv")


@api.get("/export/workbook.xlsx")
def export_workbook():
    lg = ledger()
    return Response(build(lg.rows(), lg.settings),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{lg.path.rsplit("/", 1)[-1]}"'})


@api.get("/export/package.zip")
def export_package(year: str):
    """Everything an accountant needs for one year: workbook, CSVs and every receipt."""
    lg = ledger()
    all_rows = lg.rows()
    rows = [r for r in all_rows if (r["expense_date"] or "").startswith(year)]
    store = get_store()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"t2125-summary-{year}.csv",
                   "﻿" + tax.summary_csv(tax.summarize(rows, lg.settings, int(year)), lg.settings))
        z.writestr(f"expenses-{year}.csv", "﻿" + tax.ledger_csv(rows, lg.settings))
        z.writestr(lg.path.rsplit("/", 1)[-1], build(all_rows, lg.settings))
        for r in rows:
            if r["source_item"]:
                content = store.read_item(r["source_item"])
                if content is not None:
                    z.writestr(f"receipts/{r['source_path'].rsplit('/', 1)[-1]}", content)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="expenses-{year}-package.zip"'})


@api.post("/rebuild")
def rebuild():
    lg = ledger()
    lg.rewrite()
    return {"ok": True, "path": get_store().describe(lg.path)}


app.include_router(api)
