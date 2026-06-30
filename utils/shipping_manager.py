import abc
import json
import requests

# --- Base Provider ---
class BaseShippingProvider(abc.ABC):
    @staticmethod
    def calculate_volumetric_weight(l, w, h):
        """Volumetric weight formula: (L * W * H) / 5000"""
        try:
            return round((float(l) * float(w) * float(h)) / 5000, 2)
        except Exception as e:
            print(f"Error in volumetric weight calculation: {e}")
            return 0

    @abc.abstractmethod
    def calculate_rates(self, o_pin, d_pin, weight):
        pass

    @abc.abstractmethod
    def create_shipment(self, order_data):
        pass

    @abc.abstractmethod
    def verify_pincode(self, pincode):
        pass

    @abc.abstractmethod
    def get_rates(self, o_pin, d_pin, weight, mode="Prepaid"):
        pass


# --- Delhivery Provider ---
class DelhiveryProvider(BaseShippingProvider):
    def __init__(self, api_token):
        self.api_token = (api_token or '').strip()
        self.base_url = "https://track.delhivery.com"
        self.headers = {
            "Authorization": f"Token {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def calculate_rates(self, o_pin, d_pin, weight):
        url = f"{self.base_url}/api/kinko/v1/invoice/charges/.json"
        params = {
            "pickup_postcode": str(o_pin),
            "delivery_postcode": str(d_pin),
            "weight": str(weight),
            "cod": "0"
        }
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            data = response.json()
            return data if data else {"status": False, "msg": "No rates found"}
        except Exception as e:
            print(f"Error fetching rates: {e}")
            return {"status": False, "msg": str(e)}

    def verify_pincode(self, pincode):
        """
        Check Delhivery pincode serviceability.
        Returns dict with 'status'/'serviceable', 'city', 'state'.
        """
        if not self.api_token:
            return {"status": False, "serviceable": False, "msg": "Delhivery API key not configured"}
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
                return {"status": False, "serviceable": False,
                        "msg": "Access Forbidden: Check IP Whitelisting in Delhivery Panel"}
            if "Login or API Key Required" in response.text:
                return {"status": False, "serviceable": False, "msg": "Authentication Error: Invalid Token"}
            data = response.json()
            if not data or not isinstance(data, list):
                return {"status": False, "serviceable": False, "msg": "Location not serviceable"}
            pincode_info = data[0]
            if pincode_info.get("remark", "").strip().lower() == "embargo":
                return {"status": False, "serviceable": False, "msg": "Location under embargo"}
            return {
                "status": True,
                "serviceable": True,
                "city": pincode_info.get("city"),
                "state": pincode_info.get("state_code"),
                "msg": "Serviceable"
            }
        except Exception as e:
            print(f"Delhivery pincode check error: {e}")
            return {"status": False, "serviceable": False, "msg": "Connection error to Delhivery"}

    def get_rates(self, o_pin, d_pin, weight, mode="Prepaid"):
        """
        Get shipping charge (and COD fee if applicable) for a given route/weight.
        Returns dict with 'rate'/'shipping_charge' and 'cod_fee'.
        """
        if not self.api_token:
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": "Delhivery API key not configured"}
        url = f"{self.base_url}/api/kinko/v1/invoice/charges/.json"
        cod_flag = "1" if mode == "COD" else "0"
        params = {
            "ss": "R",
            "md": mode,
            "o_pin": str(o_pin),
            "d_pin": str(d_pin),
            "wt": str(weight),
            "cod": cod_flag
        }
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            res_data = response.json()
            if res_data and isinstance(res_data, list):
                charges = res_data[0]
                total = charges.get('total_amount', 0)
                cod_charge = charges.get('cod_charges', 0) if mode == "COD" else 0
                return {"rate": total, "shipping_charge": total, "cod_fee": cod_charge}
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": "No rates found"}
        except Exception as e:
            print(f"Error fetching shipping rates: {e}")
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": str(e)}

    def create_shipment(self, order_data):
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


# --- Mock Provider (used when SHIPPING_PROVIDER=mock, e.g. local dev without API key) ---
class MockShippingProvider(BaseShippingProvider):
    def calculate_rates(self, o_pin, d_pin, weight):
        return {"status": True, "rate": 50, "msg": "Mock shipping rate"}

    def verify_pincode(self, pincode):
        # Mock: treat all 6-digit pincodes as serviceable so local/dev testing works
        pin = str(pincode).strip()
        if len(pin) == 6 and pin.isdigit():
            return {"status": True, "serviceable": True, "city": "Mock City", "state": "MC", "msg": "Serviceable (Mock)"}
        return {"status": False, "serviceable": False, "msg": "Invalid pincode"}

    def get_rates(self, o_pin, d_pin, weight, mode="Prepaid"):
        base = 50
        cod = 25 if mode == "COD" else 0
        return {"rate": base, "shipping_charge": base, "cod_fee": cod}

    def create_shipment(self, order_data):
        return {
            "status": True,
            "waybill": "MOCK-AWB-123",
            "msg": "Order created successfully (Mock)"
        }


# --- Factory ---
def get_shipping_provider(name, api_token=None):
    if name == "delhivery":
        return DelhiveryProvider(api_token)
    elif name == "mock":
        return MockShippingProvider()
    else:
        raise ValueError(f"Unknown shipping provider: {name}")
