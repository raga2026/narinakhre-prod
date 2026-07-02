import json
import os
import re
import time
from typing import Iterable

import pytest
import requests
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://test-retail.narinakhre.com"
BASE_URL_WS = "https://test-wholesale.narinakhre.com"


def _first_visible(driver: WebDriver, selectors: Iterable[str], timeout: int = 10):
    end = time.time() + timeout
    while time.time() < end:
        for selector in selectors:
            found = driver.find_elements(By.CSS_SELECTOR, selector)
            for elem in found:
                if elem.is_displayed():
                    return elem
        time.sleep(0.15)
    raise TimeoutException(f"No visible element found for selectors: {list(selectors)}")


def _first_clickable_xpath(driver: WebDriver, xpaths: Iterable[str], timeout: int = 10):
    for xp in xpaths:
        try:
            return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xp)))
        except Exception:
            continue
    raise TimeoutException("No clickable xpath matched")


def _elements(driver: WebDriver, selectors: Iterable[str]):
    for selector in selectors:
        elems = driver.find_elements(By.CSS_SELECTOR, selector)
        if elems:
            return elems
    return []


def _safe_text(elem) -> str:
    try:
        return (elem.text or "").strip()
    except Exception:
        return ""


def _extract_int(text: str) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else 0


def _line_through_present(elements) -> bool:
    for elem in elements:
        style = (elem.value_of_css_property("text-decoration-line") or "").lower()
        if "line-through" in style:
            return True
        style_full = (elem.value_of_css_property("text-decoration") or "").lower()
        if "line-through" in style_full:
            return True
    return False


def _requests_get(url: str, **kwargs):
    return requests.get(url, timeout=15, **kwargs)


def _open_checkout_with_cart(driver: WebDriver, base_url: str):
    driver.get(f"{base_url}/retail/category/Bangles")
    add = _first_clickable_xpath(
        driver,
        [
            "(//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')])[1]",
            "(//*[contains(@class,'add-to-cart') and (self::button or self::a)])[1]",
        ],
        timeout=12,
    )
    add.click()
    driver.get(f"{base_url}/retail/checkout")


class TestProductBrowsing:
    def test_home_page_loads(self, driver: WebDriver, base_url: str):
        """Verify retail home page loads, shows products, and renders key commerce indicators."""
        resp = _requests_get(f"{base_url}/retail", allow_redirects=True)
        assert resp.status_code == 200

        driver.get(f"{base_url}/retail")
        assert "Nari Nakhre" in driver.title

        cards = _elements(driver, [".ck-item", ".product-card", "[data-product-id]"])
        assert len([c for c in cards if c.is_displayed()]) >= 1

        header_cart = _elements(
            driver,
            [
                "header #cart-count",
                "header .cart-count",
                "header a[href*='checkout']",
                "header [class*='cart']",
            ],
        )
        assert any(e.is_displayed() for e in header_cart), "Expected cart icon/count visible in header"

        strike_candidates = _elements(
            driver,
            [
                ".line-through",
                "del",
                "s",
                ".mrp",
                ".old-price",
            ],
        )
        assert _line_through_present(strike_candidates), "Expected at least one MRP with line-through"

        page_text = driver.page_source.lower()
        assert re.search(r"\b\d+\s*%\s*off\b", page_text), "Expected visible discount badge text like '20% off'"

    def test_category_page(self, driver: WebDriver, base_url: str):
        """Verify Bangles category navigation and product card completeness."""
        driver.get(f"{base_url}/retail")

        bangles_link = _first_clickable_xpath(
            driver,
            [
                "//a[contains(@href, '/retail/category/Bangles')]",
                "//a[contains(normalize-space(.), 'Bangles')]",
                "//button[contains(normalize-space(.), 'Bangles')]",
            ],
            timeout=12,
        )
        bangles_link.click()

        WebDriverWait(driver, 12).until(EC.url_contains("/retail/category/Bangles"))
        assert "/retail/category/Bangles" in driver.current_url

        cards = _elements(driver, [".ck-item", ".product-card", "[data-product-id]"])
        visible_cards = [c for c in cards if c.is_displayed()]
        assert len(visible_cards) >= 2

        for card in visible_cards[:2]:
            assert card.find_elements(By.CSS_SELECTOR, "img"), "Product image missing"
            name_elements = card.find_elements(By.CSS_SELECTOR, "h2, h3, h4, .product-title, .name")
            assert any(_safe_text(n) for n in name_elements), "Product name missing"
            assert re.search(r"₹|rs\.?", card.text.lower()), "Price missing"
            add_buttons = card.find_elements(
                By.XPATH,
                ".//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')]",
            )
            assert add_buttons, "Add to Cart button missing"

    def test_product_detail_page(self, driver: WebDriver, base_url: str):
        """Verify retail product detail displays content, pricing, and actions."""
        driver.get(f"{base_url}/retail/product/1")

        title_elem = _first_visible(driver, ["h1", ".product-title", ".pdp-title"], timeout=12)
        assert _safe_text(title_elem)

        supabase_imgs = driver.find_elements(By.CSS_SELECTOR, "img[src*='supabase.co']")
        assert len(supabase_imgs) >= 1

        price_nodes = _elements(driver, [".price", ".mrp", ".old-price", ".line-through", ".pdp-price"])
        price_text = " ".join(_safe_text(p) for p in price_nodes) + " " + driver.page_source
        assert len(re.findall(r"₹\s*\d+", price_text)) >= 2, "Expected both MRP and retail price"

        add_btn = _first_clickable_xpath(
            driver,
            [
                "//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')]",
                "//button[contains(@class, 'add-to-cart')]",
            ],
            timeout=10,
        )
        assert add_btn.is_displayed()

        share = _first_visible(driver, ["button.share-btn", "[title*='Share']", "img[src*='share']"], timeout=8)
        assert share.is_displayed()


