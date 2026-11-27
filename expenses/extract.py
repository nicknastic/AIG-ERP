"""Read a receipt (photo, PDF or email text) with Claude and return structured fields.

Needs ANTHROPIC_API_KEY (or another Anthropic credential) in the environment.
Without one, uploads still work: the expense is saved with empty fields for you
to fill in by hand.
"""

import base64
import io
import json
import os
from datetime import date

from .categories import CATEGORIES

MODEL = os.environ.get("EXPENSES_MODEL", "claude-opus-5-5")

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
PDF_TYPE = "application/pdf"
HEIC_TYPES = {"image/heic", "image/heif"}

SCHEMA = {
    "type": "object",
    "properties": {
        "is_receipt": {"type": "boolean", "description": "False if this is not a receipt, invoice or bill"},
        "vendor": {"type": ["string", "null"]},
        "expense_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "description": {"type": ["string", "null"], "description": "Short summary of what was bought"},
        "currency": {"type": "string", "description": "ISO code, e.g. CAD or USD"},
        "subtotal": {"type": ["number", "null"]},
        "gst": {"type": "number", "description": "GST or HST charged. 0 if none shown"},
        "pst": {"type": "number", "description": "Provincial sales tax (PST/QST/RST) charged. 0 if none shown"},
        "tip": {"type": "number"},
        "total": {"type": ["number", "null"], "description": "Amount actually paid, including tax and tip"},
        "payment_method": {"type": ["string", "null"], "description": "e.g. Visa ****1234, cash, debit"},
        "gst_number": {"type": ["string", "null"], "description": "Supplier's GST/HST registration number if printed"},
        "category_line": {"type": "string", "enum": [c.line for c in CATEGORIES]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": ["string", "null"], "description": "Anything unclear or worth a human check"},
    },
    "required": [
        "is_receipt", "vendor", "expense_date", "description", "currency", "subtotal", "gst",
        "pst", "tip", "total", "payment_method", "gst_number", "category_line", "confidence", "notes",
    ],
    "additionalProperties": False,
}


def _system_prompt() -> str:
    cats = "\n".join(f"- {c.line} {c.name}: {c.hint}" for c in CATEGORIES)
    return f"""You read receipts, invoices and bills for a small Canadian business \
(a general partnership in British Columbia) so they can be claimed as business \
expenses on CRA form T2125. Today is {date.today().isoformat()}.

Extract the fields exactly as printed. Rules:
- Amounts are numbers without currency symbols. If a field is not shown, use null (or 0 for taxes and tip).
- In BC, GST is 5% and PST is usually 7%. Put GST or HST in "gst" and PST in "pst". Never put PST into "gst".
- "total" is what was actually paid, including taxes and tip.
- Dates: if the year is missing or ambiguous, choose the most recent plausible date not in the future, and say so in notes.
- Choose the single best T2125 line:
{cats}
- Equipment such as computers, phones, furniture or vehicles that lasts more than a year goes to 9936 (CCA).
- Software subscriptions and cloud services go to 9270 unless a better line clearly fits.
- Set confidence to low if the image is blurry, cut off, or amounts don't add up."""


def _heic_to_jpeg(data: bytes) -> bytes:
    try:
        import pillow_heif  # type: ignore
        from PIL import Image
    except ImportError as e:  # pragma: no cover - optional dependency
        raise ValueError(
            "iPhone HEIC photos need the optional 'pillow-heif' package "
            "(pip install pillow-heif), or set the camera to 'Most Compatible' (JPEG)."
        ) from e
    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _content_block(data: bytes, media_type: str) -> dict:
    if media_type in HEIC_TYPES:
        data, media_type = _heic_to_jpeg(data), "image/jpeg"
    b64 = base64.standard_b64encode(data).decode("ascii")
    if media_type in IMAGE_TYPES:
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    if media_type == PDF_TYPE:
        return {"type": "document", "source": {"type": "base64", "media_type": PDF_TYPE, "data": b64}}
    raise ValueError(f"Unsupported file type: {media_type}")


class ExtractionError(Exception):
    pass


def extraction_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _call_claude(content: list[dict]) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=_system_prompt(),
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Rate limited by the Anthropic API. Try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to read this file.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The response was cut off.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ExtractionError("No data came back from the model.")
    return json.loads(text)


def extract_from_file(data: bytes, media_type: str) -> dict:
    block = _content_block(data, media_type)
    return _call_claude([block, {"type": "text", "text": "Extract the expense from this receipt."}])


def extract_from_email(subject: str, sender: str, body: str, attachments: list[tuple[bytes, str]]) -> dict:
    content: list[dict] = []
    for data, media_type in attachments:
        try:
            content.append(_content_block(data, media_type))
        except ValueError:
            continue
    content.append({
        "type": "text",
        "text": (
            "Extract the expense from this email receipt (and any attachments above).\n\n"
            f"From: {sender}\nSubject: {subject}\n\n{body[:50000]}"
        ),
    })
    return _call_claude(content)
