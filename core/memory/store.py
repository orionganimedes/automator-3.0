"""
core.memory.store - Almacén SQLite de memoria del agente (modo WAL).

Sustituye el JSON monolítico (accounts_memory.json) y el log de texto libre
(agents.md) por un esquema consultable, con caché de LLM y estadísticas de
localizadores para guiar las estrategias de automatización.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
)
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "agent_memory.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    serial TEXT PRIMARY KEY,
    model TEXT,
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_serial TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'instagram',
    account_index INTEGER,
    username TEXT,
    label TEXT,
    last_used TEXT,
    UNIQUE(device_serial, kind, username)
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    post_url TEXT,
    action TEXT,
    comment_text TEXT,
    archetype TEXT,
    success INTEGER DEFAULT 1,
    latency_ms INTEGER,
    created_at TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);
CREATE TABLE IF NOT EXISTS agent_reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT,
    caption TEXT,
    archetype TEXT,
    visual_element TEXT,
    comment TEXT,
    thought TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    step INTEGER,
    action TEXT,
    status TEXT,
    duration_ms INTEGER,
    created_at TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS locators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    query TEXT NOT NULL,
    hits INTEGER DEFAULT 0,
    misses INTEGER DEFAULT 0,
    last_used TEXT,
    UNIQUE(strategy, query)
);
CREATE TABLE IF NOT EXISTS template_calib (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_serial TEXT,
    template TEXT,
    scale REAL,
    offset_x INTEGER,
    offset_y INTEGER,
    success INTEGER,
    UNIQUE(device_serial, template)
);
CREATE TABLE IF NOT EXISTS llm_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    response TEXT,
    created_at REAL,
    ttl REAL DEFAULT 86400,
    UNIQUE(model, prompt_hash)
);
CREATE INDEX IF NOT EXISTS idx_interactions_post ON interactions(post_url);
CREATE INDEX IF NOT EXISTS idx_interactions_account ON interactions(account_id);
CREATE INDEX IF NOT EXISTS idx_reflections_created ON agent_reflections(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_cache_lookup ON llm_cache(model, prompt_hash);
"""


