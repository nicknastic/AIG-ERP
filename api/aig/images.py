"""Make phone photos upright JPEGs of a sensible size (the phone usually does this already)."""

import io

MAX_EDGE = 2400


def normalize(raw: bytes, media_type: str) -> tuple[bytes, str]:
    if not media_type.startswith("image/") or media_type == "image/gif":
        return raw, media_type
    try:
        from PIL import Image, ImageOps
        if media_type in ("image/heic", "image/heif"):
            import pillow_heif
            pillow_heif.register_heif_opener()
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        if max(img.size) <= MAX_EDGE and media_type == "image/jpeg" and len(raw) < 1_500_000:
            return raw, media_type
        img = img.convert("RGB")
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - an odd image is still worth keeping as-is
        return raw, media_type
