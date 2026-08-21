"""
One-time (re-run only when the logo itself changes) upload of the StoqBell
logo/icon PNGs from stoqbell/static/assets/ to Supabase Storage, so
stoqbell/utils/suggestion_email.py has a stable public URL to embed in
emails -- mail clients need an absolute, publicly-fetchable image URL, not
this app's own same-origin /stocks/static/... path. Web pages don't need
this at all; they reference the local static files directly (see the nav
bar in stoqbell/templates/admin/*.html).

Deliberately NOT run automatically at app startup: it's two extra network
calls on every single boot for content that essentially never changes,
which only adds startup latency and a dependency on Supabase being
reachable before the app can finish starting -- exactly the same reasoning
as the Razorpay Plan objects being created once via a setup script rather
than on every app start (see app.py's RAZORPAY_STOCKS_PLAN_ID comment).

Run manually whenever the logo files are added or changed:

    python upload_stoqbell_assets.py

Requires SUPABASE_URL and SUPABASE_KEY in the environment (.env is loaded
automatically, same as the rest of this project).
"""
import os

from dotenv import load_dotenv

from supabase_storage import upload_bytes_to_supabase

load_dotenv()

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stoqbell', 'static', 'assets')
STORAGE_PREFIX = 'stoqbell'
# Must match the paths suggestion_email.py's _stoqbell_logo_header_html()
# constructs the public URL from.
FILENAMES = ('stoqbell-logo.png', 'stoqbell-icon.png')


def run():
    for filename in FILENAMES:
        file_path = os.path.join(ASSETS_DIR, filename)
        if not os.path.exists(file_path):
            print(f'SKIP {filename}: not found at {file_path}')
            continue
        with open(file_path, 'rb') as f:
            data = f.read()
        url = upload_bytes_to_supabase(data, f'{STORAGE_PREFIX}/{filename}', 'image/png')
        if url:
            print(f'OK   {filename} -> {url}')
        else:
            print(f'FAIL {filename}: upload_bytes_to_supabase returned None -- '
                  f'check SUPABASE_URL/SUPABASE_KEY and network connectivity.')


if __name__ == '__main__':
    run()
