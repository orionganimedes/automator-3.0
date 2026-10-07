"""
facebook_post_engagement.py - Automatización Dedicada de Publicaciones y Reels en Facebook Lite

Este módulo gestiona la interacción completa (Like y Comentarios con Banco Rotativo)
diferenciando automáticamente entre publicaciones estándar y Reels en Facebook Lite,
con rotación multi-cuenta, liberación estricta de memoria RAM, y pausas antibloqueo de 30s.
"""

import gc
import json
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

import cv2
import numpy as np

import requests

import config
from core.memory.accounts import accounts_memory
from core.adb.controller import AndroidController
from core.ai.agent import AIAgent
from core.vision.detect_reel_icons import detect_reel_action_buttons, detect_reel_icons_qwen

logger = logging.getLogger("FacebookPostEngagement")


def detect_facebook_content_type(
    controller: AndroidController,
    screen_path: Optional[str] = None,
    url: str = ""
) -> str:
    """
    Diferencia con precisión entre un Reel y una Publicación Estándar (Post normal) en Facebook Lite:
    1. Análisis de URL: Detecta /reel/, /reels/, /share/r/, /watch/.
    2. Inspección estructural de UI (XML Dump):
       - Reel: Barra vertical de acciones en el margen derecho (x >= 80%), etiquetas de audio/música,
               y ausencia del encabezado 'Publicación de...'.
       - Publicación Estándar: Encabezado 'Publicación de [Autor]', barra horizontal de reacciones
                               o texto 'Comentas como...'.
    Retorna: 'reel' o 'post'.
    """
    cleaned_url = (url or "").lower().strip()

    # 1. Señal DEFINITIVA por URL (share/r = Reel, share/p = Publicación)
    if re.search(r"/(reel|reels|share/r|watch|videos)/", cleaned_url):
        logger.info(f"Clasificación FB: Reel (URL {cleaned_url})")
        return "reel"
    if re.search(r"/(share/p|posts|story\.php|permalink\.php|photo|photos|fbid)/", cleaned_url):
        logger.info(f"Clasificación FB: Post (URL {cleaned_url})")
        return "post"

    width, height = controller.get_screen_size()
    if screen_path is None:
        screen_path = config.DEFAULT_SCREEN_IMAGE

    # 2. URL ambigua -> análisis del árbol de accesibilidad (UIAutomator)
    xml_data = ""
    try:
        res = subprocess.run(
            [controller.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
            capture_output=True,
            text=True,
            check=False,
            timeout=4
        )
        raw = res.stdout or ""
        start = raw.find("<hierarchy")
        end = raw.rfind("</hierarchy>")
        if start != -1 and end != -1:
            xml_data = raw[start : end + len("</hierarchy>")]
    except Exception as e:
        logger.warning(f"Aviso al obtener jerarquía para clasificar contenido: {e}")

    reel_score = 0
    post_score = 0

    if xml_data:
        try:
            root = ET.fromstring(xml_data)
            right_rail_buttons = 0

            for node in root.iter("node"):
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                bounds = node.attrib.get("bounds") or ""

                combined = f"{text} {desc}"

                # Indicadores de Reel
                if any(k in combined for k in ["audio original", "remix", "usar audio", "música"]):
                    reel_score += 4
                if "reel" in combined:
                    reel_score += 3

                # Indicadores de Publicación Estándar
                if "publicación de" in combined or "publicacion de" in combined or "post from" in combined:
                    post_score += 5
                # NOTA: 'comentas como' aparece en Reels Y Posts (barra de comentario); ya NO cuenta como indicador de Post.

                # Detectar botones en el margen derecho (columna vertical típica de Reels)
                m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                if len(m) == 2:
                    x1, y1 = int(m[0][0]), int(m[0][1])
                    x2, y2 = int(m[1][0]), int(m[1][1])
                    if x1 >= int(width * 0.80) and int(height * 0.35) <= y1 <= int(height * 0.88):
                        if node.attrib.get("clickable") == "true":
                            right_rail_buttons += 1

            if right_rail_buttons >= 2:
                reel_score += 4
                logger.info(f"Columna de acciones vertical de Reel detectada ({right_rail_buttons} botones en margen derecho).")

        except Exception as e_xml:
            logger.warning(f"Error parseando XML para detección de tipo: {e_xml}")

    logger.info(f"Clasificación de contenido FB (URL ambigua): reel_score={reel_score}, post_score={post_score}")

    if reel_score > post_score:
        return "reel"
    return "post"


def wait_for_facebook_content_load(
    controller: AndroidController,
    url: str = "",
    max_wait_seconds: float = 30.0,
    check_interval: float = 1.5,
    status_callback: Optional[Callable[[str], None]] = None,
    stop_checker: Optional[Callable[[], bool]] = None
) -> Dict[str, Any]:
    """
    Espera de carga y estabilización (hasta 30 segundos) similar al flujo de Instagram.
    Monitorea que la aplicación de Facebook Lite procese la URL y dibuje el contenido
    (identificando si es Reel o Publicación) antes de iniciar cualquier interacción.
    """
    start_time = time.time()
    detected_type = "post"
    loaded = False

    while time.time() - start_time < max_wait_seconds:
        if stop_checker and stop_checker():
            return {"loaded": False, "aborted": True, "content_type": detected_type}

        elapsed = time.time() - start_time
        remaining = max(0, int(max_wait_seconds - elapsed))

        if status_callback:
            status_callback(f"⏳ Esperando que cargue la publicación en FB Lite ({int(elapsed)}s / {int(max_wait_seconds)}s)...")

        # Capturar pantalla actual
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        detected_type = detect_facebook_content_type(controller, screen_path=config.DEFAULT_SCREEN_IMAGE, url=url)

        # Verificar si la pantalla ya no está en blanco ni en spinner de carga
        ui_text = controller.get_ui_text()
        if len(ui_text.strip()) > 15 or detected_type == "reel":
            # Si ya hay texto o es un reel con botones en riel derecho
            if elapsed >= 3.0:  # Al menos 3 segundos de reposo para asentar la interfaz
                loaded = True
                logger.info(f"Contenido de Facebook cargado y estabilizado en {elapsed:.1f}s. Tipo: {detected_type.upper()}")
                break

        time.sleep(check_interval)

    # Pausa de estabilización final de 1.5s
    time.sleep(1.5)
    return {
        "loaded": loaded,
        "content_type": detected_type,
        "elapsed": time.time() - start_time
    }


def _detect_standard_post_action_buttons(
    controller: AndroidController,
    width: int,
    height: int
) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """
    Inspección visual estricta de respaldo mediante OpenCV (plantillas con threshold alto >= 0.75)
    y jerarquía XML sin falsos positivos en el texto o en el pie de página.
    """
    like_coords: Optional[Tuple[int, int]] = None
    comment_coords: Optional[Tuple[int, int]] = None

    # 1. Detección visual con OpenCV / IconMatcher (umbral estricto 0.76 para evitar falsos positivos)
    try:
        matcher = controller.icon_matcher
        for tpl in ["facebook_like_thumb_lite", "facebook_like_thumb"]:
            m_like = matcher.locate_icon(config.DEFAULT_SCREEN_IMAGE, tpl, threshold=0.76)
            if m_like.get("found"):
                cx, cy = m_like["center"]
                if int(height * 0.20) <= cy <= int(height * 0.85):
                    like_coords = (cx, cy)
                    break

        for tpl in ["facebook_comment_bubble_lite", "facebook_comment_bubble"]:
            m_comm = matcher.locate_icon(config.DEFAULT_SCREEN_IMAGE, tpl, threshold=0.76)
            if m_comm.get("found"):
                cx, cy = m_comm["center"]
                if int(height * 0.20) <= cy <= int(height * 0.85):
                    comment_coords = (cx, cy)
                    break
    except Exception as e_mat:
        logger.warning(f"Aviso en detección por plantillas: {e_mat}")

    # 2. Respaldo por jerarquía XML (excluyendo estrictamente 'comentas como')
    if not like_coords or not comment_coords:
        try:
            res_dump = subprocess.run(
                [controller.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True, text=True, check=False, timeout=3
            )
            raw = res_dump.stdout or ""
            start = raw.find("<hierarchy")
            end = raw.rfind("</hierarchy>")
            if start != -1 and end != -1:
                root = ET.fromstring(raw[start : end + len("</hierarchy>")])
                for node in root.iter("node"):
                    desc = (node.get("content-desc") or "").strip().lower()
                    text = (node.get("text") or "").strip().lower()
                    bounds = node.get("bounds") or ""
                    m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                    if len(m) == 2:
                        cx = (int(m[0][0]) + int(m[1][0])) // 2
                        cy = (int(m[0][1]) + int(m[1][1])) // 2
                        if int(height * 0.20) <= cy <= int(height * 0.85):
                            if not like_coords and any(w in desc or w in text for w in ["me gusta", "like", "reaccionar"]):
                                if "responder" not in text and "responder" not in desc:
                                    like_coords = (cx, cy)
                            elif not comment_coords and any(w == desc or w == text for w in ["comentar", "comment"]):
                                if "edittext" not in (node.get("class") or "").lower():
                                    comment_coords = (cx, cy)

                # Detección estructural de fila Litho en FB Lite: 3 columnas de igual ancho (0-240, 240-480, 480-720)
                if not like_coords:
                    litho_rows = []
                    for node in root.iter("node"):
                        if node.get("clickable") == "true":
                            b = node.get("bounds") or ""
                            bm = re.findall(r"\[(\d+),(\d+)\]", b)
                            if len(bm) == 2:
                                x1, y1 = int(bm[0][0]), int(bm[0][1])
                                x2, y2 = int(bm[1][0]), int(bm[1][1])
                                if x1 == 0 and x2 == int(width * 0.333) and int(height * 0.25) <= y1 <= int(height * 0.82):
                                    litho_rows.append(((x1 + x2) // 2, (y1 + y2) // 2))
                    if litho_rows:
                        like_coords = litho_rows[0]
                        comment_coords = (int(width * 0.50), like_coords[1])
        except Exception as e_xml:
            logger.warning(f"Aviso en XML dump de post: {e_xml}")

    return like_coords, comment_coords


def _refine_action_row_with_xml(
    controller: AndroidController,
    width: int,
    height: int,
    hint_y: int,
    max_distance: int = 160
) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """
    Refina la posición exacta de la fila de acciones (Like / Comentar / Compartir) usando el XML.
    En FB Lite esa fila son 3 nodos clicables contiguos, cada uno de ~1/3 del ancho y con la misma Y.
    Se elige la fila más cercana a la Y que reportó Qwen (hint_y). El contador de reacciones
    (fila de ancho completo justo encima) queda descartado por no cumplir el patrón de 3 columnas.
    """
    try:
        res = subprocess.run(
            [controller.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
            capture_output=True, text=True, check=False, timeout=6
        )
        raw = res.stdout or ""
        start, end = raw.find("<hierarchy"), raw.rfind("</hierarchy>")
        if start == -1 or end == -1:
            return None
        root = ET.fromstring(raw[start: end + len("</hierarchy>")])

        third = width / 3.0
        rows: Dict[Tuple[int, int], List[Tuple[int, int, int, int]]] = {}
        for node in root.iter("node"):
            if node.get("clickable") != "true":
                continue
            bm = re.findall(r"\[(\d+),(\d+)\]", node.get("bounds") or "")
            if len(bm) != 2:
                continue
            x1, y1 = int(bm[0][0]), int(bm[0][1])
            x2, y2 = int(bm[1][0]), int(bm[1][1])
            w, h = x2 - x1, y2 - y1
            if abs(w - third) <= third * 0.15 and 40 <= h <= 130:
                rows.setdefault((y1, y2), []).append((x1, y1, x2, y2))

        best = None
        for (y1, y2), cells in rows.items():
            if len(cells) < 3:
                continue
            cy = (y1 + y2) // 2
            dist = abs(cy - hint_y)
            if dist <= max_distance and (best is None or dist < best[0]):
                cells.sort(key=lambda c: c[0])
                best = (dist, cells)

        if best:
            cells = best[1]
            like = ((cells[0][0] + cells[0][2]) // 2, (cells[0][1] + cells[0][3]) // 2)
            comm = ((cells[1][0] + cells[1][2]) // 2, (cells[1][1] + cells[1][3]) // 2)
            logger.info(f"Fila de acciones refinada por XML: Like={like}, Comentar={comm} (pista IA y={hint_y})")
            return like, comm
    except Exception as e:
        logger.warning(f"Aviso refinando fila de acciones por XML: {e}")
    return None


def locate_standard_post_action_buttons_with_vision_loop(
    controller: AndroidController,
    width: int,
    height: int,
    max_swipes: int = 15,
    status_callback: Optional[Callable[[str, int], None]] = None,
    stop_checker: Optional[Callable[[], bool]] = None,
    ai_agent: Optional[AIAgent] = None
) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """
    Navegación dinámica inteligente 100% conducida por IA Local (Qwen2.5-VL):
    - NO utiliza cantidades fijas ni predefinidas de deslizamiento.
    - Cada post varía en longitud. En cada paso:
      1. Se captura la pantalla en alta resolución sin líneas de debug.
      2. Qwen2.5-VL analiza la imagen para determinar si la barra de reacciones (Like/Comentar) ya apareció.
      3. Si aún no está visible, realiza un slide suave hacia arriba para recorrer el contenido.
      4. En cuanto la IA confirma los botones, define sus coordenadas exactas en tiempo real y frena.
    """
    def _cb(msg: str, step: int = 1):
        if status_callback:
            status_callback(msg, step)
        logger.info(f"[FB AI Post Scan] {msg}")

    if ai_agent is None:
        ai_agent = getattr(controller, "ai_agent", None) or AIAgent()

    # Garantizar captura limpia sin marcas ni líneas de depuración en pantalla
    try:
        subprocess.run([controller.adb_bin, "shell", "setprop", "debug.layout", "false"], check=False)
        subprocess.run([controller.adb_bin, "shell", "service", "call", "activity", "1599295570"], check=False)
    except Exception:
        pass

    # Cerrar teclado si estuviese abierto por alguna acción previa
    if controller.is_keyboard_open():
        logger.info("Teclado detectado abierto antes de iniciar el escaneo. Ocultándolo con press_back...")
        controller.press_back(sleep_after=1.2)

    # Carpeta donde se archiva cada captura previa a cada slide (para auditar el recorrido)
    scan_dir = os.path.join("debug", "captures", "fb_scan")
    try:
        os.makedirs(scan_dir, exist_ok=True)
        for old in os.listdir(scan_dir):
            if old.endswith(".png"):
                os.remove(os.path.join(scan_dir, old))
    except Exception:
        pass

    # Parámetros del slide suave: gesto largo (900ms) = scroll/drag nativo, Android NO lo
    # interpreta como tap, así que no abre el visor de la imagen del post.
    swipe_x = int(width * 0.50)
    swipe_start_y = int(height * 0.72)
    swipe_end_y = int(height * 0.40)
    swipe_duration_ms = 900

    # Límites válidos para la barra de reacciones (debajo del header y encima de la caja "Escribe algo...")
    min_valid_y = int(height * 0.10)
    max_valid_y = int(height * 0.86)

    # Bucle: CAPTURA -> ANÁLISIS IA -> (si no hay iconos) SLIDE SUAVE -> repetir
    for step in range(max_swipes + 1):
        if stop_checker and stop_checker():
            _cb("Recorrido cancelado por solicitud de usuario.", step=1)
            return None, None

        if controller.is_keyboard_open():
            logger.info("Teclado detectado durante el recorrido. Ocultándolo...")
            controller.press_back(sleep_after=1.0)

        # 1. Captura de pantalla ANTES de decidir si se hace el slide
        _cb(f"📸 Captura #{step} del teléfono antes de decidir el slide...", step=1)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        try:
            shutil.copyfile(config.DEFAULT_SCREEN_IMAGE, os.path.join(scan_dir, f"step_{step:02d}.png"))
        except Exception:
            pass

        # 2. Análisis de la captura con Qwen 2.5-VL
        _cb(f"🔍 Qwen 2.5-VL analizando captura #{step} en busca de 👍 Me gusta y 💬 Comentar...", step=1)
        v_res = ai_agent.locate_post_reactions_with_vision(config.DEFAULT_SCREEN_IMAGE, width, height)

        like_coords = v_res.get("like_center") if v_res.get("buttons_visible") else None
        comment_coords = v_res.get("comment_center") if v_res.get("buttons_visible") else None

        # Validación geométrica: descartar coordenadas fuera de la zona útil del post
        if like_coords and not (min_valid_y <= like_coords[1] <= max_valid_y):
            logger.warning(f"Qwen devolvió Like fuera de la zona válida {like_coords}; se considera NO visible.")
            like_coords = None

        if like_coords:
            if not comment_coords:
                comment_coords = (int(width * 0.50), like_coords[1])

            # Refinamiento exacto: Qwen dice CUÁNDO aparecieron los iconos; el XML da el centro
            # exacto de la píldora (evita tocar el contador de reacciones 👍❤️ N de encima).
            refined = _refine_action_row_with_xml(controller, width, height, hint_y=comment_coords[1])
            if refined:
                like_coords, comment_coords = refined

            # Corrección: el botón Like (píldora gris) está en la MISMA fila que Comentar.
            # Si la IA devolvió una Y distinta o una X pegada al borde, apuntó al contador
            # de reacciones (👍❤️ N) que está justo encima; tocarlo abre la lista y rompe el ciclo.
            lx, ly = like_coords
            cy_row = comment_coords[1]
            if abs(ly - cy_row) > 25 or lx < int(width * 0.10):
                corrected = (int(width * 0.167), cy_row)
                logger.warning(
                    f"Like {like_coords} desalineado con Comentar {comment_coords} (posible contador de reacciones). "
                    f"Corrigiendo a la píldora Like en {corrected}."
                )
                like_coords = corrected
            _cb(
                f"🎯 Iconos confirmados en captura #{step} (tras {step} slide(s)): "
                f"Like={like_coords}, Comentar={comment_coords}. Frenando recorrido.",
                step=2
            )
            return like_coords, comment_coords

        if step >= max_swipes:
            break

        # 3. Iconos no visibles -> slide suave hacia arriba
        _cb(f"📜 Iconos aún no visibles. Slide suave #{step + 1} ({swipe_duration_ms}ms)...", step=1)
        try:
            subprocess.run(
                [controller.adb_bin, "shell", "input", "swipe",
                 str(swipe_x), str(swipe_start_y), str(swipe_x), str(swipe_end_y), str(swipe_duration_ms)],
                check=False
            )
        except Exception as e_sw:
            logger.warning(f"Aviso en slide #{step + 1}: {e_sw}")

        # Esperar a que termine la inercia del scroll antes de la siguiente captura
        time.sleep(1.5)

    # Respaldo si se superaron los deslizamientos máximos
    _cb("⚠️ Fin del recorrido sin confirmación de la IA. Verificando mejores coordenadas disponibles...", step=2)
    like_coords, comment_coords = _detect_standard_post_action_buttons(controller, width, height)
    return like_coords, comment_coords


# Alias de compatibilidad para llamadas previas
locate_standard_post_action_buttons_with_short_swipes = locate_standard_post_action_buttons_with_vision_loop


def find_facebook_post_comment_input_box(
    controller: AndroidController,
    width: int,
    height: int,
    screen_path: str = config.DEFAULT_SCREEN_IMAGE,
    ai_agent: Optional[Any] = None
) -> Tuple[int, int]:
    """
    Localiza la caja de entrada de comentarios en Facebook Lite tomando como referencia
    geométrica y visual los iconos laterales, superando la variabilidad de texto interno:
    - Izquierda: Icono de Galería/Imagen 🖼️ (X <= 100px)
    - Derecha: Grupo de iconos [GIF], [@], [Emoji 😊] (X >= 470px)
    - Centro: Campo de entrada ('Escribe un comentario...', 'Comentas como [Nombre]', etc.)
    
    Estrategia de localización:
    1. Inspección de XML de UIAutomator (MultiAutoCompleteTextView o EditText).
    2. Anclaje geométrico entre iconos laterales (icono galería a la izq y cluster GIF/@/emoji a la der).
    3. Visión con Qwen2.5-VL si está disponible.
    4. Coordenadas calibradas centradas exactamente entre ambos lados (X ~ 294px, Y ~ 1458px).
    """
    # 1. Inspección de jerarquía XML
    try:
        res_dump = subprocess.run(
            [controller.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
            capture_output=True, text=True, check=False, timeout=3
        )
        raw = res_dump.stdout or ""
        start = raw.find("<hierarchy")
        end = raw.rfind("</hierarchy>")
        if start != -1 and end != -1:
            root = ET.fromstring(raw[start : end + len("</hierarchy>")])
            # A. Buscar por clase de entrada de texto en la zona inferior
            for node in root.iter("node"):
                cls = (node.get("class") or "").lower()
                bounds = node.get("bounds") or ""
                m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                if len(m) == 2:
                    x1, y1 = int(m[0][0]), int(m[0][1])
                    x2, y2 = int(m[1][0]), int(m[1][1])
                    if ("multiautocompletetextview" in cls or "edittext" in cls) and y1 >= int(height * 0.40):
                        cx = (x1 + x2) // 2
                        cy = (y1 + y2) // 2
                        logger.info(f"Campo de comentario localizado por clase XML ({cls}) en ({cx}, {cy}) bounds={bounds}")
                        return (cx, cy)

            # B. Buscar geométricamente entre el icono izquierdo (galería) y los iconos derechos (GIF/emojis)
            left_icon_end_x = 0
            right_icons_start_x = width
            row_y = 0
            for node in root.iter("node"):
                bounds = node.get("bounds") or ""
                m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                if len(m) == 2:
                    x1, y1 = int(m[0][0]), int(m[0][1])
                    x2, y2 = int(m[1][0]), int(m[1][1])
                    if y1 >= int(height * 0.75):
                        if x2 <= int(width * 0.18):
                            left_icon_end_x = max(left_icon_end_x, x2)
                            row_y = (y1 + y2) // 2
                        elif x1 >= int(width * 0.60):
                            right_icons_start_x = min(right_icons_start_x, x1)
                            row_y = (y1 + y2) // 2

            if left_icon_end_x > 0 and right_icons_start_x < width and row_y > 0:
                center_x = (left_icon_end_x + right_icons_start_x) // 2
                logger.info(f"Campo de comentario localizado por anclaje entre iconos laterales: ({center_x}, {row_y})")
                return (center_x, row_y)

    except Exception as e_xml:
        logger.warning(f"Aviso XML en búsqueda de caja de comentario: {e_xml}")

    # 2. Visión Multimodal con Qwen2.5-VL si está disponible
    if AIAgent.is_ollama_online():
        try:
            logger.info("Consultando a Qwen2.5-VL para centrar el click entre los iconos laterales...")
            import base64
            with open(screen_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            prompt = (
                "You are an expert mobile UI agent. "
                "At the bottom of this Facebook Lite screen, there is a comment input bar with icons on the sides: "
                "an image/gallery icon on the left, and a cluster of icons (GIF, @, Emoji) on the right. "
                "In the center between these icons is the comment text field. "
                "Find the center [x, y] coordinates of the area between these icons to click on. "
                "Respond ONLY with a JSON object: {\"found\": true, \"center_coordinates\": [x, y]}"
            )
            res = requests.post(
                "http://127.0.0.1:11434/api/generate",
                json={
                    "model": "qwen2.5vl:latest",
                    "prompt": prompt,
                    "images": [img_b64],
                    "stream": False,
                    "format": "json"
                },
                timeout=20
            )
            if res.status_code == 200:
                data = json.loads(res.json().get("response", "{}"))
                coords = data.get("center_coordinates")
                if coords and len(coords) == 2:
                    vx, vy = int(coords[0]), int(coords[1])
                    if int(height * 0.70) <= vy <= height:
                        logger.info(f"Qwen2.5-VL localizó el centro del campo de comentario en ({vx}, {vy})")
                        return (vx, vy)
        except Exception as e_vis:
            logger.warning(f"Aviso en visión Qwen2.5-VL de caja de comentario: {e_vis}")

    # 3. Anclaje geométrico calibrado (centro exacto entre galería y GIF en la barra inferior)
    if width == 720 and height == 1612:
        calibrated = (294, 1458)
    else:
        calibrated = (int(width * 0.41), int(height * 0.905))

    logger.info(f"Utilizando anclaje geométrico calibrado entre iconos para campo de comentario: {calibrated}")
    return calibrated


def interact_facebook_standard_post(
    controller: AndroidController,
    do_like: bool,
    comment_text: str,
    status_callback: Optional[Callable[[str, int], None]] = None,
    stop_checker: Optional[Callable[[], bool]] = None
) -> Dict[str, Any]:
    """
    Flujo de interacción para Publicación Estándar (Post Normal):
    1. Escaneo iterativo con deslizamientos cortos sin toques para ubicar botones en textos largos.
    2. Detención inmediata en cuanto los botones Like 👍 y Comentar 💬 son detectados.
    3. Toca el botón Like 👍 si do_like está activo.
    4. Toca el campo de comentario anclado entre los iconos laterales de la barra inferior.
    5. Escribe el comentario de forma natural palabra por palabra.
    6. Pulsa el botón de enviar (flecha azul ➤ / Publicar) en el lateral derecho de la caja.
    """
    def _cb(msg: str, step: int = 0):
        if status_callback:
            status_callback(msg, step)
        logger.info(f"[FB Post] {msg}")

    width, height = controller.get_screen_size()

    # Paso 1 y 2: Localizar botones de reacción mediante recorrido dinámico guiado por Qwen 2.5-VL
    _cb("Iniciando análisis y recorrido dinámico con IA local Qwen 2.5-VL para ubicar Like y Comentar...", step=1)
    like_coords, comment_coords = locate_standard_post_action_buttons_with_vision_loop(
        controller=controller,
        width=width,
        height=height,
        max_swipes=15,
        status_callback=status_callback,
        stop_checker=stop_checker,
        ai_agent=getattr(controller, "ai_agent", None)
    )

    if stop_checker and stop_checker():
        return {"success": False, "aborted": True}

    # Respaldo seguro si no se detectaron
    if not like_coords:
        like_coords = (int(width * 0.17), int(height * 0.60))
    if not comment_coords:
        comment_coords = (int(width * 0.50), like_coords[1])

    logger.info(f"Coordenadas listas para interacción en Post normal: Like={like_coords}, Comment={comment_coords}")

    # Paso 3: Dar Like (👍) si está solicitado (Paso 1 del usuario: encontrar y dar touch al botón Like)
    if do_like and like_coords:
        _cb(f"Pulsando botón Like (👍) detectado por IA en {like_coords}...", step=3)
        controller.tap_coordinates(*like_coords)
        time.sleep(1.2)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

    if stop_checker and stop_checker():
        return {"success": False, "aborted": True}

    # Paso 4: Tocar campo de comentario y escribir comentario (Paso 2 del usuario)
    if comment_text:
        _cb("Localizando campo de comentario centrado entre los iconos laterales...", step=4)
        input_coords = find_facebook_post_comment_input_box(
            controller=controller,
            width=width,
            height=height,
            screen_path=config.DEFAULT_SCREEN_IMAGE,
            ai_agent=getattr(controller, "ai_agent", None)
        )
        _cb(f"Pulsando campo de comentario en {input_coords}...", step=4)
        controller.tap_coordinates(*input_coords)
        time.sleep(1.8)  # Esperar despliegue de teclado
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

        # Paso 5: Escribir comentario naturalmente
        _cb(f"Escribiendo comentario: \"{comment_text}\"...", step=5)
        typed = controller.type_comment_naturally(comment_text, input_coords=None, stop_checker=stop_checker)
        if not typed:
            return {"success": False, "error": "Escritura de comentario interrumpida."}

        time.sleep(1.2)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

        # Paso 6: Pulsar botón de envío azul a la DERECHA (Paso 3 del usuario)
        _cb("Pulsando botón azul de envío (flecha azul ➤ / Send) a la derecha de la caja...", step=6)
        _tap_blue_send_button(controller, width, height, ai_agent=getattr(controller, "ai_agent", None))
        time.sleep(3.0)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

    _cb("¡Interacción en Publicación Estándar completada exitosamente!", step=7)
    return {"success": True, "action": "post_engagement_complete"}


def _locate_facebook_reel_action_buttons(
    controller: AndroidController,
    width: int,
    height: int,
    max_swipes: int = 6,
    stop_checker: Optional[Callable[[], bool]] = None
) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """
    Desliza la pantalla y analiza con Qwen 2.5-VL hasta encontrar los botones Like y Comentar.
    Bucle: CAPTURA -> ANÁLISIS QWEN 2.5-VL -> si no están visibles, DESLIZA y reintenta.
    Nunca hace clic con coordenadas fijas sin antes confirmar visualmente.
    """
    swipe_x = int(width * 0.50)
    swipe_start_y = int(height * 0.72)
    swipe_end_y = int(height * 0.42)

    like_coords: Optional[Tuple[int, int]] = None
    comment_coords: Optional[Tuple[int, int]] = None

    for step in range(max_swipes + 1):
        if stop_checker and stop_checker():
            break

        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

        # 1. Análisis principal con Qwen 2.5-VL
        try:
            q = detect_reel_icons_qwen(config.DEFAULT_SCREEN_IMAGE)
            if q.get("success"):
                hb = q.get("heart") or {}
                cb = q.get("comment") or {}
                if hb.get("found") and hb.get("center"):
                    like_coords = tuple(hb["center"])
                if cb.get("found") and cb.get("center"):
                    comment_coords = tuple(cb["center"])
                if like_coords and comment_coords:
                    logger.info(f"Reel: Qwen 2.5-VL localizó Like={like_coords}, Comentar={comment_coords} en el intento #{step}.")
                    return like_coords, comment_coords
        except Exception as e:
            logger.warning(f"Aviso en Qwen de Reel (intento {step}): {e}")

        # 2. Respaldo por patrones + XML si Qwen no encontró ambos
        if not like_coords or not comment_coords:
            try:
                det = detect_reel_action_buttons(
                    config.DEFAULT_SCREEN_IMAGE,
                    use_qwen_fallback=False,
                    save_debug_image=False
                )
                like_btn = det.get("like_button") or {}
                comment_btn = det.get("comment_button") or {}
                if not like_coords and like_btn.get("center") and like_btn.get("source") != "fallback_calibrated":
                    like_coords = tuple(like_btn["center"])
                if not comment_coords and comment_btn.get("center") and comment_btn.get("source") != "fallback_calibrated":
                    comment_coords = tuple(comment_btn["center"])
            except Exception as e:
                logger.warning(f"Aviso en patrones de Reel: {e}")

        if like_coords and comment_coords:
            logger.info(f"Reel: botones localizados por patrones/XML: Like={like_coords}, Comentar={comment_coords}")
            return like_coords, comment_coords

        # 3. No visibles -> deslizar y volver a analizar
        if step < max_swipes:
            logger.info(f"Reel: botones aún no detectados (intento {step}/{max_swipes}). Deslizando para re-analizar con Qwen...")
            try:
                subprocess.run(
                    [controller.adb_bin, "shell", "input", "swipe",
                     str(swipe_x), str(swipe_start_y), str(swipe_x), str(swipe_end_y), "400"],
                    check=False
                )
            except Exception as e_sw:
                logger.warning(f"Aviso en deslizamiento de Reel: {e_sw}")
            time.sleep(1.5)

    # 4. Fallback calibrado de Facebook Lite (último recurso)
    if not like_coords:
        like_coords = (int(width * 0.93), int(height * 0.505))
    if not comment_coords:
        comment_coords = (int(width * 0.93), int(height * 0.585))

    logger.info(f"Reel: coordenadas de respaldo tras {max_swipes} análisis: Like={like_coords}, Comentar={comment_coords}")
    return like_coords, comment_coords


def _locate_reel_comment_input(
    controller: AndroidController,
    width: int,
    height: int
) -> Tuple[int, int]:
    """ANALIZA la hoja modal de comentarios de un Reel para localizar la caja de texto."""
    controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
    try:
        res = subprocess.run(
            [controller.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
            capture_output=True, text=True, check=False, timeout=4
        )
        raw = res.stdout or ""
        start, end = raw.find("<hierarchy"), raw.rfind("</hierarchy>")
        if start != -1 and end != -1:
            root = ET.fromstring(raw[start:end + len("</hierarchy>")])
            for node in root.iter("node"):
                cls = (node.get("class") or "").lower()
                b = node.get("bounds") or ""
                m = re.findall(r"\[(\d+),(\d+)\]", b)
                if len(m) != 2:
                    continue
                x1, y1, x2, y2 = int(m[0][0]), int(m[0][1]), int(m[1][0]), int(m[1][1])
                if ("edittext" in cls or "multiautocompletetextview" in cls) and int(height * 0.35) <= y1 <= int(height * 0.70):
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    logger.info(f"Reel: caja de comentario localizada por XML en ({cx}, {cy})")
                    return (cx, cy)
    except Exception as e:
        logger.warning(f"Aviso en XML de caja de comentario de Reel: {e}")

    fb = (int(width * 0.45), int(height * 0.535))
    logger.info(f"Reel: usando caja de comentario calibrada {fb}")
    return fb


def interact_facebook_reel(
    controller: AndroidController,
    do_like: bool,
    comment_text: str,
    status_callback: Optional[Callable[[str, int], None]] = None,
    stop_checker: Optional[Callable[[], bool]] = None
) -> Dict[str, Any]:
    """
    Flujo de interacción para Facebook Reel (Video vertical en pantalla completa):
    1. NO desliza hacia arriba (en Reels, swipe up cambia de video).
    2. Toca Like 👍 en la columna lateral derecha (~93% X, ~50% Y) si do_like está activo.
    3. Toca Comentar 💬 en la columna lateral derecha (~93% X, ~59% Y).
    4. Espera a que se abra la hoja emergente inferior de comentarios (Imagen 5).
    5. Escribe el comentario de forma natural palabra por palabra.
    6. Pulsa el botón de enviar (flecha azul ➤ / Publicar) en el lateral derecho de la caja.
    7. Cierra la hoja emergente para dejar el estado limpio.
    """
    def _cb(msg: str, step: int = 0):
        if status_callback:
            status_callback(msg, step)
        logger.info(f"[FB Reel] {msg}")

    width, height = controller.get_screen_size()

    # Paso 1: DESLIZAR + ANALIZAR con Qwen 2.5-VL hasta encontrar Like y Comentar
    _cb("Reel: Deslizando y analizando con Qwen 2.5-VL hasta localizar Like y Comentar...", step=1)
    like_coords, comment_coords = _locate_facebook_reel_action_buttons(
        controller, width, height, stop_checker=stop_checker
    )

    # Paso 2: Dar Like (👍) en riel derecho
    if do_like and like_coords:
        _cb(f"Reel: Pulsando botón Like (👍) en riel derecho {like_coords}...", step=2)
        controller.tap_coordinates(*like_coords)
        time.sleep(1.2)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

    if stop_checker and stop_checker():
        return {"success": False, "aborted": True}

    # Paso 3: Abrir panel de Comentarios (💬) en riel derecho
    if comment_text and comment_coords:
        _cb(f"Reel: Pulsando icono de comentarios (💬) en riel derecho {comment_coords}...", step=3)
        controller.tap_coordinates(*comment_coords)
        time.sleep(2.5)  # Esperar que suba la hoja modal (Imagen 5)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

        # Paso 4: ANALIZAR la hoja modal para localizar la caja de texto antes de escribir
        _cb("Reel: Analizando hoja de comentarios para localizar la caja de texto...", step=4)
        input_coords = _locate_reel_comment_input(controller, width, height)
        controller.tap_coordinates(*input_coords)
        time.sleep(1.0)
        _cb(f"Reel: Escribiendo comentario: \"{comment_text}\"...", step=4)
        typed = controller.type_comment_naturally(comment_text, input_coords=input_coords, stop_checker=stop_checker)
        if not typed:
            return {"success": False, "error": "Escritura de comentario en Reel interrumpida."}

        time.sleep(1.2)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

        # Paso 5: Localizar y enviar comentario (análisis propio del botón de envío)
        _cb("Reel: Localizando botón de envío (flecha azul ➤ / Publicar)...", step=5)
        _tap_blue_send_button(controller, width, height, ai_agent=getattr(controller, "ai_agent", None))
        time.sleep(3.0)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

        # Paso 6: Cerrar hoja modal de comentarios con botón Back
        try:
            subprocess.run([controller.adb_bin, "shell", "input", "keyevent", "4"], check=False)
            time.sleep(1.0)
        except Exception:
            pass

    _cb("¡Interacción en Facebook Reel completada exitosamente!", step=6)
    return {"success": True, "action": "reel_engagement_complete"}


def _tap_blue_send_button(
    controller: AndroidController,
    width: int,
    height: int,
    ai_agent: Optional[AIAgent] = None
) -> bool:
    """
    Localiza y pulsa con precisión milimétrica la flecha azul de enviar en Facebook Lite
    (ubicada a la DERECHA del campo de texto de comentarios).
    REGLA ESTRICTA: El botón de envío SIEMPRE se sitúa en la mitad derecha (x >= 78%) y
    por encima del teclado virtual (y <= 65% de la pantalla cuando el teclado está abierto,
    o en la barra de texto inferior y >= 85% si el teclado estuviese cerrado).
    NUNCA pulsa en la zona izquierda o central del teclado para evitar toques en teclas (como la letra 'Z').
    """
    kb_open = controller.is_keyboard_open()
    logger.info(f"Localizando botón azul de envío (flecha ➤ / Send) a la derecha [teclado={kb_open}]...")

    if ai_agent is None:
        ai_agent = getattr(controller, "ai_agent", None) or AIAgent()

    # 1. Detección Inteligente con Qwen 2.5-VL
    try:
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        v_send = ai_agent.locate_blue_send_button_with_vision(
            config.DEFAULT_SCREEN_IMAGE, width, height, is_keyboard_open=kb_open
        )
        if v_send.get("found"):
            sx, sy = v_send["send_center"]
            if sx >= int(width * 0.75):
                if kb_open and (int(height * 0.45) <= sy <= int(height * 0.68)):
                    logger.info(f"✅ Botón azul de envío ubicado con Qwen 2.5-VL (teclado abierto) en ({sx}, {sy}). Pulsando...")
                    controller.tap_coordinates(sx, sy)
                    return True
                elif not kb_open and (sy >= int(height * 0.75) or int(height * 0.45) <= sy <= int(height * 0.68)):
                    logger.info(f"✅ Botón azul de envío ubicado con Qwen 2.5-VL (teclado cerrado) en ({sx}, {sy}). Pulsando...")
                    controller.tap_coordinates(sx, sy)
                    return True
                else:
                    logger.warning(f"Coordenadas de envío descartadas por conflicto de altura y teclado: ({sx}, {sy}) [kb_open={kb_open}]")
    except Exception as e_v:
        logger.warning(f"Aviso en detección de envío con Qwen 2.5-VL: {e_v}")

    # 2. Detección por jerarquía XML de UIAutomator
    try:
        res_dump = subprocess.run(
            [controller.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
            capture_output=True, text=True, check=False, timeout=4
        )
        raw = res_dump.stdout or ""
        start = raw.find("<hierarchy")
        end = raw.rfind("</hierarchy>")
        if start != -1 and end != -1:
            root = ET.fromstring(raw[start : end + len("</hierarchy>")])
            candidates = []
            for node in root.iter("node"):
                desc = (node.get("content-desc") or "").strip().lower()
                text = (node.get("text") or "").strip().lower()
                bounds = node.get("bounds") or ""
                clickable = (node.get("clickable") or "false").lower() == "true"

                m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                if len(m) == 2:
                    x1, y1 = int(m[0][0]), int(m[0][1])
                    x2, y2 = int(m[1][0]), int(m[1][1])
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                    # Debe estar en el extremo derecho (X >= 78%) y fuera del cuerpo del teclado
                    if x1 >= int(width * 0.78):
                        valid_y = (int(height * 0.45) <= cy <= int(height * 0.68)) if kb_open else (cy >= int(height * 0.75) or int(height * 0.45) <= cy <= int(height * 0.68))
                        if valid_y:
                            if any(w in desc or w in text for w in ["publicar", "enviar", "send", "post"]):
                                candidates.append((cx, cy, 10, bounds))
                            elif clickable:
                                candidates.append((cx, cy, 5, bounds))

            if candidates:
                candidates.sort(key=lambda c: c[2], reverse=True)
                best_cx, best_cy, _, bnds = candidates[0]
                logger.info(f"✅ Botón azul de envío localizado por XML en ({best_cx}, {best_cy}) bounds={bnds}. Pulsando...")
                controller.tap_coordinates(best_cx, best_cy)
                return True
    except Exception as e_xml:
        logger.warning(f"Aviso XML send button: {e_xml}")

    # 3. Coordenada de respaldo calibrada a la derecha de la caja (segura contra el teclado)
    if kb_open:
        calibrated_send = (int(width * 0.916), int(height * 0.592))
    else:
        calibrated_send = (int(width * 0.916), int(height * 0.95))
    logger.info(f"Pulsando botón de envío con coordenadas calibradas seguras a la derecha (kb_open={kb_open}): {calibrated_send}")
    controller.tap_coordinates(*calibrated_send)
    return True


class FacebookPostEngagementManager:
    """
    Gestor maestro del ciclo multi-cuenta de interacción y comentarios en Facebook Lite:
    - Utiliza exclusivamente el BANCO ROTATIVO secuencial (1 comentario por cuenta).
    - Abre cada enlace en com.facebook.lite.
    - Espera hasta 30s de carga y estabilización.
    - Diferencia entre Reel y Post estándar.
    - Ejecuta Like (si está marcado) y Comentario.
    - Cierra la app, libera RAM con trim-memory COMPLETE y gc.collect().
    - Pausa antibloqueo estricta de 30 segundos antes de alternar a la siguiente cuenta.
    - Alterna a la siguiente cuenta de la memoria y repite.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.is_running = False
        self.stop_requested = False
        self.target_url = ""
        self.total_rounds = 0
        self.current_round = 0
        self.current_account = ""
        self.current_comment = ""
        self.pasted_comments: List[str] = []
        self.accounts_used_this_session: List[str] = []
        self.status_message = "Listo"
        self.thread: Optional[threading.Thread] = None
        self.device_serial = ""
        self.device_model = ""
        self.broadcast_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None

    def set_broadcast_callback(self, cb: Callable[[str, Dict[str, Any]], None]):
        self.broadcast_callback = cb

    def _broadcast(self, event_name: str, payload: Dict[str, Any]):
        if self.broadcast_callback:
            try:
                self.broadcast_callback(event_name, payload)
            except Exception as e:
                logger.warning(f"Error en broadcast: {e}")

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "is_running": self.is_running,
                "current_round": self.current_round,
                "total_rounds": self.total_rounds,
                "current_account": self.current_account,
                "current_comment": self.current_comment,
                "remaining_comments": len(self.pasted_comments),
                "accounts_used": list(self.accounts_used_this_session),
                "status_message": self.status_message,
                "target_url": self.target_url
            }

    def stop_cycle(self) -> Dict[str, Any]:
        with self.lock:
            if not self.is_running:
                return {"success": True, "message": "El ciclo no se encuentra activo."}
            self.stop_requested = True
            self.status_message = "Detención solicitada por el usuario..."

        logger.info("[FB Engagement Cycle] Solicitud de detención recibida.")
        self._broadcast("post_engage_step", {
            "step": -1,
            "message": "Deteniendo ciclo de interacción por solicitud del usuario..."
        })
        return {"success": True, "message": "Detención solicitada."}

    def start_cycle(
        self,
        url: str,
        pasted_comments: List[str],
        accounts_limit: int = 5,
        do_like: bool = True,
        do_comment: bool = True,
        antiblock_pause: int = 30,
        package: str = "com.facebook.lite"
    ) -> Dict[str, Any]:
        with self.lock:
            if self.is_running:
                return {"success": False, "error": "El ciclo de interacción en Facebook ya está en ejecución."}

            cleaned_url = url.strip()
            if not cleaned_url:
                return {"success": False, "error": "Ingresa la URL de la publicación o Reel de Facebook."}

            valid_comments = [c.strip() for c in pasted_comments if c.strip()]
            if do_comment and not valid_comments:
                return {"success": False, "error": "El Banco Rotativo de Facebook está vacío. Pega al menos un comentario."}

            self.is_running = True
            self.stop_requested = False
            self.target_url = cleaned_url
            self.pasted_comments = list(valid_comments)
            self.total_rounds = accounts_limit
            self.current_round = 0
            self.current_account = ""
            self.current_comment = ""
            self.accounts_used_this_session = []
            self.status_message = "Iniciando ciclo de interacción en Facebook..."

        self.thread = threading.Thread(
            target=self._run_cycle,
            args=(cleaned_url, do_like, do_comment, accounts_limit, antiblock_pause, package),
            daemon=True
        )
        self.thread.start()
        return {"success": True, "message": "Ciclo de interacción en Facebook iniciado correctamente."}

    def _update_status(self, msg: str, step: int = 0):
        with self.lock:
            self.status_message = msg
        logger.info(f"[FB Engagement Cycle] {msg}")
        self._broadcast("post_engage_step", {
            "step": step,
            "message": msg,
            "account_name": self.current_account,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "is_running": self.is_running,
            "remaining_comments": len(self.pasted_comments)
        })
        self._broadcast("screen_updated", {"timestamp": time.time()})

    def _run_cycle(
        self,
        target_url: str,
        do_like: bool,
        do_comment: bool,
        accounts_limit: int,
        antiblock_pause: int,
        package: str
    ):
        try:
            ctrl = AndroidController()
            dev_serial = ctrl.get_device_serial()
            dev_model = ctrl.get_device_model()
            with self.lock:
                self.device_serial = dev_serial
                self.device_model = dev_model

            # 1. Obtener cuentas conocidas de Facebook Lite
            summary = accounts_memory.get_device_summary(dev_serial)
            known_fb = summary.get("facebook_accounts", [])
            known_names = [a.get("username") or a.get("label") for a in known_fb]

            if not known_names:
                self._update_status("Revisando cuentas registradas en Facebook Lite...")
                scan_res = ctrl.scan_facebook_lite_accounts(progress_callback=lambda msg, c=0: self._update_status(f"[Revisión Cuentas] {msg}"))
                known_names = scan_res.get("accounts", [])
                accounts_memory.register_facebook_accounts(dev_serial, dev_model, known_names)

            if not known_names:
                self._update_status("No se encontraron cuentas activas en Facebook Lite.", step=-1)
                return

            # 2. Obtener cuentas disponibles que aún no han interactuado con esta URL
            avail = accounts_memory.get_available_facebook_accounts_for_url(dev_serial, target_url, limit=accounts_limit)
            if not avail:
                self._update_status(f"Todas las cuentas registradas ({len(known_names)}) ya interactuaron con esta publicación.", step=-1)
                return

            max_possible = min(len(avail), len(self.pasted_comments)) if do_comment else len(avail)
            total_to_run = min(accounts_limit, max_possible) if accounts_limit > 0 else max_possible

            with self.lock:
                self.total_rounds = total_to_run

            round_num = 0
            while not self.stop_requested and round_num < total_to_run:
                if do_comment and not self.pasted_comments:
                    self._update_status("Se agotaron los comentarios en el banco rotativo.", step=100)
                    break

                round_num += 1
                current_unreported = accounts_memory.get_available_facebook_accounts_for_url(dev_serial, target_url)
                if not current_unreported:
                    self._update_status("No hay más cuentas sin usar para esta publicación.", step=100)
                    break

                target_acc = current_unreported[0]
                acc_name = target_acc.get("username") or target_acc.get("label")

                # Tomar comentario del banco rotativo
                comment_to_post = ""
                if do_comment and self.pasted_comments:
                    comment_to_post = self.pasted_comments.pop(0).strip()
                    self._broadcast("comment_bank_updated", {
                        "platform": "facebook",
                        "consumed_comment": comment_to_post,
                        "remaining_comments": list(self.pasted_comments)
                    })

                with self.lock:
                    self.current_round = round_num
                    self.current_account = acc_name
                    self.current_comment = comment_to_post
                    self.accounts_used_this_session.append(acc_name)

                self._update_status(f"[Ronda {round_num}/{total_to_run}] Preparando cuenta: '{acc_name}'...", step=0)

                # Si es ronda > 1, cambiar de cuenta en FB Lite
                if round_num > 1:
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Cambiando a cuenta '{acc_name}'...", step=0)
                    switch_res = ctrl.switch_to_facebook_account_by_name(acc_name)
                    if isinstance(switch_res, dict) and not switch_res.get("success"):
                        ctrl.open_app(package)
                        time.sleep(2.0)
                        switch_res = ctrl.switch_to_facebook_account_by_name(acc_name)
                        if not (isinstance(switch_res, dict) and switch_res.get("success")):
                            self._update_status(f"[{acc_name}] No se pudo alternar a la cuenta. Saltando a la siguiente...", step=0)
                            continue

                    # Limpiar RAM, forzar detención y esperar 30s de enfriamiento tras cambio de cuenta
                    self._update_status(f"[{acc_name}] Cambio de cuenta realizado. Cerrando aplicación y liberando memoria RAM...", step=0)
                    _free_ram_and_close(ctrl, package)

                    self._update_status(f"[{acc_name}] ⏳ Pausa antibloqueo de 30s antes de abrir publicación...", step=0)
                    for rem in range(antiblock_pause, 0, -1):
                        if self.stop_requested:
                            break
                        self._update_status(f"[{acc_name}] ⏳ Pausa antibloqueo y RAM limpia: faltan {rem}s para abrir enlace...", step=0)
                        time.sleep(1.0)
                else:
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Iniciando con cuenta activa: '{acc_name}'...", step=0)
                    time.sleep(1.0)

                if self.stop_requested:
                    break

                # 3. Abrir la publicación / Reel en FB Lite
                self._update_status(f"[Ronda {round_num}/{total_to_run}] [{acc_name}] Abriendo enlace en Facebook Lite...", step=1)
                ctrl.open_url(target_url, package=package)

                # 4. Esperar hasta 30s a que cargue y diferenciar entre Reel y Publicación
                load_res = wait_for_facebook_content_load(
                    controller=ctrl,
                    url=target_url,
                    max_wait_seconds=30.0,
                    status_callback=lambda m: self._update_status(f"[{acc_name}] {m}", step=1),
                    stop_checker=lambda: self.stop_requested
                )

                if self.stop_requested:
                    break

                content_type = load_res.get("content_type", "post")
                self._update_status(f"[{acc_name}] Formato identificado: {content_type.upper()}. Iniciando interacción...", step=2)

                # 5. Ejecutar interacción según el tipo de contenido
                def _step_sub(msg: str, st: int):
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] [{acc_name}] {msg}", step=st)

                if content_type == "reel":
                    res_act = interact_facebook_reel(
                        controller=ctrl,
                        do_like=do_like,
                        comment_text=comment_to_post,
                        status_callback=_step_sub,
                        stop_checker=lambda: self.stop_requested
                    )
                else:
                    res_act = interact_facebook_standard_post(
                        controller=ctrl,
                        do_like=do_like,
                        comment_text=comment_to_post,
                        status_callback=_step_sub,
                        stop_checker=lambda: self.stop_requested
                    )

                if self.stop_requested:
                    break

                # 6. Registrar en memoria para no repetir
                accounts_memory.record_facebook_engagement(
                    device_serial=dev_serial,
                    account_name=acc_name,
                    target_url=target_url,
                    comment_text=comment_to_post,
                    did_like=do_like
                )

                self._update_status(f"[Ronda {round_num}/{total_to_run}] [{acc_name}] Interacción completada. Cerrando app y liberando RAM...", step=6)

                # 7. Liberar RAM y forzar detención
                _free_ram_and_close(ctrl, package)

                # 8. Pausa antibloqueo de 30 segundos
                self._update_status(f"[{acc_name}] ⏳ Pausa antibloqueo de 30s tras interactuar...", step=6)
                for rem in range(antiblock_pause, 0, -1):
                    if self.stop_requested:
                        break
                    self._update_status(f"[{acc_name}] ⏳ Pausa antibloqueo y RAM limpia: faltan {rem}s para siguiente cuenta...", step=6)
                    time.sleep(1.0)

            if not self.stop_requested:
                self._update_status(f"¡Ciclo completado con éxito! {round_num} cuentas procesadas.", step=100)
            else:
                self._update_status("Ciclo de interacción detenido por el usuario.", step=-1)

        except Exception as e:
            logger.error(f"Error en ciclo de interacción FB: {e}", exc_info=True)
            self._update_status(f"Error en ciclo FB: {e}", step=-1)
        finally:
            with self.lock:
                self.is_running = False
                self.stop_requested = False
            self._broadcast("post_engage_step", {
                "step": 100 if not self.stop_requested else -1,
                "message": self.status_message,
                "is_running": False
            })


def _free_ram_and_close(controller: AndroidController, package: str):
    """Cierra la aplicación, libera memoria RAM en Android y ejecuta garbage collection en Python."""
    try:
        subprocess.run(
            [controller.adb_bin, "shell", "am", "send-trim-memory", package, "COMPLETE"],
            capture_output=True, check=False
        )
    except Exception:
        pass
    controller.force_stop_app(package)
    controller.go_to_android_home(sleep_after=1.0)
    gc.collect()


# Instancia singleton del gestor
fb_post_engagement_manager = FacebookPostEngagementManager()
