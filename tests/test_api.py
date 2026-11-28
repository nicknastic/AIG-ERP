import base64
import io
import json

from openpyxl import Workbook, load_workbook
from PIL import Image

FAKE = {
    "is_receipt": True, "vendor": "Cactus Club", "expense_date": "2026-03-02", "description": "Client dinner",
    "currency": "CAD", "subtotal": 80.0, "gst": 4.0, "pst": 0, "tip": 0, "total": 84.0,
    "payment_method": "Visa", "gst_number": None, "category_line": "8523", "confidence": "high", "notes": None,
}


def jpeg(w=60, h=40) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def principal(email):
    return {"x-ms-client-principal": base64.b64encode(json.dumps(
        {"identityProvider": "aad", "userDetails": email, "userRoles": ["anonymous", "authenticated"],
         "claims": [{"typ": "name", "val": "Nick Nastic"}]}).encode()).decode()}


def fake_reader(monkeypatch, fields=FAKE):
    from aig import extract
    monkeypatch.setattr(extract, "extraction_available", lambda: True)
    monkeypatch.setattr(extract, "extract_from_file", lambda data, media: dict(fields))


def workbook(client):
    return load_workbook(client.root / "AIG Expenses" / "AIG Expenses.xlsx")


def test_snap_classify_and_excel(client, monkeypatch):
    fake_reader(monkeypatch)
    r = client.post("/api/upload", files=[("files", ("IMG_0001.HEIC.jpg", jpeg(4032, 3024), "image/jpeg"))])
    res = r.json()["results"][0]
    assert res["status"] == "added" and res["id"] == 1

    photo = client.root / "AIG Expenses/Receipts/2026/2026-03-02 Cactus Club - Entertainment business development - 1.jpg"
    assert photo.is_file() and max(Image.open(photo).size) == 2400

    ws = workbook(client)["Expenses"]
    head = [c.value for c in ws[1]]
    row = dict(zip(head, [c.value for c in ws[2]]))
    assert row["Vendor"] == "Cactus Club" and row["Expense type"] == "Entertainment / business development"
    assert row["Deductible (CAD)"] == 42.0  # not GST registered: half of the full 84.00
    assert row["Status"] == "To review" and row["ID"] == 1
    assert workbook(client)["Summary 2026"]["A2"].value == "Entertainment / business development"

    e = client.get("/api/expenses/1").json()["expense"]
    assert e["type"] == "Entertainment / business development" and "source_item" not in e
    assert client.get("/api/expenses/1/receipt").content[:2] == b"\xff\xd8"

    # same photo again
    again = client.post("/api/upload", files=[("files", ("x.jpg", jpeg(4032, 3024), "image/jpeg"))]).json()
    assert again["results"][0]["status"] == "duplicate"


def test_edit_renames_file_and_confirms(client, monkeypatch):
    fake_reader(monkeypatch)
    client.post("/api/upload", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))])
    d = client.post("/api/expenses/1", json={
        "vendor": "Earls", "expense_date": "2026-03-02", "total": "84", "gst": "4", "category_line": "8523",
        "business_pct": "100", "action": "confirm"}).json()
    assert d["expense"]["status"] == "confirmed" and d["expense"]["vendor"] == "Earls"
    files = [p.name for p in (client.root / "AIG Expenses/Receipts/2026").iterdir()]
    assert files == ["2026-03-02 Earls - Entertainment business development - 1.jpg"]
    ws = workbook(client)["Expenses"]
    assert ws["B2"].value == "Earls" and ws["S2"].value == "Confirmed"


def test_edits_made_in_excel_are_read_back(client, monkeypatch):
    fake_reader(monkeypatch)
    client.post("/api/upload", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))])
    path = client.root / "AIG Expenses" / "AIG Expenses.xlsx"
    wb = load_workbook(path)
    ws = wb["Expenses"]
    head = {c.value: i + 1 for i, c in enumerate(ws[1])}
    ws.cell(row=2, column=head["Vendor"]).value = "Cactus Club Coal Harbour"
    ws.cell(row=2, column=head["CRA line"]).value = "9200"
    # a row typed straight into Excel, without an ID
    ws.cell(row=3, column=head["Date"]).value = "2026-04-01"
    ws.cell(row=3, column=head["Vendor"]).value = "Translink"
    ws.cell(row=3, column=head["Total paid"]).value = 3.15
    wb.save(path)
    d = client.get("/api/expenses").json()
    by_vendor = {e["vendor"]: e for e in d["expenses"]}
    assert by_vendor["Cactus Club Coal Harbour"]["type"] == "Travel"
    assert by_vendor["Translink"]["id"] == 2 and by_vendor["Translink"]["total"] == 3.15


def test_manual_delete_summary_exports(client, monkeypatch):
    fake_reader(monkeypatch)
    client.post("/api/upload", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))])
    new = client.post("/api/expenses").json()["id"]
    client.post(f"/api/expenses/{new}", json={"vendor": "Staples", "expense_date": "2026-02-14", "total": "22.40",
                                              "category_line": "8810", "action": "confirm"})
    s = client.get("/api/summary?year=2026").json()
    assert s["total_deductible"] == 64.4 and s["partner_a"] == 32.2 and s["needs_review"] == 1
    assert "Office expenses" in client.get("/api/export/summary.csv?year=2026").text
    z = client.get("/api/export/package.zip?year=2026")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert client.get("/api/export/workbook.xlsx").content[:2] == b"PK"
    client.delete("/api/expenses/1")
    assert not list((client.root / "AIG Expenses/Receipts/2026").iterdir())
    assert [e["vendor"] for e in client.get("/api/expenses").json()["expenses"]] == ["Staples"]


