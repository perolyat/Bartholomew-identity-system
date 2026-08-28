#!/usr/bin/env bash
# Generate a self-signed certificate for LOCAL DEVELOPMENT ONLY.
#
# A self-signed certificate encrypts the connection but proves nothing about
# who is on the other end. That is an acceptable trade for a container
# published to your own loopback interface; it is not acceptable for anything
# an Alpha participant connects to. Use real material there.
set -euo pipefail

OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/certs"
mkdir -p "$OUT_DIR"

if [[ -f "$OUT_DIR/cert.pem" && "${FORCE:-0}" != "1" ]]; then
  echo "Certificate already exists at $OUT_DIR/cert.pem (FORCE=1 to replace)."
  exit 0
fi

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$OUT_DIR/key.pem" \
  -out "$OUT_DIR/cert.pem" \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$OUT_DIR/key.pem"
chmod 644 "$OUT_DIR/cert.pem"

echo "Wrote $OUT_DIR/cert.pem and $OUT_DIR/key.pem (self-signed, local development only)."
