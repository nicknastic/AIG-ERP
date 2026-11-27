"""Login, OneDrive storage and photo handling used when the app is hosted on Azure."""

import io

from fastapi.testclient import TestClient
from PIL import Image

LOGIN_ENV = dict(MS_CLIENT_ID="cid", MS_CLIENT_SECRET="sec", ALLOWED_EMAILS="nick@outlook.com, Marija@example.com",
                 SESSION_SECRET="test-secret", PUBLIC_URL="https://aig.example.net")


def jpeg(w, h) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def sign_in(client, main, monkeypatch, email="nick@outlook.com", purpose_tokens=None):
    tokens = purpose_tokens or {"access_token": "at"}
    monkeypatch.setattr(main.auth, "token_request", lambda data: dict(tokens))
    monkeypatch.setattr(main.auth, "_me", lambda at: {"userPrincipalName": email, "givenName": "Nick"})
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://login.microsoftonline.com/common/")
    assert "redirect_uri=https%3A%2F%2Faig.example.net%2Fauth%2Fcallback" in r.headers["location"]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    return client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)


def test_no_login_needed_locally(app_env):
    _, main = app_env
    with TestClient(main.app) as c:
        assert c.get("/snap").status_code == 200


def test_azure_without_login_config_refuses(make_app):
    _, main = make_app(WEBSITE_SITE_NAME="aig", SESSION_SECRET="x")
    with TestClient(main.app) as c:
        assert c.get("/").status_code == 503
        assert c.get("/healthz").status_code == 200


