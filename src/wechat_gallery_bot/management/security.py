import datetime
import hashlib
import ipaddress
import secrets
from pathlib import Path

from .common import atomic_json, private_ipv4


def create_identity(root: Path, host: str, port: int) -> dict:
    """Generate TLS identity locally. Never print or log token/private key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    host = private_ipv4(host)
    root.mkdir(parents=True, exist_ok=True)
    key_path, cert_path = root / "server-key.pem", root / "server-cert.pem"
    if key_path.exists() and cert_path.exists():
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Tianyi guest")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                       .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                       .not_valid_after(now + datetime.timedelta(days=365))
                       .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), critical=False)
                       .sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    token_path = root / "access-token"
    if not token_path.exists():
        token_path.write_text(secrets.token_hex(32), encoding="ascii")
    return {"host": host, "port": port, "token": token_path.read_text(encoding="ascii").strip(),
            "fingerprint": hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()}
