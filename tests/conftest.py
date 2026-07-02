import os
import re
from pathlib import Path

import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://test-retail.narinakhre.com"
BASE_URL_WS = "https://test-wholesale.narinakhre.com"


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--headless",
        action="store_true",
        default=False,
        help="Run Chrome in headless mode",
    )


def pytest_configure(config: pytest.Config) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    _load_env_file(repo_root / ".env")
    (repo_root / "reports" / "screenshots").mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def wholesale_base_url() -> str:
    return BASE_URL_WS


@pytest.fixture(scope="session")
def admin_credentials() -> tuple[str, str]:
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        pytest.skip("ADMIN_USERNAME / ADMIN_PASSWORD are not configured in environment or .env")
    return username, password


@pytest.fixture(scope="class")
def driver(request: pytest.FixtureRequest) -> webdriver.Chrome:
    options = Options()
    if request.config.getoption("--headless"):
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    drv = webdriver.Chrome(options=options)
    drv.set_page_load_timeout(15)
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


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

    submit = driver.find_element(
        By.XPATH,
        "//button[@type='submit' or contains(translate(., 'LOGIN', 'login'), 'login')]",
    )
    submit.click()

    # Some environments require TOTP, some may not. If TOTP appears and no code is configured, skip.
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

    WebDriverWait(driver, 10).until(lambda d: "/admin" in d.current_url and "login" not in d.current_url)
    return driver


@pytest.fixture(scope="function")
def cart_driver(driver: webdriver.Chrome, base_url: str) -> webdriver.Chrome:
    driver.get(f"{base_url}/retail/category/Bangles")

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
        m = re.search(r"\d+", text or "")
        return int(m.group()) if m else 0

    WebDriverWait(driver, 10).until(
        lambda d: _count_value(
            (
                d.find_element(By.CSS_SELECTOR, "#cart-count, .cart-count, [data-cart-count]").text
                if d.find_elements(By.CSS_SELECTOR, "#cart-count, .cart-count, [data-cart-count]")
                else "0"
            )
        )
        >= 1
    )
    return driver


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(autouse=True)
def _screenshot_on_failure(request: pytest.FixtureRequest):
    yield
    rep_call = getattr(request.node, "rep_call", None)
    if not rep_call or rep_call.passed:
        return

    drv = None
    for key in ("driver", "admin_driver", "cart_driver"):
        if key in request.node.funcargs:
            drv = request.node.funcargs[key]
            break

    if drv is None:
        return

    root = Path(__file__).resolve().parents[1]
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", request.node.nodeid)
    screenshot_path = root / "reports" / "screenshots" / f"{safe_name}.png"
    try:
        drv.save_screenshot(str(screenshot_path))
    except Exception:
        pass
