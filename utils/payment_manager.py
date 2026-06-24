import abc

class BasePaymentProvider(abc.ABC):
    @abc.abstractmethod
    def initialize_payment(self, amount):
        pass

    @abc.abstractmethod
    def verify_signature(self, data):
        pass

    @abc.abstractmethod
    def handle_callback(self):
        pass

class CODPaymentProvider(BasePaymentProvider):
    def initialize_payment(self, amount):
        return {"status": True, "msg": "COD selected, no payment required."}

    def verify_signature(self, data):
        return True

    def handle_callback(self):
        return {"status": True, "msg": "COD callback handled."}

class RazorpayProvider(BasePaymentProvider):
    def initialize_payment(self, amount):
        # Placeholder for Razorpay integration
        return {"status": False, "msg": "Razorpay not implemented."}

    def verify_signature(self, data):
        return False

    def handle_callback(self):
        return {"status": False, "msg": "Razorpay callback not implemented."}

def get_payment_provider(app_config):
    name = app_config.get('PAYMENT_PROVIDER', 'cod')
    if name == "cod":
        return CODPaymentProvider()
    elif name == "razorpay":
        return RazorpayProvider()
    else:
        raise ValueError(f"Unknown payment provider: {name}")
