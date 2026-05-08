#!/usr/bin/env python3
"""
Dump the TLS certificate chain presented by a Trino (or any TLS) host to a PEM file.

Use when Python fails with CERTIFICATE_VERIFY_FAILED: point TRINO_CA_BUNDLE at the
generated file (or merge this PEM with your org root bundle).

Requires Python 3.13+ for full chain (ssl.SSLSocket.getpeercert_chain). On older
versions only the leaf certificate is written and a warning is printed.

Examples::

    cd "Travel Planner Prototype"
    python scripts/trino_dump_server_chain.py egap-adhoc-trino.egdp-analytics.aws.away.black 8443 -o trino-chain.pem

Then in .env::

    TRINO_CA_BUNDLE=C:\\...\\trino-chain.pem
    TRINO_TLS_VERIFY=1
"""

from __future__ import annotations

import argparse
import socket
import ssl
import sys
from pathlib import Path


def _write_pem(path: Path, der_certs: list[bytes]) -> None:
    chunks: list[str] = []
    for der in der_certs:
        chunks.append(ssl.DER_cert_to_PEM_cert(der))
    path.write_text("".join(chunks), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump server TLS chain to PEM for TRINO_CA_BUNDLE")
    parser.add_argument("host", help="Hostname (e.g. egap-adhoc-trino....away.black)")
    parser.add_argument("port", type=int, nargs="?", default=8443, help="TLS port (default 8443)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("trino-server-chain.pem"),
        help="Output PEM path (default: ./trino-server-chain.pem)",
    )
    args = parser.parse_args()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((args.host, args.port), timeout=60) as raw:
            with ctx.wrap_socket(raw, server_hostname=args.host) as sock:
                chain: list[bytes] = []
                getter = getattr(sock, "getpeercert_chain", None)
                if callable(getter):
                    chain = list(getter() or [])
                if not chain:
                    leaf = sock.getpeercert(binary_form=True)
                    if leaf:
                        chain = [leaf]
                    print(
                        "Warning: getpeercert_chain() unavailable or empty; only leaf cert saved. "
                        "Use Python 3.13+ or OpenSSL (see script docstring).",
                        file=sys.stderr,
                    )
    except OSError as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    _write_pem(args.output, chain)
    print(f"Wrote {len(chain)} certificate(s) to {args.output.resolve()}")
    print("Set TRINO_CA_BUNDLE to this path and TRINO_TLS_VERIFY=1, then rerun trino_smoke_basic.py")


if __name__ == "__main__":
    main()
