import os
import re
import time

import pytest
import requests
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

RETAIL = "https://test-retail.narinakhre.com"
WHOLESALE = "https://test-wholesale.narinakhre.com"


def _safe_text(element) -> str:
    try:
        return (element.text or "").strip()
    except Exception:
        return ""


def _extract_int(text: str) -> int:
    match = re.search(r"\d+", text or "")
    return int(match.group()) if match else 0


def _first_visible_css(driver: WebDriver, selectors, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        for selector in selectors:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    return element
        time.sleep(0.15)
    raise TimeoutException(f"No visible element matched selectors: {selectors}")


def _first_clickable_xpath(driver: WebDriver, xpaths, timeout=10):
    for xp in xpaths:
        try:
            return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xp)))
        except Exception:
            continue
    raise TimeoutException(f"No clickable element matched xpaths: {xpaths}")


def _all_visible(driver: WebDriver, selector: str):
    return [element for element in driver.find_elements(By.CSS_SELECTOR, selector) if element.is_displayed()]


def _open_with_retry(driver: WebDriver, url: str, retry_wait: int = 3):
    try:
        driver.get(url)
    except TimeoutException:
        time.sleep(retry_wait)
        driver.get(url)


def _add_one_item_to_cart(driver: WebDriver):
    _open_with_retry(driver, f"{RETAIL}/retail/category/Bangles")
    add_btn = _first_clickable_xpath(
        driver,
        [
            "(//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')])[1]",
            "(//*[contains(@class,'add-to-cart') and (self::button or self::a)])[1]",
        ],
        timeout=12,
    )
    add_btn.click()

    WebDriverWait(driver, 10).until(
        lambda d: _extract_int(
            _safe_text(_first_visible_css(d, ["#cart-count", ".cart-count", "[data-cart-count]"], timeout=3))
        )
        >= 1
    )


