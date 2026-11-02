"""Generate a self-signed TLS cert for the phone-capture HTTPS listener.

    python -m tools.make_cert          # writes data/cert.pem + data/key.pem

Mobile browsers require a secure context (HTTPS) for camera access and SubtleCrypto
(the sha256 the wire protocol needs), so the phone talks to the laptop over HTTPS.
This makes a self-signed cert whose SAN includes the laptop's current LAN IP,
127.0.0.1, and localhost. The phone will still show a one-time "not trusted"
warning (tap Advanced -> proceed) — expected for a self-signed demo cert.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))     # no packet sent; just picks the egress iface
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    cert = DATA / "cert.pem"
    key = DATA / "key.pem"
    ip = lan_ip()

    cfg = f"""
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = glasses-local
[v3]
subjectAltName = @alt
[alt]
IP.1 = {ip}
IP.2 = 127.0.0.1
DNS.1 = localhost
"""
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as f:
        f.write(cfg)
        cfg_path = f.name

    cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-keyout", str(key), "-out", str(cert), "-days", "825", "-config", cfg_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        print("openssl not found — install it or create data/cert.pem + data/key.pem manually.",
              file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print("openssl failed:\n" + e.stderr, file=sys.stderr)
        return 1

    print(f"wrote {cert}")
    print(f"wrote {key}")
    print(f"\nPhone: open  https://{ip}:8443/mobile   (accept the self-signed warning).")
    print("Laptop desktop stays on http://127.0.0.1:8777 as before.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
