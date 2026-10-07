"""Encrypt probe screenshots in memory before persisting remote artifacts."""
import base64
import json
import os
from pathlib import Path


def encrypt_snapshot(image, public_pem):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    public = serialization.load_pem_public_key(public_pem)
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    encrypted_key = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    ciphertext = AESGCM(key).encrypt(nonce, image, b'douyin-probe-v1')
    return json.dumps({k: base64.b64encode(v).decode() for k, v in
                       {'key': encrypted_key, 'nonce': nonce, 'ciphertext': ciphertext}.items()}).encode()


def capture_if_configured(page):
    path = os.environ.get('PROBE_SCREENSHOT_PUBLIC_KEY')
    if not path:
        return
    public_pem = Path(path).read_bytes()
    # No plaintext screenshot is written to disk or uploaded.
    image = page.screenshot(timeout=15000)
    output = Path('artifacts/private-probe/page.enc')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encrypt_snapshot(image, public_pem))