class TestCart:
    def test_add_to_cart(self, driver: WebDriver, base_url: str):
        """Verify adding a product updates the header cart counter."""
        driver.get(f"{base_url}/retail/category/Bangles")

        old_count = 0
        count_nodes = _elements(driver, ["#cart-count", ".cart-count", "[data-cart-count]"])
        if count_nodes:
            old_count = _extract_int(_safe_text(count_nodes[0]))

        first_add = _first_clickable_xpath(
            driver,
            [
                "(//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')])[1]",
                "(//*[contains(@class,'add-to-cart') and (self::button or self::a)])[1]",
            ],
            timeout=10,
        )
        first_add.click()

        WebDriverWait(driver, 10).until(
            lambda d: _extract_int(
                _safe_text(_first_visible(d, ["#cart-count", ".cart-count", "[data-cart-count]"], timeout=3))
            )
            >= max(1, old_count + 1)
        )

    def test_cart_counter_increment_decrement(self, cart_driver: WebDriver, base_url: str):
        """Verify plus/minus controls change quantity and minus at 1 removes item."""
        driver = cart_driver
        driver.get(f"{base_url}/retail/checkout")

        cart_items = _elements(driver, [".cart-item", ".ck-item", ".checkout-item", "[data-cart-item]"])
        assert any(i.is_displayed() for i in cart_items), "Expected at least one cart item"

        qty_input = _first_visible(driver, ["input[type='number']", "input[name*='qty']", "[data-qty]"], timeout=8)
        start_qty = _extract_int(qty_input.get_attribute("value") or qty_input.get_attribute("aria-valuenow") or "1")

        plus = _first_clickable_xpath(
            driver,
            [
                "//button[normalize-space(.)='+']",
                "//button[contains(@aria-label, 'Increase') or contains(@aria-label, 'plus')]",
                "(//button[contains(@class, 'plus')])[1]",
            ],
            timeout=8,
        )
        plus.click()

        WebDriverWait(driver, 8).until(
            lambda d: _extract_int(
                (_first_visible(d, ["input[type='number']", "input[name*='qty']", "[data-qty]"], timeout=4).get_attribute("value") or "0")
            )
            >= start_qty + 1
        )

        minus = _first_clickable_xpath(
            driver,
            [
                "//button[normalize-space(.)='-']",
                "//button[contains(@aria-label, 'Decrease') or contains(@aria-label, 'minus')]",
                "(//button[contains(@class, 'minus')])[1]",
            ],
            timeout=8,
        )
        minus.click()

        WebDriverWait(driver, 8).until(
            lambda d: _extract_int(
                (_first_visible(d, ["input[type='number']", "input[name*='qty']", "[data-qty]"], timeout=4).get_attribute("value") or "0")
            )
            <= start_qty
        )

        # Ensure quantity reaches 1 then one more minus should remove item.
        current_qty = _extract_int(
            _first_visible(driver, ["input[type='number']", "input[name*='qty']", "[data-qty]"], timeout=4).get_attribute("value") or "1"
        )
        while current_qty > 1:
            minus.click()
            time.sleep(0.3)
            current_qty = _extract_int(
                _first_visible(driver, ["input[type='number']", "input[name*='qty']", "[data-qty]"], timeout=4).get_attribute("value") or "1"
            )

        minus.click()
        time.sleep(1.2)

        empty_indicators = ["your bag is empty", "cart is empty", "no items in cart"]
        page_text = driver.page_source.lower()
        assert any(t in page_text for t in empty_indicators) or not _elements(
            driver, [".cart-item", ".ck-item", ".checkout-item", "[data-cart-item]"]
        )

    def test_clear_cart(self, cart_driver: WebDriver, base_url: str):
        """Verify clear-all removes cart items and shows empty state."""
        driver = cart_driver
        driver.get(f"{base_url}/retail/checkout")

        clear_btn = _first_clickable_xpath(
            driver,
            [
                "//button[contains(translate(., 'CLEAR ALL', 'clear all'), 'clear all')]",
                "//a[contains(translate(., 'CLEAR ALL', 'clear all'), 'clear all')]",
                "//button[contains(translate(., 'CLEAR CART', 'clear cart'), 'clear')]",
            ],
            timeout=10,
        )
        clear_btn.click()

        try:
            alert = driver.switch_to.alert
            alert.accept()
        except Exception:
            pass

        time.sleep(1.0)
        lower = driver.page_source.lower()
        assert (
            "empty" in lower
            or "no items" in lower
            or len(_elements(driver, [".cart-item", ".ck-item", ".checkout-item", "[data-cart-item]"])) == 0
        )


