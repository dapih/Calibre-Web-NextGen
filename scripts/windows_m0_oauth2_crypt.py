"""Experimental oauth2client signing backend using cryptography.

Installed into a locally repaired oauth2client wheel; the upstream public
class names are retained because oauth2client checks their identities.
"""

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from oauth2client import _helpers


def _private_key(key, password):
    key = _helpers._to_bytes(key)
    pem = _helpers._parse_pem_key(key)
    if pem:
        return serialization.load_pem_private_key(pem, password=None)
    pkey, _, _ = pkcs12.load_key_and_certificates(
        key, _helpers._to_bytes(password, encoding='utf-8')
    )
    if pkey is None:
        raise ValueError('PKCS#12 archive contains no private key')
    return pkey


class OpenSSLSigner:
    def __init__(self, pkey):
        if not isinstance(pkey, rsa.RSAPrivateKey):
            raise ValueError('RSA private key required')
        self._key = pkey

    def sign(self, message):
        message = _helpers._to_bytes(message, encoding='utf-8')
        return self._key.sign(message, padding.PKCS1v15(), hashes.SHA256())

    @staticmethod
    def from_string(key, password=b'notasecret'):
        return OpenSSLSigner(_private_key(key, password))


class OpenSSLVerifier:
    def __init__(self, pubkey):
        if not isinstance(pubkey, rsa.RSAPublicKey):
            raise ValueError('RSA public key required')
        self._pubkey = pubkey

    def verify(self, message, signature):
        message = _helpers._to_bytes(message, encoding='utf-8')
        signature = _helpers._to_bytes(signature, encoding='utf-8')
        try:
            self._pubkey.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            return False
        return True

    @staticmethod
    def from_string(key_pem, is_x509_cert):
        key_pem = _helpers._to_bytes(key_pem)
        if is_x509_cert:
            pubkey = x509.load_pem_x509_certificate(key_pem).public_key()
        else:
            pubkey = serialization.load_pem_private_key(key_pem, password=None).public_key()
        return OpenSSLVerifier(pubkey)


def pkcs12_key_as_pem(private_key_bytes, private_key_password):
    key = _private_key(private_key_bytes, private_key_password)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
