import abc
import json
import requests
from datetime import datetime, timedelta

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
    def verify_pincode(self, pincode, pickup_pincode=None):
        """pickup_pincode is unused by providers that can check a delivery
        pincode independently (e.g. Delhivery), but required by providers
        that can only check serviceability for a specific pickup->delivery
        route (e.g. Shiprocket) -- kept on every provider so callers can
        pass it uniformly without knowing which provider is active."""
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

    def verify_pincode(self, pincode, pickup_pincode=None):
        """
        Check Delhivery pincode serviceability. pickup_pincode is accepted
        for interface parity with ShiprocketProvider but unused -- Delhivery
        can check a delivery pincode independently of any pickup location.
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

    def track_shipment(self, waybill):
        """
        Get the live tracking status for a shipment by waybill number.
        Production endpoint: GET /api/v1/packages/json/?waybill=<waybill>
        """
        if not waybill:
            return {"status": False, "msg": "No waybill provided"}
        if not self.api_token:
            return {"status": False, "msg": "Delhivery API key not configured"}
        url = f"{self.base_url}/api/v1/packages/json/"
        params = {"waybill": waybill}
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            data = response.json()
            shipments = data.get("ShipmentData") or data.get("shipment_data") or []
            if not shipments:
                return {"status": False, "msg": "No tracking data found for this waybill"}
            shipment = shipments[0].get("Shipment") or shipments[0]
            status_block = shipment.get("Status") or {}
            scans = shipment.get("Scans") or []
            scan_list = []
            for s in scans:
                sd = s.get("ScanDetail") or s
                scan_list.append({
                    "status": sd.get("Scan") or sd.get("ScanType"),
                    "location": sd.get("ScannedLocation"),
                    "datetime": sd.get("ScanDateTime"),
                    "instructions": sd.get("Instructions"),
                })
            return {
                "status": True,
                "waybill": waybill,
                "current_status": status_block.get("Status"),
                "status_type": status_block.get("StatusType"),
                "status_datetime": status_block.get("StatusDateTime"),
                "status_location": status_block.get("StatusLocation"),
                "destination": shipment.get("Destination"),
                "origin": shipment.get("Origin"),
                "expected_delivery": shipment.get("ExpectedDeliveryDate") or shipment.get("PromisedDeliveryDate"),
                "scans": scan_list,
            }
        except Exception as e:
            print(f"Delhivery tracking error: {e}")
            return {"status": False, "msg": f"Could not fetch tracking info: {e}"}

    def get_packing_slip(self, waybill):
        """
        Fetch Delhivery's own packing-slip data for a waybill. Delhivery does
        NOT hand back a ready PDF here -- this is JSON that Delhivery's own
        docs say the client must render into a slip layout -- so callers
        still need their own printable template; this just supplies
        Delhivery-sourced fields for it instead of relying only on locally
        stored order data.

        UNVERIFIED against a live response -- Delhivery's docs don't publish
        the exact JSON field names, so this parses defensively (several
        plausible shapes) and logs the raw response when nothing matches, so
        the real shape can be read from production logs and fixed here.
        """
        if not self.api_token:
            return {"status": False, "msg": "Delhivery API key not configured"}
        if not waybill:
            return {"status": False, "msg": "No waybill provided"}
        url = f"{self.base_url}/api/p/packing_slip"
        params = {"wbns": str(waybill), "pdf": "true"}
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=15)
            data = response.json()
            packages = data.get('packages') if isinstance(data, dict) else None
            package = packages[0] if packages and isinstance(packages, list) else None
            if not package:
                print(f"Delhivery packing slip response (no package found): {data}")
                return {"status": False, "msg": "Packing slip not available for this waybill"}
            return {"status": True, "data": package}
        except Exception as e:
            print(f"Delhivery packing slip fetch error: {e}")
            return {"status": False, "msg": str(e)}

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


# --- Shiprocket Provider ---
class ShiprocketProvider(BaseShippingProvider):
    """
    Shiprocket authenticates with email/password to get a short-lived bearer
    token (valid ~10 days per their docs), unlike Delhivery's static token.
    This class accepts an optional cached token + expiry so the caller (which
    owns the DB connection -- this module intentionally has no DB access) can
    reuse a still-valid token instead of logging in on every request, and can
    persist a freshly issued token back to delivery_partner_credentials.

    After construction, check `token_refreshed` -- if True, `token` and
    `token_expires_at` hold a new token the caller should save.

    Pickup pincode is NOT stored on this provider -- pickup location is
    shared warehouse config (WAREHOUSE_PIN etc.), common to all couriers, so
    it must be passed in per-call rather than baked into courier credentials.
    """
    AUTH_URL = "https://apiv2.shiprocket.in/v1/external/auth/login"
    BASE_URL = "https://apiv2.shiprocket.in/v1/external"
    TOKEN_LIFETIME = timedelta(days=9)  # refresh a day early; Shiprocket tokens last ~10 days

    def __init__(self, email, password, cached_token=None, token_expires_at=None):
        self.email = (email or '').strip()
        self.password = password or ''
        self.token = cached_token
        self.token_expires_at = token_expires_at
        self.token_refreshed = False
        self.last_error = None
        self._ensure_token()

    def _ensure_token(self):
        if self.token and self.token_expires_at and self.token_expires_at > datetime.utcnow():
            return
        self._login()

    def _login(self):
        if not self.email or not self.password:
            self.token = None
            self.last_error = "Shiprocket email/password not configured"
            return
        try:
            response = requests.post(
                self.AUTH_URL,
                json={"email": self.email, "password": self.password},
                timeout=15
            )
            data = response.json()
            token = data.get('token')
            if not token:
                self.token = None
                self.last_error = data.get('message') or 'Shiprocket login failed'
                return
            self.token = token
            self.token_expires_at = datetime.utcnow() + self.TOKEN_LIFETIME
            self.token_refreshed = True
        except Exception as e:
            self.token = None
            self.last_error = str(e)
            print(f"Shiprocket login error: {e}")

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _serviceability(self, pickup_pin, d_pin, weight, cod=False):
        if not self.token:
            return {"status": False, "msg": self.last_error or "Shiprocket not authenticated"}
        url = f"{self.BASE_URL}/courier/serviceability/"
        params = {
            "pickup_postcode": str(pickup_pin),
            "delivery_postcode": str(d_pin),
            "weight": str(weight),
            "cod": 1 if cod else 0,
        }
        try:
            response = requests.get(url, params=params, headers=self._headers(), timeout=15)
            data = response.json()
            if data.get('status') != 200:
                return {"status": False, "msg": data.get('message') or 'No rates found'}
            return {"status": True, "data": data.get('data') or {}}
        except Exception as e:
            print(f"Shiprocket serviceability error: {e}")
            return {"status": False, "msg": str(e)}

    def calculate_rates(self, o_pin, d_pin, weight):
        result = self._serviceability(o_pin, d_pin, self._weight_kg(weight))
        if not result.get('status'):
            return {"status": False, "msg": result.get('msg')}
        couriers = result['data'].get('available_courier_companies') or []
        if not couriers:
            return {"status": False, "msg": "No rates found"}
        return {"status": True, "rate": couriers[0].get('rate'), "msg": "OK"}

    @staticmethod
    def _weight_kg(weight_grams):
        """Every caller in this codebase works in grams (see DelhiveryProvider's
        'cgm' param and app.py's cart_weight calc), but Shiprocket's
        serviceability/rate API takes 'weight' in KILOGRAMS -- without this
        conversion a normal ~1kg cart gets quoted as a 1000kg shipment, wildly
        inflating the rate. verify_pincode()'s hardcoded weight=0.5 below is
        already kg (a nominal probe weight), which is what gave this away."""
        return max(float(weight_grams) / 1000.0, 0.1)

    def verify_pincode(self, pincode, pickup_pincode=None):
        """
        Shiprocket has no standalone pincode-only lookup like Delhivery --
        serviceability always needs a pickup pincode + nominal weight.
        Caller should pass the shared warehouse pickup pincode explicitly;
        without it this cannot check serviceability.
        """
        if not pickup_pincode:
            return {"status": False, "serviceable": False, "msg": "Pickup pincode required for Shiprocket serviceability check"}
        result = self._serviceability(pickup_pincode, pincode, weight=0.5)
        if not result.get('status'):
            return {"status": False, "serviceable": False, "msg": result.get('msg')}
        couriers = result['data'].get('available_courier_companies') or []
        if not couriers:
            return {"status": False, "serviceable": False, "msg": "Location not serviceable"}
        return {"status": True, "serviceable": True, "msg": "Serviceable"}

    def get_rates(self, o_pin, d_pin, weight, mode="Prepaid"):
        is_cod = (mode == "COD")
        result = self._serviceability(o_pin, d_pin, self._weight_kg(weight), cod=is_cod)
        if not result.get('status'):
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": result.get('msg')}
        couriers = result['data'].get('available_courier_companies') or []
        if not couriers:
            return {"rate": 0, "shipping_charge": 0, "cod_fee": 0, "msg": "No rates found"}
        cheapest = min(couriers, key=lambda c: c.get('rate', float('inf')))
        total = cheapest.get('rate', 0)
        cod_charge = cheapest.get('cod_charges', 0) if is_cod else 0
        # etd ("estimated time of delivery") comes back as a date string like
        # "Aug 15, 2026" from Shiprocket -- passed through as-is, no parsing.
        return {"rate": total, "shipping_charge": total, "cod_fee": cod_charge, "eta": cheapest.get('etd')}

    def create_shipment(self, order_data):
        """order_data must already be shaped as a Shiprocket adhoc order payload
        (order_id, order_date, pickup_location, billing_* fields, order_items,
        payment_method, sub_total, weight/dimensions -- see Shiprocket API docs).
        This method does not reshape it, matching how little validation the
        Delhivery adapter does either -- the caller builds the payload."""
        if not self.token:
            return {"status": False, "msg": self.last_error or "Shiprocket not authenticated"}
        url = f"{self.BASE_URL}/orders/create/adhoc"
        try:
            response = requests.post(url, json=order_data, headers=self._headers(), timeout=20)
            data = response.json()
            waybill = data.get('awb_code') or None
            return {
                "status": True if data.get('order_id') else False,
                "waybill": waybill,
                "shiprocket_order_id": data.get('order_id'),
                "shipment_id": data.get('shipment_id'),
                "msg": data.get('message') or 'Order creation response received'
            }
        except Exception as e:
            print(f"Shiprocket order creation error: {e}")
            return {"status": False, "msg": f"Order creation failed: {e}"}

    def get_label(self, shipment_id):
        """
        Fetch Shiprocket's own ready-to-print label PDF for an already-
        created shipment. Needs Shiprocket's internal numeric shipment_id
        (NOT the AWB/waybill) -- returned by create_shipment()/order
        creation, must be persisted by the caller.

        UNVERIFIED against a live response -- 'label_url' is the commonly
        documented field name, checked at both the top level and nested
        under 'response' in case Shiprocket wraps it; if neither matches,
        the raw response is logged so the real shape can be read from
        production logs and fixed here.
        """
        if not self.token:
            return {"status": False, "msg": self.last_error or "Shiprocket not authenticated"}
        if not shipment_id:
            return {"status": False, "msg": "No Shiprocket shipment_id stored for this order"}
        url = f"{self.BASE_URL}/courier/generate/label"
        try:
            response = requests.post(url, json={"shipment_id": [shipment_id]}, headers=self._headers(), timeout=20)
            data = response.json()
            label_url = data.get('label_url') or (data.get('response') or {}).get('label_url')
            if not label_url:
                print(f"Shiprocket label response (no label_url found): {data}")
                return {"status": False, "msg": data.get('message') or "Label not generated yet"}
            return {"status": True, "label_url": label_url}
        except Exception as e:
            print(f"Shiprocket label fetch error: {e}")
            return {"status": False, "msg": str(e)}

    def track_shipment(self, waybill):
        if not waybill:
            return {"status": False, "msg": "No waybill provided"}
        if not self.token:
            return {"status": False, "msg": self.last_error or "Shiprocket not authenticated"}
        url = f"{self.BASE_URL}/courier/track/awb/{waybill}"
        try:
            response = requests.get(url, headers=self._headers(), timeout=15)
            data = response.json()
            tracking_data = data.get('tracking_data') or {}
            activities = tracking_data.get('shipment_track_activities') or []
            scan_list = [{
                "status": a.get('activity'),
                "location": a.get('location'),
                "datetime": a.get('date'),
                "instructions": a.get('activity'),
            } for a in activities]
            shipment_track = (tracking_data.get('shipment_track') or [{}])[0]
            return {
                "status": True,
                "waybill": waybill,
                "current_status": tracking_data.get('shipment_status') or shipment_track.get('current_status'),
                "status_type": None,
                "status_datetime": scan_list[0]['datetime'] if scan_list else None,
                "status_location": scan_list[0]['location'] if scan_list else None,
                "destination": shipment_track.get('destination'),
                "origin": shipment_track.get('origin'),
                "expected_delivery": shipment_track.get('edd'),
                "scans": scan_list,
            }
        except Exception as e:
            print(f"Shiprocket tracking error: {e}")
            return {"status": False, "msg": f"Could not fetch tracking info: {e}"}


# --- Mock Provider (used when SHIPPING_PROVIDER=mock, e.g. local dev without API key) ---
class MockShippingProvider(BaseShippingProvider):
    def calculate_rates(self, o_pin, d_pin, weight):
        return {"status": True, "rate": 50, "msg": "Mock shipping rate"}

    def verify_pincode(self, pincode, pickup_pincode=None):
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

    def get_label(self, shipment_id):
        return {"status": False, "msg": "Mock provider has no real label to fetch"}

    def get_packing_slip(self, waybill):
        return {"status": False, "msg": "Mock provider has no real packing slip to fetch"}

    def track_shipment(self, waybill):
        return {
            "status": True,
            "waybill": waybill,
            "current_status": "In Transit",
            "status_type": "UD",
            "status_datetime": "",
            "status_location": "Mock Hub",
            "destination": "Mock City",
            "origin": "Mock Warehouse",
            "expected_delivery": "",
            "scans": [
                {"status": "Pickup Scheduled", "location": "Mock Warehouse", "datetime": "", "instructions": ""},
                {"status": "In Transit", "location": "Mock Hub", "datetime": "", "instructions": ""},
            ],
        }


# --- Factory ---
def get_shipping_provider(name, api_token=None, email=None, password=None,
                           cached_token=None, token_expires_at=None):
    if name == "delhivery":
        return DelhiveryProvider(api_token)
    elif name == "shiprocket":
        return ShiprocketProvider(email, password, cached_token=cached_token, token_expires_at=token_expires_at)
    elif name == "mock":
        return MockShippingProvider()
    else:
        raise ValueError(f"Unknown shipping provider: {name}")
