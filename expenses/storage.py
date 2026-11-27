"""Where receipt files and the Excel workbook are saved.

Two places are supported, picked in Settings:

- local:    folders on the computer (or server) running the app
- onedrive: folders in the connected OneDrive (used when hosted on Azure)

Receipts are named so the folder is useful on its own, without the app:

    <receipts folder>/2026/2026-03-02 Cactus Club - Entertainment business development - 17.jpg
"""

import io
import re
from pathlib import Path

from . import db, onedrive
from .categories import category_for

ONEDRIVE_PREFIX = "onedrive:"
DEFAULT_ONEDRIVE_RECEIPTS = "AIG Expenses/Receipts"
DEFAULT_ONEDRIVE_EXCEL = "AIG Expenses/Excel"
MAX_IMAGE_EDGE = 2400


class StorageError(Exception):
    pass


def using_onedrive() -> bool:
    return db.get_settings().get("storage") == "onedrive" and onedrive.connected()


def _onedrive_folder(setting: str, default: str) -> str:
    value = db.get_settings().get(setting, "").strip().replace("\\", "/").strip("/")
    return value or default


def receipts_location() -> str:
    if using_onedrive():
        return "OneDrive: " + _onedrive_folder("receipts_folder", DEFAULT_ONEDRIVE_RECEIPTS)
    return str(db.receipts_dir())


def excel_location() -> str:
    if using_onedrive():
        return "OneDrive: " + _onedrive_folder("excel_folder", DEFAULT_ONEDRIVE_EXCEL)
    return str(db.excel_dir())


def _clean(s: str, limit: int) -> str:
    s = re.sub(r'[<>:"/\\|?*#%\x00-\x1f]+', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip()


def receipt_name(row, ext: str) -> tuple[str, str]:
    """(year folder, file name) for an expense."""
    year = (row["expense_date"] or "")[:4]
    parts = [
        row["expense_date"] or "undated",
        _clean(row["vendor"] or "", 40) or "Unknown vendor",
        "-",
        _clean(category_for(row["category_line"]).type, 40),
        "-",
        str(row["id"]),
    ]
    return (year if year.isdigit() else "Undated"), " ".join(parts) + ext


def normalize_image(raw: bytes, media_type: str) -> tuple[bytes, str]:
    """Turn phone photos into upright JPEGs no larger than MAX_IMAGE_EDGE pixels.

    Keeps uploads quick, files small, and stays under the size limit for reading.
    """
    if not media_type.startswith("image/") or media_type == "image/gif":
        return raw, media_type
    try:
        from PIL import Image, ImageOps
        if media_type in ("image/heic", "image/heif"):
            import pillow_heif
            pillow_heif.register_heif_opener()
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        if max(img.size) <= MAX_IMAGE_EDGE and media_type == "image/jpeg" and len(raw) < 1_500_000:
            return raw, media_type
        img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - an odd image is still worth keeping as-is
        return raw, media_type


def _ext(row_or_ref: str) -> str:
    return Path(row_or_ref).suffix.lower() or ".jpg"


def save_receipt(expense_id: int, data: bytes, ext: str) -> None:
    row = db.get_expense(expense_id)
    year, name = receipt_name(row, ext)
    if using_onedrive():
        path = f"{_onedrive_folder('receipts_folder', DEFAULT_ONEDRIVE_RECEIPTS)}/{year}/{name}"
        try:
            item = onedrive.upload(path, data)
        except onedrive.OneDriveError as e:
            raise StorageError(str(e)) from e
        db.update_expense(expense_id, {"source_file": ONEDRIVE_PREFIX + item["id"], "source_path": path,
                                       "source_url": item.get("webUrl")})
    else:
        target = db.receipts_dir() / year / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        db.update_expense(expense_id, {"source_file": str(target), "source_path": str(target), "source_url": None})


def read_receipt(row) -> bytes | None:
    ref = row["source_file"]
    if not ref:
        return None
    if ref.startswith(ONEDRIVE_PREFIX):
        try:
            return onedrive.download(ref[len(ONEDRIVE_PREFIX):])
        except onedrive.OneDriveError:
            return None
    p = Path(ref)
    return p.read_bytes() if p.is_file() else None


def delete_receipt(row) -> None:
    ref = row["source_file"]
    if not ref:
        return
    if ref.startswith(ONEDRIVE_PREFIX):
        try:
            onedrive.delete(ref[len(ONEDRIVE_PREFIX):])
        except onedrive.OneDriveError:
            pass
    else:
        Path(ref).unlink(missing_ok=True)


def file_into_place(expense_id: int) -> None:
    """Rename/move the receipt after its details change, or after the folder setting changes."""
    row = db.get_expense(expense_id)
    if not row or not row["source_file"]:
        return
    ext = _ext(row["source_path"] or row["source_file"])
    year, name = receipt_name(row, ext)
    on_od = row["source_file"].startswith(ONEDRIVE_PREFIX)
    if using_onedrive():
        wanted = f"{_onedrive_folder('receipts_folder', DEFAULT_ONEDRIVE_RECEIPTS)}/{year}/{name}"
        if on_od and row["source_path"] == wanted:
            return
    else:
        wanted = str(db.receipts_dir() / year / name)
        if not on_od and row["source_file"] == wanted:
            return
    data = read_receipt(row)
    if data is None:
        return
    save_receipt(expense_id, data, ext)
    delete_receipt(row)


def write_excel(filename: str, data: bytes) -> str:
    if using_onedrive():
        path = f"{_onedrive_folder('excel_folder', DEFAULT_ONEDRIVE_EXCEL)}/{filename}"
        try:
            onedrive.upload(path, data)
        except onedrive.OneDriveError as e:
            raise StorageError(str(e)) from e
        return "OneDrive/" + path
    path = db.excel_dir() / filename
    tmp = path.with_name(path.stem + ".tmp.xlsx")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)  # PermissionError on Windows if the workbook is open in Excel
    except PermissionError as e:
        raise StorageError("Couldn't update the Excel file because it is open. Close it in Excel, "
                           "then use 'Update Excel now' in Settings.") from e
    except OSError as e:
        raise StorageError(f"Couldn't update the Excel file: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)
    return str(path)
