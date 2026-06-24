import requests
import json

class DelhiveryService:
    def __init__(self, api_token, debug_mode=False):
        self.api_token = api_token.strip()
        self.base_url = "https://track.delhivery.com"
        self.debug_mode = debug_mode
        self.headers = {
            "Authorization": f"Token {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def calculate_volumetric_weight(self, l, w, h):
        """
        Calculate volumetric weight using (L * W * H) / 5000.
        All dimensions should be in centimeters.
        """
        try:
            return round((float(l) * float(w) * float(h)) / 5000, 2)
        except Exception as e:
            print(f"Error in volumetric weight calculation: {e}")
            return 0

    def check_pincode(self, pincode):
        if self.debug_mode:
            print("DEBUG MODE: Returning mock pincode serviceability response.")
            return {
                "status": True,
                "city": "MockCity",
                "state": "MockState",
                "msg": "Serviceable (Mock)"
            }
        url = f"{self.base_url}/c/api/pin-codes/json/"
        params = {"filter_codes": str(pincode).strip()}
        headers = {
            "Authorization": f"Token {self.api_token}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Flask/NariNakhre"
        }
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 403:
                print("--- DELHIYERY 403 DEBUG INFO ---")
                print(f"URL Attempted: {response.url}")
                print(f"Response Body: {response.text}")
                print(f"Response Headers: {response.headers}")
                return {"status": False, "msg": "Access Forbidden: Check IP Whitelisting in Delhivery Panel"}
            if "Login or API Key Required" in response.text:
                return {"status": False, "msg": "Authentication Error: Invalid Token"}
            data = response.json()
            if not data or not isinstance(data, list):
                return {"status": False, "msg": "Location not serviceable"}
            pincode_info = data[0]
            if pincode_info.get("remark", "").strip().lower() == "embargo":
                return {"status": False, "msg": "Location under embargo"}
            return {
                "status": True,
                "city": pincode_info.get("city"),
                "state": pincode_info.get("state_code"),
                "msg": "Serviceable"
            }
        except Exception as e:
            print(f"Connection Error: {e}")
            return {"status": False, "msg": "Internal server error connecting to Delhivery"}

    def get_shipping_cost(self, o_pin, d_pin, weight_grams, mode="Prepaid"):
        url = f"{self.base_url}/api/kinko/v1/invoice/charges/.json"
        params = {"ss": "R", "md": mode, "o_pin": o_pin, "d_pin": d_pin, "wt": weight_grams}
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            res_data = response.json()
            return res_data[0]['total_amount'] if res_data else 0
        except Exception as e:
            print(f"Error: {e}")
            return 0

    def create_order(self, order_data):
        if self.debug_mode:
            print("DEBUG MODE: Returning mock order creation response.")
            return {
                "status": True,
                "waybill": "MOCK123456789",
                "msg": "Order created successfully (Mock)"
            }
        url = f"{self.base_url}/api/cmu/create.json"
        payload = {
            "shipments": [order_data],
            "pickup_location": {"name": "YOUR_WAREHOUSE_NAME"}
        }
        data_string = {
            "format": "json",
            "data": json.dumps(payload)
        }
        try:
            response = requests.post(
                url,
                data=data_string,
                headers={
                    "Authorization": f"Token {self.api_token}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )
            res_json = response.json()
            waybill = None
            if "packages" in res_json and isinstance(res_json["packages"], list) and res_json["packages"]:
                waybill = res_json["packages"][0].get("waybill")
            return {
                "status": True if waybill else False,
                "waybill": waybill,
                "msg": res_json.get("status", "Order creation response received")
            }
        except Exception as e:
            print(f"Order Creation Error: {e}")
            return {"status": False, "msg": f"Order creation failed: {e}"}