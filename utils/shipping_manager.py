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

            # Delhivery's real response shape: {"delivery_codes": [{"postal_code": {...}}]}
            postal_code = None
            if isinstance(data, dict) and "delivery_codes" in data:
                codes = data.get("delivery_codes") or []
                if codes and isinstance(codes, list):
                    postal_code = codes[0].get("postal_code")
            elif isinstance(data, list) and data:
                # Fallback for older/alternate response shape: [{"city":..., "remark":...}]
                postal_code = data[0]

            if not postal_code:
                return {"status": False, "serviceable": False, "msg": "Location not serviceable"}

            remark = (postal_code.get("remarks") or postal_code.get("remark") or "").strip().lower()
            if remark == "embargo":
                return {"status": False, "serviceable": False, "msg": "Location under embargo"}

            # 'pre_paid' and 'cod' fields indicate whether prepaid/COD delivery is offered here
            pre_paid_ok = postal_code.get("pre_paid", "Y") == "Y"
            cod_ok = postal_code.get("cod", "Y") == "Y"

            return {
                "status": True,
                "serviceable": True,
                "city": postal_code.get("city") or postal_code.get("district"),
                "state": postal_code.get("state_code"),
                "prepaid_available": pre_paid_ok,
                "cod_available": cod_ok,
                "msg": "Serviceable"
            }
        except Exception as e:
            print(f"Delhivery pincode check error: {e}")
            return {"status": False, "serviceable": False, "msg": "Connection error to Delhivery"}

    def get_rates(self, o_pin, d_pin, weight, mode="Prepaid"):
        """
        Get shipping charge for a given route/weight.
        Returns dict with 'rate'/'shipping_charge' and 'cod_fee'.

        Delhivery's invoice/charges API official mandatory params (per their docs):
          - md  = Billing Mode: 'E' (Express) or 'S' (Surface)
          - cgm = Chargeable weight in GRAMS (integer)
          - o_pin, d_pin = 6-digit pincodes
          - ss  = Shipment Status: 'Delivered', 'RTO', or 'DTO' (required, even for an estimate —
                  'Delivered' is used here since we're quoting a forward/delivered shipment)
        Note: this endpoint does NOT take a payment-type param; COD surcharge is applied
        separately by Delhivery's COD policy and isn't returned by this invoice endpoint,
        so we estimate cod_fee with a flat business rule below.
        """
        if not self.api_token:
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": "Delhivery API key not configured"}
        url = f"{self.base_url}/api/kinko/v1/invoice/charges/.json"
        is_cod = (mode == "COD")
        params = {
            "md": "S",            # Surface shipping (use 'E' for Express if needed)
            "cgm": str(int(float(weight))),  # chargeable weight in grams, integer
            "o_pin": str(o_pin),
            "d_pin": str(d_pin),
            "ss": "Delivered",    # required status param for a forward-shipment quote
        }
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            res_data = response.json()
            if isinstance(res_data, dict) and res_data.get('error'):
                return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": res_data['error']}
            if res_data and isinstance(res_data, list) and res_data:
                charges = res_data[0]
                total = charges.get('total_amount', 0) or charges.get('gross_amount', 0)
                # Delhivery's invoice API doesn't return a COD surcharge directly;
                # apply a standard flat COD handling fee when payment mode is COD.
                cod_charge = 25 if is_cod else 0
                return {"rate": total, "shipping_charge": total, "cod_fee": cod_charge}
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": "No rates found"}
        except Exception as e:
            print(f"Error fetching shipping rates: {e}")
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": str(e)}

    def create_shipment(self, order_data, pickup_location_name=None):
        import os as _os
        url = f"{self.base_url}/api/cmu/create.json"
        payload = {
            "shipments": [order_data],
            "pickup_location": {"name": pickup_location_name or _os.environ.get('DELHIVERY_PICKUP_LOCATION', 'NARI NAKHRE')}
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
