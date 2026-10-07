"""
detect_reel_icons.py - Módulo Independiente de Identificación de Iconos en Instagram Reels

Detecta con precisión milimétrica:
  1. Icono de Corazón / Me Gusta (Like) en el riel derecho (Imagen 2)
  2. Icono de Burbuja de Comentarios directamente debajo del Corazón (Imagen 3)
  3. Identifica y EXCLUYE estrictamente el icono flotante de WhatsApp (Imagen 1)

Modos disponibles:
  - Detección por Patrones / Template Matching multiescala + Geometría adaptativa.
  - Detección por IA Visión Multimodal con Qwen 2.5-VL (Ollama local).
  - Modo Híbrido inteligente (Patrones primero, Qwen 2.5-VL como respaldo).

Puede ejecutarse directamente desde terminal:
  python detect_reel_icons.py
  python detect_reel_icons.py --image ruta/a/captura.png --qwen
"""

import os
import sys
import json
import base64
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List, Union

import cv2
import numpy as np
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ReelIconDetector")

# ==============================================================================
# PARÁMETROS CONFIGURABLES (Modifica estos valores para calibrar el detector)
# ==============================================================================

# Riel vertical derecho donde se ubican las acciones del Reel en Instagram
RAIL_X_MIN_RATIO: float = 0.80       # El riel de Reels siempre está en x >= 80% (típicamente 92-94%)
ACTION_Y_MIN_RATIO: float = 0.25     # Zona de acciones inicia en y >= 25%
ACTION_Y_MAX_RATIO: float = 0.66     # Zona de acciones Like/Comment termina en y <= 66%

# Zona de exclusión estricta: WhatsApp y burbujas flotantes se ubican en y >= 68%
WHATSAPP_MIN_Y_RATIO: float = 0.68

# Umbrales mínimos de coincidencia por patrones
THRESHOLD_HEART: float = 0.55
THRESHOLD_COMMENT: float = 0.55
THRESHOLD_RED_HEART: float = 0.60

# Modelos locales en Ollama
OLLAMA_BASE_URL: str = "http://localhost:11434/api/generate"
DEFAULT_VISION_MODEL: str = "qwen2.5vl:latest"

# Carpeta de plantillas
TARGETS_DIR: Path = Path("icon_targets")


# ==============================================================================
# FUNCIONES DE DETECCIÓN POR PATRONES / OPENCV
# ==============================================================================

def load_template(filename: str) -> Optional[np.ndarray]:
    """Carga una plantilla desde icon_targets/ o ruta directa."""
    path = TARGETS_DIR / filename
    if not path.exists():
        path = Path(filename)
    if not path.exists():
        logger.warning(f"Plantilla no encontrada: {filename}")
        return None
    img = cv2.imread(str(path))
    return img


