"""Who is calling. Static Web Apps signs people in and passes their identity in a header.

The site's routes only let signed-in people through (staticwebapp.config.json);
this module additionally limits the API to the emails in ALLOWED_EMAILS.
When the app runs on a developer's computer (not on Azure) there is no sign-in.
"""

import base64
import json
import os

from fastapi import HTTPException, Request


def allowed_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}


def on_azure() -> bool:
    return bool(os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("WEBSITE_INSTANCE_ID"))


def principal(request: Request) -> dict | None:
    raw = request.headers.get("x-ms-client-principal")
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw))
    except (ValueError, json.JSONDecodeError):
        return None


def current_user(request: Request) -> dict:
    p = principal(request)
    if p is None:
        if on_azure():
            raise HTTPException(401, "Please sign in.")
        return {"email": "local", "name": "Local user"}
    email = (p.get("userDetails") or "").lower()
    allowed = allowed_emails()
    if not allowed and on_azure():
        raise HTTPException(503, "ALLOWED_EMAILS is not set in the Static Web App settings.")
    if allowed and email not in allowed:
        raise HTTPException(403, f"{email or 'This account'} isn't allowed to use this site.")
    claims = {c.get("typ"): c.get("val") for c in p.get("claims") or []}
    name = claims.get("name") or email.split("@")[0].title()
    return {"email": email, "name": name.split(" ")[0]}
