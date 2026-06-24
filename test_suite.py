import unittest
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class NariNakhreTest(unittest.TestCase):
    def setUp(self):
        # Initialize Chrome
        self.driver = webdriver.Chrome()
        self.base_url = "http://127.0.0.1:5000"
        self.driver.implicitly_wait(5) # Give elements 5 seconds to load
        self.driver.maximize_window()

    def tearDown(self):
        self.driver.quit()

    def get_first_product_sku(self):
        """Finds the first SKU available on the current page to avoid SKU123 errors"""
        try:
            # Look for the first 'Add' button and extract its data-sku or data-product-id
            btn = self.driver.find_element(By.CSS_SELECTOR, ".add-to-cart-btn, .add-to-quote-btn")
            sku = btn.get_attribute("data-sku") or btn.get_attribute("data-product-id")
            return sku
        except:
            return None

    def test_wholesale_workflow(self):
        driver = self.driver
        driver.get(f"{self.base_url}/wholesale")
        time.sleep(2) # Let carousel load

        # 1. Check if Hero Section exists
        self.assertTrue(driver.find_element(By.ID, "hero-section").is_displayed(), "Wholesale Hero section missing")

        # 2. Get a REAL SKU from your actual products
        real_sku = self.get_first_product_sku()
        self.assertIsNotNone(real_sku, "No products found on Wholesale home page!")

        # 3. Verify Tier and Size visibility for that real SKU
        # We use CSS Selector to find IDs that start with 'tier-select-'
        tier_select = driver.find_element(By.ID, f"tier-select-{real_sku}")
        size_select = driver.find_element(By.ID, f"size-{real_sku}")
        
        self.assertTrue(tier_select.is_displayed(), f"Tier select for {real_sku} not visible")
        self.assertTrue(size_select.is_displayed(), f"Size select for {real_sku} not visible")

        # 4. Test Add to Quote
        btn = driver.find_element(By.CSS_SELECTOR, f"[data-sku='{real_sku}'], [data-product-id='{real_sku}']")
        self.assertIn("Quote", btn.text, "Button text should be 'Add to Quote'")
        btn.click()

        # Handle Alert
        WebDriverWait(driver, 5).until(EC.alert_is_present())
        alert = driver.switch_to.alert
        self.assertIn("Added", alert.text)
        alert.accept()

    def test_retail_workflow(self):
        driver = self.driver
        driver.get(f"{self.base_url}/retail")
        
        # 1. Get a REAL SKU
        real_sku = self.get_first_product_sku()
        
        # 2. Verify only Size is visible (Tiers should be hidden in Retail)
        size_select = driver.find_elements(By.ID, f"size-{real_sku}")
        tier_select = driver.find_elements(By.ID, f"tier-select-{real_sku}")
        
        self.assertGreater(len(size_select), 0, "Retail should have size selection")
        self.assertEqual(len(tier_select), 0, "Retail should NOT have wholesale tiers")

        # 3. Test Add to Cart
        btn = driver.find_element(By.CSS_SELECTOR, f"[data-sku='{real_sku}'], [data-product-id='{real_sku}']")
        self.assertIn("Cart", btn.text, "Button text should be 'Add to Cart'")
        btn.click()

        # Handle Alert
        WebDriverWait(driver, 5).until(EC.alert_is_present())
        driver.switch_to.alert.accept()

if __name__ == "__main__":
    unittest.main()