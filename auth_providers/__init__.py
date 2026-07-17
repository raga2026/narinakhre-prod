from .base import BaseAuthProvider
from .google import GoogleAuthProvider

_providers = {}

_PROVIDER_CLASSES = {
    'google': GoogleAuthProvider,
}


def init_provider(name, flask_app):
    """Instantiate and cache an auth provider by name. Call once at app
    startup for each provider you plan to use (e.g. in app.py)."""
    provider_cls = _PROVIDER_CLASSES.get(name)
    if provider_cls is None:
        raise ValueError(f'Unknown auth provider: {name}')
    _providers[name] = provider_cls(flask_app)
    return _providers[name]


def get_auth_provider(name):
    provider = _providers.get(name)
    if provider is None:
        raise RuntimeError(f'Auth provider "{name}" was not initialized. Call init_provider() at startup.')
    return provider
