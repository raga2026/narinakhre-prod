# Claude Code Prompts — NariNakhre Invoicing & Catalogue Feature

Context for Claude Code (paste this once at the start of the session, or keep it in `CLAUDE_CODE_CONTEXT.md`):

> This is the NariNakhre repo (`raga2026/narinakhre-prod`, branch `main`). Single Flask `app.py` serves both retail (narinakhre.com) and wholesale (wholesale.narinakhre.com). Database is Supabase PostgreSQL accessed via a custom `execute_sql` RPC wrapper using `?` placeholders and a custom `SupabaseCursor` class. Image storage is a Supabase bucket named `products`, WebP format, naming pattern `SKU_imageNumber.webp`. Hosted on Render (`narinakhre-test` / `narinakhre-production`). Payments: Razorpay + COD. Shipping: Delhivery. Email: Zeptomail SMTP.

Run these prompts one at a time, in order. Review and test each before moving to the next — don't chain them blindly in one session.

---

## Prompt 1 — Database schema for export orders & invoicing

```
Add a new set of Supabase tables for handling export order tracking and invoicing on NariNakhre, alongside the existing orders schema (don't modify existing order tables — this is additive).

Tables needed:

1. export_orders
   - id, order_id (FK to existing orders table if applicable, else nullable for manual export orders)
   - buyer_name, buyer_email, buyer_address, buyer_country
   - currency (default 'USD'), invoice_value_foreign, exchange_rate, invoice_value_inr
   - status: enum ('design_shared', 'advance_pending', 'advance_received', 'invoiced',
     'shipped', 'balance_pending', 'completed')
   - advance_percent (default 50), advance_received_at, balance_received_at
   - firc_reference, ad_code_reference
   - lut_reference (for GST zero-rated export)
   - created_at, updated_at

2. export_invoices
   - id, export_order_id (FK)
   - invoice_type: enum ('proforma', 'commercial')
   - invoice_number (auto-generated, sequential, e.g. NN/EXP/2026/001)
   - invoice_date, gst_treatment ('zero_rated_lut')
   - line_items (jsonb: product name, model/SKU, quantity, unit_price, total)
   - subtotal, total (no GST since zero-rated export)
   - pdf_url (Supabase storage path once generated)
   - created_at

3. export_shipments
   - id, export_order_id (FK)
   - courier, awb_number, shipping_bill_number
   - shipped_at, estimated_delivery, delivered_at
   - tracking_url

Write the SQL migration (ALTER/CREATE TABLE statements matching the existing project's Supabase conventions — check how existing tables like order_shipping are structured first and follow the same style). Then create the Python model/data access functions in app.py (or a new export_orders.py module if that fits the existing code organization better — check how the codebase is currently split before deciding).

Do not build any UI yet — this prompt is schema and data-access layer only.
```

---

## Prompt 2 — Admin panel: order status dashboard

```
Build an admin panel page at /admin/export-orders that lists all export_orders with their current status (using the status enum from the export_orders table), buyer name, invoice value, and a quick-view of what stage they're at (design shared → advance pending → advance received → invoiced → shipped → balance pending → completed).

Requirements:
- Table view with filters by status
- Click into an order to see a detail page showing: buyer info, full status timeline, any invoices generated for it, and shipment info if available
- On the detail page, allow the admin to manually update: status, advance_received_at, firc_reference, ad_code_reference
- Follow the existing admin panel's visual style and auth pattern (check how other /admin/* routes handle the existing 2FA/session auth and reuse it — don't build a separate auth path)
- Use the existing admin panel's CSS/layout conventions rather than introducing a new framework

This is the "single view of order status" dashboard — it should give a clear at-a-glance picture of where every export order stands.
```

---

## Prompt 3 — Invoice generation (proforma & commercial)

