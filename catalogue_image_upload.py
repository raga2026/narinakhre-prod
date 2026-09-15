"""Compresses and uploads catalogue product images to Supabase Storage.

Mirrors app.py's upload_image_to_supabase (WebP conversion, quality=85)
but adds the two things catalogue uploads specifically need that the main
product-image path doesn't: a max-dimension cap (so oversized source
photos don't bloat storage) and before/after byte counts so the admin
upload UI can show the compression actually happened. Kept separate from
that function rather than changing it, since the main product-image
pipeline isn't in scope here.
"""
import io
import re

from PIL import Image as PILImage

from supabase_storage import upload_bytes_to_supabase

MAX_DIMENSION = 1600
WEBP_QUALITY = 85


def sanitize_filename_part(text):
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', (text or '').strip()).strip('-')
    return slug.lower() or 'image'


def compress_image_to_webp(raw_bytes):
    """Returns (webp_bytes, original_size, compressed_size). Shrinks the
    image (never enlarges it) so its longest side is at most MAX_DIMENSION,
    then encodes at WEBP_QUALITY -- quality is the primary compression
    lever per the brief; the dimension cap only stops absurdly large source
    photos from being stored at full size."""
    original_size = len(raw_bytes)
    img = PILImage.open(io.BytesIO(raw_bytes))
    if img.mode in ('RGBA', 'P', 'LA'):
        img = img.convert('RGBA')
    else:
        img = img.convert('RGB')

    if max(img.size) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), PILImage.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format='WEBP', quality=WEBP_QUALITY, method=6)
    webp_bytes = buf.getvalue()
    return webp_bytes, original_size, len(webp_bytes)


def compress_and_upload_catalogue_image(file_storage, catalogue_id, model_number_or_name, index):
    """Returns {'url', 'original_size', 'compressed_size'}, or None if
    conversion/upload failed -- callers should skip that one image rather
    than abort the whole product-add, same tolerant style as the rest of
    this codebase's optional-asset uploads."""
    try:
        if hasattr(file_storage, 'stream') and hasattr(file_storage.stream, 'seek'):
            file_storage.stream.seek(0)
        raw_bytes = file_storage.read()
        webp_bytes, original_size, compressed_size = compress_image_to_webp(raw_bytes)
    except Exception:
        return None

    name_part = sanitize_filename_part(model_number_or_name)
    path = f'catalogues/{catalogue_id}/{name_part}_{index}.webp'
    url = upload_bytes_to_supabase(webp_bytes, path, 'image/webp')
    if not url:
        return None
    return {'url': url, 'original_size': original_size, 'compressed_size': compressed_size}
