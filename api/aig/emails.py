"""Parse saved email files (.eml) into text plus receipt attachments.

Outlook on the web: open the message, ... menu > Download > Download as EML.
Outlook desktop (new Outlook / Windows): drag the message to a folder, or File > Save As.
Classic Outlook saves .msg files; those are read if the optional
'extract-msg' package is installed.
"""

import email
import html
import re
from email import policy
from email.message import EmailMessage

from .extract import HEIC_TYPES, IMAGE_TYPES, PDF_TYPE

ATTACHMENT_TYPES = IMAGE_TYPES | HEIC_TYPES | {PDF_TYPE}


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def parse_eml(raw: bytes) -> dict:
    msg: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = _html_to_text(body)
    attachments = []
    for part in msg.iter_attachments():
        ctype = part.get_content_type()
        if ctype in ATTACHMENT_TYPES:
            attachments.append((part.get_content(), ctype, part.get_filename() or "attachment"))
    return {
        "subject": str(msg.get("subject", "")),
        "from": str(msg.get("from", "")),
        "date": str(msg.get("date", "")),
        "body": body,
        "attachments": attachments,
    }


def parse_msg(raw: bytes) -> dict:
    try:
        import extract_msg  # type: ignore
    except ImportError as e:  # pragma: no cover - optional dependency
        raise ValueError(
            "Outlook .msg files need the optional 'extract-msg' package "
            "(pip install extract-msg). Or save the email as .eml instead."
        ) from e
    import io
    import mimetypes

    m = extract_msg.Message(io.BytesIO(raw))
    attachments = []
    for a in m.attachments:
        name = a.longFilename or a.shortFilename or "attachment"
        ctype = mimetypes.guess_type(name)[0] or ""
        if ctype in ATTACHMENT_TYPES and isinstance(a.data, bytes):
            attachments.append((a.data, ctype, name))
    body = m.body or (_html_to_text(m.htmlBody.decode("utf-8", "ignore")) if m.htmlBody else "")
    return {
        "subject": m.subject or "",
        "from": m.sender or "",
        "date": str(m.date or ""),
        "body": body,
        "attachments": attachments,
    }