```
Add invoice generation to the export order detail page (/admin/export-orders/<id>).

Two buttons: "Generate Proforma Invoice" and "Generate Commercial Invoice". Each should:
1. Open a form pre-filled with buyer details from export_orders, letting the admin add/edit line items (product name, model/SKU, quantity, unit price in USD)
2. On submit, calculate subtotal/total, save a row to export_invoices with an auto-incremented invoice_number in the format NN/EXP/2026/001
3. Generate a PDF of the invoice using [use whatever PDF library is already a dependency in the project, or reportlab/weasyprint if none exists] with:
   - Company letterhead (Mohini Cosmetics style — logo top, address, GSTIN, contact)
   - Buyer details, invoice number, date
   - Line items table
   - For commercial invoices: mark "GST: Zero-rated export under LUT — [lut_reference]" clearly, no GST charged
   - Bank details (account number, IFSC, SWIFT if available) for USD wire transfer
4. Upload the generated PDF to Supabase storage and save the path to export_invoices.pdf_url
5. Show a "Download Invoice" link on the order detail page once generated

Keep the invoice HTML template as a separate file (e.g. templates/export_invoice.html) so the layout is easy to adjust later, and render it to PDF from that template rather than building the PDF programmatically field by field.
```

---

## Prompt 4 — Catalogue schema & admin creation flow

```
Build a new "catalogues" feature for NariNakhre, separate from the main product listing. A catalogue is a curated, shareable set of products the admin puts together (e.g. to send to a specific wholesale buyer), each with its own public link.

Schema:

1. catalogues
   - id, slug (unique, used in the public URL, auto-generated from title or custom)
   - title, description (optional)
   - is_active (boolean, so admin can disable a link without deleting it)
   - created_at

2. catalogue_products
   - id, catalogue_id (FK)
   - category (matching existing product category taxonomy — check the existing products table for how categories are structured and reuse the same values/enum)
   - model_number_or_name
   - display_order (for controlling sort order within the catalogue)
   - created_at

3. catalogue_product_images
   - id, catalogue_product_id (FK)
   - image_url (Supabase storage path)
   - display_order
   - created_at

Build the admin UI at /admin/catalogues:
- List existing catalogues with their public link and active/inactive toggle
- "Create New Catalogue" form: title, description, then an interface to add products one at a time — each with category dropdown (reuse existing category list), model number/name field, and a multi-image upload
- Ability to reorder products and reorder images within a product (simple drag-and-drop or up/down buttons, whichever is faster to build well given the existing frontend stack)
- Ability to edit/remove products from a catalogue after creation

Check the existing product-add admin form for image upload handling conventions before building this — reuse that pattern rather than inventing a new one.
```

---

## Prompt 5 — Image compression pipeline for catalogue uploads

```
When images are uploaded for a catalogue product (from Prompt 4's upload form), compress them before storing in Supabase, following the existing product image convention (WebP format).

Requirements:
- Convert to WebP on upload (server-side, using Pillow or the library already used elsewhere in the codebase for the main product images — check how the existing product upload flow does this and match it)
- Compress using WebP quality setting in the 80–85 range, which keeps visible quality high while meaningfully reducing file size — do not just resize dimensions down as the primary compression method, use WebP's quality parameter
- Cap max dimension at something reasonable for web display (e.g. 1600px on the longest side) so unnecessarily huge source photos don't bloat storage, but don't upscale smaller images
- Naming pattern consistent with the existing convention: use the catalogue product's model_number_or_name plus an image index, sanitized for use as a filename
- Show a compression result (before/after file size) in the admin upload UI so the admin can see it's working

Test this against a few real product photos (JPEG, varying sizes) and confirm visual quality holds up at normal viewing size before considering this done.
```

---



## Notes for you (not for Claude Code)

- Run Prompt 1 and 2 in one sitting if you want the admin order dashboard live first — that's the piece you need soonest given the Ashika order.
- Prompts 4–6 (catalogue) are independent of 1–3 (invoicing) and can be done in either order or by different sessions.
- After each prompt, actually click through the feature yourself on `narinakhre-test` before promoting to `narinakhre-production` — don't just review the diff.