def test_login_required_and_allowed_emails(make_app, monkeypatch):
    _, main = make_app(**LOGIN_ENV)
    with TestClient(main.app, base_url="https://testserver") as c:
        r = c.get("/summary", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert c.post("/upload").status_code == 401
        r = sign_in(c, main, monkeypatch, email="stranger@gmail.com")
        assert r.status_code == 403
        assert c.get("/", follow_redirects=False).status_code == 303
        r = sign_in(c, main, monkeypatch, email="marija@example.com")
        assert r.status_code == 303 and r.headers["location"] == "/"  # back to the last page she tried
        assert c.get("/").status_code == 200
        assert "Sign out" in c.get("/snap").text
        c.get("/logout")
        assert c.get("/", follow_redirects=False).status_code == 303


def test_work_tenant_and_mail_differs_from_upn(make_app, monkeypatch):
    _, main = make_app(**dict(LOGIN_ENV, MS_TENANT="adriaticintelligence.com",
                              ALLOWED_EMAILS="nick@adriaticintelligence.com"))
    monkeypatch.setattr(main.auth, "token_request", lambda data: {"access_token": "at"})
    monkeypatch.setattr(main.auth, "_me", lambda at: {"userPrincipalName": "nnastic@adriaticintelligence.onmicrosoft.com",
                                                      "mail": "Nick@adriaticintelligence.com"})
    with TestClient(main.app, base_url="https://testserver") as c:
        r = c.get("/login", follow_redirects=False)
        assert r.headers["location"].startswith("https://login.microsoftonline.com/adriaticintelligence.com/")
        state = r.headers["location"].split("state=")[1].split("&")[0]
        assert c.get(f"/auth/callback?code=x&state={state}", follow_redirects=False).status_code == 303
        assert c.get("/").status_code == 200


def test_callback_rejects_bad_state(make_app, monkeypatch):
    _, main = make_app(**LOGIN_ENV)
    with TestClient(main.app, base_url="https://testserver") as c:
        c.get("/login", follow_redirects=False)
        assert c.get("/auth/callback?code=x&state=wrong").status_code == 400


class FakeDrive:
    def __init__(self):
        self.files, self.n = {}, 0

    def upload(self, path, data):
        for i, (p, _) in list(self.files.items()):
            if p == path:
                del self.files[i]
        self.n += 1
        self.files[str(self.n)] = (path, data)
        return {"id": str(self.n), "webUrl": f"https://onedrive.live.com/{self.n}"}

    def download(self, item_id):
        return self.files[item_id][1]

    def delete(self, item_id):
        self.files.pop(item_id, None)

    def paths(self):
        return sorted(p for p, _ in self.files.values())


def test_onedrive_storage_end_to_end(make_app, monkeypatch):
    db, main = make_app(**LOGIN_ENV)
    drive = FakeDrive()
    od = main.onedrive
    for name in ("upload", "download", "delete"):
        monkeypatch.setattr(od, name, getattr(drive, name))
    monkeypatch.setattr(od, "check_folder", lambda path: None)
    monkeypatch.setattr(main.extract, "extraction_available", lambda: True)
    monkeypatch.setattr(main.extract, "extract_from_file", lambda data, media: {
        "is_receipt": True, "vendor": "Air Canada", "expense_date": "2026-05-01", "description": "YVR-YYZ",
        "currency": "CAD", "subtotal": 400.0, "gst": 20.0, "pst": 0, "tip": 0, "total": 420.0,
        "payment_method": "Visa", "gst_number": None, "category_line": "9200", "confidence": "high", "notes": None})

    with TestClient(main.app, base_url="https://testserver") as c:
        sign_in(c, main, monkeypatch)
        # a local receipt from before OneDrive was connected
        c.post("/upload", files=[("files", ("old.jpg", jpeg(50, 50), "image/jpeg"))])
        # Nick connects OneDrive
        r = c.get("/onedrive/connect", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        assert "Files.ReadWrite" in r.headers["location"].replace("%20", " ").replace("+", " ")
        monkeypatch.setattr(main.auth, "token_request",
                            lambda data: {"access_token": "at", "refresh_token": "secret-refresh-xyz", "expires_in": 3600})
        r = c.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
        assert "OneDrive+connected" in r.headers["location"]
        assert db.get_settings()["storage"] == "onedrive"
        assert db.get_private("onedrive_refresh_token") == "secret-refresh-xyz"
        assert "secret-refresh-xyz" not in c.get("/settings").text

        # set folders: existing receipt moves into OneDrive
        r = c.post("/settings", data={"business_name": "AIG", "partner_a_name": "Nick", "partner_a_pct": "50",
                                       "partner_b_name": "Marija", "partner_b_pct": "50", "storage": "onedrive",
                                       "receipts_folder": "Business/AIG/Receipts", "excel_folder": "Business/AIG"})
        assert "Moved 1" in r.text
        assert all(e["source_file"].startswith("onedrive:") for e in db.list_expenses())

        # a big iPhone-size photo goes straight to OneDrive, shrunk
        r = c.post("/upload", data={"next": "capture"},
                   files=[("files", ("IMG_1234.jpg", jpeg(4032, 3024), "image/jpeg"))], follow_redirects=False)
        eid = int(r.headers["location"].split("/expenses/")[1].split("?")[0])
        e = db.get_expense(eid)
        assert e["source_path"] == f"Business/AIG/Receipts/2026/2026-05-01 Air Canada - Travel - {eid}.jpg"
        img = Image.open(io.BytesIO(c.get(f"/expenses/{eid}/receipt").content))
        assert max(img.size) == 2400
        assert "Business/AIG/AIG Expenses 2026.xlsx" in drive.paths()

        # editing renames the OneDrive file
        c.post(f"/expenses/{eid}", data={"vendor": "WestJet", "expense_date": "2026-05-01", "total": "420",
                                         "gst": "20", "category_line": "9200", "business_pct": "100", "action": "save"})
        assert any("WestJet - Travel" in p for p in drive.paths())
        assert not any(f"Air Canada - Travel - {eid}" in p for p in drive.paths())

        z = c.get("/export/package.zip?year=2026")
        assert z.status_code == 200


def test_onedrive_failure_keeps_photo(make_app, monkeypatch):
    db, main = make_app(**LOGIN_ENV)
    db.set_private("onedrive_refresh_token", "rt")
    db.save_settings({"storage": "onedrive"})

    def down(path, data):
        raise main.onedrive.OneDriveError("offline")

    monkeypatch.setattr(main.onedrive, "upload", down)
    with TestClient(main.app, base_url="https://testserver") as c:
        sign_in(c, main, monkeypatch)
        c.post("/upload", files=[("files", ("r.jpg", jpeg(60, 60), "image/jpeg"))])
        e = db.list_expenses()[0]
        assert "unsynced" in e["source_file"] and "Not saved to OneDrive" in e["notes"]
        assert c.get(f"/expenses/{e['id']}/receipt").status_code == 200


def test_normalize_image_rotates_and_shrinks():
    from expenses import storage
    img = Image.new("RGB", (4000, 3000), "white")
    exif = img.getexif()
    exif[0x0112] = 6  # camera held upright: rotate 90°
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    out, media = storage.normalize_image(buf.getvalue(), "image/jpeg")
    assert media == "image/jpeg"
    assert Image.open(io.BytesIO(out)).size == (1800, 2400)
