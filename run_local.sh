#!/bin/bash
set -e

CERT_DIR=".certs"
CERT_FILE="$CERT_DIR/localhost+1.pem"
KEY_FILE="$CERT_DIR/localhost+1-key.pem"

# Generate certs if they don't exist
if [ ! -f "$CERT_FILE" ]; then
    echo "Generating local HTTPS certificates..."

    if ! command -v mkcert &> /dev/null; then
        echo "Installing mkcert..."
        brew install mkcert
        mkcert -install
    fi

    mkdir -p "$CERT_DIR"
    cd "$CERT_DIR"
    mkcert localhost 127.0.0.1
    cd ..
    echo "Certificates created in $CERT_DIR/"
fi

echo "Starting local HTTPS server on https://localhost:8000"
uv run uvicorn src.main:app --port 8000 --reload \
    --ssl-keyfile "$KEY_FILE" \
    --ssl-certfile "$CERT_FILE"