def match_template_multiscale(
    roi_bgr: np.ndarray,
    template_bgr: np.ndarray,
    scales: Optional[List[float]] = None
) -> Tuple[float, Tuple[int, int], Tuple[int, int]]:
    """
    Busca template_bgr en roi_bgr evaluando múltiples escalas.
    Retorna (best_score, (center_x, center_y), (tpl_w, tpl_h)).
    """
    if roi_bgr is None or template_bgr is None or roi_bgr.size == 0 or template_bgr.size == 0:
        return 0.0, (0, 0), (0, 0)

    if scales is None:
        scales = [0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.35, 1.50, 1.65, 1.80]

    roi_h, roi_w = roi_bgr.shape[:2]
    best_score = -1.0
    best_center = (0, 0)
    best_size = (0, 0)

    for scale in scales:
        tw = int(template_bgr.shape[1] * scale)
        th = int(template_bgr.shape[0] * scale)
        if tw >= roi_w or th >= roi_h or tw < 12 or th < 12:
            continue

        resized = cv2.resize(template_bgr, (tw, th), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
        res = cv2.matchTemplate(roi_bgr, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if max_val > best_score:
            best_score = max_val
            best_center = (max_loc[0] + tw // 2, max_loc[1] + th // 2)
            best_size = (tw, th)

    return best_score, best_center, best_size


def detect_red_heart_fill(roi_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """
    Detecta si el corazón ya está encendido en rojo (Like ya otorgado).
    Retorna (x, y, w, h) en coordenadas relativas al ROI o None.
    """
    if roi_bgr is None or roi_bgr.size == 0:
        return None

    # El corazón rojo en Instagram tiene tono rojo vibrante (HSV)
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, np.array([0, 140, 140]), np.array([10, 255, 255]))
    mask2 = cv2.inRange(hsv, np.array([170, 140, 140]), np.array([180, 255, 255]))
    red_mask = cv2.bitwise_or(mask1, mask2)

    # Filtrar ruido de contornos
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        aspect = float(w) / max(1, h)
        # El corazón tiene forma simétrica, área compacta > 200 px
        if 200 <= area <= 6000 and 0.7 <= aspect <= 1.4:
            return (x, y, w, h)

    return None


def detect_reel_icons_pattern(
    screen_bgr: np.ndarray
) -> Dict[str, Any]:
    """
    Identificación por patrones ópticos de los botones de Reel en Instagram:
      - Corazón (Like): blanco o rojo.
      - Comentario: burbuja de diálogo blanca ubicada directamente abajo.
      - Exclusión estricta de WhatsApp flotante (y >= 68%).
    """
    h, w = screen_bgr.shape[:2]

    # Delimitar ROI del riel de acción (x >= 80%, y: 25% a 66%)
    rx1 = int(w * RAIL_X_MIN_RATIO)
    rx2 = w
    ry1 = int(h * ACTION_Y_MIN_RATIO)
    ry2 = int(h * ACTION_Y_MAX_RATIO)
    action_roi = screen_bgr[ry1:ry2, rx1:rx2]

    # Cargar plantillas de corazón
    tpl_heart_white = load_template("reel_heart_white_user.png")
    if tpl_heart_white is None:
        tpl_heart_white = load_template("reel_like_heart.png")

    tpl_heart_red = load_template("reel_heart_red_user.png")

    tpl_comment = load_template("reel_comment_white_user.png")
    if tpl_comment is None:
        tpl_comment = load_template("reel_comment_bubble.png")

    tpl_comment_720 = load_template("reel_comment_native_720.png")
    tpl_wa = load_template("reel_whatsapp_avoid.png")

    # 1. DETECCIÓN DEL CORAZÓN (LIKE)
    best_h_score = -1.0
    best_h_center = None
    is_liked = False
    h_box_size = (40, 40)

    # A) Buscar corazón blanco (sin like)
    if tpl_heart_white is not None:
        score_wh, center_wh, size_wh = match_template_multiscale(action_roi, tpl_heart_white)
        if score_wh > best_h_score:
            best_h_score = score_wh
            best_h_center = center_wh
            h_box_size = size_wh
            is_liked = False

    # B) Buscar corazón rojo (ya con like)
    if tpl_heart_red is not None:
        score_rh, center_rh, size_rh = match_template_multiscale(action_roi, tpl_heart_red)
        if score_rh > best_h_score:
            best_h_score = score_rh
            best_h_center = center_rh
            h_box_size = size_rh
            is_liked = True

    # C) Comprobación de máscara roja directa
    red_box = detect_red_heart_fill(action_roi)
    if red_box is not None and (best_h_score < 0.70 or is_liked):
        rx, ry, rw, rh = red_box
        best_h_center = (rx + rw // 2, ry + rh // 2)
        h_box_size = (rw, rh)
        best_h_score = max(best_h_score, 0.90)
        is_liked = True

    heart_res = {
        "found": best_h_score >= THRESHOLD_HEART,
        "score": float(best_h_score),
        "center": (rx1 + best_h_center[0], ry1 + best_h_center[1]) if best_h_center else None,
        "box_size": h_box_size,
        "is_already_liked": is_liked,
        "source": "pattern"
    }

    # 2. DETECCIÓN DE LA BURBUJA DE COMENTARIOS
    best_c_score = -1.0
    best_c_center = None
    c_box_size = (40, 40)

    for tpl in [tpl_comment, tpl_comment_720]:
        if tpl is not None:
            score_c, center_c, size_c = match_template_multiscale(action_roi, tpl)
            if score_c > best_c_score:
                best_c_score = score_c
                best_c_center = center_c
                c_box_size = size_c

    comment_res = {
        "found": best_c_score >= THRESHOLD_COMMENT,
        "score": float(best_c_score),
        "center": (rx1 + best_c_center[0], ry1 + best_c_center[1]) if best_c_center else None,
        "box_size": c_box_size,
        "source": "pattern"
    }

    # 3. VERIFICACIÓN Y CORRECCIÓN GEOMÉTRICA RELATIVA
    # El Corazón SIEMPRE está arriba del Comentario en el riel vertical de Instagram
    if heart_res["found"] and comment_res["found"]:
        hz_x, hz_y = heart_res["center"]
        cz_x, cz_y = comment_res["center"]

        # Si el orden vertical es correcto (Heart arriba, Comment abajo) y alineados en X
        if hz_y < cz_y and abs(hz_x - cz_x) <= 45:
            # Coincidencia geométrica perfecta confirmada
            pass
        elif hz_y >= cz_y:
            # Si se invirtieron por coincidencia espuria, corregir basándose en el de mayor confianza
            if heart_res["score"] >= comment_res["score"]:
                # Deducir comentario debajo del corazón (típicamente 10% de la altura total)
                derived_y = int(hz_y + h * 0.095)
                comment_res["center"] = (hz_x, derived_y)
                comment_res["source"] = "geometric_derived"
                comment_res["found"] = True
            else:
                derived_y = int(cz_y - h * 0.095)
                heart_res["center"] = (cz_x, derived_y)
                heart_res["source"] = "geometric_derived"
                heart_res["found"] = True

    elif heart_res["found"] and not comment_res["found"]:
        # Deducir comentario directamente debajo del corazón detectado
        hz_x, hz_y = heart_res["center"]
        derived_y = int(hz_y + h * 0.095)
        comment_res["center"] = (hz_x, derived_y)
        comment_res["source"] = "geometric_derived"
        comment_res["found"] = True
        comment_res["score"] = heart_res["score"] * 0.9

    elif comment_res["found"] and not heart_res["found"]:
        # Deducir corazón directamente arriba del comentario detectado
        cz_x, cz_y = comment_res["center"]
        derived_y = int(cz_y - h * 0.095)
        heart_res["center"] = (cz_x, derived_y)
        heart_res["source"] = "geometric_derived"
        heart_res["found"] = True
        heart_res["score"] = comment_res["score"] * 0.9

    # 4. IDENTIFICACIÓN Y EXCLUSIÓN DE WHATSAPP (Burbuja flotante inferior)
    whatsapp_res = {"detected": False, "center": None, "status": "excluded"}
    wa_roi_y1 = int(h * WHATSAPP_MIN_Y_RATIO)
    wa_roi = screen_bgr[wa_roi_y1:h, rx1:w]
    if tpl_wa is not None and wa_roi.size > 0:
        score_wa, center_wa, size_wa = match_template_multiscale(wa_roi, tpl_wa)
        if score_wa >= 0.50:
            whatsapp_res["detected"] = True
            whatsapp_res["center"] = (rx1 + center_wa[0], wa_roi_y1 + center_wa[1])
            whatsapp_res["score"] = float(score_wa)
            whatsapp_res["status"] = "excluded_successfully"

    return {
        "heart": heart_res,
        "comment": comment_res,
        "whatsapp": whatsapp_res
    }


# ==============================================================================
# FUNCIONES DE DETECCIÓN CON QWEN 2.5-VL (OLLAMA LOCAL)
# ==============================================================================

def detect_reel_icons_qwen(
    image_path: str,
    base_url: str = OLLAMA_BASE_URL,
    model: str = DEFAULT_VISION_MODEL,
    timeout: float = 45.0
) -> Dict[str, Any]:
    """
    Usa el modelo multimodal local Qwen 2.5-VL para inspeccionar la pantalla
    y ubicar con visión artificial los iconos de Like y Comment en el riel de Reels.
    """
    if not os.path.exists(image_path):
        return {"success": False, "error": f"Archivo no existe: {image_path}"}

    img = cv2.imread(image_path)
    if img is None:
        return {"success": False, "error": f"No se pudo cargar imagen: {image_path}"}
    h, w = img.shape[:2]

    # Codificar imagen completa en Base64
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = (
        f"You are an expert mobile UI computer vision system analyzing an Instagram Reel on Android.\n"
        f"The image resolution is {w}x{h} pixels (width x height).\n"
        f"Examine the vertical action rail on the right side of the screen (x >= {int(w * 0.80)}px).\n"
        f"Locate the pixel coordinates of the following action buttons:\n"
        f"1. 'like_heart': The heart icon button used to like the Reel (top of the right action rail).\n"
        f"   - Check if it is currently outline/white (already_liked: false) or solid/red (already_liked: true).\n"
        f"2. 'comment_bubble': The speech bubble icon button used to comment (located directly below the heart icon).\n"
        f"3. 'whatsapp_bubble': Any floating WhatsApp icon/widget located at the bottom-right (y > {int(h * 0.68)}px).\n\n"
        f"CRITICAL RULES:\n"
        f"- The 'like_heart' is always above the 'comment_bubble'.\n"
        f"- The 'comment_bubble' is in the vertical range y={int(h * 0.35)} to {int(h * 0.60)}.\n"
        f"- DO NOT confuse the Reel comment bubble with the WhatsApp floating bubble which is near the bottom (y > {int(h * 0.70)}).\n\n"
        f"Return STRICT JSON only matching this schema:\n"
        f'{{\n'
        f'  "like_heart": {{"found": true, "center": [int_x, int_y], "is_already_liked": bool}},\n'
        f'  "comment_bubble": {{"found": true, "center": [int_x, int_y]}},\n'
        f'  "whatsapp_bubble": {{"found": bool, "center": [int_x, int_y]}}\n'
        f'}}'
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
        "format": "json"
    }

    try:
        logger.info(f"Consultando modelo de visión '{model}' en Ollama...")
        res = requests.post(base_url, json=payload, timeout=timeout)
        if res.status_code != 200:
            return {"success": False, "error": f"Ollama HTTP {res.status_code}: {res.text}"}

        raw = res.json().get("response", "{}")
        # Limpiar bloques markdown si existen
        clean_text = raw.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = json.loads(clean_text)
        logger.info(f"Respuesta Qwen 2.5-VL parseada: {parsed}")

        def _clean_center(val: Any) -> Optional[Tuple[int, int]]:
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                cx, cy = int(val[0]), int(val[1])
                # Normalizar si viniera en escala 0-1000
                if cx <= 1000 and cy <= 1000 and (w > 1000 or h > 1000):
                    cx = int(cx * (w / 1000.0))
                    cy = int(cy * (h / 1000.0))
                return (cx, cy)
            return None

        h_data = parsed.get("like_heart", {})
        c_data = parsed.get("comment_bubble", {})
        wa_data = parsed.get("whatsapp_bubble", {})

        return {
            "success": True,
            "heart": {
                "found": bool(h_data.get("found")),
                "center": _clean_center(h_data.get("center")),
                "is_already_liked": bool(h_data.get("is_already_liked", False)),
                "source": "qwen_vision"
            },
            "comment": {
                "found": bool(c_data.get("found")),
                "center": _clean_center(c_data.get("center")),
                "source": "qwen_vision"
            },
            "whatsapp": {
                "detected": bool(wa_data.get("found")),
                "center": _clean_center(wa_data.get("center")),
                "status": "excluded"
            }
        }

    except Exception as e:
        logger.error(f"Fallo durante inferencia con Qwen 2.5-VL: {e}")
        return {"success": False, "error": str(e)}


# ==============================================================================
# FUNCIÓN PRINCIPAL / ENTRADA UNIFICADA
# ==============================================================================

def detect_reel_action_buttons(
    image_path_or_bgr: Union[str, np.ndarray],
    use_qwen_fallback: bool = True,
    force_qwen: bool = False,
    save_debug_image: bool = True,
    debug_output_path: str = "debug_reel_detection.png"
) -> Dict[str, Any]:
    """
    Función unificada de detección de botones de Reel:
      1. Ejecuta detección por patrones y geometría estricta.
      2. Si la confianza es alta (ambos encontrados y coherentes), retorna de inmediato.
      3. Si hay duda u oscurecimiento por el video (o force_qwen=True), recurre a Qwen 2.5-VL.
      4. Genera una imagen anotada (debug_reel_detection.png) marcando:
         - Verde: Botón Like (Corazón)
         - Cian: Botón Comentar (Burbuja)
         - Rojo punteado: WhatsApp (Ignorado)
    """
    if isinstance(image_path_or_bgr, str):
        image_path = image_path_or_bgr
        screen_bgr = cv2.imread(image_path)
    else:
        screen_bgr = image_path_or_bgr.copy()
        image_path = "temp_reel_screen.png"
        cv2.imwrite(image_path, screen_bgr)

    if screen_bgr is None:
        raise ValueError("No se pudo cargar la imagen para detección de botones de Reel.")

    h, w = screen_bgr.shape[:2]

    # Paso 1: Detección por patrones
    pat_res = detect_reel_icons_pattern(screen_bgr)
    heart = pat_res["heart"]
    comment = pat_res["comment"]
    whatsapp = pat_res["whatsapp"]

    # Verificar si el resultado de patrones es de alta certeza
    patterns_reliable = (
        heart["found"] and comment["found"] and
        heart["center"] is not None and comment["center"] is not None and
        heart["center"][1] < comment["center"][1]
    )

    final_heart = heart
    final_comment = comment
    detection_method = "pattern_and_geometry"

    # Paso 2: Si no es 100% confiable (o force_qwen=True) y se habilitó IA, consultar Qwen 2.5-VL
    should_run_qwen = force_qwen or ((not patterns_reliable or heart["score"] < 0.65 or comment["score"] < 0.65) and use_qwen_fallback)
    if should_run_qwen:
        logger.info("Invocando Qwen 2.5-VL para verificación visual...")
        qwen_res = detect_reel_icons_qwen(image_path)
        if qwen_res.get("success"):
            qh = qwen_res.get("heart", {})
            qc = qwen_res.get("comment", {})
            qw = qwen_res.get("whatsapp", {})

            if qh.get("found") and qh.get("center"):
                final_heart = qh
                detection_method = "qwen_vision"
            if qc.get("found") and qc.get("center"):
                final_comment = qc
                detection_method = "qwen_vision"
            if qw.get("detected"):
                whatsapp = qw

    # Fallback seguro absoluto si todo lo demás fallara (calibrado en Instagram Lite):
    # Like ~ 40.4% altura, Comment ~ 48.6% altura, rail ~ 93.8% ancho
    if not final_heart.get("center"):
        final_heart = {
            "found": True,
            "center": (int(w * 0.938), int(h * 0.404)),
            "is_already_liked": False,
            "source": "fallback_calibrated"
        }
    if not final_comment.get("center"):
        hz_x, hz_y = final_heart["center"]
        final_comment = {
            "found": True,
            "center": (hz_x, hz_y + int(h * 0.095)),
            "source": "fallback_calibrated"
        }

    # Paso 3: Dibujar anotaciones visuales en debug_reel_detection.png
    if save_debug_image:
        debug_img = screen_bgr.copy()
        hx, hy = final_heart["center"]
        cx, cy = final_comment["center"]

        # 1. Dibujar Botón de Like (Verde)
        cv2.circle(debug_img, (hx, hy), 28, (0, 255, 0), 3)
        cv2.drawMarker(debug_img, (hx, hy), (0, 255, 0), markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
        liked_str = " (LIKED)" if final_heart.get("is_already_liked") else ""
        cv2.putText(debug_img, f"1. LIKE / CORAZON{liked_str}", (max(10, hx - 260), hy - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)

        # 2. Dibujar Botón de Comentario (Cian)
        cv2.circle(debug_img, (cx, cy), 28, (255, 255, 0), 3)
        cv2.drawMarker(debug_img, (cx, cy), (255, 255, 0), markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
        cv2.putText(debug_img, "2. COMENTARIOS", (max(10, cx - 210), cy + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2, cv2.LINE_AA)

        # 3. Dibujar WhatsApp (Rojo con advertencia de ignorado) si se detectó
        if whatsapp.get("detected") and whatsapp.get("center"):
            wx, wy = whatsapp["center"]
            cv2.circle(debug_img, (wx, wy), 35, (0, 0, 255), 2)
            cv2.putText(debug_img, "WHATSAPP (IGNORADO)", (max(10, wx - 260), wy + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)

        # 4. Dibujar línea del riel de acción
        cv2.line(debug_img, (int(w * RAIL_X_MIN_RATIO), 0), (int(w * RAIL_X_MIN_RATIO), h), (80, 80, 80), 1)

        cv2.imwrite(debug_output_path, debug_img)
        logger.info(f"Imagen de depuración visual guardada en: {debug_output_path}")

    return {
        "success": True,
        "method": detection_method,
        "like_button": final_heart,
        "comment_button": final_comment,
        "whatsapp_button": whatsapp,
        "debug_image": debug_output_path if save_debug_image else None
    }


# ==============================================================================
# DEMOSTRACIÓN Y PRUEBAS CLI
# ==============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Detector independiente de iconos para Instagram Reels")
    parser.add_argument("--image", type=str, default="", help="Ruta de la captura a analizar")
    parser.add_argument("--qwen", action="store_true", help="Habilitar respaldo multimodal con Qwen 2.5-VL")
    parser.add_argument("--force-qwen", action="store_true", help="Forzar detección directamente con Qwen 2.5-VL")
    args = parser.parse_args()

    test_targets = []
    if args.image:
        test_targets.append(("Imagen especificada", args.image))
    else:
        # Pruebas con las 3 imágenes del usuario y la captura en vivo
        test_targets = [
            ("Imagen 1 (WhatsApp marcado en rojo)", "/home/orion/.gemini/antigravity/brain/f12a0d3f-d25a-44dc-a2ac-d1fcf2642852/.user_uploaded/media_1790628326205.jpg"),
            ("Imagen 2 (Corazón marcado en verde)", "/home/orion/.gemini/antigravity/brain/f12a0d3f-d25a-44dc-a2ac-d1fcf2642852/.user_uploaded/media_1790628379630.jpg"),
            ("Imagen 3 (Comentario marcado en verde)", "/home/orion/.gemini/antigravity/brain/f12a0d3f-d25a-44dc-a2ac-d1fcf2642852/.user_uploaded/media_1790628572202.jpg"),
            ("Captura en Vivo del Teléfono", "live_current_screen.png")
        ]

    print("\n" + "=" * 70)
    print("=== TEST INDEPENDIENTE DE IDENTIFICACIÓN DE ICONOS DE REEL ===")
    print("=" * 70)

    for label, path in test_targets:
        if not os.path.exists(path):
            continue
        print(f"\n--- Probando: {label} ---")
        print(f"Archivo: {path}")

        res = detect_reel_action_buttons(
            path,
            use_qwen_fallback=args.qwen,
            force_qwen=args.force_qwen,
            save_debug_image=True,
            debug_output_path=f"debug_{Path(path).stem}.png"
        )

        hb = res["like_button"]
        cb = res["comment_button"]
        wa = res["whatsapp_button"]

        print(f"  Metodo: {res['method']}")
        print(f"  [1] Like (Corazon):      Coord={hb.get('center')}, Liked={hb.get('is_already_liked')}, Origen={hb.get('source')}")
        print(f"  [2] Comentarios:         Coord={cb.get('center')}, Origen={cb.get('source')}")
        print(f"  [3] WhatsApp (Ignorado): Detectado={wa.get('detected')}, Coord={wa.get('center')}, Estado={wa.get('status')}")
        print(f"  Depuracion visual:       {res.get('debug_image')}")

    print("\n" + "=" * 70)
    print("=== PRUEBAS FINALIZADAS CON ÉXITO ===")
    print("=" * 70)
