"""SQLite storage. One file, kept in the data folder next to the receipt images."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("EXPENSES_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    source          TEXT NOT NULL,            -- 'receipt', 'email', 'manual'
    source_file     TEXT,                     -- full path of the saved receipt file
    source_hash     TEXT,                     -- sha256 of the original file, to catch duplicates
    email_subject   TEXT,
    email_from      TEXT,
    vendor          TEXT,
    expense_date    TEXT,                     -- YYYY-MM-DD
    description     TEXT,
    currency        TEXT NOT NULL DEFAULT 'CAD',
    fx_rate         REAL NOT NULL DEFAULT 1,  -- CAD per unit of currency (1 for CAD)
    subtotal        REAL,
    gst             REAL NOT NULL DEFAULT 0,  -- GST, or the federal+provincial HST
    pst             REAL NOT NULL DEFAULT 0,  -- BC PST (not recoverable, part of the cost)
    tip             REAL NOT NULL DEFAULT 0,
    total           REAL,
    payment_method  TEXT,
    paid_by         TEXT,                     -- which partner paid, or 'business'
    category_line   TEXT NOT NULL DEFAULT '9270',
    business_pct    INTEGER NOT NULL DEFAULT 100,
    gst_number      TEXT,                     -- supplier's GST/HST registration number, needed for ITCs
    status          TEXT NOT NULL DEFAULT 'review',  -- 'review' or 'confirmed'
    confidence      TEXT,
    notes           TEXT,
    raw_extraction  TEXT
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date);
CREATE INDEX IF NOT EXISTS idx_expenses_hash ON expenses(source_hash);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "business_name": "AIG",
    "partner_a_name": "Nick",
    "partner_a_pct": "50",
    "partner_b_name": "Marija",
    "partner_b_pct": "50",
    "gst_registered": "no",
    "province": "BC",
    "receipts_folder": "",  # where receipt photos are saved; empty = data/receipts
    "excel_folder": "",     # where the Excel workbook is kept; empty = data/excel
}


def db_path() -> Path:
    return DATA_DIR / "expenses.db"


def _folder(setting: str, default: Path) -> Path:
    value = get_settings().get(setting, "").strip()
    d = Path(value).expanduser() if value else default
    d.mkdir(parents=True, exist_ok=True)
    return d


def receipts_dir() -> Path:
    return _folder("receipts_folder", DATA_DIR / "receipts")


def excel_dir() -> Path:
    return _folder("excel_folder", DATA_DIR / "excel")


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))


def get_settings() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


def save_settings(values: dict[str, str]) -> None:
    with connect() as conn:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, str(v)),
                )


EXPENSE_FIELDS = [
    "source", "source_file", "source_hash", "email_subject", "email_from", "vendor",
    "expense_date", "description", "currency", "fx_rate", "subtotal", "gst", "pst", "tip", "total",
    "payment_method", "paid_by", "category_line", "business_pct", "gst_number", "status",
    "confidence", "notes", "raw_extraction",
]


def insert_expense(data: dict) -> int:
    cols = [k for k in EXPENSE_FIELDS if k in data]
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO expenses ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [data[k] for k in cols],
        )
        return int(cur.lastrowid)


def update_expense(expense_id: int, data: dict) -> None:
    cols = [k for k in EXPENSE_FIELDS if k in data]
    if not cols:
        return
    with connect() as conn:
        conn.execute(
            f"UPDATE expenses SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
            [data[k] for k in cols] + [expense_id],
        )


def delete_expense(expense_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))


def get_expense(expense_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()


def find_by_hash(source_hash: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM expenses WHERE source_hash = ? LIMIT 1", (source_hash,)
        ).fetchone()


def list_expenses(year: int | None = None, status: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM expenses WHERE 1=1"
    args: list = []
    if year:
        sql += " AND substr(expense_date, 1, 4) = ?"
        args.append(str(year))
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY expense_date DESC, id DESC"
    with connect() as conn:
        return conn.execute(sql, args).fetchall()


def years() -> list[int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(expense_date, 1, 4) AS y FROM expenses "
            "WHERE expense_date IS NOT NULL ORDER BY y DESC"
        ).fetchall()
    return [int(r["y"]) for r in rows if r["y"] and r["y"].isdigit()]