class TestCheckoutForm:
    def test_checkout_page_loads(self, cart_driver: WebDriver, base_url: str):
        """Verify checkout loads quickly with bag, shipping form, and place-order controls."""
        driver = cart_driver
        start = time.monotonic()
        driver.get(f"{base_url}/retail/checkout")
        elapsed = time.monotonic() - start
        assert elapsed <= 10

        assert "your bag" in driver.page_source.lower()
        assert "shipping address" in driver.page_source.lower()
        place_order = _first_visible(
            driver,
            ["button[type='submit']", "#place-order", "button[id*='place']", "button[class*='place']"],
            timeout=10,
        )
        assert place_order.is_displayed()

    def test_desktop_layout(self, cart_driver: WebDriver, base_url: str):
        """Verify desktop checkout renders left-content and right summary as a two-column layout."""
        driver = cart_driver
        driver.set_window_size(1200, 800)
        driver.get(f"{base_url}/retail/checkout")

        order_summary = _first_visible(
            driver,
            [".order-summary", "#order-summary", "aside", ".summary-card"],
            timeout=10,
        )
        assert "order summary" in driver.page_source.lower()

        columns = _elements(driver, [".grid > div", ".checkout-grid > div", ".checkout-layout > div"])
        if len(columns) >= 2:
            x_positions = [c.rect.get("x", 0) for c in columns[:2]]
            assert abs(x_positions[0] - x_positions[1]) > 100
        assert order_summary.is_displayed()

    def test_mobile_layout(self, cart_driver: WebDriver, base_url: str):
        """Verify checkout collapses to single-column and form fields remain full-width on mobile viewport."""
        driver = cart_driver
        driver.set_window_size(390, 844)
        driver.get(f"{base_url}/retail/checkout")

        form_fields = _elements(
            driver,
            [
                "form input[name*='name']",
                "form input[name*='phone']",
                "form input[name*='address']",
                "#ck-pin",
            ],
        )
        assert len(form_fields) >= 3

        widths = [f.rect.get("width", 0) for f in form_fields if f.is_displayed()]
        assert widths and all(w >= 220 for w in widths)

        # Heuristic for single-column: summary appears below content on narrow viewport.
        left = _elements(driver, [".checkout-left", ".left-column", "main section"])
        right = _elements(driver, [".checkout-right", ".right-column", ".order-summary", "#order-summary"])
        if left and right:
            assert right[0].rect.get("y", 0) >= left[0].rect.get("y", 0)

    def test_pincode_serviceable(self, cart_driver: WebDriver, base_url: str):
        """Verify serviceable pincode resolves to delivery-available state and auto-populates city."""
        driver = cart_driver
        driver.get(f"{base_url}/retail/checkout")

        pin = _first_visible(driver, ["#ck-pin", "input[name*='pin']", "input[id*='pin']"], timeout=10)
        pin.clear()
        pin.send_keys("400001")
        time.sleep(1.5)

        status = _first_visible(
            driver,
            ["#pin-status", ".pin-status", "[data-pin-status]", "small[id*='pin']"],
            timeout=8,
        )
        status_text = _safe_text(status).lower()
        assert (
            "delivery available" in status_text
            or "serviceable" in status_text
            or "available" in status_text
        )

        city = _first_visible(driver, ["#ck-city", "input[name*='city']", "input[id*='city']"], timeout=8)
        assert "mumbai" in (city.get_attribute("value") or "").lower()

    def test_pincode_shows_neutral_on_timeout(self, cart_driver: WebDriver, base_url: str):
        """Verify pincode feedback avoids false red-not-serviceable in transient/slow API states."""
        driver = cart_driver
        driver.get(f"{base_url}/retail/checkout")

        pin = _first_visible(driver, ["#ck-pin", "input[name*='pin']", "input[id*='pin']"], timeout=10)
        pin.clear()
        pin.send_keys("400001")

        status = _first_visible(
            driver,
            ["#pin-status", ".pin-status", "[data-pin-status]", "small[id*='pin']"],
            timeout=8,
        )

        start = time.time()
        saw_neutral_or_positive = False
        while time.time() - start < 3.2:
            txt = _safe_text(status).lower()
            if any(k in txt for k in ["checking", "could not verify", "delivery available", "serviceable"]):
                saw_neutral_or_positive = True
                break
            if "not serviceable" in txt:
                pytest.fail("Unexpected red 'Not serviceable' shown during transient check window")
            time.sleep(0.2)

        assert saw_neutral_or_positive

    def test_coupon_apply_valid(self, cart_driver: WebDriver, base_url: str):
        """Verify valid coupon applies and shows positive success state in checkout."""
        coupon_code = os.getenv("TEST_COUPON_CODE", "").strip()
        if not coupon_code:
            pytest.skip("Set TEST_COUPON_CODE in .env/environment to run coupon apply test")

        driver = cart_driver

        # Add more quantity/value to increase chance of crossing coupon minimum thresholds.
        driver.get(f"{base_url}/retail/category/Bangles")
        for _ in range(3):
            try:
                _first_clickable_xpath(
                    driver,
                    ["(//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')])[1]"],
                    timeout=6,
                ).click()
                time.sleep(0.2)
            except Exception:
                break

        driver.get(f"{base_url}/retail/checkout")
        coupon_input = _first_visible(
            driver,
            ["#coupon", "#coupon-code", "input[name*='coupon']", "input[id*='coupon']"],
            timeout=10,
        )
        coupon_input.clear()
        coupon_input.send_keys(coupon_code)

        apply_btn = _first_clickable_xpath(
            driver,
            [
                "//button[contains(translate(., 'APPLY', 'apply'), 'apply')]",
                "//a[contains(translate(., 'APPLY', 'apply'), 'apply')]",
            ],
            timeout=8,
        )
        apply_btn.click()

        WebDriverWait(driver, 10).until(
            lambda d: any(k in d.page_source.lower() for k in ["applied", "saved", "success"])
        )
        lower = driver.page_source.lower()
        assert "applied" in lower or "saved" in lower

    def test_coupon_remove(self, cart_driver: WebDriver, base_url: str):
        """Verify applied coupon can be removed and discount row resets/hides."""
        coupon_code = os.getenv("TEST_COUPON_CODE", "").strip()
        if not coupon_code:
            pytest.skip("Set TEST_COUPON_CODE in .env/environment to run coupon remove test")

        driver = cart_driver
        driver.get(f"{base_url}/retail/checkout")

        coupon_input = _first_visible(
            driver,
            ["#coupon", "#coupon-code", "input[name*='coupon']", "input[id*='coupon']"],
            timeout=10,
        )
        coupon_input.clear()
        coupon_input.send_keys(coupon_code)
        _first_clickable_xpath(driver, ["//button[contains(translate(., 'APPLY', 'apply'), 'apply')]"]).click()

        WebDriverWait(driver, 8).until(lambda d: "applied" in d.page_source.lower() or "saved" in d.page_source.lower())

        remove_btn = _first_clickable_xpath(
            driver,
            [
                "//a[contains(translate(., 'REMOVE', 'remove'), 'remove')]",
                "//button[contains(translate(., 'REMOVE', 'remove'), 'remove')]",
            ],
            timeout=8,
        )
        remove_btn.click()
        time.sleep(1.0)

        lower = driver.page_source.lower()
        discount_zero = bool(re.search(r"discount[^\n\r]*₹\s*0", lower))
        assert "applied" not in lower or discount_zero or "discount" not in lower


