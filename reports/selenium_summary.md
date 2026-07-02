# Selenium E2E Run Summary

## Command
`python -m pytest tests/test_selenium_e2e.py -v --html=reports/selenium_report.html --self-contained-html 2>&1`

## Totals
- Total collected: 17
- Passed: 14
- Failed: 0
- Errors: 0
- Skipped: 3

## Skipped Tests
- `tests/test_selenium_e2e.py::test_admin_orders_page`
  - Skipped: `Admin credentials not configured`
- `tests/test_selenium_e2e.py::test_admin_coupons_page`
  - Skipped: `Admin credentials not configured`
- `tests/test_selenium_e2e.py::test_invoice_page`
  - Skipped: `Admin credentials not configured`

## Result
- Target achieved: **14/17 tests passing**.
- All non-skipped tests passed.

## Artifacts
- HTML report: `reports/selenium_report.html`
- Full console output: `reports/selenium_results.txt`
- Failure screenshots (if any future failures): `reports/screenshots/`
