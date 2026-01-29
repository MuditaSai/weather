import time
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from config import KALSHI_API_KEY, KALSHI_PRIVATE_KEY_PATH


def load_private_key():
    """Load RSA private key from PEM file."""
    with open(KALSHI_PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def sign_request(method, path, timestamp):
    """
    Sign a request using RSA-PSS with SHA-256.

    Args:
        method: HTTP method (GET, POST, DELETE)
        path: Request path WITHOUT query parameters
        timestamp: Timestamp in milliseconds

    Returns:
        Base64-encoded signature
    """
    private_key = load_private_key()
    message = f"{timestamp}{method}{path}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode()


def get_auth_headers(method, path):
    """
    Generate authentication headers for Kalshi API.

    Args:
        method: HTTP method (GET, POST, DELETE)
        path: Request path WITHOUT query parameters (e.g., /trade-api/v2/portfolio/orders)

    Returns:
        Dictionary of headers to include in request
    """
    timestamp = str(int(time.time() * 1000))
    signature = sign_request(method, path, timestamp)
    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "Content-Type": "application/json"
    }
