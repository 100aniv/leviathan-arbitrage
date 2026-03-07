#!/usr/bin/env bash
# =============================================================================
# LEVIATHAN — generate-certs.sh
# Generates a self-signed TLS certificate for local development.
# Output: infra/nginx/certs/server.key  and  infra/nginx/certs/server.crt
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="${SCRIPT_DIR}/certs"

DAYS=825          # ~2 years; browsers cap trust at 825 days for self-signed
COUNTRY="US"
STATE="California"
CITY="San Francisco"
ORG="LEVIATHAN Dev"
CN="localhost"

# Subject Alternative Names — add more IPs/hostnames as needed
SAN="subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1,IP:::1"

mkdir -p "${CERTS_DIR}"

echo "[generate-certs] Generating self-signed TLS certificate..."
echo "  Output dir : ${CERTS_DIR}"
echo "  Valid for  : ${DAYS} days"
echo "  CN         : ${CN}"

openssl req \
    -x509 \
    -newkey rsa:4096 \
    -sha256 \
    -days "${DAYS}" \
    -nodes \
    -keyout "${CERTS_DIR}/server.key" \
    -out    "${CERTS_DIR}/server.crt" \
    -subj   "/C=${COUNTRY}/ST=${STATE}/L=${CITY}/O=${ORG}/CN=${CN}" \
    -addext "${SAN}"

chmod 600 "${CERTS_DIR}/server.key"
chmod 644 "${CERTS_DIR}/server.crt"

echo ""
echo "[generate-certs] Done."
echo "  Key  : ${CERTS_DIR}/server.key"
echo "  Cert : ${CERTS_DIR}/server.crt"
echo ""
echo "  To trust this cert on macOS:"
echo "    sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ${CERTS_DIR}/server.crt"
echo ""
echo "  To trust this cert on Linux (Ubuntu/Debian):"
echo "    sudo cp ${CERTS_DIR}/server.crt /usr/local/share/ca-certificates/leviathan-dev.crt"
echo "    sudo update-ca-certificates"
