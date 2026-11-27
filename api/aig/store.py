"""File storage: the connected OneDrive (on Azure) or a local folder (for development and tests).

On Azure the app uses Microsoft Graph with an app-only token (client
credentials) to read and write files in one document library. Settings:

    GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, and either
    SHAREPOINT_SITE_ID  the "AIG Expenses" SharePoint site (app has Sites.Selected on it only), or
    ONEDRIVE_USER       a person's OneDrive for Business (needs the broader Files.ReadWrite.All)

Without GRAPH_CLIENT_ID, files go to the folder in AIG_LOCAL_ROOT (default ./data).
"""

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
CHUNK = 320 * 1024 * 10  # upload-session chunks must be multiples of 320 KiB


class StoreError(Exception):
    pass


class ConflictError(StoreError):
    """Someone else changed the file since we read it."""


@dataclass
class FileInfo:
    id: str
    path: str
    url: str | None = None
    etag: str | None = None


def _clean_path(path: str) -> str:
    return "/".join(p for p in path.replace("\\", "/").split("/") if p and p not in (".", ".."))


class LocalStore:
    kind = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def describe(self, path: str) -> str:
        return str(self.root / _clean_path(path))

    def _p(self, path: str) -> Path:
        return self.root / _clean_path(path)

    @staticmethod
    def _etag(p: Path) -> str:
        st = p.stat()
        return hashlib.sha1(f"{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()

    def read(self, path: str) -> tuple[bytes, str] | None:
        p = self._p(path)
        if not p.is_file():
            return None
        return p.read_bytes(), self._etag(p)

    def write(self, path: str, data: bytes, if_match: str | None = None, must_not_exist: bool = False) -> FileInfo:
        p = self._p(path)
        if must_not_exist and p.exists():
            raise ConflictError(path)
        if if_match is not None and (not p.is_file() or self._etag(p) != if_match):
            raise ConflictError(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        try:
            tmp.replace(p)
        except PermissionError as e:
            tmp.unlink(missing_ok=True)
            raise StoreError(f"{p.name} is open in another program. Close it and try again.") from e
        return FileInfo(id=_clean_path(path), path=_clean_path(path), url=None, etag=self._etag(p))

    def read_item(self, item_id: str) -> bytes | None:
        found = self.read(item_id)
        return found[0] if found else None

    def delete_item(self, item_id: str) -> None:
        self._p(item_id).unlink(missing_ok=True)

    def delete_path(self, path: str) -> None:
        self._p(path).unlink(missing_ok=True)

    def check_folder(self, folder: str) -> None:
        info = self.write(f"{folder}/.aig-write-test", b"ok")
        self.delete_item(info.id)


class GraphStore:
    kind = "onedrive"

    def __init__(self, tenant: str, client_id: str, secret: str, user: str = "", site_id: str = ""):
        self.tenant, self.client_id, self.secret = tenant, client_id, secret
        self._token, self._expires = None, 0.0
        self._lock = threading.Lock()
        if site_id:
            self.base, self.label = f"{GRAPH}/sites/{site_id}/drive", "SharePoint (AIG Expenses site)"
        else:
            self.base, self.label = f"{GRAPH}/users/{quote(user)}/drive", f"OneDrive ({user})"

    def describe(self, path: str) -> str:
        return f"{self.label}: {_clean_path(path)}"

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires:
                return self._token
            try:
                r = httpx.post(
                    f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", timeout=30,
                    data={"grant_type": "client_credentials", "client_id": self.client_id,
                          "client_secret": self.secret, "scope": "https://graph.microsoft.com/.default"})
                data = r.json()
            except (httpx.HTTPError, ValueError) as e:
                raise StoreError(f"Couldn't get a OneDrive token: {e}") from e
            if "access_token" not in data:
                raise StoreError("OneDrive access was refused: " +
                                 (data.get("error_description") or data.get("error") or "unknown error")[:300])
            self._token = data["access_token"]
            self._expires = time.time() + int(data.get("expires_in", 3600)) - 120
            return self._token

    def _req(self, method: str, url: str, ok404: bool = False, **kw) -> httpx.Response | None:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token()}"
        try:
            r = httpx.request(method, url, headers=headers, timeout=60, **kw)
        except httpx.HTTPError as e:
            raise StoreError(f"Couldn't reach OneDrive: {e}") from e
        if r.status_code == 404 and ok404:
            return None
        if r.status_code in (409, 412):
            raise ConflictError(url)
        if r.status_code == 423:
            raise StoreError("The file is locked in OneDrive (probably open for editing). Close it and try again.")
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                msg = r.text
            raise StoreError(f"OneDrive error {r.status_code}: {msg[:300]}")
        return r

    def _path_url(self, path: str) -> str:
        return f"{self.base}/root:/{quote(_clean_path(path))}:"

    def read(self, path: str) -> tuple[bytes, str] | None:
        meta = self._req("GET", self._path_url(path), ok404=True)
        if meta is None:
            return None
        m = meta.json()
        content = self._req("GET", f"{self.base}/items/{m['id']}/content", follow_redirects=True)
        return content.content, m.get("eTag", "")

    def write(self, path: str, data: bytes, if_match: str | None = None, must_not_exist: bool = False) -> FileInfo:
        headers = {}
        if if_match is not None:
            headers["If-Match"] = if_match
        query = "?@microsoft.graph.conflictBehavior=fail" if must_not_exist else ""
        if len(data) <= SIMPLE_UPLOAD_LIMIT:
            m = self._req("PUT", self._path_url(path) + "/content" + query, content=data, headers=headers).json()
        else:
            session = self._req("POST", self._path_url(path) + "/createUploadSession", headers=headers,
                                json={"item": {"@microsoft.graph.conflictBehavior":
                                               "fail" if must_not_exist else "replace"}}).json()
            url, total, m = session["uploadUrl"], len(data), {}
            for start in range(0, total, CHUNK):
                chunk = data[start:start + CHUNK]
                r = httpx.put(url, content=chunk, timeout=120,
                              headers={"Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{total}"})
                if r.status_code >= 400:
                    raise StoreError(f"OneDrive upload failed ({r.status_code})")
                if r.status_code in (200, 201):
                    m = r.json()
        return FileInfo(id=m["id"], path=_clean_path(path), url=m.get("webUrl"), etag=m.get("eTag"))

    def read_item(self, item_id: str) -> bytes | None:
        r = self._req("GET", f"{self.base}/items/{item_id}/content", ok404=True, follow_redirects=True)
        return r.content if r is not None else None

    def delete_item(self, item_id: str) -> None:
        self._req("DELETE", f"{self.base}/items/{item_id}", ok404=True)

    def delete_path(self, path: str) -> None:
        self._req("DELETE", self._path_url(path), ok404=True)

    def check_folder(self, folder: str) -> None:
        info = self.write(f"{folder}/.aig-write-test.txt", b"ok")
        self.delete_item(info.id)


_store = None


def get_store():
    global _store
    if _store is None:
        if os.environ.get("GRAPH_CLIENT_ID"):
            missing = [k for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_SECRET") if not os.environ.get(k)]
            if not (os.environ.get("SHAREPOINT_SITE_ID") or os.environ.get("ONEDRIVE_USER")):
                missing.append("SHAREPOINT_SITE_ID or ONEDRIVE_USER")
            if missing:
                raise StoreError("Missing app settings: " + ", ".join(missing))
            _store = GraphStore(os.environ["GRAPH_TENANT_ID"], os.environ["GRAPH_CLIENT_ID"],
                                os.environ["GRAPH_CLIENT_SECRET"], user=os.environ.get("ONEDRIVE_USER", ""),
                                site_id=os.environ.get("SHAREPOINT_SITE_ID", ""))
        else:
            _store = LocalStore(os.environ.get("AIG_LOCAL_ROOT", Path.cwd() / "data"))
    return _store


def reset_store(store=None) -> None:
    global _store
    _store = store