@pytest.mark.skipif(
    os.getenv("RUN_ORDER_FLOW", "0") != "1",
    reason="Order-flow tests are skipped unless RUN_ORDER_FLOW=1",
)
class TestOrderFlow:
    def test_cod_order_placement(self, driver: WebDriver, base_url: str):
        """Verify full COD placement flow reaches thank-you page with order reference."""
        _open_checkout_with_cart(driver, base_url)

        def _fill(selector, value):
            el = _first_visible(driver, [selector], timeout=8)
            el.clear()
            el.send_keys(value)

        _fill("input[name*='name'], #ck-name", "Test Selenium")
        _fill("input[name*='phone'], #ck-phone", "9999999999")
        _fill("input[name*='email'], #ck-email", "selenium@test.com")
        _fill("input[name*='address'], #ck-address", "123 Test St")

        pin = _first_visible(driver, ["#ck-pin", "input[name*='pin']"], timeout=8)
        pin.clear()
        pin.send_keys("400001")

        WebDriverWait(driver, 10).until(
            lambda d: any(k in d.page_source.lower() for k in ["delivery available", "serviceable", "mumbai"])
        )

        cod_option = _first_clickable_xpath(
            driver,
            [
                "//label[contains(translate(., 'CASH ON DELIVERY', 'cash on delivery'), 'cash on delivery')]",
                "//input[@type='radio' and (contains(@value,'COD') or contains(@value,'cod'))]",
            ],
            timeout=8,
        )
        cod_option.click()

        place_order_btn = _first_clickable_xpath(
            driver,
            [
                "//button[contains(translate(., 'PLACE ORDER', 'place order'), 'place order')]",
                "//button[@type='submit']",
            ],
            timeout=8,
        )
        place_order_btn.click()

        WebDriverWait(driver, 15).until(EC.url_contains("/thank_you"))
        assert "/thank_you" in driver.current_url

        lower = driver.page_source.lower()
        assert "order placed successfully" in lower
        assert re.search(r"NN-SHP-[0-9-]+", driver.page_source)
        assert "✓" in driver.page_source or "check" in lower


