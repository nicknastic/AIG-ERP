"""Minimal Microsoft Graph client for one connected OneDrive.

One person (normally Nick) connects their OneDrive from the Settings page. The
app keeps that account's refresh token in the database and uses it to save
receipt photos and the Excel workbook into the folders chosen in Settings, so
both partners' receipts land in the same OneDrive.
"""

import threading
import time
from urllib.parse import quote

import httpx

from . import auth, db

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Files.ReadWrite"
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
CHUNK = 320 * 1024 * 10  # upload-session chunks must be multiples of 320 KiB

_lock = threading.Lock()
_token: dict = {"access": None, "expires": 0.0}


class OneDriveError(Exception):
    pass


def connected() -> bool:
    return bool(db.get_private("onedrive_refresh_token"))


def account() -> str | None:
    return db.get_private("onedrive_account")


def save_connection(token_response: dict, email: str) -> None:
    db.set_private("onedrive_refresh_token", token_response["refresh_token"])
    db.set_private("onedrive_account", email)
    with _lock:
        _token["access"] = token_response.get("access_token")
        _token["expires"] = time.time() + int(token_response.get("expires_in", 0)) - 120


def disconnect() -> None:
    db.set_private("onedrive_refresh_token", None)
    db.set_private("onedrive_account", None)
    with _lock:
        _token.update(access=None, expires=0.0)


def _access_token() -> str:
    with _lock:
        if _token["access"] and time.time() < _token["expires"]:
            return _token["access"]
        refresh = db.get_private("onedrive_refresh_token")
        if not refresh:
            raise OneDriveError("OneDrive is not connected. Connect it in Settings.")
        data = auth.token_request({"grant_type": "refresh_token", "refresh_token": refresh, "scope": SCOPES})
        if "access_token" not in data:
            raise OneDriveError(
                "OneDrive sign-in has expired. Reconnect it in Settings. "
                f"({data.get('error_description') or data.get('error')})"
            )
        if data.get("refresh_token"):
            db.set_private("onedrive_refresh_token", data["refresh_token"])
        _token["access"] = data["access_token"]
        _token["expires"] = time.time() + int(data.get("expires_in", 3600)) - 120
        return _token["access"]


def _request(method: str, url: str, **kw) -> httpx.Response:
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {_access_token()}"
    try:
        r = httpx.request(method, url, headers=headers, timeout=60, **kw)
    except httpx.HTTPError as e:
        raise OneDriveError(f"Couldn't reach OneDrive: {e}") from e
    if r.status_code == 423:
        raise OneDriveError("The file is open and locked in OneDrive. Close it and try again.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except ValueError:
            msg = r.text
        raise OneDriveError(f"OneDrive error {r.status_code}: {msg[:300]}")
    return r


def _path_url(path: str) -> str:
    path = path.strip("/")
    return f"{GRAPH}/me/drive/root:/{quote(path)}:"


def upload(path: str, data: bytes) -> dict:
    """Create or replace the file at `path` (folders are created as needed)."""
    if len(data) <= SIMPLE_UPLOAD_LIMIT:
        return _request("PUT", _path_url(path) + "/content", content=data).json()
    session = _request(
        "POST", _path_url(path) + "/createUploadSession",
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
    ).json()
    upload_url, total, item = session["uploadUrl"], len(data), {}
    for start in range(0, total, CHUNK):
        chunk = data[start:start + CHUNK]
        end = start + len(chunk) - 1
        # The upload URL is pre-authorised: no bearer token on these requests.
        r = httpx.put(upload_url, content=chunk, timeout=120,
                      headers={"Content-Range": f"bytes {start}-{end}/{total}"})
        if r.status_code >= 400:
            raise OneDriveError(f"OneDrive upload failed ({r.status_code})")
        if r.status_code in (200, 201):
            item = r.json()
    return item


def download(item_id: str) -> bytes:
    return _request("GET", f"{GRAPH}/me/drive/items/{item_id}/content", follow_redirects=True).content


def delete(item_id: str) -> None:
    try:
        _request("DELETE", f"{GRAPH}/me/drive/items/{item_id}")
    except OneDriveError as e:
        if "404" not in str(e):
            raise


def check_folder(path: str) -> None:
    """Write and remove a tiny file to prove the folder is usable."""
    item = upload(f"{path.strip('/')}/.aig-write-test.txt", b"ok")
    delete(item["id"])
