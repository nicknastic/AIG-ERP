import json
from pathlib import Path
from email.message import EmailMessage

from fastapi.testclient import TestClient

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")

FAKE = {
    "is_receipt": True, "vendor": "Staples", "expense_date": "2026-02-14", "description": "Printer paper",
    "currency": "CAD", "subtotal": 20.0, "gst": 1.0, "pst": 1.4, "tip": 0, "total": 22.4,
    "payment_method": "Visa", "gst_number": "R123456789", "category_line": "8810", "confidence": "high", "notes": None,
}


def make_eml() -> bytes:
    m = EmailMessage()
    m["Subject"] = "Your Zoom receipt"
    m["From"] = "billing@zoom.us"
    m.set_content("Zoom Pro monthly, total $21.00 CAD incl. GST $1.00")
    m.add_attachment(PNG, maintype="image", subtype="png", filename="invoice.png")
    return bytes(m)


def test_upload_without_key_creates_review_item(app_env):
    db, main = app_env
    with TestClient(main.app) as client:
        r = client.post("/upload", files=[("files", ("r.png", PNG, "image/png"))])
        assert r.status_code == 200 and "Added" in r.text
        rows = db.list_expenses()
        assert len(rows) == 1 and rows[0]["status"] == "review"
        # same file again is caught as duplicate
        r = client.post("/upload", files=[("files", ("copy.png", PNG, "image/png"))])
        assert "Already uploaded" in r.text
        assert len(db.list_expenses()) == 1


def test_upload_with_extraction(app_env, monkeypatch):
    db, main = app_env
    monkeypatch.setattr(main.extract, "extraction_available", lambda: True)
    monkeypatch.setattr(main.extract, "extract_from_file", lambda data, media: dict(FAKE))
    seen = {}

    def fake_email(subject, sender, body, atts):
        seen.update(subject=subject, sender=sender, body=body, n=len(atts))
        return dict(FAKE, vendor="Zoom", category_line="9270", total=21.0, gst=1.0, pst=0)

    monkeypatch.setattr(main.extract, "extract_from_email", fake_email)
    with TestClient(main.app) as client:
        client.post("/upload", files=[("files", ("r.png", PNG, "image/png")),
                                      ("files", ("zoom.eml", make_eml(), "message/rfc822"))])
        rows = {r["vendor"]: r for r in db.list_expenses()}
        assert rows["Staples"]["total"] == 22.4 and rows["Staples"]["category_line"] == "8810"
        assert rows["Zoom"]["source"] == "email" and rows["Zoom"]["email_subject"] == "Your Zoom receipt"
        assert seen["n"] == 1 and "Zoom Pro" in seen["body"]

        eid = rows["Staples"]["id"]
        assert client.get(f"/expenses/{eid}").status_code == 200
        r = client.post(f"/expenses/{eid}", data={
            "vendor": "Staples", "expense_date": "2026-02-14", "total": "22.40", "gst": "1.00", "pst": "1.40",
            "category_line": "8810", "business_pct": "100", "action": "confirm", "gst_number": "R123456789",
        }, follow_redirects=False)
        assert r.status_code == 303
        assert db.get_expense(eid)["status"] == "confirmed"

        s = client.get("/summary?year=2026")
        assert s.status_code == 200 and "8810" in s.text
        csv = client.get("/export/summary.csv?year=2026").text
        assert "Office expenses" in csv and "22.4" in csv and "ITC" not in csv  # not GST registered
        z = client.get("/export/package.zip?year=2026")
        assert z.status_code == 200 and z.content[:2] == b"PK"
        assert client.get("/export/ledger.csv").status_code == 200


def test_extraction_error_still_saves(app_env, monkeypatch):
    db, main = app_env
    monkeypatch.setattr(main.extract, "extraction_available", lambda: True)

    def boom(data, media):
        raise main.extract.ExtractionError("API down")

    monkeypatch.setattr(main.extract, "extract_from_file", boom)
    with TestClient(main.app) as client:
        r = client.post("/upload", files=[("files", ("r.png", PNG, "image/png"))])
        assert "API down" in r.text
        assert db.list_expenses()[0]["confidence"] == "low"