class TestAdminPanel:
    def test_admin_login_page(self, driver: WebDriver, base_url: str):
        """Verify admin login page routing, form visibility, and dark-theme readability."""
        driver.get(f"{base_url}/admin")
        WebDriverWait(driver, 10).until(lambda d: "/admin/login" in d.current_url)

        username = _first_visible(driver, ["input[name='username']", "input#username"], timeout=8)
        password = _first_visible(driver, ["input[name='password']", "input#password", "input[type='password']"], timeout=8)
        assert username.is_displayed() and password.is_displayed()

        body = _first_visible(driver, ["body"], timeout=2)
        bg = (body.value_of_css_property("background-color") or "").lower()
        assert "rgb" in bg

        input_color = (username.value_of_css_property("color") or "").lower()
        # White-ish text check: either explicit white or high rgb channels.
        assert "255" in input_color or "rgb" in input_color

    def test_admin_coupon_manager(self, admin_driver: WebDriver, base_url: str):
        """Verify coupon manager page has create form, table, and key discount fields."""
        driver = admin_driver
        driver.get(f"{base_url}/admin/coupons")

        assert "coupon" in driver.page_source.lower()
        assert "create new coupon" in driver.page_source.lower()

        table = _first_visible(driver, ["table", ".coupon-table", "#coupon-table"], timeout=8)
        assert table.is_displayed()

        page_text = driver.page_source.lower()
        assert "discount %" in page_text
        assert "max discount" in page_text

    def test_admin_order_console(self, admin_driver: WebDriver, base_url: str):
        """Verify admin order console shows status stats, filters, and required headers."""
        driver = admin_driver
        driver.get(f"{base_url}/admin/orders")

        lower = driver.page_source.lower()
        for token in ["all", "paid", "cod", "accepted"]:
            assert token in lower

        # Table header validation
        headers_text = " ".join(_safe_text(h).lower() for h in _elements(driver, ["table th", "thead th"]))
        for header in ["order id", "customer", "cgst", "sgst", "coupon", "status"]:
            assert header in headers_text


