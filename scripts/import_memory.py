#!/usr/bin/env python3
"""
scripts/import_memory.py - Importa accounts_memory.json a SQLite (data/agent_memory.db).

Uso:
    python3 scripts/import_memory.py

Es idempotente: las cuentas y dispositivos no se duplican (claves UNIQUE).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.memory.store import MemoryStore, DEFAULT_DB_PATH  # noqa: E402


def main() -> None:
    store = MemoryStore(DEFAULT_DB_PATH)
    n = store.import_accounts_memory_json()
    summary = store.summary()
    print(f"Importación completada: {n} dispositivo(s) -> {DEFAULT_DB_PATH}")
    print("Resumen:", summary)


if __name__ == "__main__":
    main()
