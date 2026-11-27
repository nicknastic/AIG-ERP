"""App settings, kept as a small JSON file next to the workbook in OneDrive.

The settings file lives at CONFIG_PATH (default "AIG Expenses/settings.json").
It holds the folders for receipt photos and the Excel workbook, the partner
split, and whether the partnership is GST-registered.
"""

import json
import os

from .store import get_store

DEFAULTS = {
    "business_name": "AIG",
    "partner_a_name": "Nick",
    "partner_a_pct": "50",
    "partner_b_name": "Marija",
    "partner_b_pct": "50",
    "gst_registered": "no",
    "receipts_folder": "AIG Expenses/Receipts",
    "excel_folder": "AIG Expenses",
}


def config_path() -> str:
    return os.environ.get("CONFIG_PATH", "AIG Expenses/settings.json")


def load() -> dict:
    s = dict(DEFAULTS)
    found = get_store().read(config_path())
    if found:
        try:
            s.update({k: str(v) for k, v in json.loads(found[0]).items() if k in DEFAULTS})
        except (ValueError, AttributeError):
            pass
    for k in ("receipts_folder", "excel_folder"):
        s[k] = (s[k] or DEFAULTS[k]).replace("\\", "/").strip("/") or DEFAULTS[k]
    return s


def save(values: dict) -> dict:
    s = load()
    s.update({k: str(v) for k, v in values.items() if k in DEFAULTS})
    get_store().write(config_path(), json.dumps(s, indent=2).encode())
    return s
