"""Make archived invoice scans digestible for the model. Pass-through for formats it accepts."""
import io

MODEL_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_BYTES = 4_500_000
MAX_EDGE = 1568


class UnsupportedImage(Exception):
    pass


def normalize_image(data: bytes, media_type: str) -> tuple[bytes, str]:
    media_type = (media_type or "").split(";")[0].strip().lower()
    if media_type in MODEL_TYPES and len(data) <= MAX_BYTES:
        return data, media_type
    if media_type == "application/pdf":
        raise UnsupportedImage("PDF scans must be rendered to PNG upstream (not supported here)")
    try:
        from PIL import Image   # optional dependency: pip install pillow
    except ImportError:
        raise UnsupportedImage(f"cannot convert {media_type} ({len(data)} bytes): install pillow")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise UnsupportedImage(f"unreadable image: {e}")
    img = img.convert("L") if img.mode in ("1", "P", "LA") else img.convert("RGB") if img.mode not in ("L", "RGB") else img
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    if out.tell() > MAX_BYTES:
        raise UnsupportedImage("image too large after conversion")
    return out.getvalue(), "image/png"
