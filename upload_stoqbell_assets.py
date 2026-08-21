"""
One-time (re-run only when a logo file itself changes) upload of the
StoqBell logo/icon files from stoqbell/static/assets/ to Supabase Storage,
so both emails (stoqbell/utils/suggestion_email.py, which needs an
absolute, publicly-fetchable URL -- mail clients can't load this app's own
same-origin /stocks/static/... path) and the web pages that reference the
Supabase-hosted copy directly (home/login nav bars -- see
stoqbell/templates/admin/stocks_home.html/stocks_login.html) have a stable
URL. Most web pages still reference the local static file instead (still
correct and simpler when it works); the Supabase copy is there specifically
for the couple of pages that were switched over.

Deliberately NOT run automatically at app startup: it's a handful of extra
network calls on every single boot for content that essentially never
changes, which only adds startup latency and a dependency on Supabase being
reachable before the app can finish starting -- exactly the same reasoning
as the Razorpay Plan objects being created once via a setup script rather
than on every app start (see app.py's RAZORPAY_STOCKS_PLAN_ID comment).

Run manually whenever a logo file is added or changed:

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
_CONTENT_TYPES = {'.png': 'image/png', '.svg': 'image/svg+xml'}
# Must match the paths suggestion_email.py's _stoqbell_logo_header_html()
# and the templates' hardcoded Supabase URLs construct from.
FILENAMES = ('stoqbell-logo.png', 'stoqbell-icon.png', 'stoqbell-icon-dark.png')


def run():
    for filename in FILENAMES:
        file_path = os.path.join(ASSETS_DIR, filename)
        if not os.path.exists(file_path):
            print(f'SKIP {filename}: not found at {file_path}')
            continue
        content_type = _CONTENT_TYPES.get(os.path.splitext(filename)[1], 'application/octet-stream')
        with open(file_path, 'rb') as f:
            data = f.read()
        url = upload_bytes_to_supabase(data, f'{STORAGE_PREFIX}/{filename}', content_type)
        if url:
            print(f'OK   {filename} -> {url}')
        else:
            print(f'FAIL {filename}: upload_bytes_to_supabase returned None -- '
                  f'check SUPABASE_URL/SUPABASE_KEY and network connectivity.')


if __name__ == '__main__':
    run()
