"""
web.routes.memory - Blueprint de métricas y consulta de memoria.

Rutas:
    GET /api/memory/stats     -> resumen de la memoria SQLite
    GET /api/memory/locators  -> tasa de acierto por estrategia (template/vision)
    GET /api/memory/recent    -> memoria reciente del agente (arquetipos/comentarios)
    GET /api/memory/raw       -> contenido completo de agents.md
"""

import os

from flask import Blueprint, jsonify, Response

from config import logger
from core.ai.agent import AIAgent
from core.memory.store import memory_store

memory_bp = Blueprint("memory", __name__, url_prefix="/api/memory")


@memory_bp.route("/stats", methods=["GET"])
def memory_stats() -> Response:
    """Resumen de la memoria SQLite (cuentas, interacciones, reflexiones, caché)."""
    try:
        return jsonify({"success": True, "stats": memory_store.summary()})
    except Exception as e:
        logger.error(f"memory_stats failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/locators", methods=["GET"])
def memory_locators() -> Response:
    """Tasa de acierto por estrategia de localización (template/vision)."""
    try:
        return jsonify({"success": True, "locators": memory_store.locator_stats()})
    except Exception as e:
        logger.error(f"memory_locators failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/recent", methods=["GET"])
def memory_recent() -> Response:
    """Memoria reciente del agente: arquetipos, comentarios y elementos visuales."""
    try:
        mem = AIAgent.load_persistent_memory("agents.md", max_entries=10)
        return jsonify({"success": True, "memory": mem})
    except Exception as e:
        logger.error(f"Error reading persistent memory: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@memory_bp.route("/raw", methods=["GET"])
def memory_raw() -> Response:
    """Contenido completo del archivo agents.md."""
    try:
        target = "agents.md"
        if not os.path.exists(target) and os.path.exists("agent.md"):
            target = "agent.md"
        content = ""
        if os.path.exists(target):
            real_p = os.path.realpath(target)
            with open(real_p, "r", encoding="utf-8") as f:
                content = f.read()
        return jsonify({"success": True, "content": content})
    except Exception as e:
        logger.error(f"Error reading raw agents.md: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