def test_settings_and_manual_entry(app_env):
    db, main = app_env
    with TestClient(main.app) as client:
        r = client.post("/settings", data={"business_name": "AIG", "partner_a_name": "Nick", "partner_a_pct": "60",
                                           "partner_b_name": "Marija", "partner_b_pct": "40", "gst_registered": "no"})
        assert r.status_code == 200
        assert db.get_settings()["partner_a_pct"] == "60"
        bad = client.post("/settings", data={"business_name": "AIG", "partner_a_name": "Nick", "partner_a_pct": "60",
                                             "partner_b_name": "Marija", "partner_b_pct": "60"})
        assert bad.status_code == 400
        r = client.post("/expenses/new", follow_redirects=False)
        assert r.status_code == 303 and len(db.list_expenses()) == 1


def test_receipt_served_by_id_only(app_env):
    _, main = app_env
    with TestClient(main.app) as client:
        assert client.get("/receipts/..%2Fexpenses.db").status_code == 404
        assert client.get("/expenses/999/receipt").status_code == 404


def test_snap_flow_folders_and_excel(app_env, monkeypatch, tmp_path):
    db, main = app_env
    from openpyxl import load_workbook
    receipts, xl = tmp_path / "My Receipts", tmp_path / "My Excel"
    monkeypatch.setattr(main.extract, "extraction_available", lambda: True)
    monkeypatch.setattr(main.extract, "extract_from_file", lambda data, media: dict(
        FAKE, vendor="Cactus Club", category_line="8523", total=84.0, gst=3.75, pst=0, expense_date="2026-03-02"))
    with TestClient(main.app) as client:
        assert "Take a photo" in client.get("/snap").text
        assert client.get("/manifest.webmanifest").json()["start_url"] == "/snap"
        assert client.get("/apple-touch-icon.png").content[:4] == b"\x89PNG"
        r = client.post("/settings", data={
            "business_name": "AIG", "partner_a_name": "Nick", "partner_a_pct": "50", "partner_b_name": "Marija",
            "partner_b_pct": "50", "gst_registered": "no", "receipts_folder": str(receipts), "excel_folder": str(xl)})
        assert r.status_code == 200 and "Saved" in r.text
        r = client.post("/upload", data={"next": "capture"},
                        files=[("files", ("IMG_0001.jpg", PNG, "image/jpeg"))], follow_redirects=False)
        assert r.status_code == 303 and "snapped=added" in r.headers["location"]
        e = db.list_expenses()[0]
        assert e["source_file"].startswith(str(receipts / "2026"))
        assert "Cactus Club - Entertainment business development" in e["source_file"]
        assert client.get(f"/expenses/{e['id']}/receipt").status_code == 200
        page = client.get(r.headers["location"]).text
        assert "Entertainment / business development" in page

        wb = load_workbook(xl / "AIG Expenses 2026.xlsx")
        ws = wb["Expenses"]
        assert ws["B2"].value == "Cactus Club" and ws["D2"].value == "Entertainment / business development"
        assert ws["N2"].value == 42.0  # not GST registered: 50% of the full 84.00
        assert wb["Summary"]["A2"].value == "Entertainment / business development"

        # editing the vendor renames the file and updates the workbook
        client.post(f"/expenses/{e['id']}", data={
            "vendor": "Earls", "expense_date": "2026-03-02", "total": "84", "gst": "3.75",
            "category_line": "8523", "business_pct": "100", "action": "save"})
        e = db.get_expense(e["id"])
        assert "Earls" in e["source_file"] and Path(e["source_file"]).is_file()
        assert load_workbook(xl / "AIG Expenses 2026.xlsx")["Expenses"]["B2"].value == "Earls"

        bad = client.post("/settings", data={
            "business_name": "AIG", "partner_a_name": "Nick", "partner_a_pct": "50", "partner_b_name": "Marija",
            "partner_b_pct": "50", "receipts_folder": "relative/path"})
        assert "full folder path" in bad.text


def test_extract_schema_and_blocks():
    from expenses import extract
    assert extract._content_block(PNG, "image/png")["type"] == "image"
    assert extract._content_block(b"%PDF-1.4", "application/pdf")["type"] == "document"
    json.dumps(extract.SCHEMA)
