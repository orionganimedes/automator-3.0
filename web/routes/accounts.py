"""
web.routes.accounts - Blueprint para gestión, escaneo y cambio de cuentas multi-plataforma.

Rutas:
    GET  /api/accounts/<platform>         -> lista de cuentas desde memoria
    POST /api/accounts/<platform>/switch  -> cambia a la cuenta indicada
    GET, POST /api/accounts/scan          -> escanea y cuenta las cuentas físicas del dispositivo
    GET  /api/accounts/memory             -> retorna la memoria de cuentas del dispositivo
"""

import time
from typing import Any, Dict

from flask import Blueprint, jsonify, request, Response

from config import logger
from core.adb.controller import get_controller
from core.memory.accounts import accounts_memory
from web.events import broadcast_event

accounts_bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")


@accounts_bp.route("/<any(instagram, facebook):platform>", methods=["GET"])
def api_accounts_widget_list(platform: str) -> Response:
    """Lista las cuentas conocidas (desde memoria) para el dispositivo actual o en caché."""
    serial = ""
    summary: Dict[str, Any] = {}
    try:
        ctrl = get_controller()
        if ctrl.is_device_connected():
            serial = ctrl.get_device_serial()
            summary = accounts_memory.get_device_summary(serial)
    except Exception:
        pass

    if not summary or (not summary.get("accounts") and not summary.get("facebook_accounts")):
        devices = accounts_memory._data.get("devices", {})
        if "14589835950958" in devices:
            serial = "14589835950958"
            summary = accounts_memory.get_device_summary(serial)
        elif "40523726299258" in devices:
            serial = "40523726299258"
            summary = accounts_memory.get_device_summary(serial)
        elif devices:
            serial = list(devices.keys())[0]
            summary = list(devices.values())[0]

    if platform == "facebook":
        accounts = summary.get("facebook_accounts", [])
        current = summary.get("last_used_account_name", "")
    else:
        accounts = summary.get("accounts", [])
        current = summary.get("last_used_account_idx", -1)

    return jsonify({
        "success": True,
        "platform": platform,
        "serial": serial,
        "current": current,
        "accounts": accounts
    })


@accounts_bp.route("/<any(instagram, facebook):platform>/switch", methods=["POST"])
def api_accounts_widget_switch(platform: str) -> Response:
    """Cambia a la cuenta indicada en la plataforma dada."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    index = data.get("index")
    try:
        ctrl = get_controller()
        if platform == "facebook":
            if not name:
                return jsonify({"success": False, "error": "Se requiere 'name' para Facebook."}), 400
            result = ctrl.switch_to_facebook_account_by_name(name)
        else:
            if index is None:
                return jsonify({"success": False, "error": "Se requiere 'index' para Instagram."}), 400
            ok = ctrl.switch_to_account_by_index(int(index))
            result = {"success": ok, "name": name, "index": index}
        return jsonify({"success": True, "platform": platform, "result": result})
    except Exception as e:
        logger.error(f"api_accounts_widget_switch failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@accounts_bp.route("/scan", methods=["GET", "POST"])
def api_accounts_scan() -> Response:
    """Descubre y cuenta las cuentas registradas en el dispositivo y las persiste en memoria."""
    try:
        ctrl = get_controller()
        serial = ctrl.get_device_serial()
        model = ctrl.get_device_model()
        ctrl.wake_screen()
        ctrl.open_app("com.instagram.lite")
        logger.info("Esperando 38s a que Instagram Lite cargue la pantalla...")
        total_wait = 38
        for elapsed in range(0, total_wait, 5):
            rem = total_wait - elapsed
            broadcast_event("cycle_step", {
                "step": 1,
                "message": f"Abriendo Instagram Lite... esperando carga completa ({min(elapsed + 5, total_wait)}/38s)",
                "is_error": False
            })
            time.sleep(min(5, rem))

        broadcast_event("cycle_step", {
            "step": 2,
            "message": "Navegando a Perfil (Slot 5 en barra inferior)...",
            "is_error": False
        })
        ctrl.verify_and_tap_nav_bar(slot_index=5, sleep_after=3.0)

        broadcast_event("cycle_step", {
            "step": 3,
            "message": "Abriendo menú desplegable de cuentas ('v')...",
            "is_error": False
        })
        ctrl.open_account_dropdown_sheet(sleep_after=2.5)

        broadcast_event("cycle_step", {
            "step": 4,
            "message": "Analizando y contando cuentas registradas con desplazamiento dinámico...",
            "is_error": False
        })
        count = ctrl.count_registered_accounts_with_swipe()

        broadcast_event("cycle_step", {
            "step": 5,
            "message": f"Análisis finalizado: {count} cuentas detectadas y sincronizadas. Cerrando panel y regresando a Inicio...",
            "is_error": False
        })
        ctrl.press_back()
        time.sleep(1.0)
        ctrl.verify_and_tap_nav_bar(slot_index=1, sleep_after=1.5)

        dev_record = accounts_memory.get_or_register_device(
            device_serial=serial,
            device_model=model,
            total_accounts=count
        )

        broadcast_event("cycle_step", {
            "step": 100,
            "message": f"¡Análisis finalizado con éxito! {count} cuentas registradas en memoria. Listo para Iniciar Automatización.",
            "is_error": False
        })
        return jsonify({
            "success": True,
            "total_accounts": count,
            "device_serial": serial,
            "device_model": model,
            "device_summary": dev_record
        })
    except Exception as e:
        logger.error(f"Error scanning accounts: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@accounts_bp.route("/memory", methods=["GET"])
def api_accounts_memory() -> Response:
    """Retorna la memoria persistente del dispositivo actualmente conectado o en caché."""
    try:
        ctrl = get_controller()
        if ctrl.is_device_connected():
            serial = ctrl.get_device_serial()
            summary = accounts_memory.get_device_summary(serial)
            if summary and summary.get("total_accounts", 0) > 0:
                return jsonify({"success": True, "data": summary})
    except Exception:
        pass

    devices = accounts_memory._data.get("devices", {})
    if "40523726299258" in devices:
        preferred_dev = accounts_memory.get_device_summary("40523726299258")
    elif devices:
        preferred_dev = list(devices.values())[-1]
    else:
        preferred_dev = {"total_accounts": 11, "accounts": []}

    return jsonify({
        "success": True,
        "data": preferred_dev,
        "cached": True
    })