def test_settings_move_folders(client, monkeypatch):
    fake_reader(monkeypatch)
    client.post("/api/upload", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))])
    r = client.post("/api/settings", json={
        "business_name": "AIG", "partner_a_name": "Nick", "partner_a_pct": "60", "partner_b_name": "Marija",
        "partner_b_pct": "40", "gst_registered": "no", "receipts_folder": "Business/Receipts",
        "excel_folder": "Business"})
    assert r.status_code == 200 and r.json()["moved"] == 1
    assert (client.root / "Business/AIG Expenses.xlsx").is_file()
    assert not (client.root / "AIG Expenses/AIG Expenses.xlsx").exists()
    assert list((client.root / "Business/Receipts/2026").iterdir())
    wb = load_workbook(client.root / "Business/AIG Expenses.xlsx")
    assert wb["Summary 2026"]["G1"].value == "Nick 60%"
    bad = client.post("/api/settings", json={"partner_a_pct": "60", "partner_b_pct": "60"})
    assert bad.status_code == 400


def test_reading_failure_still_saves_photo(client, monkeypatch):
    from aig import extract
    monkeypatch.setattr(extract, "extraction_available", lambda: True)

    def boom(data, media):
        raise extract.ExtractionError("API down")
    monkeypatch.setattr(extract, "extract_from_file", boom)
    res = client.post("/api/upload", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))]).json()["results"][0]
    assert res["status"] == "error" and "API down" in res["error"]
    e = client.get(f"/api/expenses/{res['id']}").json()["expense"]
    assert e["has_receipt"] and e["confidence"] == "low"


def test_email_receipt(client, monkeypatch):
    from email.message import EmailMessage

    from aig import extract
    monkeypatch.setattr(extract, "extraction_available", lambda: True)
    seen = {}

    def fake_email(subject, sender, body, atts):
        seen.update(subject=subject, n=len(atts))
        return dict(FAKE, vendor="Zoom", category_line="9270", total=21.0)
    monkeypatch.setattr(extract, "extract_from_email", fake_email)
    m = EmailMessage()
    m["Subject"], m["From"] = "Your Zoom receipt", "billing@zoom.us"
    m.set_content("Zoom Pro monthly $21.00")
    m.add_attachment(jpeg(), maintype="image", subtype="jpeg", filename="invoice.jpg")
    res = client.post("/api/upload", files=[("files", ("zoom.eml", bytes(m), "message/rfc822"))]).json()["results"][0]
    e = client.get(f"/api/expenses/{res['id']}").json()["expense"]
    assert e["source"] == "email" and e["vendor"] == "Zoom" and seen["n"] == 1
    assert e["receipt_name"].endswith(".eml")


def test_access_control_on_azure(client, monkeypatch):
    monkeypatch.setenv("WEBSITE_INSTANCE_ID", "x")
    assert client.get("/api/expenses").status_code == 401
    monkeypatch.setenv("ALLOWED_EMAILS", "nick@adriaticintelligence.com,marija@adriaticintelligence.com")
    assert client.get("/api/expenses", headers=principal("someone@gmail.com")).status_code == 403
    r = client.get("/api/me", headers=principal("Nick@AdriaticIntelligence.com"))
    assert r.status_code == 200 and r.json() == {"email": "nick@adriaticintelligence.com", "name": "Nick"}
    up = client.post("/api/upload", headers=principal("marija@adriaticintelligence.com"),
                     files=[("files", ("a.jpg", jpeg(), "image/jpeg"))]).json()["results"][0]
    assert client.get(f"/api/expenses/{up['id']}", headers=principal("nick@adriaticintelligence.com")).json()[
        "expense"]["added_by"] == "marija@adriaticintelligence.com"


def test_path_without_api_prefix(client):
    # Azure Functions may pass the path without /api
    assert client.get("/config").status_code == 200


def test_conflicting_save_retries(client, monkeypatch):
    from aig import store as store_mod
    from aig.workbook import Ledger
    fake_reader(monkeypatch)
    client.post("/api/upload", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))])
    st = store_mod.get_store()
    real_write, calls = st.write, {"n": 0}

    def flaky(path, data, if_match=None, must_not_exist=False):
        if path.endswith(".xlsx") and calls["n"] == 0:
            calls["n"] += 1
            raise store_mod.ConflictError(path)
        return real_write(path, data, if_match=if_match, must_not_exist=must_not_exist)
    monkeypatch.setattr(st, "write", flaky)
    r = client.post("/api/expenses/1", json={"vendor": "Retry Inc", "expense_date": "2026-03-02", "category_line": "8523"})
    assert r.status_code == 200 and calls["n"] == 1
    assert Ledger(st, {"business_name": "AIG", "excel_folder": "AIG Expenses"}).get(1)["vendor"] == "Retry Inc"


def test_graph_store_paths():
    from aig.store import GraphStore
    s = GraphStore("t", "c", "s", site_id="contoso.sharepoint.com,1,2")
    assert s.base.endswith("/sites/contoso.sharepoint.com,1,2/drive")
    assert s._path_url("AIG Expenses/Receipts/2026/a b.jpg").endswith(":/AIG%20Expenses/Receipts/2026/a%20b.jpg:")
    u = GraphStore("t", "c", "s", user="nick@adriaticintelligence.com")
    assert "/users/nick%40adriaticintelligence.com/drive" in u.base


def test_azure_without_graph_settings_refuses_local_files(monkeypatch):
    import pytest
    from aig import store
    store.reset_store()
    for k in ("GRAPH_CLIENT_ID", "AIG_LOCAL_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WEBSITE_INSTANCE_ID", "x")
    with pytest.raises(store.StoreError):
        store.get_store()
    store.reset_store()
