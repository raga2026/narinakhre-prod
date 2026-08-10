import json
import os

from cryptography.fernet import Fernet


def _get_fernet():
    key = os.environ.get('CREDENTIAL_ENCRYPTION_KEY')
    if not key:
        raise RuntimeError(
            'CREDENTIAL_ENCRYPTION_KEY is not set. Generate one with '
            'Fernet.generate_key() and set it in the environment before '
            'storing or reading courier credentials.'
        )
    return Fernet(key.encode('utf-8') if isinstance(key, str) else key)


def encrypt_credentials(credentials):
    """Encrypt a dict of courier credentials into an opaque string for storage."""
    fernet = _get_fernet()
    payload = json.dumps(credentials).encode('utf-8')
    return fernet.encrypt(payload).decode('utf-8')


def decrypt_credentials(encrypted):
    """Decrypt a stored string back into the original credentials dict."""
    if not encrypted:
        return {}
    fernet = _get_fernet()
    payload = fernet.decrypt(encrypted.encode('utf-8'))
    return json.loads(payload.decode('utf-8'))
