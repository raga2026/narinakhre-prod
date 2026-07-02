import os
import re
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

RETAIL_BASE_URL = "https://test-retail.narinakhre.com"
WHOLESALE_BASE_URL = "https://test-wholesale.narinakhre.com"


def _navigate_with_retry(driver: webdriver.Chrome, url: str, retry_wait: int = 3) -> None:
    try:
        driver.get(url)
    except TimeoutException:
        time.sleep(retry_wait)
        driver.get(url)


def pytest_configure(config: pytest.Config) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    (repo_root / "reports" / "screenshots").mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def base_url() -> str:
    return RETAIL_BASE_URL


@pytest.fixture(scope="session")
def wholesale_base_url() -> str:
    return WHOLESALE_BASE_URL


@pytest.fixture(scope="session")
def admin_credentials() -> tuple[str, str]:
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        pytest.skip("Admin credentials not configured")
    return username, password


@pytest.fixture(scope="function")
def driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    drv = webdriver.Chrome(options=options)
    drv.implicitly_wait(5)
    drv.set_page_load_timeout(30)
    yield drv
    drv.quit()


@pytest.fixture(scope="function")
def cart_driver(driver: webdriver.Chrome, base_url: str) -> webdriver.Chrome:
    _navigate_with_retry(driver, f"{base_url}/retail/category/Bangles")

    add_btn = WebDriverWait(driver, 12).until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "(//button[contains(translate(., 'ADD TO CART', 'add to cart'), 'add to cart') or contains(@class, 'add-to-cart')])[1]",
            )
        )
    )
    add_btn.click()

    def _count_value(text: str) -> int:
        match = re.search(r"\d+", text or "")
        return int(match.group()) if match else 0

    WebDriverWait(driver, 10).until(
        lambda d: _count_value(
            d.find_element(By.CSS_SELECTOR, "#cart-count, .cart-count, [data-cart-count]").text
            if d.find_elements(By.CSS_SELECTOR, "#cart-count, .cart-count, [data-cart-count]")
            else "0"
        )
        >= 1
    )
    return driver


@pytest.fixture(scope="function")
def admin_driver(
    driver: webdriver.Chrome,
    base_url: str,
    admin_credentials: tuple[str, str],
) -> webdriver.Chrome:
    username, password = admin_credentials
    driver.get(f"{base_url}/admin/login")

    username_input = WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "input[name='username'], input#username"))
    )
    password_input = driver.find_element(By.CSS_SELECTOR, "input[name='password'], input#password, input[type='password']")

    username_input.clear()
    username_input.send_keys(username)
    password_input.clear()
    password_input.send_keys(password)
    driver.find_element(By.XPATH, "//button[@type='submit']").click()

    if "/admin/verify-totp" in driver.current_url:
        totp_secret = os.getenv("ADMIN_TOTP_SECRET", "").strip()
        if not totp_secret:
            pytest.skip("Admin login requires TOTP but ADMIN_TOTP_SECRET is not configured")
        try:
            import pyotp

            code = pyotp.TOTP(totp_secret).now()
            totp_input = WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "input[name='totp_code'], input#totp_code"))
            )
            totp_input.clear()
            totp_input.send_keys(code)
            driver.find_element(By.XPATH, "//button[@type='submit']").click()
        except Exception as exc:
            pytest.skip(f"Could not complete TOTP login flow: {exc}")

    WebDriverWait(driver, 12).until(lambda d: "/admin" in d.current_url and "login" not in d.current_url)
    return driver


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)

    if report.when != "call" or report.passed:
        return

    drv = None
    for key in ("driver", "cart_driver", "admin_driver"):
        if key in item.funcargs:
            drv = item.funcargs[key]
            break

    if drv is None:
        return

    root = Path(__file__).resolve().parents[1]
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", item.nodeid)
    screenshot_path = root / "reports" / "screenshots" / f"{safe_name}.png"
    drv.save_screenshot(str(screenshot_path))

    # Attach screenshot to pytest-html report when plugin is available.
    pytest_html = item.config.pluginmanager.getplugin("html")
    extra = getattr(report, "extra", [])
    if pytest_html:
        extra.append(pytest_html.extras.image(str(screenshot_path)))
    report.extra = extra
