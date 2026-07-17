import abc


class BaseAuthProvider(abc.ABC):
    """Common interface every OAuth provider (Google, Facebook, ...) implements,
    mirroring the BaseShippingProvider pattern in utils/shipping_manager.py so
    route handlers never depend on a specific provider or library."""

    @abc.abstractmethod
    def get_auth_url(self, redirect_uri):
        """Build the provider's consent-screen URL for this redirect_uri and
        persist whatever CSRF state the provider needs to verify at callback
        time. Returns the URL as a string."""

    @abc.abstractmethod
    def exchange_code(self):
        """Validate the callback request's state, then exchange the
        authorization code for tokens. Returns a provider-specific token
        dict. Raises on any validation/exchange failure -- callers should
        catch and redirect to an error state rather than let it propagate."""

    @abc.abstractmethod
    def get_user_info(self, token):
        """Given the token dict from exchange_code(), return a normalized
        dict with at least: sub, email, name, picture."""
