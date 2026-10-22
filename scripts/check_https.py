"""Check IIS over local TLS with the real SNI/Host, even before DNS is provisioned."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import socket
import ssl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("hostname")
    parser.add_argument("certificate", type=Path)
    args = parser.parse_args()
    certificate = args.certificate.read_bytes()
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
    for path in ("/healthz", "/api/me", "/"):
        with socket.create_connection(("127.0.0.1", 443), timeout=10) as tcp:
            with context.wrap_socket(tcp, server_hostname=args.hostname) as tls:
                if hashlib.sha256(tls.getpeercert(binary_form=True)).digest() != hashlib.sha256(certificate).digest():
                    raise RuntimeError("IIS returned a different certificate")
                tls.sendall((f"GET {path} HTTP/1.1\r\nHost: {args.hostname}\r\nConnection: close\r\n\r\n").encode("ascii"))
                response = http.client.HTTPResponse(tls)
                response.begin()
                body = response.read()
                if response.status != 200:
                    raise RuntimeError(f"HTTPS {path}: HTTP {response.status}")
                if path == "/healthz" and json.loads(body) != {"status": "ok"}:
                    raise RuntimeError("HTTPS database health check failed")
                if path == "/api/me" and "secure" not in response.getheader("Set-Cookie", "").lower():
                    raise RuntimeError("Session cookie must be Secure")
    print("IIS HTTPS OK: certificate, hostname, proxy, database, login page and Secure session cookie")


if __name__ == "__main__":
    main()
