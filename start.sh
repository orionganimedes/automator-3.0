#!/bin/bash
cd "$(dirname "$0")"
echo "=== AUTOMATOR 2.0: Iniciando Servidor Python ==="
fuser -k 5000/tcp 2>/dev/null || true
sleep 0.5
exec .venv/bin/python3 app.py