def _parse_rgb(color_value: str):
    # Handles 'rgb(r, g, b)' and 'rgba(r, g, b, a)'.
    match = re.search(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", color_value or "")
    if not match:
        return None
    return tuple(int(match.group(i)) for i in range(1, 4))


def _is_dark(rgb):
    if not rgb:
        return False
    r, g, b = rgb
    return (r + g + b) / 3.0 < 70


def _is_whiteish(rgb):
    if not rgb:
        return False
    r, g, b = rgb
    return (r + g + b) / 3.0 > 180


def test_retail_home_loads(driver):
    response = requests.get(f"{RETAIL}/retail", timeout=15)
    assert response.status_code == 200

    _open_with_retry(driver, f"{RETAIL}/retail")
    assert "Nari Nakhre" in driver.title

    header = _first_visible_css(driver, ["header"], timeout=8)
    header_cart = header.find_elements(By.CSS_SELECTOR, "#cart-count, .cart-count, a[href*='checkout'], [class*='cart']")
    assert any(element.is_displayed() for element in header_cart), "Expected visible cart icon/count in header"

    cards = _all_visible(driver, ".ck-item, .product-card, [data-product-id]")
    if len(cards) == 0:
        pytest.skip("No products on homepage to check MRP display")

    strike_nodes = driver.find_elements(By.CSS_SELECTOR, ".line-through, del, s, .old-price, .mrp")
    strike_found = False
    for node in strike_nodes:
        style = (node.value_of_css_property("text-decoration-line") or "").lower()
        style_alt = (node.value_of_css_property("text-decoration") or "").lower()
        if "line-through" in style or "line-through" in style_alt:
            strike_found = True
            break
    discount_badge = any(_all_visible(driver, ".prod-disc")) or bool(re.search(r"\b\d+\s*%\s*off\b", driver.page_source.lower()))
    assert strike_found or discount_badge, "Expected MRP strikethrough or discount badge display logic"


def test_product_detail(driver):
    driver.get(f"{RETAIL}/retail/product/1")

    name = _first_visible_css(driver, ["h1", ".product-title", ".pdp-title"], timeout=12)
    assert _safe_text(name), "Expected non-empty product name"

    supabase_images = driver.find_elements(By.CSS_SELECTOR, "img[src*='supabase.co']")
    assert len(supabase_images) >= 1, "Expected at least one Supabase-hosted product image"

    full_text = driver.page_source
    assert len(re.findall(r"₹\s*\d+", full_text)) >= 2, "Expected both MRP and retail price to be visible"

    add_btn = _first_clickable_xpath(
        driver,
        [
            "//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')]",
            "//button[contains(@class, 'add-to-cart')]",
        ],
        timeout=10,
    )
    assert add_btn.is_displayed(), "Expected Add to Cart button"


def test_add_to_cart(driver):
    _open_with_retry(driver, f"{RETAIL}/retail/category/Bangles")
    before = 0
    count_nodes = driver.find_elements(By.CSS_SELECTOR, "#cart-count, .cart-count, [data-cart-count]")
    if count_nodes:
        before = _extract_int(_safe_text(count_nodes[0]))

    _first_clickable_xpath(
        driver,
        [
            "(//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')])[1]",
            "(//*[contains(@class,'add-to-cart') and (self::button or self::a)])[1]",
        ],
        timeout=10,
    ).click()

    WebDriverWait(driver, 10).until(
        lambda d: _extract_int(_safe_text(_first_visible_css(d, ["#cart-count", ".cart-count", "[data-cart-count]"], timeout=3)))
        >= max(1, before + 1)
    )


def test_checkout_loads(cart_driver):
    cart_driver.get(f"{RETAIL}/retail/checkout")

    assert "your bag" in cart_driver.page_source.lower()
    assert "shipping address" in cart_driver.page_source.lower()

    phone_input = _first_visible_css(cart_driver, ["#ck-phone", "input[name*='phone']"], timeout=8)
    pin_input = _first_visible_css(cart_driver, ["#ck-pin", "input[name*='pin']"], timeout=8)
    assert (phone_input.get_attribute("inputmode") or "").lower() == "numeric"
    assert (pin_input.get_attribute("inputmode") or "").lower() == "numeric"


def test_checkout_desktop_layout(cart_driver):
    cart_driver.set_window_size(1200, 800)
    _open_with_retry(cart_driver, f"{RETAIL}/retail/checkout")

    summary = _first_visible_css(cart_driver, [".ck-right", ".ck-right .ck-section", ".ck-sum-total-row"], timeout=10)
    assert "order summary" in cart_driver.page_source.lower()

    left_cols = _all_visible(cart_driver, ".ck-left")
    right_cols = _all_visible(cart_driver, ".ck-right")
    if left_cols and right_cols:
        left, right = left_cols[0], right_cols[0]
        assert abs(left.rect.get("x", 0) - right.rect.get("x", 0)) > 100


def test_checkout_mobile_layout(cart_driver):
    cart_driver.set_window_size(390, 844)
    cart_driver.get(f"{RETAIL}/retail/checkout")

    form = _first_visible_css(cart_driver, ["form"], timeout=8)
    assert form.rect.get("width", 0) >= 300, "Expected full-width form on mobile"

    left = _all_visible(cart_driver, ".checkout-left, .left-column, main section")
    right = _all_visible(cart_driver, ".checkout-right, .right-column, .order-summary, #order-summary")
    if left and right:
        assert right[0].rect.get("y", 0) >= left[0].rect.get("y", 0), "Expected single-column stacking on mobile"


def test_pincode_serviceable(cart_driver):
    _open_with_retry(cart_driver, f"{RETAIL}/retail/checkout")

    pin = _first_visible_css(cart_driver, ["#ck-pin", "input[name*='pin']"], timeout=8)
    pin.clear()
    pin.send_keys("400001")
    time.sleep(2)

    status = _first_visible_css(cart_driver, ["#ck-pin-status", ".ck-pin-status", "small[id*='pin-status']"], timeout=10)
    status_text = _safe_text(status).lower()
    color = _parse_rgb(status.value_of_css_property("color") or "")

    assert ("delivery available" in status_text or "serviceable" in status_text), "Expected serviceable confirmation text"
    assert "not serviceable" not in status_text, "Must not show red not-serviceable state simultaneously"
    if color:
        r, g, b = color
        assert g >= r, "Expected status color to lean green for serviceable pincode"


def test_coupon_apply(cart_driver):
    driver = cart_driver

    # Add more items to improve chance that cart value crosses coupon thresholds.
    _open_with_retry(driver, f"{RETAIL}/retail/category/Bangles")
    for _ in range(4):
        try:
            _first_clickable_xpath(
                driver,
                ["(//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart')])[1]"],
                timeout=6,
            ).click()
            time.sleep(0.15)
        except Exception:
            break

    _open_with_retry(driver, f"{RETAIL}/retail/checkout")
    coupon_code = os.getenv("TEST_COUPON_CODE", "WELCOME10")

    coupon_input = _first_visible_css(driver, ["#coupon", "#coupon-code", "input[name*='coupon']", "input[id*='coupon']"], timeout=10)
    coupon_input.clear()
    coupon_input.send_keys(coupon_code)

    _first_clickable_xpath(
        driver,
        [
            "//button[contains(translate(., 'APPLY', 'apply'), 'apply')]",
            "//a[contains(translate(., 'APPLY', 'apply'), 'apply')]",
        ],
        timeout=8,
    ).click()

    WebDriverWait(driver, 10).until(
        lambda d: any(token in d.page_source.lower() for token in ["applied", "saved", "invalid", "error", "coupon"])
    )

    page = driver.page_source.lower()
    assert any(token in page for token in ["applied", "saved", "invalid", "error"]), "Expected success or error response (not blank)"


def test_admin_login_page(driver):
    driver.get(f"{RETAIL}/admin/login")

    username = _first_visible_css(driver, ["input[name='username']", "input#username"], timeout=8)
    password = _first_visible_css(driver, ["input[name='password']", "input#password", "input[type='password']"], timeout=8)
    assert username.is_displayed() and password.is_displayed()

    body = _first_visible_css(driver, ["body"], timeout=2)
    bg_rgb = _parse_rgb(body.value_of_css_property("background-color") or "")
    assert _is_dark(bg_rgb), "Expected dark admin background (e.g. #0f172a)"

    input_rgb = _parse_rgb(username.value_of_css_property("color") or "")
    assert _is_whiteish(input_rgb), "Expected white/bright text for readability"


def test_admin_orders_page(admin_driver):
    admin_driver.get(f"{RETAIL}/admin/orders")

    # Stats bar validation by token presence.
    lower = admin_driver.page_source.lower()
    for token in ["all", "paid", "cod", "accepted", "dispatched"]:
        assert token in lower

    headers = " ".join(_safe_text(h).lower() for h in admin_driver.find_elements(By.CSS_SELECTOR, "table th, thead th"))
    for field in ["order id", "cgst", "sgst"]:
        assert field in headers


def test_admin_coupons_page(admin_driver):
    admin_driver.get(f"{RETAIL}/admin/coupons")

    assert "coupon" in admin_driver.page_source.lower()

    max_discount_fields = admin_driver.find_elements(
        By.XPATH,
        "//*[contains(translate(., 'MAX DISCOUNT', 'max discount'), 'max discount')]",
    )
    assert any(field.is_displayed() for field in max_discount_fields), "Expected Max Discount (₹) field label"

    table = _first_visible_css(admin_driver, ["table", ".coupon-table", "#coupon-table"], timeout=8)
    assert table.is_displayed()


def test_tracking_page(driver):
    url = f"{RETAIL}/track/MOCK-AWB-123"
    response = requests.get(url, timeout=15)
    assert response.status_code != 500

    driver.get(url)
    lower = driver.page_source.lower()
    assert "mock-awb-123" in lower, "Expected waybill number to be shown"
    assert any(
        token in lower
        for token in ["no tracking", "could not fetch", "not available", "unavailable", "no data"]
    ), "Expected graceful no-tracking-data style message"


def test_invoice_page(admin_driver):
    admin_driver.get(f"{RETAIL}/admin/orders")

    page = admin_driver.page_source
    order_ids = re.findall(r"NN-SHP-[0-9\-]+", page)
    if not order_ids:
        pytest.skip("No real order_id available")

    order_id = order_ids[0]
    admin_driver.get(f"{RETAIL}/invoice/{order_id}")

    lower = admin_driver.page_source.lower()
    assert "tax invoice" in lower
    assert "cgst" in lower
    assert "sgst" in lower


def test_wholesale_home(driver):
    response = requests.get(f"{WHOLESALE}/wholesale", timeout=15)
    assert response.status_code == 200

    _open_with_retry(driver, f"{WHOLESALE}/wholesale")
    lower = driver.page_source.lower()

    header = _first_visible_css(driver, ["header"], timeout=8)
    assert "cart" not in header.text.lower(), "Wholesale header must not show cart icon"
    assert "add to cart" not in lower
    assert "buy now" not in lower


def test_wholesale_tracking_blocked(driver):
    url = f"{WHOLESALE}/track/MOCK-AWB-123"
    response = requests.get(url, timeout=15, allow_redirects=False)
    assert response.status_code != 500

    driver.get(url)
    lower = driver.page_source.lower()
    assert "track order" not in lower or "/track/" not in driver.current_url.lower(), "Wholesale must not serve retail tracking page"


def test_sitemap():
    response = requests.get(f"{RETAIL}/sitemap.xml", timeout=15)
    assert response.status_code == 200
    assert "<url>" in response.text


def test_robots_txt():
    response = requests.get(f"{RETAIL}/robots.txt", timeout=15)
    assert response.status_code == 200
    assert "Disallow: /admin/" in response.text
