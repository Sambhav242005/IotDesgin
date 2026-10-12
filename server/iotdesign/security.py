import hashlib
import ipaddress
import json
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def lan_addresses():
    import psutil

    addresses = []
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family == socket.AF_INET:
                address = ipaddress.ip_address(entry.address)
                if not address.is_loopback and not address.is_link_local:
                    addresses.append(entry.address)
    return sorted(set(addresses))


def ensure_certificate(data):
    cert_dir = data / "tls"
    cert_dir.mkdir(exist_ok=True)
    cert_path, key_path = cert_dir / "server.pem", cert_dir / "server-key.pem"
    if not cert_path.exists() or not key_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "IoTDesign local server")])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=5))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName([
                    x509.DNSName("localhost"), x509.DNSName(socket.gethostname()),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    *[x509.IPAddress(ipaddress.ip_address(ip)) for ip in lan_addresses()],
                ]), critical=False).sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return cert_path, key_path, fingerprint


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Pairing:
    def __init__(self):
        self.code = ""
        self.expires = 0.0
        self.failures = {}
        self.rotate()

    def rotate(self):
        self.code = f"{secrets.randbelow(100000000):08d}"
        self.expires = time.monotonic() + 600

    def check(self, host, code):
        cutoff, attempts = self.failures.get(host, (time.monotonic() + 60, 0))
        if time.monotonic() > cutoff:
            cutoff, attempts = time.monotonic() + 60, 0
        if attempts >= 5:
            return "rate_limited"
        if time.monotonic() > self.expires or not secrets.compare_digest(code, self.code):
            self.failures[host] = (cutoff, attempts + 1)
            return "invalid_pairing_code"
        return None

    def payload(self, address, server_id, fingerprint):
        return json.dumps({"type": "iotdesign-pair", "version": 1, "address": address,
                           "server_id": server_id, "fingerprint": fingerprint, "code": self.code})
