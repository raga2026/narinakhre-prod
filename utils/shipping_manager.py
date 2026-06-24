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

# --- Delhivery Provider ---
class DelhiveryProvider(BaseShippingProvider):
    def __init__(self, api_token):
        self.api_token = api_token.strip()
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

# --- Mock Provider ---
class MockShippingProvider(BaseShippingProvider):
    def calculate_rates(self, o_pin, d_pin, weight):
        return {
            "status": True,
            "rate": 50,
            "msg": "Mock shipping rate"
        }

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
