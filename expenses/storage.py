"""Receipt files in the receipts folder chosen in Settings.

Files are named so the folder is useful on its own, without the app:

    <receipts folder>/2026/2026-03-02 Cactus Club - Entertainment business development - 17.jpg
"""

import re
import shutil
from pathlib import Path

from . import db
from .categories import category_for


def _clean(s: str, limit: int) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip()


def save_incoming(raw: bytes, digest: str, ext: str) -> Path:
    d = db.receipts_dir() / "_incoming"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{digest[:16]}{ext}"
    path.write_bytes(raw)
    return path


def target_path(row, ext: str) -> Path:
    year = (row["expense_date"] or "")[:4]
    folder = db.receipts_dir() / (year if year.isdigit() else "Undated")
    parts = [
        row["expense_date"] or "undated",
        _clean(row["vendor"] or "", 40) or "Unknown vendor",
        "-",
        _clean(category_for(row["category_line"]).type, 40),
        "-",
        str(row["id"]),
    ]
    return folder / (" ".join(parts) + ext)


def file_into_place(expense_id: int) -> str | None:
    """Move the expense's receipt to its descriptive name. Returns the new path."""
    row = db.get_expense(expense_id)
    if not row or not row["source_file"]:
        return None
    current = Path(row["source_file"])
    if not current.is_file():
        return None
    target = target_path(row, current.suffix.lower())
    if target == current:
        return str(current)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(current), str(target))
    db.update_expense(expense_id, {"source_file": str(target)})
    return str(target)
