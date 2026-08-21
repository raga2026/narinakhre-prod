# NariNakhre — Claude Code Knowledge Transfer

Last updated: 21 August 2026. This file is a starting-point summary for Claude Code, not the full source of truth — always check current app.py and recent git log too, since this file lags behind day-to-day work.

---

## 1. About the developer

Raghavendran G. Blind, uses JAWS screen reader. Banking professional at Union Bank of India, building this as a side project.

Working via Claude Code CLI now, which reads and edits files and runs commands directly — you don't need to type or paste commands yourself unless you want to.

---

## 2. This project

Nari Nakhre / Mohini Cosmetics — e-commerce cosmetics and wholesale apparel, retail and wholesale on one Flask codebase. FanDeck / Arena is a separate, unrelated project.

Domain: narinakhre.com, registered at GoDaddy.

Nari Nakhre Stocks (a separate stock-recommendation subscription product, branded **StoqBell**) lives in the same repo/process but on its own domain, www.stoqbell.com, and its own code package `stoqbell/` (see section 10). A GET to any `/stocks/*` path on narinakhre.com/www.narinakhre.com/wholesale.narinakhre.com 301-redirects to the same path on www.stoqbell.com (see app.py's `redirect_stocks_to_own_domain`).

Local path: C:\Users\ragha\Documents\NariNakhre\

---

## 3. Tech stack

Backend: Python 3, Flask, Gunicorn.
Database: Supabase Postgres, accessed through a REST RPC function called execute_sql, not a direct Postgres driver.
Image storage: Supabase Storage, bucket named products.
CSS: Tailwind CSS 2.2.19 via CDN.
Hosting: Render, Singapore region.
Payment: Razorpay.
Shipping: Delhivery.
Email: Zeptomail, sent through its HTTP API, not SMTP. The old SMTP based sender caused hangs and was replaced.
Spam protection: Google reCAPTCHA v3 on contact forms.
SQLAlchemy has been fully removed from the app; do not reintroduce it.

---

## 4. Render deployment

Two services from one repo, both currently live:

narinakhre-test, Free plan, domains test-retail.narinakhre.com and test-wholesale.narinakhre.com.
narinakhre-production, Starter plan, domains narinakhre.com, www.narinakhre.com, wholesale.narinakhre.com, and www.stoqbell.com (Nari Nakhre Stocks' own branded domain — same service/process, see section 10).

Both use buildCommand "pip install -r requirements.txt" and startCommand "gunicorn app:app". Render auto-deploys on every push to main. Wait a couple of minutes after a push before checking the live site.

---

## 5. Supabase configuration

Project URL: https://eopqwvssznmxfxrfzqbx.supabase.co
Storage bucket: products, public, stores WebP images.
Database access goes through a Postgres function called execute_sql, called via Supabase RPC. The SupabaseDB and SupabaseCursor wrapper classes (now in db.py, not app.py — see section 10) convert normal ? style SQL placeholders into calls to that RPC. Both the storefront and Stocks import get_db()/get_supabase() from this same db.py.
A background keep-alive thread pings the database periodically so the free Supabase project doesn't pause from inactivity.
Nari Nakhre Stocks uses the same Supabase project, its own tables, own admin login (see section 10) — not a separate database.

---

## 6. Environment variables

Set both locally in .env and in the Render dashboard for both services. Names only, no values here:

SUPABASE_URL, SUPABASE_KEY, FLASK_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_TOTP_SECRET, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, SHIPPING_PROVIDER, WAREHOUSE_PIN, DELHIVERY_API_KEY, DELHIVERY_API_TOKEN, DELHIVERY_CLIENT_NAME, DELHIVERY_PICKUP_LOCATION, SHIPROCKET_PICKUP_LOCATION, ZEPTOMAIL_API_KEY, ZEPTOMAIL_API_URL (optional, defaults to the .in region endpoint), SMTP_FROM, SMTP_FROM_ORDERS, RECAPTCHA_SITE_KEY, RECAPTCHA_SECRET_KEY, DB_PATH, CREDENTIAL_ENCRYPTION_KEY.

Never put any of these in render.yaml or commit them to git.

CREDENTIAL_ENCRYPTION_KEY encrypts courier credentials (Shiprocket, Delhivery) stored in the delivery_partner_credentials table via utils/credential_crypto.py. Generate one locally with:

python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Set that exact same value in .env for local work and in the Render dashboard for both narinakhre-test and narinakhre-production — local, test, and production all share one Supabase project, so all three must use the identical key or credentials saved in one place won't decrypt in another. If this key is ever lost or changed, every previously saved courier credential becomes undecryptable and must be re-entered through the admin panel.

SHIPROCKET_PICKUP_LOCATION is the pickup address nickname registered in the Shiprocket dashboard (Settings -> Pickup Addresses) -- their order-creation API requires this exact string, not a raw address. Currently set to "work", confirmed live via their /settings/company/pickup API on 2026-08-10 -- that's the Jabalpur warehouse address, auto-named "work" by Shiprocket when it was added (no custom nickname was ever typed). Set the same value in Render for both services since local/test/production all use the one Shiprocket account. If a new pickup address is ever registered instead, re-check its nickname the same way rather than guessing.

---

## 7. Database tables

products, quotes, categories, users, order_shipping, and a coupons table used by the admin coupon manager. Check schema.sql or query Supabase directly for exact columns rather than trusting a hardcoded list here, since columns have changed over time (GST, weight/dimensions, discount fields etc. were added incrementally).

---

## 8. What's implemented

Retail and wholesale storefronts, product search, category browsing.
Full checkout flow: Razorpay prepaid and COD, GST split, coupons, shipping serviceability and rates via Delhivery.
Order confirmation emails and live order tracking by waybill, retail only (wholesale is quote-based, no shipments).
Admin panel: login with TOTP 2FA, dashboard, bulk Excel product sync, per-SKU image manager, add/edit/delete products, order processing console with accept/dispatch/cancel and shipping label generation, coupon manager, quotes inbox, Excel exports for users/quotes/products.
Contact forms (retail and wholesale) with honeypot and reCAPTCHA v3 spam protection.
Admin keep-alive thread for Supabase.

Recent work (see git log for detail) has focused on fixing checkout edge cases, COD order handling, email delivery reliability (moved from SMTP to Zeptomail's HTTP API to stop hangs), contact form spam, and a wholesale product detail 500 error.

---

## 9. Working conventions

Never commit .env or credentials.
Never put secrets in render.yaml.
SQL in the app uses ? placeholders; SupabaseCursor converts them for the RPC call.
Product images always come from Supabase URLs, not the static folder.
Push to git push origin main; Render auto-deploys.
The same codebase serves both retail and wholesale; site type is detected from the request hostname.

---

## 10. Nari Nakhre Stocks (StoqBell) — package structure

Restructured (21 August 2026) out of the storefront's monolithic app.py into its own self-contained package, so it can eventually be split onto a separate server/repo without touching the storefront:

- `stoqbell/routes.py` — every `/stocks/*` route, on its own Flask Blueprint (`stocks_bp`, registered in app.py via `app.register_blueprint(stocks_bp)`). URL paths are unchanged; the only externally-visible effect is that endpoint names for `url_for()` gained a `stocks.` prefix (e.g. `stocks_home` → `stocks.stocks_home`).
- `stoqbell/utils/` — the ~32 stocks-only modules formerly in the root `utils/` (auto_trader, suggestion_engine, stock_auth, kite_*, etc.), moved via `git mv` so history is intact.
- `stoqbell/templates/admin/` — the 21 `stocks_*.html` templates (still under an `admin/` subfolder so `render_template('admin/stocks_x.html', ...)` call sites didn't need touching). Registered via the Blueprint's `template_folder`.
- `stoqbell/static/assets/` — the StoqBell logo/icon files (PNG + SVG), added 2026-08-21. Referenced directly (`url_for('stocks.static', filename='assets/...')`) in the web templates' nav bars. Don't put Stocks assets under the storefront's `static/assets/` — that's storefront branding only.

Three small root-level modules exist specifically because both the storefront and Stocks need them, so they can't live under either side without creating a circular import — kept generic/infra-only, no business logic:
- `db.py` — SupabaseDB/SupabaseCursor/get_db()/get_supabase() (extracted from app.py).
- `razorpay_shared.py` — get_razorpay_client() (same Razorpay account/keys, storefront Orders API + Stocks Subscriptions API).
- `recaptcha_shared.py` — verify_recaptcha()/RECAPTCHA_SITE_KEY (storefront contact forms + Stocks signup/login forms).
- `supabase_storage.py` — upload_bytes_to_supabase()/public_url() (generic Supabase Storage REST PUT, same 'products' public bucket the storefront's product photos use, under a `stoqbell/` path prefix for Stocks' own files — the logo and per-suggestion chart images, see below).

All four are lazily initialized (read env vars / build clients on first call, not at module import time) — app.py's own `.env` loader (`load_env_file`) runs partway through app.py, after its own top-of-file imports, so anything read eagerly at import time would see unset env vars. `utils/credential_crypto.py` (courier credentials + Stocks' Kite credentials) and `auth_providers/` (one GoogleAuthProvider instance, used by both the storefront's and Stocks' Google logins) are also genuinely shared, but needed no extraction — they already lived outside app.py with no circular-import issue.

If Stocks is ever actually split onto a separate server/repo: copy `stoqbell/` plus the four shared modules above plus `utils/credential_crypto.py` and `auth_providers/`, give it a small standalone Flask entrypoint that just does `app.register_blueprint(stocks_bp)`, and point its own Render service at the new repo. Same Supabase project either way (see section 5) — no DB migration needed.

**Branding/emails (2026-08-21)**: Stocks' customer-facing name is "StoqBell" (product/UI text only — internal identifiers like `nns_score`/`compute_nns_score`/the DB column stay as NNS, and the storefront itself is still "Nari Nakhre"). Stocks emails go out via `send_zeptomail_stocks_email` (utils/stock_alerting.py), reusing the storefront's own "support" Zeptomail Mail Agent TOKEN (`SMTP_SUPPORT_EMAIL_PASSWORD`) but a Stocks-specific sender, `STOCKS_SMTP_FROM_EMAIL` (defaults to `support-noreply@stoqbell.com`) — the stoqbell.com domain was added to that same Mail Agent, so one token now sends as either address depending which FROM a given send uses; no second Mail Agent needed. The logo shown in emails is the Supabase-hosted copy of `stoqbell/static/assets/stoqbell-logo.png` (see `upload_stoqbell_assets.py` — a one-time script, re-run only if the logo file itself changes; deliberately NOT run on every app boot, to avoid adding startup-time network calls). The per-suggestion projection chart in daily/starters/large-cap-bonus emails (`stoqbell/utils/suggestion_chart.py`) is also uploaded to Supabase (content-hashed path) and referenced by URL rather than embedded as a base64 `data:` URI — the old data-URI approach was silently stripped by Outlook desktop and some corporate mail gateways.
