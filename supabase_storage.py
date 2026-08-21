"""Shared Supabase Storage upload helper -- used by the storefront's own
product-image upload (app.py's upload_image_to_supabase) and by Stocks
(stoqbell/) for the StoqBell logo/icon and per-suggestion chart images.
Generic HTTP PUT against Supabase Storage's REST API, no business logic,
so it lives at the repo root like db.py/razorpay_shared.py.

Reads SUPABASE_URL/SUPABASE_KEY from the environment on every call (not
cached at import time) for the same reason as db.py/razorpay_shared.py --
app.py's .env loader runs after its own top-of-file imports, so anything
read eagerly at import time would see them unset.
"""
import os

import requests


def upload_bytes_to_supabase(binary_payload, path, content_type, bucket=None):
    """Uploads binary_payload to Supabase Storage at `path` within `bucket`
    (defaults to the SUPABASE_BUCKET_NAME env var, same 'products' bucket
    the storefront's product photos already use -- public, confirmed
    working, no new bucket to create). `path` may include subfolders (e.g.
    'stoqbell/charts/<hash>.png'). Uses upsert, so re-uploading the same
    path is safe and just overwrites.

    Returns the public URL on success, or None if Supabase isn't
    configured or the upload fails -- callers should treat None the same
    as "no image", not raise, since a missing/broken image should never
    break an email send or a page render."""
    supabase_url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    supabase_key = os.environ.get('SUPABASE_KEY')
    bucket = bucket or os.environ.get('SUPABASE_BUCKET_NAME', 'products')
    if not supabase_url or not supabase_key:
        return None

    upload_url = f'{supabase_url}/storage/v1/object/{bucket}/{path}'
    headers = {
        'Authorization': f'Bearer {supabase_key}',
        'apikey': supabase_key,
        'Content-Type': content_type,
        'x-upsert': 'true',
    }
    try:
        response = requests.put(upload_url, headers=headers, data=binary_payload, timeout=30)
    except Exception:
        return None

    if response.status_code != 200:
        return None
    return f'{supabase_url}/storage/v1/object/public/{bucket}/{path}'


def public_url(path, bucket=None):
    """Constructs the public URL for a path that's already been uploaded
    (Supabase Storage public URLs are deterministic), without making an
    HTTP call -- use when you already know the object exists (e.g. the
    logo, uploaded once at startup) and just need the URL string."""
    supabase_url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    bucket = bucket or os.environ.get('SUPABASE_BUCKET_NAME', 'products')
    if not supabase_url:
        return None
    return f'{supabase_url}/storage/v1/object/public/{bucket}/{path}'