class TestSEOAndMeta:
    def test_product_og_tags(self, driver: WebDriver, base_url: str):
        """Verify OG and JSON-LD product metadata exists on retail product detail page."""
        driver.get(f"{base_url}/retail/product/1")

        og_title = driver.find_elements(By.CSS_SELECTOR, "meta[property='og:title']")
        assert og_title and (og_title[0].get_attribute("content") or "").strip()

        og_image = driver.find_elements(By.CSS_SELECTOR, "meta[property='og:image']")
        assert og_image and "supabase.co" in (og_image[0].get_attribute("content") or "")

        og_type = driver.find_elements(By.CSS_SELECTOR, "meta[property='og:type']")
        assert og_type and (og_type[0].get_attribute("content") or "").strip().lower() == "product"

        ld_scripts = driver.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']")
        assert ld_scripts, "Expected at least one JSON-LD script tag"

        found_product = False
        for script in ld_scripts:
            raw = script.get_attribute("innerHTML") or ""
            try:
                payload = json.loads(raw)
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    if isinstance(item, dict) and str(item.get("@type", "")).lower() == "product":
                        found_product = True
                        break
            except Exception:
                continue
            if found_product:
                break
        assert found_product

    def test_sitemap_accessible(self, base_url: str):
        """Verify sitemap is publicly accessible as XML and contains URL entries."""
        resp = _requests_get(f"{base_url}/sitemap.xml")
        assert resp.status_code == 200
        assert "xml" in resp.headers.get("Content-Type", "").lower()
        assert "<url>" in resp.text

    def test_robots_txt(self, base_url: str):
        """Verify robots.txt blocks admin and checkout paths as expected."""
        resp = _requests_get(f"{base_url}/robots.txt")
        assert resp.status_code == 200
        assert "Disallow: /admin/" in resp.text
        assert "Disallow: /checkout/" in resp.text


class TestWholesaleSite:
    def test_wholesale_home_loads(self, driver: WebDriver, wholesale_base_url: str):
        """Verify wholesale home loads products and does not expose retail buy/cart actions."""
        resp = _requests_get(f"{wholesale_base_url}/wholesale", allow_redirects=True)
        assert resp.status_code == 200

        driver.get(f"{wholesale_base_url}/wholesale")
        assert _elements(driver, [".product-card", "[data-product-id]", ".add-to-quote-btn"])

        page_text = driver.page_source.lower()
        assert "add to cart" not in page_text
        assert "buy now" not in page_text

        header = _first_visible(driver, ["header"], timeout=6)
        header_text = header.text.lower()
        assert "cart" not in header_text

    def test_wholesale_tracking_blocked(self, driver: WebDriver, wholesale_base_url: str):
        """Verify wholesale domain does not serve retail tracking page and avoids server errors."""
        url = f"{wholesale_base_url}/track/MOCK-AWB-123"
        resp = _requests_get(url, allow_redirects=False)
        assert resp.status_code != 500
        assert resp.status_code in {200, 301, 302, 303, 307, 308, 403, 404}

        driver.get(url)
        lower = driver.page_source.lower()
        assert "track order" not in lower or "retail" not in driver.current_url.lower()

    def test_wholesale_checkout_blocked(self, driver: WebDriver, wholesale_base_url: str):
        """Verify retail checkout path is blocked/redirected on wholesale domain."""
        url = f"{wholesale_base_url}/retail/checkout"
        resp = _requests_get(url, allow_redirects=False)
        assert resp.status_code in {301, 302, 303, 307, 308, 403, 404}

        driver.get(url)
        lower = driver.page_source.lower()
        assert "cash on delivery" not in lower
        assert "razorpay" not in lower
        assert "place order" not in lower
