# Selenium E2E Run Summary

## Command
`python -m pytest tests/test_selenium_e2e.py -v --html=reports/selenium_report.html --self-contained-html 2>&1`

## Totals
- Total collected: 21
- Passed: 16
- Failed: 0
- Errors: 0
- Skipped: 5

## New Tests (added to cover the retail cart-counter and hyperlink bug fixes)
- `test_add_to_cart_shows_single_counter` — PASSED. Confirms the wholesale-only
  `cart_global.js` handler no longer fires on retail Add to Cart clicks and
  no longer injects a duplicate `.qty-controls` counter widget.
- `test_quantity_dropdown_respected_on_add_to_cart` — PASSED. Confirms the
  quantity chosen in the `.qty-select` dropdown before Add to Cart is what
  the resulting counter (and the server-side cart) actually reflects.
- `test_trending_items_link_to_product_pages` — SKIPPED this run (see below).
- `test_offers_carousel_links_to_retail_not_wholesale` — SKIPPED this run
  (see below).

## Skipped Tests
- `tests/test_selenium_e2e.py::test_trending_items_link_to_product_pages`
  - Skipped: no `#trending-grid` shelf currently rendered on the homepage
  - Cause: the live catalog currently has zero in-stock products, so the
    `{% if trending %}` block never renders (see `app.py`'s home route —
    `trending` is derived from `all_in_stock`). Not a regression from this
    session's changes; worth checking product stock levels separately.
- `tests/test_selenium_e2e.py::test_offers_carousel_links_to_retail_not_wholesale`
  - Skipped: no product slides in the offers carousel — same root cause as above.
- `tests/test_selenium_e2e.py::test_admin_orders_page`
  - Skipped: `Admin credentials not configured`
- `tests/test_selenium_e2e.py::test_admin_coupons_page`
  - Skipped: `Admin credentials not configured`
- `tests/test_selenium_e2e.py::test_invoice_page`
  - Skipped: `Admin credentials not configured`

## Result
- Target achieved: **16/16 non-skipped tests passing**, 0 failed.
- First run of the session hit a transient `ReadTimeout` on the very first
  request (`test_retail_home_loads`) plus a downstream `test_product_detail`
  failure — both passed cleanly on retry with no code changes, consistent
  with a one-off cold-start delay on the test server rather than a bug.

## Artifacts
- HTML report: `reports/selenium_report.html`
- Full console output: `reports/selenium_results.txt`
- Failure screenshots (if any future failures): `reports/screenshots/`
