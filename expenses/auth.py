"""Sign in with a Microsoft account, limited to the email addresses in ALLOWED_EMAILS.

Environment variables (set as App Service application settings on Azure):

    MS_CLIENT_ID      Application (client) ID of the app registration
    MS_CLIENT_SECRET  A client secret of that app registration
    MS_TENANT         'common' (default) allows personal Microsoft accounts such as outlook.com
    ALLOWED_EMAILS    Comma-separated list, e.g. nick@outlook.com,marija@example.com
    SESSION_SECRET    Long random string used to sign the login cookie
    PUBLIC_URL        e.g. https://aig-expenses.azurewebsites.net (used for the sign-in redirect)

When MS_CLIENT_ID is not set the app runs without a login, which is only meant
for running it on your own computer. On Azure (WEBSITE_SITE_NAME is set) the
app refuses to serve pages until the login is configured.
"""

import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

LOGIN_SCOPES = "openid profile email User.Read"
PUBLIC_PATHS = {"/login", "/auth/callback", "/logout", "/manifest.webmanifest", "/icon.svg",
                "/apple-touch-icon.png", "/healthz"}

router = APIRouter()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def enabled() -> bool:
    return bool(_env("MS_CLIENT_ID"))


def on_azure() -> bool:
    return bool(_env("WEBSITE_SITE_NAME"))


def allowed_emails() -> set[str]:
    return {e.strip().lower() for e in _env("ALLOWED_EMAILS").split(",") if e.strip()}


def _authority() -> str:
    return f"https://login.microsoftonline.com/{_env('MS_TENANT', 'common')}/oauth2/v2.0"


def redirect_uri(request: Request) -> str:
    base = _env("PUBLIC_URL") or str(request.base_url).rstrip("/")
    return base.rstrip("/") + "/auth/callback"


def token_request(data: dict) -> dict:
    payload = {"client_id": _env("MS_CLIENT_ID"), "client_secret": _env("MS_CLIENT_SECRET"), **data}
    try:
        r = httpx.post(f"{_authority()}/token", data=payload, timeout=30)
    except httpx.HTTPError as e:
        return {"error": "network", "error_description": str(e)}
    try:
        return r.json()
    except ValueError:
        return {"error": f"http_{r.status_code}"}


def _me(access_token: str) -> dict:
    r = httpx.get("https://graph.microsoft.com/v1.0/me",
                  headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _email(me: dict) -> str:
    return (me.get("mail") or me.get("userPrincipalName") or "").lower()


def current_user(request: Request) -> dict | None:
    return request.session.get("user") if "session" in request.scope else None


def gate(request: Request):
    """Return a response if the request must not go through, else None."""
    if request.url.path in PUBLIC_PATHS:
        return None
    if not enabled():
        if on_azure():
            return HTMLResponse(
                "<h1>Login not configured</h1><p>Set MS_CLIENT_ID, MS_CLIENT_SECRET, ALLOWED_EMAILS and "
                "SESSION_SECRET in the App Service settings.</p>", status_code=503)
        return None
    if current_user(request):
        return None
    if request.method != "GET":
        return HTMLResponse("Signed out. Reload the page and sign in again.", status_code=401)
    request.session["after_login"] = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse("/login", status_code=303)


def _authorize_url(request: Request, purpose: str, scopes: str, prompt: str = "select_account") -> str:
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    request.session["oauth_purpose"] = purpose
    return f"{_authority()}/authorize?" + urlencode({
        "client_id": _env("MS_CLIENT_ID"), "response_type": "code", "redirect_uri": redirect_uri(request),
        "response_mode": "query", "scope": scopes, "state": state, "prompt": prompt,
    })


@router.get("/login")
def login(request: Request):
    if not enabled():
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(_authorize_url(request, "login", LOGIN_SCOPES), status_code=303)


@router.get("/onedrive/connect")
def onedrive_connect(request: Request):
    from .onedrive import SCOPES
    if not enabled():
        return HTMLResponse("Set up the Microsoft sign-in first (see deploy/AZURE.md).", status_code=400)
    return RedirectResponse(_authorize_url(request, "onedrive", SCOPES, prompt="consent"), status_code=303)


@router.post("/onedrive/disconnect")
def onedrive_disconnect():
    from . import onedrive
    onedrive.disconnect()
    return RedirectResponse("/settings?" + urlencode({"saved": "OneDrive disconnected."}), status_code=303)


def _error_page(msg: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<div style="font-family:system-ui;max-width:480px;margin:40px auto;padding:0 16px">'
        f'<h2>Can\'t sign in</h2><p>{msg}</p><p><a href="/login">Try again</a></p></div>', status_code=status)


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return _error_page(error_description or error)
    if not state or state != request.session.pop("oauth_state", None):
        return _error_page("The sign-in link expired. Please try again.")
    purpose = request.session.pop("oauth_purpose", "login")
    from .onedrive import SCOPES
    tokens = token_request({
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(request),
        "scope": SCOPES if purpose == "onedrive" else LOGIN_SCOPES,
    })
    if "access_token" not in tokens:
        return _error_page(tokens.get("error_description") or tokens.get("error") or "Unknown error")
    try:
        me = _me(tokens["access_token"])
    except httpx.HTTPError:
        return _error_page("Couldn't read your Microsoft profile.")
    email = _email(me)
    if email not in allowed_emails():
        request.session.clear()
        return _error_page(f"{email or 'This account'} isn't allowed to use this site.", 403)

    if purpose == "onedrive":
        from . import db, onedrive
        if "refresh_token" not in tokens:
            return _error_page("Microsoft didn't grant offline access to OneDrive.")
        onedrive.save_connection(tokens, email)
        db.save_settings({"storage": "onedrive"})
        return RedirectResponse("/settings?" + urlencode({"saved": f"OneDrive connected ({email})."}), status_code=303)

    request.session["user"] = {"email": email, "name": me.get("givenName") or me.get("displayName") or email}
    return RedirectResponse(request.session.pop("after_login", "/snap") or "/snap", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return HTMLResponse('<meta name="viewport" content="width=device-width, initial-scale=1">'
                        '<div style="font-family:system-ui;margin:40px 16px">Signed out. <a href="/login">Sign in</a></div>')


def session_secret() -> str:
    secret = _env("SESSION_SECRET")
    if secret:
        return secret
    if enabled() or on_azure():
        raise RuntimeError("SESSION_SECRET must be set when the Microsoft login is enabled.")
    return "local-only-not-secret"
