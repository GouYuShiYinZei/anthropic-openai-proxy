#!/bin/bash
# Anthropic -> OpenAI Protocol Proxy (Linux/macOS)
# Usage:
#   ./start_proxy.sh
#   ./start_proxy.sh --host 0.0.0.0 --port 8080
#   ./start_proxy.sh --upstream https://api.openai.com/v1/chat/completions

cd "$(dirname "$0")"

PYTHON=""
for py in python3 python python3.12 python3.11 python3.10 python3.9 python3.8 python3.7; do
    if command -v "$py" >/dev/null 2>&1; then
        PYTHON="$py"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python 3.7+ not found."
    echo "  Install: apt install python3  (Debian/Ubuntu)"
    echo "          brew install python3  (macOS)"
    exit 1
fi

exec "$PYTHON" anthropic_openai_proxy.py "$@"
