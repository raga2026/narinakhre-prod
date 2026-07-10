# NariNakhre — Claude Code Knowledge Transfer

Last updated: 10 July 2026. This file is a starting-point summary for Claude Code, not the full source of truth — always check current app.py and recent git log too, since this file lags behind day-to-day work.

---

## 1. About the developer

Raghavendran G. Blind, uses JAWS screen reader. Banking professional at Union Bank of India, building this as a side project.

Working via Claude Code CLI now, which reads and edits files and runs commands directly — you don't need to type or paste commands yourself unless you want to.

---

## 2. This project

Nari Nakhre / Mohini Cosmetics — e-commerce cosmetics and wholesale apparel, retail and wholesale on one Flask codebase. FanDeck / Arena is a separate, unrelated project.

Domain: narinakhre.com, registered at GoDaddy.

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
narinakhre-production, Starter plan, domains narinakhre.com, www.narinakhre.com, wholesale.narinakhre.com.

Both use buildCommand "pip install -r requirements.txt" and startCommand "gunicorn app:app". Render auto-deploys on every push to main. Wait a couple of minutes after a push before checking the live site.

---

## 5. Supabase configuration

Project URL: https://eopqwvssznmxfxrfzqbx.supabase.co
Storage bucket: products, public, stores WebP images.
Database access goes through a Postgres function called execute_sql, called via Supabase RPC. The app's SupabaseDB and SupabaseCursor wrapper classes convert normal ? style SQL placeholders into calls to that RPC.
A background keep-alive thread pings the database periodically so the free Supabase project doesn't pause from inactivity.

---

## 6. Environment variables

Set both locally in .env and in the Render dashboard for both services. Names only, no values here:

SUPABASE_URL, SUPABASE_KEY, FLASK_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_TOTP_SECRET, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, SHIPPING_PROVIDER, WAREHOUSE_PIN, DELHIVERY_API_KEY, DELHIVERY_API_TOKEN, DELHIVERY_CLIENT_NAME, DELHIVERY_PICKUP_LOCATION, ZEPTOMAIL_API_KEY, ZEPTOMAIL_API_URL (optional, defaults to the .in region endpoint), SMTP_FROM, SMTP_FROM_ORDERS, RECAPTCHA_SITE_KEY, RECAPTCHA_SECRET_KEY, DB_PATH.

Never put any of these in render.yaml or commit them to git.

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
