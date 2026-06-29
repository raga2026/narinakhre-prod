#Bulk Uploa to Superbase
import os
import io   
import sqlite3
import requests
from PIL import Image

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
# These are read from your .env file automatically.
# If not found, edit the values below directly.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_env():
    env_path = os.path.join(BASE_DIR, '.env')
    env = {}
    if not os.path.exists(env_path):
        return env
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env

env = load_env()

SUPABASE_URL    = env.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY    = env.get('SUPABASE_KEY', '')
BUCKET_NAME     = 'products'
DB_PATH         = env.get('DB_PATH', os.path.join(BASE_DIR, 'narinakhre.db'))
IMAGES_FOLDER   = os.path.join(BASE_DIR, 'static', 'assets', 'products')
WEBP_QUALITY    = 85   # 85% quality — excellent quality, ~60% smaller file size

# ─── VALIDATION ───────────────────────────────────────────────────────────────

def validate_config():
    errors = []
    if not SUPABASE_URL:
        errors.append('SUPABASE_URL is missing from your .env file')
    if not SUPABASE_KEY:
        errors.append('SUPABASE_KEY is missing from your .env file')
    if not os.path.exists(IMAGES_FOLDER):
        errors.append(f'Images folder not found: {IMAGES_FOLDER}')
    if not os.path.exists(DB_PATH):
        errors.append(f'Database not found: {DB_PATH}')
    if errors:
        print('\n=== CONFIGURATION ERRORS ===')
        for e in errors:
            print(f'  ERROR: {e}')
        print('\nPlease fix the above errors and run again.')
        exit(1)

# ─── IMAGE COMPRESSION ────────────────────────────────────────────────────────

def compress_to_webp(image_path, quality=85):
    """
    Open any image (jpg, png, webp, etc), convert to WebP at given quality.
    Returns bytes ready to upload.
    """
    with Image.open(image_path) as img:
        # Convert RGBA or palette images to RGB for WebP compatibility
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGBA')
        else:
            img = img.convert('RGB')

        buffer = io.BytesIO()
        img.save(buffer, format='WEBP', quality=quality, method=6)
        buffer.seek(0)
        original_kb = os.path.getsize(image_path) / 1024
        compressed_kb = len(buffer.getvalue()) / 1024
        print(f'    Compressed: {original_kb:.0f}KB → {compressed_kb:.0f}KB WebP ({quality}% quality)')
        buffer.seek(0)
        return buffer.read()

# ─── SUPABASE UPLOAD ──────────────────────────────────────────────────────────

def upload_to_supabase(image_bytes, filename):
    """
    Upload image bytes to Supabase storage bucket.
    Returns the public URL if successful, None otherwise.
    """
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{filename}"
    headers = {
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'apikey': SUPABASE_KEY,
        'Content-Type': 'image/webp',
        'x-upsert': 'true',
    }
    try:
        response = requests.put(
            upload_url,
            headers=headers,
            data=image_bytes,
            timeout=60
        )
        if response.status_code == 200:
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{filename}"
            return public_url
        else:
            print(f'    UPLOAD FAILED: HTTP {response.status_code} — {response.text[:200]}')
            return None
    except Exception as e:
        print(f'    UPLOAD ERROR: {e}')
        return None

# ─── DATABASE UPDATE ──────────────────────────────────────────────────────────

def update_db_image_field(sku, url):
    """
    Update the image_field column for the given SKU in narinakhre.db.
    Only updates if the product exists in the database.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('UPDATE products SET image_field = ? WHERE sku = ?', (url, sku))
        updated = cur.rowcount
        conn.commit()
        conn.close()
        return updated > 0
    except Exception as e:
        print(f'    DB UPDATE ERROR for {sku}: {e}')
        return False

# ─── MAIN SCRIPT ──────────────────────────────────────────────────────────────

def main():
    print('\n' + '=' * 60)
    print('  Nari Nakhre — Bulk Image Upload to Supabase')
    print('=' * 60)

    validate_config()

    # Collect all image files
    supported_ext = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff')
    all_files = [
        f for f in os.listdir(IMAGES_FOLDER)
        if f.lower().endswith(supported_ext)
        and not f.startswith('default')
    ]

    if not all_files:
        print(f'\nNo image files found in: {IMAGES_FOLDER}')
        print('Please check the folder path and try again.')
        return

    print(f'\nFound {len(all_files)} image files to process.')
    print(f'Uploading to Supabase bucket: {BUCKET_NAME}')
    print(f'WebP quality: {WEBP_QUALITY}% (excellent quality, smaller file size)\n')

    # Track results
    success_count = 0
    fail_count = 0
    db_update_count = 0
    sku_first_image = {}  # Track first image URL per SKU

    for idx, filename in enumerate(sorted(all_files), 1):
        filepath = os.path.join(IMAGES_FOLDER, filename)
        name_without_ext = os.path.splitext(filename)[0]

        print(f'[{idx}/{len(all_files)}] Processing: {filename}')

        # Parse SKU and image number from filename
        # Expected format: SKU_1, SKU_2 etc
        parts = name_without_ext.rsplit('_', 1)
        if len(parts) == 2:
            sku = parts[0]
            img_num = parts[1]
        else:
            sku = name_without_ext
            img_num = '1'

        # Compress to WebP
        try:
            image_bytes = compress_to_webp(filepath, quality=WEBP_QUALITY)
        except Exception as e:
            print(f'    COMPRESSION ERROR: {e}')
            fail_count += 1
            continue

        # Upload to Supabase with WebP filename
        webp_filename = f"{name_without_ext}.webp"
        public_url = upload_to_supabase(image_bytes, webp_filename)

        if public_url:
            print(f'    Uploaded: {public_url}')
            success_count += 1

            # Track first image per SKU for database update
            if sku not in sku_first_image:
                sku_first_image[sku] = public_url
        else:
            fail_count += 1

        print()

    # Update database with first image URL for each SKU
    print('-' * 60)
    print('Updating database with image URLs...\n')
    for sku, url in sku_first_image.items():
        updated = update_db_image_field(sku, url)
        if updated:
            print(f'  DB updated: {sku}')
            db_update_count += 1
        else:
            print(f'  DB SKIP (SKU not in database yet): {sku}')

    # Final report
    print('\n' + '=' * 60)
    print('  UPLOAD COMPLETE — Summary')
    print('=' * 60)
    print(f'  Total images processed : {len(all_files)}')
    print(f'  Successfully uploaded  : {success_count}')
    print(f'  Failed uploads         : {fail_count}')
    print(f'  Database records updated: {db_update_count}')
    print(f'  Unique SKUs processed  : {len(sku_first_image)}')
    print('=' * 60)

    if fail_count > 0:
        print('\nSome uploads failed. Check the errors above and run again.')
        print('Successfully uploaded images will be skipped on re-run (x-upsert).')
    else:
        print('\nAll images uploaded successfully.')
        print('You can now upload your products Excel via the admin panel.')
        print('Images will appear automatically once products are in the database.')


if __name__ == '__main__':
    main()
