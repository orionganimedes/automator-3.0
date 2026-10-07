"""
web.routes.icons - Blueprint para gestión de plantillas visuales, matching y toques automáticos.

Rutas:
    GET  /api/icons                    -> lista de plantillas disponibles
    GET  /api/icons/image/<filename>   -> sirve la imagen de la plantilla
    GET  /api/icons/debug-image        -> sirve la última imagen de coincidencia visual de depuración
    POST /api/icons/upload             -> sube una nueva plantilla o captura recortada
    POST /api/icons/locate-and-tap     -> localiza un icono en pantalla y lo pulsa
    POST /api/icons/sequence-tap       -> ejecuta secuencia de pulsaciones de iconos
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict

import cv2
from flask import Blueprint, jsonify, request, send_file, send_from_directory, Response
from werkzeug.utils import secure_filename

import config
from config import logger
from core.adb.controller import get_controller
from core.ai.agent import AIAgent
from core.vision.icon_matcher import IconMatcher
from web.events import broadcast_event

icons_bp = Blueprint("icons", __name__, url_prefix="/api/icons")


@icons_bp.route("", methods=["GET"])
@icons_bp.route("/", methods=["GET"])
def api_get_icons() -> Response:
    """Returns a list of all icon templates available in icon_targets/."""
    try:
        matcher = IconMatcher(Path(config.ICON_TARGETS_DIR))
        templates = matcher.list_templates()
        return jsonify({
            "success": True,
            "directory": config.ICON_TARGETS_DIR,
            "count": len(templates),
            "templates": templates
        })
    except Exception as e:
        logger.error(f"Error listing icon templates: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@icons_bp.route("/image/<path:filename>", methods=["GET"])
def api_get_icon_image(filename: str) -> Response:
    """Serves an icon template image file."""
    safe_filename = secure_filename(os.path.basename(filename))
    directory = os.path.abspath(config.ICON_TARGETS_DIR)
    file_path = os.path.join(directory, safe_filename)
    if os.path.isfile(file_path):
        return send_from_directory(directory, safe_filename)
    return jsonify({"error": "Icon template image not found"}), 404


@icons_bp.route("/debug-image", methods=["GET"])
def api_get_debug_image() -> Response:
    """Serves the latest visual match debug image."""
    debug_path = os.path.abspath("debug_match.png")
    if os.path.isfile(debug_path):
        return send_file(debug_path, mimetype="image/png")
    return jsonify({"error": "No debug match image available"}), 404


@icons_bp.route("/upload", methods=["POST"])
def api_upload_icon() -> Response:
    """
    Accepts an uploaded image to be stored in icon_targets/.
    Can be a cropped icon or a full/partial screenshot with a RED box/circle around the icon.
    """
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No se subió ningún archivo."}), 400

    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        return jsonify({"success": False, "error": "Nombre de archivo vacío."}), 400

    custom_name = request.form.get("template_name", "").strip()
    orig_ext = Path(uploaded_file.filename).suffix.lower()
    if orig_ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        return jsonify({"success": False, "error": f"Formato no soportado: {orig_ext}. Usa PNG o JPG."}), 400

    if custom_name:
        clean_name = re.sub(r"[^\w\-]", "_", custom_name).strip("_")
        target_filename = f"{clean_name}{orig_ext}"
    else:
        target_filename = secure_filename(uploaded_file.filename)

    save_dir = Path(config.ICON_TARGETS_DIR)
    save_dir.mkdir(exist_ok=True)
    destination = save_dir / target_filename

    uploaded_file.save(str(destination))
    logger.info(f"Nuevo icono o captura guardada en: {destination}")

    matcher = IconMatcher(save_dir)
    img = cv2.imread(str(destination))
    has_red = False
    dims = ""
    if img is not None:
        h, w = img.shape[:2]
        dims = f"{w}x{h}"
        has_red = matcher.detect_red_annotation_box(img) is not None

    return jsonify({
        "success": True,
        "message": f"Icono guardado como '{target_filename}'.",
        "filename": target_filename,
        "has_red_marker": has_red,
        "dimensions": dims
    })


@icons_bp.route("/locate-and-tap", methods=["POST"])
def api_locate_and_tap() -> Response:
    """
    Locates an icon on the current screen (supporting red box or direct template),
    taps its center, waits for the specified sleep delay, and updates screen.
    """
    data = request.get_json() or {}
    icon_name = data.get("icon_name", "").strip()
    sleep_seconds = float(data.get("sleep_seconds", config.ICON_SLEEP_DEFAULT))
    v_model = data.get("vision_model", "").strip() or config.VISION_MODEL
    use_ai = bool(data.get("use_ai_fallback", True))

    if not icon_name:
        return jsonify({"success": False, "error": "El nombre del icono no puede estar vacío."}), 400

    try:
        controller = get_controller()
        ai_agent = None
        if use_ai:
            ai_agent = AIAgent(
                base_url=config.OLLAMA_BASE_URL,
                vision_model=v_model,
                text_model=config.TEXT_MODEL,
            )

        result = controller.locate_and_tap_icon(
            template_name=icon_name,
            sleep_after=sleep_seconds,
            ai_agent=ai_agent,
            draw_debug=True
        )

        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        broadcast_event("screen_updated", {"timestamp": time.time()})

        return jsonify({
            "success": result.get("found", False),
            "result": result
        })

    except Exception as e:
        err = f"Error al buscar y tocar icono: {e}"
        logger.error(err)
        return jsonify({"success": False, "error": err}), 500


@icons_bp.route("/sequence-tap", methods=["POST"])
def api_sequence_tap() -> Response:
    """
    Executes a sequence of icon touches in order, with sleep between each.
    """
    data = request.get_json() or {}
    sequence = data.get("sequence", [])
    sleep_between = float(data.get("sleep_between", config.ICON_SLEEP_DEFAULT))
    v_model = data.get("vision_model", "").strip() or config.VISION_MODEL
    use_ai = bool(data.get("use_ai_fallback", True))

    if not sequence or not isinstance(sequence, list):
        return jsonify({"success": False, "error": "La lista de secuencia no puede estar vacía."}), 400

    try:
        controller = get_controller()
        ai_agent = None
        if use_ai:
            ai_agent = AIAgent(
                base_url=config.OLLAMA_BASE_URL,
                vision_model=v_model,
                text_model=config.TEXT_MODEL,
            )

        results = controller.tap_icon_sequence(
            template_names=sequence,
            sleep_between=sleep_between,
            ai_agent=ai_agent
        )

        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        broadcast_event("screen_updated", {"timestamp": time.time()})

        return jsonify({
            "success": True,
            "results": results
        })

    except Exception as e:
        err = f"Error en secuencia de iconos: {e}"
        logger.error(err)
        return jsonify({"success": False, "error": err}), 500