class MemoryStore:
    """Almacén SQLite con conexión por hilo y caché de LLM."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._local = threading.local()
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # --- Caché de LLM (llm_cache) ---
    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8", errors="ignore")).hexdigest()

    def llm_get(self, model: str, prompt: str) -> Optional[str]:
        ph = self._hash_prompt(prompt)
        conn = self._connect()
        row = conn.execute(
            "SELECT response, created_at, ttl FROM llm_cache WHERE model=? AND prompt_hash=?",
            (model, ph),
        ).fetchone()
        if row is None:
            return None
        if time.time() - row["created_at"] > row["ttl"]:
            conn.execute("DELETE FROM llm_cache WHERE model=? AND prompt_hash=?", (model, ph))
            conn.commit()
            return None
        return row["response"]

    def llm_set(self, model: str, prompt: str, response: str, ttl: float = 86400.0) -> None:
        ph = self._hash_prompt(prompt)
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache (model, prompt_hash, response, created_at, ttl) VALUES (?, ?, ?, ?, ?)",
            (model, ph, response, time.time(), ttl),
        )
        conn.commit()

    def clear_cache(self) -> None:
        """Limpia la caché volátil: llm_cache, locators y template_calib."""
        conn = self._connect()
        conn.execute("DELETE FROM llm_cache")
        conn.execute("DELETE FROM locators")
        conn.execute("DELETE FROM template_calib")
        conn.commit()

    # --- Localizadores (locators) ---
    def record_locator(self, strategy: str, query: str, hit: bool) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO locators (strategy, query, hits, misses, last_used)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(strategy, query) DO UPDATE SET
                 hits = hits + excluded.hits,
                 misses = misses + excluded.misses,
                 last_used = excluded.last_used""",
            (strategy, query, 1 if hit else 0, 0 if hit else 1, self._now_iso()),
        )
        conn.commit()

    def locator_stats(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT strategy, query, hits, misses,
                      CAST(hits AS REAL) / (hits + misses) AS hit_rate
               FROM locators ORDER BY strategy, hit_rate DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Reflexiones del agente (agent_reflections) ---
    def record_reflection(
        self,
        account: str = "",
        caption: str = "",
        archetype: str = "",
        visual_element: str = "",
        comment: str = "",
        thought: str = "",
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            """INSERT INTO agent_reflections
               (account, caption, archetype, visual_element, comment, thought, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (account, caption, archetype, visual_element, comment, thought, self._now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)

    def recent_reflections(self, limit: int = 8) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM agent_reflections ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Interacciones (interactions) ---
    def record_interaction(
        self,
        account_id: int,
        post_url: str = "",
        action: str = "",
        comment_text: str = "",
        archetype: str = "",
        success: bool = True,
        latency_ms: Optional[int] = None,
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            """INSERT INTO interactions
               (account_id, post_url, action, comment_text, archetype, success, latency_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, post_url, action, comment_text, archetype, 1 if success else 0, latency_ms, self._now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)

    # --- Cuentas ---
    def register_device(self, serial: str, model: str = "Android Device") -> None:
        conn = self._connect()
        now = self._now_iso()
        conn.execute(
            """INSERT INTO devices (serial, model, first_seen, last_seen) VALUES (?, ?, ?, ?)
               ON CONFLICT(serial) DO UPDATE SET model=excluded.model, last_seen=excluded.last_seen""",
            (serial, model, now, now),
        )
        conn.commit()

    def register_account(
        self,
        serial: str,
        kind: str,
        account_index: int,
        username: str,
        label: str = "",
        last_used: Optional[str] = None,
    ) -> int:
        conn = self._connect()
        conn.execute(
            """INSERT INTO accounts (device_serial, kind, account_index, username, label, last_used)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_serial, kind, username) DO UPDATE SET
                 account_index=excluded.account_index,
                 label=excluded.label,
                 last_used=COALESCE(excluded.last_used, accounts.last_used)""",
            (serial, kind, account_index, username, label or username, last_used),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM accounts WHERE device_serial=? AND kind=? AND username=?",
            (serial, kind, username),
        ).fetchone()
        return int(row["id"]) if row else 0

    def get_accounts(self, serial: str, kind: str = "instagram") -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM accounts WHERE device_serial=? AND kind=? ORDER BY account_index",
            (serial, kind),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Migración desde accounts_memory.json ---
    def import_accounts_memory_json(self, path: Optional[str] = None) -> int:
        """Importa el JSON antiguo a SQLite. Idempotente (no duplica)."""
        if path is None:
            path = os.path.join(DATA_DIR, "accounts_memory.json")
        if not os.path.exists(path):
            return 0
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        devices = data.get("devices", {})
        conn = self._connect()

        def _has_interaction(aid: int, post_url: str, act: str) -> bool:
            if not post_url:
                return False
            r = conn.execute(
                "SELECT 1 FROM interactions WHERE account_id=? AND post_url=? AND action=? LIMIT 1",
                (aid, post_url, act),
            ).fetchone()
            return r is not None

        for serial, dev in devices.items():
            model = dev.get("model") or "Android Device"
            self.register_device(serial, model)

            for acc in dev.get("accounts", []):
                aid = self.register_account(
                    serial, "instagram", acc.get("index", 0),
                    acc.get("username") or f"cuenta_{acc.get('index', 0) + 1}",
                    acc.get("label") or "",
                    acc.get("last_used"),
                )
                if not aid:
                    continue
                for url in acc.get("used_on_posts", []):
                    if not _has_interaction(aid, url, "like_and_comment"):
                        self.record_interaction(aid, url, action="like_and_comment")
                for h in acc.get("interaction_history", []):
                    u = h.get("post_url", "")
                    act = h.get("action", "like_and_comment")
                    if not _has_interaction(aid, u, act):
                        self.record_interaction(
                            aid, u, act,
                            h.get("comment", ""), h.get("archetype", ""),
                        )

            for acc in dev.get("facebook_accounts", []):
                aid = self.register_account(
                    serial, "facebook", acc.get("index", 0),
                    acc.get("username") or acc.get("label") or f"cuenta_{acc.get('index', 0) + 1}",
                    acc.get("label") or "",
                    acc.get("last_used"),
                )
                if not aid:
                    continue
                for url in acc.get("used_on_posts", []):
                    if not _has_interaction(aid, url, "facebook_engagement"):
                        self.record_interaction(aid, url, action="facebook_engagement")
                for h in acc.get("comments_history", []):
                    u = h.get("url", "")
                    if not _has_interaction(aid, u, "facebook_comment"):
                        self.record_interaction(
                            aid, u, "facebook_comment",
                            h.get("comment", ""),
                        )

        return len(devices)

    def summary(self) -> Dict[str, Any]:
        conn = self._connect()
        def count(table: str) -> int:
            return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        return {
            "devices": count("devices"),
            "accounts": count("accounts"),
            "interactions": count("interactions"),
            "reflections": count("agent_reflections"),
            "locators": count("locators"),
            "llm_cache": count("llm_cache"),
        }


# Instancia global accesible desde el resto del proyecto
memory_store = MemoryStore()
