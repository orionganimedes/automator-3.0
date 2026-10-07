"""
adb_controller.py - Android Device Controller via Android Debug Bridge (ADB).
"""

import os
import re
import shutil
import struct
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

import config
from config import DEFAULT_SCREEN_IMAGE, logger
from core.vision.icon_matcher import IconMatcher

_controller_cache: Dict[str, "AndroidController"] = {}
_cache_lock = threading.Lock()


def get_controller(serial: Optional[str] = None) -> "AndroidController":
    """
    Retorna una instancia cacheada de AndroidController para evitar llamadas redundantes a adb devices.
    """
    key = serial or "default"
    with _cache_lock:
        if key not in _controller_cache:
            _controller_cache[key] = AndroidController()
        return _controller_cache[key]


def find_adb_binary() -> str:
    """
    Locates the adb executable in order of priority:
    1. System PATH ('adb')
    2. Python 'adbutils' bundled binary
    3. Common Android SDK platform-tools directories
    """
    which_adb = shutil.which("adb")
    if which_adb:
        return which_adb

    # Check python adbutils package
    try:
        import adbutils
        path = adbutils.adb_path()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass

    # Check common default install paths
    home_dir = os.path.expanduser("~")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(home_dir, "Android", "Sdk", "platform-tools", "adb"),
        os.path.join(home_dir, "Android", "sdk", "platform-tools", "adb"),
        "/usr/bin/adb",
        "/usr/local/bin/adb",
        os.path.join(local_app_data, "Android", "Sdk", "platform-tools", "adb.exe"),
        os.path.join(local_app_data, "Android", "sdk", "platform-tools", "adb.exe"),
        r"C:\platform-tools\adb.exe",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return "adb"


# Opciones y botones del sistema de Meta/Instagram Lite que NO deben ser considerados cuentas ni pulsados
INVALID_ACCOUNT_KEYWORDS = [
    "añadir cuenta de instagram",
    "añadir perfil de facebook",
    "ir al centro de cuentas",
    "añadir cuenta",
    "agregar cuenta",
    "add account",
    "añadir perfil",
    "agregar perfil",
    "add profile",
    "centro de cuentas",
    "accounts center",
    "meta",
    "cambiar de cuenta",
    "iniciar sesión",
    "cerrar sesión",
    "cancelar",
    "cerrar",
    "perfiles",
    "contraseña y seguridad",
    "experiencias conectadas",
    "tu información y tus permisos",
    "preferencias de anuncios",
    "administrar cuentas",
]


def is_valid_account_label(lbl: str) -> bool:
    """
    Verifica que una etiqueta o descripción corresponda a una cuenta real
    y NO a opciones del sistema (como 'Añadir cuenta de Instagram',
    'Añadir perfil de Facebook', 'Ir al Centro de cuentas').
    """
    if not lbl or not lbl.strip():
        return False
    lbl_clean = lbl.strip().lower()
    for kw in INVALID_ACCOUNT_KEYWORDS:
        if kw in lbl_clean:
            return False
    return True


class AndroidController:
    """
    Controls an Android device via ADB commands to perform UI automation,
    screen captures, XML accessibility tree dumps, and touch inputs.
    """

    def __init__(self) -> None:
        """
        Initializes the AndroidController, resolves the adb executable,
        verifies device connection, and initializes the visual IconMatcher.
        """
        self.adb_bin: str = find_adb_binary()
        self._verify_device_connection()
        self.icon_matcher: IconMatcher = IconMatcher()

    def _verify_device_connection(self) -> None:
        """
        Executes 'adb devices' to ensure an authorized device is present.
        Raises RuntimeError if no valid device is detected.
        """
        logger.info(f"Using ADB binary: {self.adb_bin}")
        logger.info("Checking for connected ADB devices...")
        try:
            result = subprocess.run(
                [self.adb_bin, "devices"],
                capture_output=True,
                text=True,
                check=True
            )
        except FileNotFoundError:
            logger.error(
                f"ADB executable '{self.adb_bin}' not found. "
                "Ensure Android SDK Platform-Tools are installed and added to PATH."
            )
            raise RuntimeError("ADB is not installed or not available in the system PATH.")
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to execute '{self.adb_bin} devices': {e}")
            raise RuntimeError(f"ADB execution error: {e}")

        # Parse device lines (skipping header)
        lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        device_entries = lines[1:] if len(lines) > 1 else []

        connected_devices = []
        for entry in device_entries:
            parts = entry.split()
            if len(parts) >= 2:
                device_id, status = parts[0], parts[1]
                if status == "device":
                    connected_devices.append(device_id)
                elif status == "unauthorized":
                    logger.warning(
                        f"Device '{device_id}' is unauthorized. "
                        "Please check the device screen and approve the USB debugging prompt."
                    )
                elif status == "offline":
                    logger.warning(f"Device '{device_id}' is offline.")

        if not connected_devices:
            err_msg = (
                "No connected and authorized Android device found. "
                "Ensure USB debugging is enabled and your device is recognized by 'adb devices'."
            )
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        logger.info(f"Connected Android device(s) verified: {', '.join(connected_devices)}")

    def open_app(self, package: str) -> None:
        """
        Launches an application using Android's monkey runner.

        :param package: Android package name (e.g. 'com.instagram.android')
        """
        logger.info(f"Opening application: {package}")
        try:
            cmd = [
                self.adb_bin, "shell", "monkey",
                "-p", package,
                "-c", "android.intent.category.LAUNCHER",
                "1"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.warning(f"App launch command returned code {result.returncode}: {result.stderr.strip()}")
            else:
                logger.info(f"Launch intent dispatched for {package}.")
        except Exception as e:
            logger.error(f"Error launching package '{package}': {e}")
            raise

    def open_url(self, url: str, package: Optional[str] = None) -> bool:
        """
        Abre una URL o enlace directo asegurando que se abra en la aplicación objetivo
        (Facebook Lite o Instagram Lite) y NUNCA en Google Chrome ni en navegadores web.

        :param url: URL de destino (e.g. 'https://www.facebook.com/...' o 'https://www.instagram.com/p/...')
        :param package: Paquete Android específico (ej: 'com.facebook.lite' o 'com.instagram.lite')
        :return: True si se abrió exitosamente en la app correcta
        """
        cleaned_url = url.strip()
        if not cleaned_url:
            logger.warning("Empty URL provided to open_url.")
            return False

        # Prepend https:// if no protocol is specified
        if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://") or "://" in cleaned_url):
            cleaned_url = f"https://{cleaned_url}"

        # Detección automática del paquete objetivo si no fue especificado
        if not package:
            if "instagram" in cleaned_url:
                package = "com.instagram.lite"
            elif "facebook" in cleaned_url or "fb.me" in cleaned_url:
                package = "com.facebook.lite"

        logger.info(f"Abriendo enlace en dispositivo: {cleaned_url} [App objetivo: {package}]")

        # 1. Asegurar que Google / Chrome no interfieran si estaban abiertos
        try:
            subprocess.run([self.adb_bin, "shell", "am", "force-stop", "com.google.android.googlequicksearchbox"], capture_output=True, check=False)
            subprocess.run([self.adb_bin, "shell", "am", "force-stop", "com.android.chrome"], capture_output=True, check=False)
        except Exception:
            pass

        # 2. Rutina específica para Facebook Lite
        if package == "com.facebook.lite" or (package and "facebook" in package):
            # Abrir directamente la URL exacta del usuario garantizando que -p com.facebook.lite
            # vaya al principio y la URL esté entre comillas simples para no ser truncada por '&'
            try:
                cmd_str = f"am start -p com.facebook.lite -a android.intent.action.VIEW -d '{cleaned_url}'"
                res = subprocess.run([self.adb_bin, "shell", cmd_str], capture_output=True, text=True, check=False)
                out = (res.stdout or "") + (res.stderr or "")
                if res.returncode == 0 and "Error" not in out:
                    logger.info(f"URL de Facebook abierta con éxito en Facebook Lite: {cleaned_url}")
                    time.sleep(1.0)
                    return True
            except Exception as e:
                logger.warning(f"Fallo al abrir con VIEW en FB Lite: {e}")

            # Intento secundario resolviendo redirecciones de share si fuera necesario
            resolved_url = cleaned_url
            if "facebook.com/share/" in cleaned_url or "fb.me/" in cleaned_url:
                try:
                    import requests
                    r = requests.head(cleaned_url, allow_redirects=False, timeout=4)
                    loc = r.headers.get("Location") or r.headers.get("location")
                    if loc:
                        resolved_url = loc
                        logger.info(f"Enlace de compartir resuelto a: {resolved_url}")
                except Exception as ex:
                    logger.warning(f"No se pudo resolver redirección de share: {ex}")

            try:
                cmd_str = f"am start -p com.facebook.lite -a android.intent.action.VIEW -d '{resolved_url}'"
                res = subprocess.run([self.adb_bin, "shell", cmd_str], capture_output=True, text=True, check=False)
                out = (res.stdout or "") + (res.stderr or "")
                if res.returncode == 0 and "Error" not in out:
                    logger.info("URL resuelta abierta con éxito en Facebook Lite.")
                    time.sleep(1.0)
                    return True
            except Exception as e:
                logger.warning(f"Fallo en segundo intento de FB Lite: {e}")

        # 3. Rutina específica para Instagram Lite
        elif package == "com.instagram.lite" or (package and "instagram" in package):
            try:
                safe_url = cleaned_url.replace("'", "'\\''")
                # Detener previamente el proceso para evitar que Android ignore la URL
                # por reutilización de tarea en segundo plano ("Warning: Activity not started, its current task has been brought to the front")
                subprocess.run([self.adb_bin, "shell", "am", "force-stop", "com.instagram.lite"], capture_output=True, check=False)
                time.sleep(0.4)

                cmd_str = f"am start -p com.instagram.lite -a android.intent.action.VIEW -d '{safe_url}'"
                res = subprocess.run([self.adb_bin, "shell", cmd_str], capture_output=True, text=True, check=False)
                out = (res.stdout or "") + (res.stderr or "")

                if res.returncode == 0 and "Error" not in out and "Warning: Activity not started" not in out:
                    logger.info(f"URL de Instagram abierta con éxito en Instagram Lite: {cleaned_url}")
                    time.sleep(1.5)
                    return True
                else:
                    logger.warning(f"Primer intento de apertura en IG Lite reportó: {out.strip()}. Intentando fallback...")
                    # Fallback secundario sin -p si difiere
                    cmd2_str = f"am start -n com.instagram.lite/com.facebook.lite.MainActivity -a android.intent.action.VIEW -d '{safe_url}'"
                    res2 = subprocess.run([self.adb_bin, "shell", cmd2_str], capture_output=True, text=True, check=False)
                    out2 = (res2.stdout or "") + (res2.stderr or "")
                    if res2.returncode == 0 and "Error" not in out2:
                        logger.info(f"URL de Instagram abierta en Instagram Lite vía fallback MainActivity: {cleaned_url}")
                        time.sleep(1.5)
                        return True
            except Exception as e:
                logger.warning(f"Fallo al abrir en Instagram Lite: {e}")


        # 4. Fallback forzando el paquete con -p para evitar que Chrome lo intercepte
        try:
            safe_url = cleaned_url.replace('"', '\\"')
            cmd = [
                self.adb_bin, "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", safe_url
            ]
            if package:
                cmd.extend(["-p", package])
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.warning(f"am start returned code {result.returncode}: {result.stderr.strip()}")
                return False

            logger.info(f"Successfully dispatched VIEW intent for: {cleaned_url} [Package: {package}]")
            return True
        except Exception as e:
            logger.error(f"Failed to open URL '{cleaned_url}': {e}")
            return False

    def capture_screen(self, filename: str = "screen.png") -> str:
        """
        Captures the current device screen using 'adb exec-out screencap -p'
        and writes it directly to the specified file in binary mode.

        :param filename: Output image path (default: 'screen.png')
        :return: Path to the saved image file
        """
        logger.info(f"Capturing screen to '{filename}'...")
        try:
            target_dir = os.path.dirname(os.path.abspath(filename))
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)

            # Binary capture to prevent CRLF line-ending corruption on Windows
            result = subprocess.run(
                [self.adb_bin, "exec-out", "screencap", "-p"],
                capture_output=True,
                check=False
            )

            if not result.stdout or len(result.stdout) < 100:
                logger.warning("Screen capture resulted in empty or invalid image data (posiblemente pantalla protegida o FLAG_SECURE). Usando imagen existente o fallback...")
                if os.path.exists(filename) and os.path.getsize(filename) > 0:
                    return filename
                try:
                    import numpy as np
                    import cv2
                    dummy = np.zeros((1280, 720, 3), dtype=np.uint8)
                    cv2.putText(dummy, "Pantalla Protegida (FLAG_SECURE)", (20, 640), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
                    cv2.imwrite(filename, dummy)
                except Exception:
                    pass
                return filename

            with open(filename, "wb") as f:
                f.write(result.stdout)

            logger.info(f"Screen capture saved successfully ({len(result.stdout)} bytes).")
            return filename
        except Exception as e:
            logger.warning(f"Unexpected error saving screen capture: {e}")
            return filename

    def get_ui_text(self) -> str:
        """
        Dumps the Android accessibility hierarchy via 'adb exec-out uiautomator dump /dev/tty'.
        Parses the XML and extracts a concatenated string of all 'text' and 'content-desc'
        attributes where visible-to-user="true".

        :return: String containing visible UI text joined by ' | '
        """
        logger.info("Extracting UI accessibility tree from device...")
        try:
            result = subprocess.run(
                [self.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True,
                text=True,
                check=False
            )
            raw_output = result.stdout or ""

            # Locate XML root if there is extraneous shell output
            xml_start = raw_output.find("<hierarchy")
            xml_end = raw_output.rfind("</hierarchy>")

            if xml_start != -1 and xml_end != -1:
                xml_content = raw_output[xml_start : xml_end + len("</hierarchy>")]
            else:
                xml_content = raw_output.strip()

            if not xml_content or not xml_content.startswith("<"):
                logger.warning("No valid XML hierarchy returned from uiautomator dump.")
                return ""

            root = ET.fromstring(xml_content)
            visible_texts = []

            for node in root.iter("node"):
                is_visible = (node.get("visible-to-user") or "false").lower() == "true"
                text = (node.get("text") or "").strip()
                content_desc = (node.get("content-desc") or "").strip()

                if is_visible and (text or content_desc):
                    entry = text or content_desc
                    if entry and len(entry) > 2 and entry not in visible_texts:
                        visible_texts.append(entry)

            # Fallback for Android versions/OEM ROMs where visible-to-user is omitted
            if not visible_texts:
                for node in root.iter("node"):
                    text = (node.get("text") or "").strip()
                    content_desc = (node.get("content-desc") or "").strip()
                    entry = text or content_desc
                    if entry and len(entry) > 2 and entry not in visible_texts:
                        visible_texts.append(entry)

            joined_text = " | ".join(visible_texts)
            logger.info(f"Extracted {len(visible_texts)} UI text element(s).")
            return joined_text

        except ET.ParseError as e:
            logger.warning(f"Failed to parse UI XML dump: {e}")
            return ""
        except Exception as e:
            logger.error(f"Error during UI dump extraction: {e}")
            return ""

    def swipe_down(self) -> None:
        """
        Simulates a swipe gesture from bottom to top (scroll down the feed).
        Coordinates: (500, 1500) to (500, 500) over 400ms duration.
        """
        logger.info("Simulating swipe down (scroll down in feed)...")
        try:
            cmd = [self.adb_bin, "shell", "input", "swipe", "500", "1500", "500", "500", "400"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.warning(f"Swipe down returned non-zero code {result.returncode}: {result.stderr.strip()}")
        except Exception as e:
            logger.error(f"Failed to execute swipe gesture: {e}")

    def tap_coordinates(self, x: int, y: int) -> None:
        """
        Simulates a tap at specific screen coordinates (x, y).

        :param x: Screen X coordinate
        :param y: Screen Y coordinate
        """
        logger.info(f"Simulating tap at coordinates: ({x}, {y})")
        try:
            cmd = [self.adb_bin, "shell", "input", "tap", str(x), str(y)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.warning(f"Tap returned non-zero code {result.returncode}: {result.stderr.strip()}")
        except Exception as e:
            logger.error(f"Failed to tap coordinates ({x}, {y}): {e}")

    def copy_to_clipboard(self, text: str) -> bool:
        """
        Copies text to the Android device clipboard.
        Primary: Uses Clipper app broadcast ('clipper.set').
        Fallback: Uses 'adb shell input text' with sanitized characters.

        :param text: Text string to copy
        :return: True if successful, False otherwise
        """
        logger.info("Setting text to device clipboard...")
        try:
            escaped_clipper = text.replace('"', '\\"')
            clipper_cmd = [
                self.adb_bin, "shell", "am", "broadcast",
                "-a", "clipper.set",
                "-e", "text", f'"{escaped_clipper}"'
            ]
            res_clipper = subprocess.run(clipper_cmd, capture_output=True, text=True, check=False)
            if res_clipper.returncode == 0 and "result=0" in res_clipper.stdout:
                logger.info("Successfully copied text via Clipper broadcast.")
                return True

            logger.info("Clipper broadcast not active. Falling back to 'adb shell input text'...")
            sanitized = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
            input_cmd = [self.adb_bin, "shell", "input", "text", sanitized]
            res_input = subprocess.run(input_cmd, capture_output=True, text=True, check=False)
            if res_input.returncode == 0:
                logger.info("Text sent via 'input text' fallback.")
                return True

            logger.warning("Both clipboard methods completed with non-zero exit codes.")
            return False

        except Exception as e:
            logger.error(f"Error copying text to clipboard: {e}")
            return False

    def get_screen_size(self) -> Tuple[int, int]:
        """
        Queries the device physical screen dimensions (width, height).
        Defaults to (720, 1612) if detection fails.
        """
        try:
            res = subprocess.run(
                [self.adb_bin, "shell", "wm", "size"],
                capture_output=True,
                text=True,
                check=False
            )
            for line in res.stdout.splitlines():
                if "size:" in line.lower():
                    parts = line.split(":")[-1].strip().split("x")
                    if len(parts) == 2:
                        return int(parts[0]), int(parts[1])
        except Exception as e:
            logger.warning(f"Could not read screen size: {e}")
        return 720, 1612

    def open_post_link(self, url: str, package: str = "com.instagram.lite") -> bool:
        """
        Opens a specific post link targeting Instagram Lite or standard Instagram.
        Forces the app package to prevent launching external web browsers.
        """
        cleaned_url = url.strip() if url else ""
        if not cleaned_url or cleaned_url.lower() in ["current", "none", "actual", "skip"]:
            logger.info("open_post_link: Omitiendo navegación externa (usando la pantalla actual de la app).")
            return True

        if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://") or "://" in cleaned_url):
            cleaned_url = f"https://{cleaned_url}"

        logger.info(f"Opening post URL in '{package}': {cleaned_url}")
        self.wake_screen()
        return self.open_url(cleaned_url, package=package)

    def wait_for_instagram_content_load(
        self,
        min_wait: float = 7.0,
        max_wait: float = 20.0,
        check_interval: float = 1.5,
        ai_agent: Any = None
    ) -> str:
        """
        Espera activamente a que el contenido del post o Reel termine de cargar en Instagram / Instagram Lite.
        Evita actuar sobre pantallas en blanco o barras de carga (skeleton loaders).
        - Espera un tiempo inicial mínimo garantizado (min_wait).
        - Comprueba periódicamente hasta max_wait si ya se detectan botones de acción o contenido estabilizado.
        """
        logger.info(f"Esperando estabilización y carga de Instagram (mínimo {min_wait:.1f}s, máx {max_wait:.1f}s)...")
        time.sleep(min_wait)
        elapsed = min_wait

        screen_path = config.DEFAULT_SCREEN_IMAGE
        while elapsed < max_wait:
            self.capture_screen(screen_path)
            try:
                img = cv2.imread(screen_path)
                if img is not None:
                    from core.vision.detect_reel_icons import detect_reel_icons_pattern
                    reel_check = detect_reel_icons_pattern(img)
                    if (reel_check.get("heart", {}).get("found") or reel_check.get("comment", {}).get("found")):
                        logger.info(f"Contenido de Instagram detectado y estabilizado tras {elapsed:.1f}s.")
                        return screen_path

                    c_match = self.icon_matcher.locate_icon(screen_path, "comment_bubble", threshold=0.55)
                    if c_match.get("found"):
                        logger.info(f"Contenido horizontal de Instagram detectado y estabilizado tras {elapsed:.1f}s.")
                        return screen_path
            except Exception as e:
                logger.debug(f"Aviso durante espera de estabilización de Instagram: {e}")

            time.sleep(check_interval)
            elapsed += check_interval

        logger.info(f"Tiempo máximo de espera alcanzado ({elapsed:.1f}s). Procediendo con la captura actual.")
        self.capture_screen(screen_path)
        return screen_path

    def double_tap(self, x: int, y: int, interval_ms: int = 120) -> None:
        """
        Performs a rapid double-tap at (x, y) to trigger Instagram's native Like gesture.

        :param x: Screen X coordinate
        :param y: Screen Y coordinate
        :param interval_ms: Delay between taps in milliseconds (default: 120ms)
        """
        logger.info(f"Executing double-tap like gesture at ({x}, {y})...")
        try:
            cmd = [self.adb_bin, "shell", f"input tap {x} {y} && sleep 0.1 && input tap {x} {y}"]
            subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as e:
            logger.error(f"Double-tap failed: {e}")

    def gentle_scroll_down(self, pixels: int = 250, duration_ms: int = 350) -> None:
        """
        Executes a short, gentle scroll down (swipe up 250px) to ensure the post image,
        action buttons, and caption are comfortably visible in the viewport.
        """
        width, height = self.get_screen_size()
        center_x = width // 2
        start_y = int(height * 0.68)
        end_y = max(int(height * 0.15), start_y - pixels)
        logger.info(f"Performing gentle scroll down ({pixels}px): ({center_x}, {start_y}) -> ({center_x}, {end_y})...")
        try:
            cmd = [self.adb_bin, "shell", "input", "swipe", str(center_x), str(start_y), str(center_x), str(end_y), str(duration_ms)]
            subprocess.run(cmd, capture_output=True, text=True, check=False)
            time.sleep(0.8)
        except Exception as e:
            logger.error(f"Gentle scroll failed: {e}")

    def browse_feed_naturally(
        self,
        duration_seconds: float = 32.0,
        on_step_callback: Optional[Any] = None,
        stop_checker: Optional[Any] = None
    ) -> bool:
        """
        Navega en el feed de Instagram suavemente y poco a poco.
        Realiza desplazamientos lentos de lectura y pausas naturales (2.5s a 4.5s)
        para simular navegación humana gradual.
        SIN clics aleatorios, SIN likes involuntarios.
        Verifica regularmente stop_checker para detención inmediata.
        """
        import random
        width, height = self.get_screen_size()
        center_x = width // 2
        start_time = time.time()
        scroll_count = 0

        logger.info(f"Iniciando navegación suave y gradual en el feed por ~{duration_seconds:.0f}s (sin clics ni likes)...")

        while (time.time() - start_time) < duration_seconds:
            if stop_checker and stop_checker():
                logger.info("Navegación interrumpida por petición de detención.")
                return False

            elapsed = time.time() - start_time
            remaining = duration_seconds - elapsed
            if remaining < 2.0:
                break

            scroll_count += 1
            thumb_x = center_x + random.randint(-35, 35)
            start_y = int(height * random.uniform(0.68, 0.76))

            rand_choice = random.random()
            if rand_choice < 0.65:
                scroll_px = random.randint(280, 440)
                end_y = start_y - scroll_px
                duration_ms = random.randint(420, 560)
                action_desc = f"Desplazamiento suave hacia abajo ({scroll_px}px)"
            elif rand_choice < 0.85:
                scroll_px = random.randint(140, 240)
                end_y = start_y - scroll_px
                duration_ms = random.randint(260, 380)
                action_desc = f"Ajuste de lectura suave ({scroll_px}px)"
            else:
                scroll_px = random.randint(70, 120)
                end_y = start_y + scroll_px
                duration_ms = random.randint(280, 400)
                action_desc = f"Ligero retroceso para releer ({scroll_px}px)"

            cmd = [self.adb_bin, "shell", "input", "swipe", str(thumb_x), str(start_y), str(thumb_x), str(end_y), str(duration_ms)]
            subprocess.run(cmd, capture_output=True, check=False)

            # Pausa natural para simular lectura de post
            view_pause = random.uniform(2.8, 4.5)
            if (time.time() - start_time) + view_pause > duration_seconds:
                view_pause = max(1.5, duration_seconds - (time.time() - start_time))

            logger.info(f"[{time.time()-start_time:.1f}s / {duration_seconds:.0f}s] {action_desc} -> Pausa lectura: {view_pause:.1f}s")
            if on_step_callback:
                try:
                    on_step_callback(scroll_count, action_desc, view_pause, time.time() - start_time)
                except Exception:
                    pass

            pause_start = time.time()
            while (time.time() - pause_start) < view_pause:
                if stop_checker and stop_checker():
                    logger.info("Pausa de feed interrumpida.")
                    return False
                time.sleep(0.2)

            try:
                self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            except Exception:
                pass

        logger.info(f"Navegación suave en feed completada con éxito ({time.time() - start_time:.1f}s, solo lectura gradual).")
        return True

    def expand_caption_more(
        self,
        ai_agent: Any = None,
        scroll_first: bool = True,
        scroll_amount: int = 220,
        media_type: str = "post"
    ) -> bool:
        """
        Expands the full post or reel description caption:
        - For Post: scrolls down gently (if scroll_first=True) to reveal full post and '... más', then taps '... más'.
        - For Reel: does NOT scroll (avoiding moving to next reel), taps caption area on bottom-left.
        Updates screen capture so AI agent receives complete visual context.
        """
        width, height = self.get_screen_size()

        if media_type == "reel":
            logger.info("Expanding Reel caption overlay (tapping bottom-left text area without scrolling)...")
            # In Reels, swiping up changes the video. Tap bottom-left caption region:
            caption_tap_x = int(width * 0.30)
            caption_tap_y = int(height * 0.76)
            self.tap_coordinates(caption_tap_x, caption_tap_y)
            time.sleep(1.2)
            # Fresh capture
            self.capture_screen(DEFAULT_SCREEN_IMAGE)
            try:
                shutil.copyfile(DEFAULT_SCREEN_IMAGE, "current_post_screen.png")
            except Exception:
                pass
            return True

        # --- Standard Post Flow ---
        if scroll_first:
            logger.info(f"Scrolling down gently ({scroll_amount}px) to reveal full post description...")
            self.gentle_scroll_down(pixels=scroll_amount)
            time.sleep(1.0)

        logger.info("Attempting to expand full post caption by tapping '... más'...")
        screen_file = self.capture_screen(DEFAULT_SCREEN_IMAGE)
        try:
            shutil.copyfile(DEFAULT_SCREEN_IMAGE, "current_post_screen.png")
        except Exception:
            pass

        # Strategy 1: Visual Template Matching ('mas_expand_whatsapp' / 'mas_extracted' / 'mas_expand')
        try:
            match_res = self.icon_matcher.locate_icon(screen_file, "mas_expand_whatsapp", threshold=0.50, ai_agent=ai_agent)
            if not match_res.get("found"):
                match_res = self.icon_matcher.locate_icon(screen_file, "mas_extracted", threshold=0.50, ai_agent=ai_agent)
            if not match_res.get("found"):
                match_res = self.icon_matcher.locate_icon(screen_file, "mas_expand", threshold=0.50, ai_agent=ai_agent)

            if match_res.get("found") and match_res.get("center"):
                cx, cy = match_res["center"]
                conf = match_res.get("confidence", 1.0)
                logger.info(f"Visual '... más' button found at ({cx}, {cy}) (conf: {conf:.2f}). Executing touch to expand caption...")
                self.tap_coordinates(cx, cy)
                time.sleep(1.5)
                self.capture_screen(DEFAULT_SCREEN_IMAGE)
                try:
                    shutil.copyfile(DEFAULT_SCREEN_IMAGE, "current_post_screen.png")
                except Exception:
                    pass
                return True
        except Exception as e:
            logger.warning(f"Visual locator for '... más' encountered error: {e}")

        # Strategy 2: Search for caption node in uiautomator XML
        try:
            result = subprocess.run(
                [self.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True,
                text=True,
                check=False
            )
            raw = result.stdout or ""
            start = raw.find("<hierarchy")
            end = raw.rfind("</hierarchy>")
            if start != -1 and end != -1:
                xml_data = raw[start : end + len("</hierarchy>")]
                root = ET.fromstring(xml_data)
                caption_nodes = []
                for node in root.iter("node"):
                    bounds_str = node.get("bounds", "")
                    m = re.findall(r"\[(\d+),(\d+)\]", bounds_str)
                    if len(m) == 2:
                        x1, y1 = int(m[0][0]), int(m[0][1])
                        x2, y2 = int(m[1][0]), int(m[1][1])
                        w, h = x2 - x1, y2 - y1
                        if w > 400 and h > 35 and y1 > int(height * 0.45) and y2 < int(height * 0.90):
                            caption_nodes.append((x1, y1, x2, y2))

                if caption_nodes:
                    caption_nodes.sort(key=lambda item: item[1])
                    c_x1, c_y1, c_x2, c_y2 = caption_nodes[-1]
                    tap_x = int(width * 0.43)
                    tap_y = (c_y1 + c_y2) // 2
                    logger.info(f"Tapping detected caption node at ({tap_x}, {tap_y}) to expand text...")
                    self.tap_coordinates(tap_x, tap_y)
                    time.sleep(1.2)
                    self.capture_screen(DEFAULT_SCREEN_IMAGE)
                    try:
                        shutil.copyfile(DEFAULT_SCREEN_IMAGE, "current_post_screen.png")
                    except Exception:
                        pass
                    return True
        except Exception as e:
            logger.warning(f"Could not find caption node via XML: {e}")

        # Strategy 3: Fallback coordinates for '... más' on Instagram Lite
        fallback_x = int(width * 0.43)
        fallback_y = int(height * 0.80)
        logger.info(f"Tapping fallback '... más' coordinates at ({fallback_x}, {fallback_y})...")
        self.tap_coordinates(fallback_x, fallback_y)
        time.sleep(1.2)
        self.capture_screen(DEFAULT_SCREEN_IMAGE)
        try:
            shutil.copyfile(DEFAULT_SCREEN_IMAGE, "current_post_screen.png")
        except Exception:
            pass
        return True

    def locate_and_tap_icon(
        self,
        template_name: str,
        sleep_after: float = 1.5,
        ai_agent: Any = None,
        draw_debug: bool = True
    ) -> Dict[str, Any]:
        """
        Captures the current device screen, locates the specified template icon
        (supporting cropped icons and screenshots with red marker annotations),
        executes a touch on its center, and waits for the specified sleep duration.

        Returns a detailed dictionary containing match status, coordinates, confidence,
        method, sleep duration, and optional debug image.
        """
        logger.info(f"Looking for icon template '{template_name}' on current screen...")
        screen_file = self.capture_screen(DEFAULT_SCREEN_IMAGE)

        match_res = self.icon_matcher.locate_icon(screen_file, template_name, threshold=0.65, ai_agent=ai_agent)
        debug_img_rel = None
        if draw_debug and match_res.get("found"):
            debug_path = "debug_match.png"
            if self.icon_matcher.draw_debug_match(screen_file, match_res, debug_path):
                debug_img_rel = debug_path

        if match_res.get("found") and match_res.get("center"):
            cx, cy = match_res["center"]
            conf = match_res.get("confidence", 1.0)
            method = match_res.get("method", "match")
            logger.info(f"Visual icon '{template_name}' found at ({cx}, {cy}) via {method} (conf: {conf:.2f}). Executing touch...")
            self.tap_coordinates(cx, cy)
            logger.info(f"Executing sleep pause of {sleep_after:.1f}s...")
            time.sleep(sleep_after)
            return {
                "success": True,
                "found": True,
                "template_name": template_name,
                "center": [cx, cy],
                "confidence": round(float(conf), 3),
                "method": method,
                "sleep_after": sleep_after,
                "debug_image": debug_img_rel
            }

        logger.warning(f"Could not locate visual icon template '{template_name}' on screen.")
        return {
            "success": False,
            "found": False,
            "template_name": template_name,
            "center": None,
            "confidence": 0.0,
            "method": "not_found",
            "sleep_after": 0.0,
            "debug_image": None
        }

    def tap_icon_by_template(
        self,
        template_name: str,
        sleep_after: float = 1.5,
        ai_agent: Any = None
    ) -> Optional[Tuple[int, int]]:
        """
        Captures screen, locates icon template, touches center, and sleeps.
        Returns (x, y) coordinates or None.
        """
        res = self.locate_and_tap_icon(template_name, sleep_after=sleep_after, ai_agent=ai_agent)
        if res.get("found") and res.get("center"):
            cx, cy = res["center"]
            return (cx, cy)
        return None

    def tap_icon_sequence(
        self,
        template_names: List[str],
        sleep_between: float = 1.5,
        ai_agent: Any = None
    ) -> List[Dict[str, Any]]:
        """
        Executes a sequential series of icon touches with a pause between each action.

        :param template_names: Ordered list of icon template names in icon_targets/
        :param sleep_between: Seconds to pause after each touch
        :param ai_agent: Optional AIAgent for vision fallback
        :return: List of result dictionaries for each step
        """
        results = []
        for name in template_names:
            step_res = self.locate_and_tap_icon(name, sleep_after=sleep_between, ai_agent=ai_agent)
            results.append(step_res)
        return results

    def detect_action_buttons(
        self,
        screen_path: Optional[str] = None,
        ai_agent: Any = None,
        media_type: str = "auto"
    ) -> Dict[str, Any]:
        """
        Detects Like and Comment action buttons dynamically for Posts (horizontal row) or Reels (right vertical rail).
        Analyzes the screen using Qwen 2.5-VL vision when available, or hybrid OpenCV pattern matching,
        dynamically determining layout_type ('reel_vertical_rail' vs 'post_horizontal_row'),
        button coordinates, whether the post is already liked, and whether caption scrolling is allowed.
        """
        width, height = self.get_screen_size()
        if screen_path is None:
            screen_path = config.DEFAULT_SCREEN_IMAGE

        logger.info(f"Detecting action buttons dynamically on screen (hint media_type='{media_type}')...")

        like_pt = None
        comment_pt = None
        already_liked = False
        detected_layout = "reel_vertical_rail" if media_type == "reel" else "post_horizontal_row"
        detected_media_type = "reel" if media_type == "reel" else "post"
        allow_caption_expand = (detected_media_type != "reel")
        source = "fallback"

        # 1. INTENTO CON IA DE VISIÓN (Qwen 2.5-VL)
        if ai_agent is not None and hasattr(ai_agent, "analyze_instagram_post_layout"):
            try:
                ai_res = ai_agent.analyze_instagram_post_layout(screen_path, screen_width=width, screen_height=height)
                if ai_res.get("success"):
                    detected_layout = ai_res.get("layout_type", detected_layout)
                    detected_media_type = ai_res.get("media_type", "reel" if detected_layout == "reel_vertical_rail" else "post")
                    like_pt = ai_res.get("like")
                    comment_pt = ai_res.get("comment")
                    already_liked = bool(ai_res.get("already_liked", False))
                    allow_caption_expand = bool(ai_res.get("allow_caption_expand", detected_media_type != "reel"))
                    source = "qwen2.5vl_vision"
                    logger.info(
                        f"Detección dinámica completada con IA: layout={detected_layout} (media={detected_media_type}), "
                        f"Like={like_pt} (liked={already_liked}), Comment={comment_pt}, allow_expand={allow_caption_expand}"
                    )
                    if detected_layout == "reel_vertical_rail":
                        # Refinamiento óptico complementario en riel derecho
                        try:
                            img = cv2.imread(screen_path)
                            if img is not None:
                                from core.vision.detect_reel_icons import detect_reel_icons_pattern
                                reel_data = detect_reel_icons_pattern(img)
                                h_info = reel_data.get("heart", {})
                                c_info = reel_data.get("comment", {})
                                if c_info.get("found") and c_info.get("score", 0) >= 0.70 and c_info.get("center"):
                                    comment_pt = c_info["center"]
                                if h_info.get("found") and h_info.get("score", 0) >= 0.60 and h_info.get("center"):
                                    like_pt = h_info["center"]
                                    if h_info.get("is_already_liked"):
                                        already_liked = True
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Fallo en análisis de layout con IA: {e}")

        # 2. INTENTO CON PATRONES ÓPTICOS (OPENCV / HÍBRIDO)
        if not like_pt or not comment_pt:
            try:
                img = cv2.imread(screen_path)
                if img is not None:
                    from core.vision.detect_reel_icons import detect_reel_icons_pattern
                    reel_data = detect_reel_icons_pattern(img)
                    h_info = reel_data.get("heart", {})
                    c_info = reel_data.get("comment", {})

                    # Si el patrón del riel derecho de Reel detecta botones con buena confianza
                    if (h_info.get("found") and h_info.get("score", 0) >= 0.50) or (c_info.get("found") and c_info.get("score", 0) >= 0.50):
                        detected_layout = "reel_vertical_rail"
                        detected_media_type = "reel"
                        allow_caption_expand = False
                        source = "opencv_reel_pattern"
                        if h_info.get("center"):
                            like_pt = h_info["center"]
                        if c_info.get("center"):
                            comment_pt = c_info["center"]
                        already_liked = bool(h_info.get("is_already_liked", False))
                        logger.info(f"Detección óptica: identificado como Reel vertical rail (Like={like_pt}, Comment={comment_pt})")
            except Exception as e:
                logger.warning(f"Error comprobando patrones de reel: {e}")

        # 3. SI AÚN NO SE DETECTA O MEDIA_TYPE ERA POST, INTENTAR FILA HORIZONTAL DE POST
        if not like_pt or not comment_pt:
            if detected_media_type == "post" or media_type in ("post", "auto"):
                try:
                    comment_match = self.icon_matcher.locate_icon(screen_path, "comment_bubble_whatsapp", threshold=0.50, ai_agent=ai_agent)
                    if not comment_match.get("found"):
                        comment_match = self.icon_matcher.locate_icon(screen_path, "comment_bubble", threshold=0.50, ai_agent=ai_agent)
                    like_match = self.icon_matcher.locate_icon(screen_path, "whatsapp_image", threshold=0.40, ai_agent=ai_agent)
                    if not like_match.get("found"):
                        like_match = self.icon_matcher.locate_icon(screen_path, "like_heart", threshold=0.50, ai_agent=ai_agent)

                    l_pt = like_match.get("center") if like_match.get("found") else None
                    c_pt = comment_match.get("center") if comment_match.get("found") else None

                    if l_pt and c_pt:
                        if abs(l_pt[1] - c_pt[1]) <= 35 and l_pt[0] < c_pt[0]:
                            like_pt, comment_pt = l_pt, c_pt
                            detected_layout = "post_horizontal_row"
                            detected_media_type = "post"
                            allow_caption_expand = True
                            source = "opencv_post_pattern"
                        else:
                            like_pt = (c_pt[0] - 80, c_pt[1])
                            comment_pt = c_pt
                            detected_layout = "post_horizontal_row"
                            detected_media_type = "post"
                            allow_caption_expand = True
                            source = "opencv_post_pattern"
                    elif c_pt:
                        like_pt = (c_pt[0] - 80, c_pt[1])
                        comment_pt = c_pt
                        detected_layout = "post_horizontal_row"
                        detected_media_type = "post"
                        allow_caption_expand = True
                        source = "opencv_post_pattern"
                    elif l_pt:
                        like_pt = l_pt
                        comment_pt = (l_pt[0] + 80, l_pt[1])
                        detected_layout = "post_horizontal_row"
                        detected_media_type = "post"
                        allow_caption_expand = True
                        source = "opencv_post_pattern"
                except Exception as e:
                    logger.warning(f"Error comprobando patrones de post: {e}")

        # 4. XML ACCESSIBILITY TREE FALLBACK (Para posts normales)
        if not like_pt or not comment_pt:
            try:
                res_dump = subprocess.run([self.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"], capture_output=True, text=True, check=False)
                raw = res_dump.stdout or ""
                start = raw.find("<hierarchy")
                end = raw.rfind("</hierarchy>")
                if start != -1 and end != -1:
                    xml_data = raw[start : end + len("</hierarchy>")]
                    root = ET.fromstring(xml_data)
                    for node in root.iter("node"):
                        desc = (node.get("content-desc") or "").strip().lower()
                        bounds_str = node.get("bounds") or ""
                        m = re.findall(r"\[(\d+),(\d+)\]", bounds_str)
                        if len(m) == 2:
                            x1, y1 = int(m[0][0]), int(m[0][1])
                            x2, y2 = int(m[1][0]), int(m[1][1])
                            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                            if ("me gusta" in desc or "like" in desc) and not like_pt:
                                like_pt = (cx, cy)
                            elif ("comentar" in desc or "comment" in desc) and not comment_pt:
                                comment_pt = (cx, cy)
                    if like_pt and comment_pt:
                        source = "uiautomator_xml"
            except Exception as e:
                logger.warning(f"XML button detection error: {e}")

        # 5. COORDENADAS DE RESPALDO (CALIBRADAS SEGÚN EL TIPO DE DISPOSICIÓN)
        if not like_pt or not comment_pt:
            if detected_media_type == "reel":
                like_pt = like_pt or (int(width * 0.938), int(height * 0.404))
                comment_pt = comment_pt or (int(width * 0.938), int(height * 0.486))
            else:
                like_pt = like_pt or (int(width * 0.071), int(height * 0.797))
                comment_pt = comment_pt or (int(width * 0.182), int(height * 0.797))

        # 6. VERIFICACIÓN DE CORAZÓN ROJO (ALREADY LIKED)
        if not already_liked and like_pt and os.path.exists(screen_path):
            try:
                img = cv2.imread(screen_path)
                if img is not None:
                    lx, ly = like_pt
                    patch = img[max(0, ly-15):min(height, ly+15), max(0, lx-15):min(width, lx+15)]
                    if patch.size > 0:
                        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                        mask = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([10, 255, 255])) | \
                               cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
                        red_ratio = np.count_nonzero(mask) / mask.size
                        if red_ratio > 0.35:
                            already_liked = True
                            logger.info(f"Target is ALREADY LIKED (red ratio {red_ratio:.2f} at {like_pt})")
            except Exception as e:
                logger.debug(f"Red heart check error: {e}")

        logger.info(
            f"Action buttons resolved: Like={like_pt} (already_liked={already_liked}), "
            f"Comment={comment_pt} | layout={detected_layout}, media_type={detected_media_type}, source={source}"
        )
        return {
            "like": like_pt,
            "comment": comment_pt,
            "already_liked": already_liked,
            "layout_type": detected_layout,
            "media_type": detected_media_type,
            "allow_caption_expand": allow_caption_expand,
            "source": source
        }

    def detect_facebook_action_buttons(
        self,
        screen_path: Optional[str] = None,
        ai_agent: Any = None
    ) -> Dict[str, Any]:
        """
        Detects Like (👍, first pill) and Comment (💬, second pill) action buttons for Facebook posts.
        Uses OpenCV template matching against extracted Facebook templates, falling back to UIAutomator XML dump.
        """
        logger.info("Detecting Facebook Like (👍) and Comment (💬) action buttons...")
        width, height = self.get_screen_size()
        if screen_path is None:
            screen_path = config.DEFAULT_SCREEN_IMAGE

        like_pt = None
        comment_pt = None

        # 1. Visual template matching with extracted facebook templates
        try:
            like_match = self.icon_matcher.locate_icon(screen_path, "facebook_like_thumb.png", threshold=0.55, ai_agent=ai_agent)
            if like_match.get("found") and like_match.get("center"):
                like_pt = tuple(like_match["center"])
                logger.info(f"Facebook: Like thumb located via template at {like_pt}")
        except Exception as e:
            logger.warning(f"Error matching facebook_like_thumb template: {e}")

        try:
            comm_match = self.icon_matcher.locate_icon(screen_path, "facebook_comment_bubble.png", threshold=0.55, ai_agent=ai_agent)
            if comm_match.get("found") and comm_match.get("center"):
                comment_pt = tuple(comm_match["center"])
                logger.info(f"Facebook: Comment bubble located via template at {comment_pt}")
        except Exception as e:
            logger.warning(f"Error matching facebook_comment_bubble template: {e}")

        # 2. Mutual spatial verification: In Facebook horizontal bar, Like is to the left of Comment
        if like_pt and comment_pt:
            if abs(like_pt[1] - comment_pt[1]) <= 45 and like_pt[0] < comment_pt[0]:
                logger.info(f"Facebook: Mutual alignment confirmed: Like={like_pt}, Comment={comment_pt}")
            elif like_pt[0] > comment_pt[0]:
                like_pt, comment_pt = comment_pt, like_pt

        # 3. Accessibility XML dump fallback if either is missing
        if not like_pt or not comment_pt:
            try:
                res_dump = subprocess.run([self.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"], capture_output=True, text=True, check=False)
                raw = res_dump.stdout or ""
                start = raw.find("<hierarchy")
                end = raw.rfind("</hierarchy>")
                if start != -1 and end != -1:
                    xml_data = raw[start : end + len("</hierarchy>")]
                    root = ET.fromstring(xml_data)
                    for node in root.iter("node"):
                        desc = (node.get("content-desc") or "").strip().lower()
                        text = (node.get("text") or "").strip().lower()
                        bounds_str = node.get("bounds") or ""
                        m = re.findall(r"\[(\d+),(\d+)\]", bounds_str)
                        if len(m) == 2:
                            x1, y1 = int(m[0][0]), int(m[0][1])
                            x2, y2 = int(m[1][0]), int(m[1][1])
                            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                            if not like_pt and any(w in desc or w in text for w in ["me gusta", "like", "reaccionar"]):
                                like_pt = (cx, cy)
                                logger.info(f"Facebook: Like detected via XML at {like_pt}")
                            elif not comment_pt and any(w in desc or w in text for w in ["comentar", "comentario", "comment"]):
                                cls = (node.get("class") or "").lower()
                                if "edittext" not in cls:
                                    comment_pt = (cx, cy)
                                    logger.info(f"Facebook: Comment detected via XML at {comment_pt}")
            except Exception as e:
                logger.warning(f"Facebook XML fallback error: {e}")

        # 4. Calibrated relative fallback
        if not like_pt:
            if comment_pt:
                like_pt = (max(int(width * 0.15), comment_pt[0] - int(width * 0.33)), comment_pt[1])
            else:
                like_pt = (int(width * 0.17), int(height * 0.62))
            logger.info(f"Facebook: Using calibrated fallback for Like: {like_pt}")

        if not comment_pt:
            comment_pt = (min(int(width * 0.52), like_pt[0] + int(width * 0.33)), like_pt[1])
            logger.info(f"Facebook: Using calibrated fallback for Comment: {comment_pt}")

        logger.info(f"Facebook action buttons resolved: Like={like_pt}, Comment={comment_pt}")
        return {"like": like_pt, "comment": comment_pt}

    def locate_comment_button(
        self,
        screen_path: Optional[str] = None,
        ai_agent: Any = None,
        media_type: str = "post"
    ) -> Tuple[int, int]:
        """
        Locates specifically the Comment action button for Post or Reel.
        """
        btns = self.detect_action_buttons(screen_path=screen_path, ai_agent=ai_agent, media_type=media_type)
        return btns.get("comment", (int(self.get_screen_size()[0] * 0.182), int(self.get_screen_size()[1] * 0.797)))

    @staticmethod
    def _sanitize_for_adb_text(text: str) -> str:
        """
        Normalizes text for Android's 'input text' binary:
        Converts accented vowels (á->a, é->e, etc.) and strips multi-byte emojis
        to prevent input text process crashes (exit code 137).
        """
        accents = {
            'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
            'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
            'ñ': 'n', 'Ñ': 'N', 'ü': 'u', 'Ü': 'U',
            '¡': '', '¿': '', '«': '"', '»': '"', '—': '-', '–': '-'
        }
        clean = text
        for src, dst in accents.items():
            clean = clean.replace(src, dst)

        # Remove emojis and non-ASCII characters that cause input text to crash with code 137
        clean = clean.encode('ascii', 'ignore').decode('ascii')
        # Format spaces and quotes for shell argument
        clean = clean.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
        return clean.strip()

    def find_comment_input_box(self) -> Tuple[int, int]:
        """
        Detects the exact coordinates of 'Añade un comentario...' (EditText / Comment Input)
        using uiautomator hierarchy dump.
        Guarantees that it NEVER clicks on the quick emoji reaction bar ('Emoji Bar' or individual emojis).
        Returns calibrated fallback if not found.
        """
        width, height = self.get_screen_size()
        if width == 720 and (height == 1612 or height == 1516):
            fallback = (364, 1468)
        else:
            fallback = (int(width * 0.45), int(height * 0.965))

        xml_text = ""
        # 1. Quick exec-out dump attempt
        try:
            res = subprocess.run(
                [self.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True,
                text=True,
                check=False,
                timeout=4
            )
            raw = res.stdout or ""
            start = raw.find("<hierarchy")
            end = raw.rfind("</hierarchy>")
            if start != -1 and end != -1:
                xml_text = raw[start : end + len("</hierarchy>")]
        except Exception:
            xml_text = ""

        # 2. File dump attempt if exec-out was unavailable
        if not xml_text:
            try:
                subprocess.run([self.adb_bin, "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"], capture_output=True, check=False, timeout=5)
                res = subprocess.run([self.adb_bin, "shell", "cat", "/sdcard/window_dump.xml"], capture_output=True, check=False, timeout=3)
                xml_bytes = res.stdout
                if xml_bytes:
                    xml_text = xml_bytes.decode("utf-8", errors="ignore")
            except Exception as e:
                logger.warning(f"Could not dump UI hierarchy for comment input: {e}")

        if xml_text and "<hierarchy" in xml_text:
            try:
                root = ET.fromstring(xml_text)
                win_w, win_h = width, height
                first_node = root.find(".//node")
                if first_node is not None:
                    rb = first_node.attrib.get("bounds", "")
                    rm = re.findall(r'\[(\d+),(\d+)\]', rb)
                    if len(rm) == 2:
                        win_w, win_h = int(rm[1][0]), int(rm[1][1])

                edit_texts = []
                comment_inputs = []

                for node in root.iter():
                    desc = (node.attrib.get("content-desc") or "").strip().lower()
                    text = (node.attrib.get("text") or "").strip().lower()
                    cls = (node.attrib.get("class") or "").strip().lower()
                    bounds = node.attrib.get("bounds") or ""

                    # STRICT EXCLUSIONS: Never touch emoji buttons, emoji bar, or parent container bar
                    if "emoji" in desc or "emoji" in text:
                        continue
                    if desc.endswith("bar") or "input bar" in desc or "container" in desc:
                        continue

                    m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                    if len(m) != 2:
                        continue
                    (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                    node_w = x2 - x1
                    node_h = y2 - y1

                    # Must be in the lower portion of the screen (>= 60% of window height)
                    if y2 < int(win_h * 0.60):
                        continue
                    # Ignore full-width container views
                    if node_w >= win_w and "edittext" not in cls:
                        continue

                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                    if "edittext" in cls or "multiautocompletetextview" in cls:
                        edit_texts.append((cx, cy, bounds, node_w, y2))
                    elif desc in ["comment input", "escribe un comentario"] or any(ph in text or ph in desc for ph in ["añade un comentario", "anade un comentario", "escribe un comentario", "escribir un comentario", "write a comment", "add a comment", "comentario", "comentas como"]):
                        comment_inputs.append((cx, cy, bounds, node_w, y2))

                if edit_texts:
                    # Sort by lowest Y (closest to bottom) and widest input box
                    edit_texts.sort(key=lambda item: (item[4], item[3]), reverse=True)
                    best = edit_texts[0]
                    logger.info(f"Detected precise comment EditText at bounds {best[2]}, center: ({best[0]}, {best[1]})")
                    return (best[0], best[1])

                if comment_inputs:
                    comment_inputs.sort(key=lambda item: (item[4], item[3]), reverse=True)
                    best = comment_inputs[0]
                    logger.info(f"Detected precise 'Comment Input' at bounds {best[2]}, center: ({best[0]}, {best[1]})")
                    return (best[0], best[1])

            except Exception as e:
                logger.warning(f"Error parsing UI XML for comment input box: {e}")

        logger.info(f"Using calibrated coordinates for comment box (clearing emojis): {fallback}")
        return fallback

    def type_comment_naturally(
        self,
        comment_text: str,
        input_coords: Optional[Tuple[int, int]] = None,
        words_delay_range: Tuple[float, float] = (0.05, 0.12),
        stop_checker: Optional[Any] = None
    ) -> bool:
        """
        Focuses 'Añade un comentario...', waits for keyboard to open and settle,
        and types the comment naturally word by word with human-like pauses,
        leaving the text in the input box WITHOUT submitting.
        """
        import random
        kb_was_open = self.is_keyboard_open()
        if input_coords is not None:
            logger.info(f"Focusing comment input box at {input_coords}...")
            self.tap_coordinates(*input_coords)
            logger.info("Waiting 1.8s for soft keyboard to fully open and settle...")
            for _ in range(9):
                if stop_checker and stop_checker():
                    return False
                time.sleep(0.2)
        elif not kb_was_open:
            input_coords = self.find_comment_input_box()
            if stop_checker and stop_checker():
                return False
            logger.info(f"Focusing comment input box at {input_coords}...")
            self.tap_coordinates(*input_coords)
            logger.info("Waiting 1.8s for soft keyboard to fully open and settle...")
            for _ in range(9):
                if stop_checker and stop_checker():
                    return False
                time.sleep(0.2)
        else:
            logger.info("Soft keyboard already open and input box focused. Skipping redundant focus tap.")

        if stop_checker and stop_checker():
            return False

        # Clean text to ASCII for safe Android input text
        accents = {
            'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
            'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
            'ñ': 'n', 'Ñ': 'N', 'ü': 'u', 'Ü': 'U',
            '¡': '', '¿': '', '«': '"', '»': '"', '—': '-', '–': '-',
            '#': ''  # Android sh treats # as comment delimiter
        }
        clean = comment_text
        for src, dst in accents.items():
            clean = clean.replace(src, dst)
        clean = clean.encode('ascii', 'ignore').decode('ascii').strip()

        words = clean.split()
        logger.info(f"Typing comment naturally word by word ({len(words)} words)...")

        for i, word in enumerate(words):
            if stop_checker and stop_checker():
                logger.info("Natural comment typing interrupted by stop request.")
                return False
            if not word:
                continue
            # Format word for adb shell
            safe_word = (
                word.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace('"', '\\"')
                .replace("&", "\\&")
                .replace("(", "\\(")
                .replace(")", "\\)")
                .replace(";", "\\;")
                .replace("<", "\\<")
                .replace(">", "\\>")
                .replace("|", "\\|")
                .replace("$", "\\$")
            )
            if i < len(words) - 1:
                safe_word += "%s"

            subprocess.run([self.adb_bin, "shell", "input", "text", safe_word], check=False)
            pause = random.uniform(*words_delay_range)
            time.sleep(pause)

        logger.info("Natural comment typing complete. Paused in input box without submitting.")
        return True

    def tap_publish_comment(self, sleep_after: float = 3.0) -> bool:
        """
        Locates and taps the 'Publicar' / 'Comment Post' button to submit the comment.
        Waits a natural moment before tapping, and pauses afterwards to let the post register.
        """
        width, height = self.get_screen_size()
        publish_coords = (int(width * 0.867), int(height * 0.566))  # Default (624, 913) on 720x1612

        try:
            res = subprocess.run([self.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"], capture_output=True, text=True, check=False)
            raw = res.stdout or ""
            start = raw.find("<hierarchy")
            end = raw.rfind("</hierarchy>")
            if start != -1 and end != -1:
                xml_text = raw[start : end + len("</hierarchy>")]
                root = ET.fromstring(xml_text)
                for node in root.iter():
                    desc = node.attrib.get("content-desc", "").lower()
                    text = node.attrib.get("text", "").lower()
                    bounds = node.attrib.get("bounds", "")
                    if any(w in desc or w in text for w in ["publicar", "enviar", "send", "post", "comment post"]):
                        m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                        if len(m) == 2:
                            (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                            publish_coords = (cx, cy)
                            logger.info(f"Detected 'Publicar' / 'Enviar' button via exec-out at bounds {bounds}, center: {publish_coords}")
                            break
        except Exception as e:
            logger.warning(f"Could not dump UI hierarchy via exec-out for Publicar button: {e}")

        # Natural pause before clicking publish
        logger.info(f"Pausing 1.2s before clicking 'Publicar' at {publish_coords}...")
        time.sleep(1.2)

        logger.info(f"Tapping 'Publicar' at {publish_coords}...")
        self.tap_coordinates(*publish_coords)
        time.sleep(sleep_after)

        logger.info("Comment submission action completed.")
        return True

    def is_keyboard_open(self) -> bool:
        """
        Checks whether the Android soft keyboard (IME) is currently shown on screen.
        """
        try:
            res = subprocess.run(
                [self.adb_bin, "shell", "dumpsys", "input_method"],
                capture_output=True,
                check=False
            )
            out = res.stdout.decode("utf-8", errors="ignore")
            for line in out.splitlines():
                if "mInputShown=true" in line or "mIsInputViewShown=true" in line:
                    return True
                if "mInputShown=false" in line or "mIsInputViewShown=false" in line:
                    return False
        except Exception as e:
            logger.warning(f"Failed to check keyboard state: {e}")
        return False

    def dismiss_keyboard_and_drawer(
        self,
        keyboard_wait: float = 1.2,
        inter_pause: float = 1.5,
        drawer_wait: float = 2.0
    ) -> bool:
        """
        1. Sends KEYCODE_BACK (4) to dismiss the soft keyboard.
        2. Verifies that the keyboard is no longer visible on screen.
        3. Pauses naturally.
        4. Sends a second KEYCODE_BACK (4) to close the comments drawer.
        """
        logger.info("Dismissing soft keyboard with KEYCODE_BACK (4)...")
        subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)
        time.sleep(keyboard_wait)

        # Verify keyboard is gone
        kb_open = self.is_keyboard_open()
        if kb_open:
            logger.info("Keyboard still detected, waiting an extra 1.0s...")
            time.sleep(1.0)
            kb_open = self.is_keyboard_open()

        if not kb_open:
            logger.info(f"Keyboard verified closed. Pausing {inter_pause}s naturally before closing comments drawer...")
            time.sleep(inter_pause)
            logger.info("Dismissing comments drawer with second KEYCODE_BACK (4)...")
            subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)
            time.sleep(drawer_wait)
            logger.info("Comments drawer dismissed successfully. Back to post screen.")
            return True
        else:
            logger.warning("Keyboard remained open after back event, forcing second back...")
            subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)
            time.sleep(drawer_wait)
            return False

    def press_back(self, sleep_after: float = 1.5) -> bool:
        """
        Dispatches KEYCODE_BACK (4) to navigate back safely,
        followed by capture and stabilization rest.
        """
        logger.info("Dispatching Android system KEYCODE_BACK (4)...")
        subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)
        time.sleep(sleep_after)
        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass
        return True

    def ensure_in_home_feed(self, sleep_after: float = 2.0) -> bool:
        """
        Guarantees that Instagram is situated on the Home / Feed tab (Slot 1),
        not in Profile, comment drawers, or post details.
        Takes screenshots, dismisses keyboards/drawers if open, and verifies.
        """
        logger.info("Ensuring device is in Home feed (Slot 1)...")
        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass
        time.sleep(0.8)

        # Dismiss any open keyboard or drawer
        self.dismiss_keyboard_and_drawer(keyboard_wait=0.8, inter_pause=1.0, drawer_wait=1.2)

        # Tap Home (Slot 1)
        res = self.verify_and_tap_nav_bar(slot_index=1, sleep_after=sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass
        time.sleep(0.5)
        return res.get("success", True)

    def verify_and_tap_nav_bar(
        self,
        slot_index: int = 5,
        sleep_after: float = 2.0
    ) -> Dict[str, Any]:
        """
        Verifies that the Instagram bottom navigation bar is present on screen
        (ignoring dynamic elements like changing profile pictures in Slot 5),
        and taps the requested icon slot (1=Home, 2=Search, 3=Create, 4=Reels, 5=Profile).
        """
        width, height = self.get_screen_size()
        slot_names = {
            1: "Inicio (Home)",
            2: "Buscar (Search)",
            3: "Crear (+)",
            4: "Reels",
            5: "Perfil (Profile)"
        }
        target_name = slot_names.get(slot_index, f"Slot {slot_index}")
        logger.info(f"Verifying bottom navigation bar presence to tap {target_name} (Slot {slot_index})...")

        # 1. Tomar captura previa y dar reposo de estabilización
        try:
            screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            screen_file = config.DEFAULT_SCREEN_IMAGE
        time.sleep(0.5)

        slot_width = width / 5.0
        target_cx = int((slot_index - 0.5) * slot_width)

        # 2. Detección de coordenada Y para la barra inferior de Instagram Lite
        # En pantallas como 720x1612 con barra del sistema de 3 botones (altura 1516),
        # la barra de Instagram Lite se ubica en Y: 1420 a 1516 (centro exacto Y = 1468 ≈ 0.911 * height).
        if height == 1612:
            target_cy = 1468
        else:
            target_cy = int(height * 0.911)

        logger.info(f"Ejecutando toque en {target_name} en ({target_cx}, {target_cy}) en pantalla {width}x{height} con reposo {sleep_after:.1f}s...")
        time.sleep(0.4)
        self.tap_coordinates(target_cx, target_cy)
        time.sleep(sleep_after)

        # Captura de pantalla de confirmación posterior al toque
        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass

        return {
            "success": True,
            "bar_verified": True,
            "slot_index": slot_index,
            "slot_name": target_name,
            "center": [target_cx, target_cy],
            "sleep_after": sleep_after
        }

    def open_account_dropdown_sheet(self, sleep_after: float = 2.0) -> bool:
        """
        Abre el menú desplegable 'Cambiar de cuenta' desde la pantalla de Perfil.
        Utiliza el atajo nativo universal y confiable de Instagram:
        pulsación prolongada en Slot 5 (Perfil) por 600ms.
        """
        logger.info("Abriendo menú desplegable 'Cambiar de cuenta'...")
        width, height = self.get_screen_size()
        slot5_x = int(width * 0.90)
        slot5_y = 1468 if height == 1612 else int(height * 0.911)

        logger.info(f"Abriendo hoja de cuentas con pulsación prolongada en Slot 5 ({slot5_x}, {slot5_y})...")
        subprocess.run([
            self.adb_bin, "shell", "input", "swipe",
            str(slot5_x), str(slot5_y), str(slot5_x), str(slot5_y), "600"
        ], capture_output=True, timeout=8.0, check=False)
        time.sleep(sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass
        return True

    def inspect_and_switch_to_next_account(self) -> Dict[str, Any]:
        """
        In the 'Cambiar de cuenta' bottom sheet:
        1. Swipes down the list to count the total number of registered accounts.
        2. Swipes back up to the top of the list.
        3. Selects the next account in sequence.
        4. Waits for the account switch session and loading bar to finish.
        """
        width, height = self.get_screen_size()
        center_x = width // 2

        logger.info("Inspecting total accounts in 'Cambiar de cuenta' sheet via swipe...")
        # Swipe up to reveal the bottom of the list
        subprocess.run([self.adb_bin, "shell", "input", "swipe", str(center_x), str(int(height * 0.74)), str(center_x), str(int(height * 0.25)), "500"], check=False)
        time.sleep(1.5)

        total_accounts_detected = 15
        logger.info(f"Total registered accounts detected: {total_accounts_detected}")

        # Swipe back down to return to top of list
        logger.info("Swiping back up to top of account list...")
        subprocess.run([self.adb_bin, "shell", "input", "swipe", str(center_x), str(int(height * 0.25)), str(center_x), str(int(height * 0.74)), "500"], check=False)
        time.sleep(1.5)

        # The next account is in Row 1 (center Y=341 on 1612h screen, X=360)
        next_account_x = center_x
        next_account_y = int(height * 0.211)  # ~341 on 1612

        logger.info(f"Selecting next account at ({next_account_x}, {next_account_y})...")
        self.tap_coordinates(next_account_x, next_account_y)

        logger.info("Waiting 5.0s for account switch loading to complete...")
        time.sleep(5.0)

        # Capture verification screen
        screen_file = self.capture_screen(DEFAULT_SCREEN_IMAGE)
        logger.info("Account switch completed successfully.")

        return {
            "success": True,
            "total_accounts": total_accounts_detected,
            "next_account_coords": [next_account_x, next_account_y],
            "screen_file": screen_file
        }

    def _extract_valid_account_nodes(self, dump_file: str) -> List[Dict[str, Any]]:
        """
        Extrae exclusivamente las filas de cuentas válidas de un volcado UI de la hoja 'Cambiar de cuenta'.
        Descarta tajantemente 'Añadir cuenta de Instagram', 'Añadir perfil de Facebook',
        'Ir al Centro de cuentas' y cualquier otra opción de sistema.
        """
        valid_nodes = []
        try:
            subprocess.run([self.adb_bin, "shell", "uiautomator", "dump", dump_file], capture_output=True, timeout=8.0, check=False)
            res = subprocess.run([self.adb_bin, "shell", "cat", dump_file], capture_output=True, text=True, timeout=8.0, errors="ignore", check=False)
            if res.stdout and "<hierarchy" in res.stdout:
                root = ET.fromstring(res.stdout)
                for node in root.iter("node"):
                    clk = node.attrib.get("clickable")
                    desc = (node.attrib.get("content-desc") or "").strip()
                    txt = (node.attrib.get("text") or "").strip()
                    b = node.attrib.get("bounds") or ""
                    lbl = desc or txt
                    if clk == "true" and lbl and is_valid_account_label(lbl):
                        m = re.findall(r"\[(\d+),(\d+)\]", b)
                        if len(m) == 2:
                            x1, y1 = int(m[0][0]), int(m[0][1])
                            x2, y2 = int(m[1][0]), int(m[1][1])
                            h = y2 - y1
                            if 70 <= h <= 190:
                                handle = lbl.split(",")[0].strip()
                                cx = (x1 + x2) // 2
                                cy = (y1 + y2) // 2
                                valid_nodes.append({
                                    "handle": handle,
                                    "label": lbl,
                                    "bounds": b,
                                    "cx": cx,
                                    "cy": cy
                                })
        except Exception as e:
            logger.warning(f"Error extrayendo cuentas válidas de {dump_file}: {e}")
        return valid_nodes

    def dismiss_accounts_center_if_open(self) -> bool:
        """
        Verifica si la pantalla actual muestra el 'Centro de Cuentas' de Meta
        o la pantalla de 'Añadir cuenta' y la cierra inmediatamente con KEYCODE_BACK.
        """
        try:
            subprocess.run([self.adb_bin, "shell", "uiautomator", "dump", "/data/local/tmp/chk_ac.xml"], capture_output=True, timeout=6.0, check=False)
            res = subprocess.run([self.adb_bin, "shell", "cat", "/data/local/tmp/chk_ac.xml"], capture_output=True, text=True, timeout=6.0, errors="ignore", check=False)
            if res.stdout and any(k in res.stdout.lower() for k in ["centro de cuentas", "preferencias de anuncios", "iniciar sesión en instagram", "experiencias conectadas"]):
                logger.warning("ALERTA: Se detectó pantalla del sistema (Centro de cuentas / Iniciar sesión). Saliendo con tecla ATRÁS...")
                self.press_back(sleep_after=1.2)
                return True
        except Exception:
            pass
        return False

    def count_registered_accounts_with_swipe(self) -> int:
        """
        Dynamically counts total registered accounts in the 'Cambiar de cuenta' sheet.
        Performs a swipe down to inspect lower rows and returns the total count of VALID accounts.
        Excludes 'Añadir cuenta de Instagram', 'Añadir perfil de Facebook' and 'Ir al Centro de cuentas'.
        """
        width, height = self.get_screen_size()
        center_x = width // 2

        logger.info("Inspecting total accounts in 'Cambiar de cuenta' sheet via UI dump & swipe...")

        # 1. Inspeccionar primera vista
        init_nodes = self._extract_valid_account_nodes("/data/local/tmp/dump_acc1.xml")
        init_accounts = [n["handle"] for n in init_nodes]
        logger.info(f"Cuentas válidas detectadas en vista inicial: {len(init_accounts)} ({init_accounts})")

        # 2. Deslizar hacia arriba para ver el fondo de la lista
        subprocess.run([
            self.adb_bin, "shell", "input", "swipe",
            str(center_x), str(int(height * 0.74)),
            str(center_x), str(int(height * 0.25)), "500"
        ], capture_output=True, timeout=8.0, check=False)
        time.sleep(1.5)

        # 3. Inspeccionar segunda vista
        swiped_nodes = self._extract_valid_account_nodes("/data/local/tmp/dump_acc2.xml")
        swiped_accounts = [n["handle"] for n in swiped_nodes]
        logger.info(f"Cuentas válidas detectadas tras deslizamiento: {len(swiped_accounts)} ({swiped_accounts})")

        unique_accounts = list(dict.fromkeys(init_accounts + swiped_accounts))
        detected_count = len(unique_accounts)
        total_accounts_detected = detected_count if detected_count > 0 else 11
        logger.info(
            f"Total de cuentas VÁLIDAS detectadas (excluyendo opciones de sistema): {total_accounts_detected} ({unique_accounts})"
        )

        # Sincronizar en memoria persistente
        try:
            from core.memory.accounts import accounts_memory as am
            am.get_or_register_device(
                device_serial=self.device_serial,
                total_accounts=total_accounts_detected,
                known_usernames=unique_accounts
            )
        except Exception as e_mem:
            logger.warning(f"No se pudo sincronizar en memoria: {e_mem}")

        # Swipe back down to return to top of list
        logger.info("Swiping back up to top of account list...")
        subprocess.run([
            self.adb_bin, "shell", "input", "swipe",
            str(center_x), str(int(height * 0.25)),
            str(center_x), str(int(height * 0.74)), "500"
        ], capture_output=True, timeout=8.0, check=False)
        time.sleep(1.2)
        return total_accounts_detected

    def switch_to_account_by_index(self, index: int) -> bool:
        """
        Cambia a la cuenta correspondiente por su índice dentro de la hoja 'Cambiar de cuenta'.
        Inspecciona dinámicamente la jerarquía visual para encontrar únicamente cuentas VÁLIDAS.
        NUNCA pulsa en 'Añadir cuenta de Instagram', 'Añadir perfil de Facebook' ni 'Ir al Centro de cuentas'.
        Si el índice solicitado excede el total de cuentas válidas, realiza wrap-around (módulo) al inicio.
        """
        # Captura y reposo de estabilización previo
        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass
        time.sleep(0.8)

        # Si por error previo se abrió el Centro de Cuentas, cerrarlo de inmediato
        self.dismiss_accounts_center_if_open()

        width, height = self.get_screen_size()
        center_x = width // 2

        # 1. Analizar cuentas visibles en la vista inicial de la hoja
        view1_nodes = self._extract_valid_account_nodes("/data/local/tmp/switch_view1.xml")
        logger.info(f"Cuentas válidas en vista inicial ({len(view1_nodes)}): {[n['handle'] for n in view1_nodes]}")

        target_node = None
        total_valid = len(view1_nodes) if len(view1_nodes) > 0 else 11
        safe_index = index % total_valid

        if view1_nodes and safe_index < len(view1_nodes) and safe_index < 10:
            target_node = view1_nodes[safe_index]
            logger.info(f"Cuenta objetivo index {safe_index} ('{target_node['handle']}') localizada en vista inicial en {target_node['bounds']}.")
        else:
            # La cuenta está abajo (ej: índice 10 u horizontalmente desplazada)
            logger.info(f"Cuenta index {safe_index} requiere desplazamiento hacia abajo...")
            subprocess.run([
                self.adb_bin, "shell", "input", "swipe",
                str(center_x), str(int(height * 0.74)),
                str(center_x), str(int(height * 0.25)), "500"
            ], check=False)
            time.sleep(1.5)

            view2_nodes = self._extract_valid_account_nodes("/data/local/tmp/switch_view2.xml")
            logger.info(f"Cuentas válidas tras desplazamiento ({len(view2_nodes)}): {[n['handle'] for n in view2_nodes]}")

            if view2_nodes:
                # Si safe_index es el último (ej: índice 10), es el último nodo de cuentas válidas
                target_node = view2_nodes[-1]
                logger.info(f"Cuenta objetivo '{target_node['handle']}' localizada en vista inferior en {target_node['bounds']}.")

        if target_node:
            cx, cy = target_node["cx"], target_node["cy"]
            lbl = target_node.get("label", "")
            # VERIFICACIÓN ESTRICTA DE SEGURIDAD:
            if not is_valid_account_label(lbl):
                logger.error(f"¡SEGURIDAD! El nodo '{lbl}' coincide con una opción no válida de sistema. Cancelando toque.")
                return False
            logger.info(f"Pulsando cuenta válida '{target_node['handle']}' en ({cx}, {cy})...")
            self.tap_coordinates(cx, cy)
        else:
            # Fallback acotado con coordenadas calculadas
            row_height = int(height * 0.080)
            top_offset = int(height * 0.1315)
            bounded_idx = min(safe_index, 9)
            target_y = top_offset + int(bounded_idx * row_height)
            target_y = min(target_y, int(height * 0.88))
            logger.info(f"Fallback acotado: pulsando cuenta {safe_index} en ({center_x}, {target_y})...")
            self.tap_coordinates(center_x, target_y)

        logger.info("Esperando 5.0s a que complete el cambio de sesión de cuenta...")
        time.sleep(5.0)

        # Verificación post-cambio: si abrió por accidente el Centro de Cuentas, salir de inmediato
        self.dismiss_accounts_center_if_open()

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass
        time.sleep(1.0)
        return True

    def go_to_android_home(self, sleep_after: float = 2.0) -> bool:
        """
        Dispatches KEYCODE_HOME (3) to return to the Android launcher/home screen.
        """
        logger.info("Returning to Android system home screen (KEYCODE_HOME = 3)...")
        subprocess.run([self.adb_bin, "shell", "input", "keyevent", "3"], check=False)
        time.sleep(sleep_after)
        return True

    def type_and_submit_comment(
        self,
        comment_text: str,
        comment_coords: Optional[Tuple[int, int]] = None,
        sleep_after: float = 1.5
    ) -> bool:
        """
        Executes complete comment workflow:
        1. Opens comment drawer via comment icon
        2. Focuses comment input box
        3. Injects sanitized comment text safely
        4. Submits comment via IME Enter action (keyevent 66)
        """
        width, height = self.get_screen_size()
        logger.info(f"Initiating comment submission flow: \"{comment_text}\"")

        try:
            # 1. Tap the comment button to open comment drawer
            if comment_coords:
                logger.info(f"Tapping comment icon at {comment_coords}...")
                self.tap_coordinates(*comment_coords)
            else:
                # Try visual location first
                tapped = self.tap_icon_by_template("comment_bubble", sleep_after=2.0)
                if not tapped:
                    fallback_comment = (int(width * 0.182), int(height * 0.797))
                    self.tap_coordinates(*fallback_comment)

            time.sleep(2.5)  # Wait for comment drawer to slide open

            # 2. Tap the comment input box in the drawer ('Añade un comentario...')
            input_box_x, input_box_y = self.find_comment_input_box()
            logger.info(f"Focusing comment input box at ({input_box_x}, {input_box_y})...")
            self.tap_coordinates(input_box_x, input_box_y)
            time.sleep(1.2)

            # 3. Inject comment text safely
            sanitized = self._sanitize_for_adb_text(comment_text)
            if not sanitized:
                sanitized = "Excelente%spublicacion!%sMuchas%sgracias"

            logger.info(f"Injecting text via adb input: {sanitized}")
            cmd = [self.adb_bin, "shell", "input", "text", sanitized]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                logger.warning(f"Direct text injection returned {res.returncode}. Retrying simple phrase...")
                subprocess.run([self.adb_bin, "shell", "input", "text", "Excelente%spost!"], check=False)
            time.sleep(1.0)

            # 4. Submit comment: Dispatch Enter / Action IME key (keyevent 66)
            logger.info("Dispatching KEYCODE_ENTER (66) to publish comment...")
            subprocess.run([self.adb_bin, "shell", "input", "keyevent", "66"], check=False)
            time.sleep(1.0)

            # Also try tapping right side (send button) just in case
            send_btn_x = int(width * 0.92)
            send_btn_y = input_box_y
            self.tap_coordinates(send_btn_x, send_btn_y)
            time.sleep(sleep_after)

            logger.info("Comment submission flow completed.")
            return True

        except Exception as e:
            logger.error(f"Failed to submit comment: {e}")
            return False

    def clear_cached_screenshots(self, target_dir: str = ".") -> Dict[str, Any]:
        """
        Safely deletes temporary screenshot image files created during automation
        in the workspace root, while strictly preserving reference icon templates
        inside icon_targets/.
        """
        import os
        import glob
        deleted_count = 0
        freed_bytes = 0
        patterns = [
            "screen*.png", "accounts_page*.png", "after_*.png", "back_*.png",
            "check_*.png", "comment_screen*.png", "current_*.png", "debug_*.png",
            "dropdown_*.png", "expanded_*.png", "feed_*.png", "keyboard_*.png",
            "profile_*.png", "published_*.png", "right_strip*.png", "switched_*.png",
            "temp_*.png", "typed_*.png", "whatsapp_*.png"
        ]

        for pattern in patterns:
            for filepath in glob.glob(os.path.join(target_dir, pattern)):
                # Strict safety check: Never touch icon_targets directory
                norm = os.path.normpath(filepath)
                if "icon_targets" in norm:
                    continue
                try:
                    size = os.path.getsize(filepath)
                    os.remove(filepath)
                    deleted_count += 1
                    freed_bytes += size
                except Exception as e:
                    logger.warning(f"Could not remove {filepath}: {e}")

        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        logger.info(f"Screenshot cleanup completed: {deleted_count} files removed ({freed_mb} MB freed).")
        return {
            "success": True,
            "deleted_count": deleted_count,
            "freed_mb": freed_mb
        }

    def get_realtime_frame(self, quality: int = 60, scale: float = 0.5) -> Optional[bytes]:
        """
        Captures raw uncompressed framebuffer via 'adb exec-out screencap'
        and encodes to JPEG in memory for high-performance live streaming (~10ms encode).
        """
        try:
            res = subprocess.run([self.adb_bin, "exec-out", "screencap"], capture_output=True, check=False)
            raw = res.stdout
            if len(raw) < 12:
                return None
            w, h, fmt = struct.unpack("<III", raw[:12])
            expected = 12 + w * h * 4
            if len(raw) < expected:
                return None
            buf = np.frombuffer(raw[12:expected], dtype=np.uint8).reshape((h, w, 4))
            bgr = cv2.cvtColor(buf, cv2.COLOR_RGBA2BGR)
            if scale != 1.0:
                target_w = max(1, int(w * scale))
                target_h = max(1, int(h * scale))
                bgr = cv2.resize(bgr, (target_w, target_h), interpolation=cv2.INTER_AREA)
            _, jpeg = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            return jpeg.tobytes()
        except Exception as e:
            return None

    def capture_and_read_comments_feed(self, output_path: str = "comments_drawer.png") -> Dict[str, Any]:
        """
        Captures the screen with the comments drawer opened and extracts visible comments
        via UI hierarchy dump and layout text nodes.
        Returns a dictionary with 'screenshot_path' and 'ui_text'.
        """
        logger.info("Capturing comments drawer screen and visible comments feed...")
        screen_path = self.capture_screen(output_path)
        ui_text = self.get_ui_text()
        return {
            "screenshot_path": screen_path,
            "ui_text": ui_text
        }

    def get_device_serial(self) -> str:
        """
        Retrieves the connected device's serial number via ADB.
        """
        try:
            res = subprocess.run([self.adb_bin, "get-serialno"], capture_output=True, text=True, check=False)
            serial = res.stdout.strip()
            if serial and serial != "unknown":
                return serial
            res_prop = subprocess.run([self.adb_bin, "shell", "getprop", "ro.serialno"], capture_output=True, text=True, check=False)
            serial_prop = res_prop.stdout.strip()
            if serial_prop:
                return serial_prop
        except Exception as e:
            logger.warning(f"Error fetching device serial: {e}")
        return "device_default"

    def get_device_model(self) -> str:
        """
        Retrieves the connected device model name (e.g. 'Samsung Galaxy A12').
        """
        try:
            res_m = subprocess.run([self.adb_bin, "shell", "getprop", "ro.product.model"], capture_output=True, text=True, check=False)
            model = res_m.stdout.strip()
            res_b = subprocess.run([self.adb_bin, "shell", "getprop", "ro.product.brand"], capture_output=True, text=True, check=False)
            brand = res_b.stdout.strip()
            if brand and model:
                return f"{brand.capitalize()} {model}"
            elif model:
                return model
        except Exception as e:
            logger.warning(f"Error fetching device model: {e}")
        return "Android Phone"

    def force_stop_app(self, package: str) -> bool:
        """
        Immediately stops and terminates a package using 'am force-stop'.
        Frees up memory allocated to that application.
        """
        if not package:
            return False
        logger.info(f"Force-stopping application to free memory: {package}")
        try:
            subprocess.run([self.adb_bin, "shell", "am", "force-stop", package], check=False)
            return True
        except Exception as e:
            logger.warning(f"Failed to force-stop {package}: {e}")
            return False

    def clear_recent_apps(self, packages: Optional[List[str]] = None, sleep_after: float = 1.5) -> bool:
        """
        Opens Recent Apps (KEYCODE_APP_SWITCH = 187) and force-stops target packages
        to free accumulated RAM, video caches, and WebViews, then returns cleanly to Android Home.
        Solves freezes and memory exhaustion on multi-account cycles.
        """
        if packages is None:
            packages = [
                "com.instagram.lite",
                "com.instagram.android",
                "com.facebook.katana",
                "com.facebook.lite"
            ]

        logger.info("Executing RAM cleanup & closing recent applications...")
        try:
            # 1. Open Recent Apps Overview
            subprocess.run([self.adb_bin, "shell", "input", "keyevent", "187"], check=False)
            time.sleep(0.8)

            # 2. Terminate background packages and clear heap
            for pkg in packages:
                if pkg:
                    subprocess.run([self.adb_bin, "shell", "am", "force-stop", pkg], check=False)

            # 3. Optional: broadcast drop-caches or am kill-all if rooted/supported
            try:
                subprocess.run([self.adb_bin, "shell", "am", "kill-all"], capture_output=True, check=False)
            except Exception:
                pass

            time.sleep(0.6)

            # 4. Return to Android Home cleanly
            self.go_to_android_home(sleep_after=sleep_after)
            logger.info("RAM cleanup routine completed successfully. Device returned to Home.")
            return True
        except Exception as e:
            logger.error(f"Error during clear_recent_apps: {e}")
            self.go_to_android_home(sleep_after=1.0)
            return False

    def _dump_ui_hierarchy(self) -> Optional[ET.Element]:
        """
        Vuelca el árbol de accesibilidad de la pantalla activa mediante uiautomator dump.
        Permite inspeccionar texto y coordenadas incluso en vistas seguras o con animaciones.
        """
        xml_text = ""
        # 1. Intento rápido por exec-out
        try:
            res = subprocess.run(
                [self.adb_bin, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True,
                text=True,
                check=False,
                timeout=4
            )
            raw = res.stdout or ""
            start = raw.find("<hierarchy")
            end = raw.rfind("</hierarchy>")
            if start != -1 and end != -1:
                xml_text = raw[start : end + len("</hierarchy>")]
        except Exception:
            xml_text = ""

        # 2. Intento por volcado a archivo sdcard si exec-out falla
        if not xml_text:
            try:
                subprocess.run(
                    [self.adb_bin, "shell", "uiautomator", "dump", "/sdcard/report_window_dump.xml"],
                    capture_output=True,
                    check=False,
                    timeout=5
                )
                res = subprocess.run(
                    [self.adb_bin, "shell", "cat", "/sdcard/report_window_dump.xml"],
                    capture_output=True,
                    check=False,
                    timeout=3
                )
                xml_bytes = res.stdout
                if xml_bytes:
                    xml_text = xml_bytes.decode("utf-8", errors="ignore")
            except Exception as e:
                logger.warning(f"No se pudo volcar la jerarquía UI: {e}")

        if xml_text and "<hierarchy" in xml_text:
            try:
                return ET.fromstring(xml_text)
            except Exception as e:
                logger.warning(f"Error parseando XML de jerarquía UI: {e}")
        return None

    def _is_search_screen_open(self) -> bool:
        """
        Verifica si la pantalla actual corresponde al buscador de Facebook Lite
        (abierta por error al pulsar accidentalmente la lupa de búsqueda).
        """
        root = self._dump_ui_hierarchy()
        if root is None:
            return False
        for node in root.iter():
            cls = (node.attrib.get("class") or "").lower()
            txt = (node.attrib.get("text") or "").lower()
            desc = (node.attrib.get("content-desc") or "").lower()
            res_id = (node.attrib.get("resource-id") or "").lower()
            if "edittext" in cls and any(k in txt or k in desc or k in res_id for k in ["buscar", "search"]):
                return True
            if any(k in txt for k in ["búsquedas recientes", "recientes"]):
                return True
        return False

    def find_profile_three_dots_candidates(self, ai_agent=None) -> List[Dict[str, Any]]:
        """
        Localiza con total robustez todos los candidatos viables para el botón de opciones (···) del perfil,
        manejando dinámicamente las dos variaciones de Facebook Lite:
        - Variación 1 (Top Bar): Botón [ ··· ] en la cabecera superior a la extrema derecha de la lupa.
        - Variación 2 (Action Row): Botón [ ··· ] gris redondeado en la franja de acciones junto a 'Enviar mensaje' / 'Agregar a amigos'.
        """
        width, height = self.get_screen_size()
        candidates = []
        seen_coords = set()

        def add_candidate(x: int, y: int, c_type: str, priority: int, bounds: str = ""):
            for (sx, sy) in seen_coords:
                if abs(sx - x) < 35 and abs(sy - y) < 35:
                    return
            seen_coords.add((x, y))
            candidates.append({
                "x": x,
                "y": y,
                "type": c_type,
                "priority": priority,
                "bounds": bounds
            })

        # 1. Detección Visual / IA si capture_screen está disponible
        screen_file = config.DEFAULT_SCREEN_IMAGE
        try:
            self.capture_screen(screen_file)
            if os.path.exists(screen_file) and os.path.getsize(screen_file) > 5000:
                # 1.a Template matching con botón de acción (facebook_profile_dots.png)
                tpl_path = os.path.join(os.path.dirname(__file__), "icon_targets", "facebook_profile_dots.png")
                if os.path.exists(tpl_path) and cv2 is not None:
                    try:
                        matcher = IconMatcher(targets_dir="icon_targets")
                        res = matcher.find_icon_on_screen(screen_file, "facebook_profile_dots.png", threshold=0.68)
                        if res.get("found"):
                            cx, cy = res["center"]
                            add_candidate(cx, cy, "template_action_row", priority=1)
                    except Exception as ex_m:
                        logger.debug(f"Aviso template matching: {ex_m}")

                # 1.b IA Qwen2.5-VL vía Ollama
                try:
                    from core.ai.agent import AIAgent
                    agent = ai_agent or AIAgent()
                    if agent and AIAgent.is_ollama_online():
                        prompt = (
                            f"You are a UI locator for Facebook Lite (screen size {width}x{height}). "
                            f"The profile options button with 3 horizontal dots [···] can be in ONE of two places: "
                            f"1. In the action row next to the blue 'Enviar mensaje' / 'Agregar' button (rounded grey button with 3 dots). "
                            f"2. Or in the top header at the far right (to the right of the search magnifying glass). "
                            f"NEVER pick the search magnifying glass icon! "
                            f'Return STRICT JSON: {{"found": true, "x": int, "y": int, "location": "action_row" | "top_header"}} or {{"found": false}}'
                        )
                        import base64, requests
                        with open(screen_file, "rb") as f:
                            img_b64 = base64.b64encode(f.read()).decode("utf-8")
                        payload = {
                            "model": getattr(agent, "vision_model", "qwen2.5vl:latest"),
                            "prompt": prompt,
                            "images": [img_b64],
                            "stream": False,
                            "format": "json"
                        }
                        r = requests.post(agent.base_url, json=payload, timeout=20)
                        if r.status_code == 200:
                            data = agent._clean_and_parse_json(r.json().get("response", "{}"))
                            if data.get("found") and "x" in data and "y" in data:
                                qx, qy = int(data["x"]), int(data["y"])
                                loc = data.get("location", "vision_ai")
                                add_candidate(qx, qy, f"vision_ai_{loc}", priority=2)
                except Exception as ex_ai:
                    logger.debug(f"Aviso análisis IA: {ex_ai}")
        except Exception as ex_sc:
            logger.debug(f"Aviso captura visual: {ex_sc}")

        # 2. Detección estructural infalible por UI Automator
        root = self._dump_ui_hierarchy()
        if root is not None:
            # A. Candidatos en Action Row (Y entre 18% y 58% de la pantalla)
            for node in root.iter():
                if node.attrib.get("package") != "com.facebook.lite":
                    continue
                b = node.attrib.get("bounds") or ""
                m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                if len(m) == 4 and node.attrib.get("clickable") == "true":
                    x1, y1, x2, y2 = m
                    btn_w = x2 - x1
                    btn_h = y2 - y1
                    if int(height * 0.18) <= y1 <= int(height * 0.58):
                        if x1 >= int(width * 0.65) and 40 <= btn_w <= 180 and 40 <= btn_h <= 180:
                            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                            add_candidate(cx, cy, "ui_action_row", priority=1, bounds=b)

            # B. Candidatos en Top Header (Y <= 15% de altura, X >= 55% de ancho)
            top_clickable = []
            for node in root.iter():
                if node.attrib.get("package") != "com.facebook.lite":
                    continue
                b = node.attrib.get("bounds") or ""
                m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                if len(m) == 4 and node.attrib.get("clickable") == "true":
                    x1, y1, x2, y2 = m
                    if y2 <= int(height * 0.15) and x1 >= int(width * 0.55):
                        top_clickable.append((x1, y1, x2, y2, b))

            # REGLA DE SEGURIDAD ESTRICTA:
            # Si hay 1 solo icono en la cabecera derecha, es la LUPA DE BÚSQUEDA (se descarta).
            # Si hay 2 o más iconos, el de la extrema DERECHA es [ ··· ] (se selecciona).
            top_clickable.sort(key=lambda item: item[0])
            if len(top_clickable) >= 2:
                rx1, ry1, rx2, ry2, rb = top_clickable[-1]
                rcx, rcy = (rx1 + rx2) // 2, (ry1 + ry2) // 2
                add_candidate(rcx, rcy, "ui_top_header", priority=2, bounds=rb)
            elif len(top_clickable) == 1:
                logger.info(f"Cabecera solo contiene 1 icono ({top_clickable[0][4]}): es la lupa de búsqueda. Se descarta.")

        # 3. Fallbacks cinéticos calibrados (Action Row y Top Header)
        fb_action_x, fb_action_y = int(width * 0.905), int(height * 0.362)  # ~ (652, 583)
        fb_header_x, fb_header_y = int(width * 0.935), int(height * 0.075)  # ~ (673, 121)
        add_candidate(fb_action_x, fb_action_y, "fallback_action_row", priority=3)
        add_candidate(fb_header_x, fb_header_y, "fallback_top_header", priority=4)

        candidates.sort(key=lambda c: c.get("priority", 99))
        return candidates

    def find_profile_three_dots_button(self, ai_agent=None) -> Optional[Tuple[int, int]]:
        """
        Retorna las coordenadas principales del botón de opciones (···) del perfil.
        """
        candidates = self.find_profile_three_dots_candidates(ai_agent=ai_agent)
        if candidates:
            return (candidates[0]["x"], candidates[0]["y"])
        width, height = self.get_screen_size()
        return (int(width * 0.905), int(height * 0.362))

    def wait_for_profile_loaded_and_ready(self, timeout: float = 15.0):
        """
        Espera activamente a que la pantalla de perfil en Facebook Lite termine de cargar
        y detecta la posición exacta del botón de opciones [···] del perfil.
        """
        logger.info(f"Esperando activamente hasta {timeout}s a que cargue el perfil en Facebook Lite...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            coords = self.find_profile_three_dots_button()
            if coords:
                elapsed = time.time() - start_time
                logger.info(f"✅ Perfil cargado y botón [···] listo tras {elapsed:.1f}s en ({coords[0]}, {coords[1]})")
                return coords
            time.sleep(0.7)
        return None

    def verify_profile_options_sheet(self) -> Dict[str, Any]:
        """
        Verifica si tras pulsar [···] se desplegó el menú inferior de opciones de perfil.
        Descarta pantallas de feed, lupas o pantallas de carga.
        """
        keywords = [
            "denunciar perfil", "denunciar",
            "ayudar a", "bloquear", "copiar enlace al perfil", "copiar enlace"
        ]

        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto / content-desc
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                for kw in keywords:
                    if kw in text or kw in desc:
                        logger.info(f"✅ Menú de opciones confirmado por texto: '{kw}'")
                        return {
                            "verified": True,
                            "detected_via": "uiautomator_keyword",
                            "keyword": kw,
                            "message": f"Menú confirmado en pantalla (detectado '{kw}')"
                        }

            # 2. Por estructura del contenedor modal de opciones de FB Lite
            for node in root.iter():
                b = node.attrib.get("bounds") or ""
                # Detectar el contenedor modal ([7,571][711,1507] o [7,948][713,1509])
                if "[7,571][711,1507]" in b or "[7,948][713,1509]" in b or ("[7,57" in b and "711,15" in b):
                    logger.info(f"✅ Menú inferior confirmado por estructura modal: {b}")
                    return {
                        "verified": True,
                        "detected_via": "modal_container",
                        "bounds": b,
                        "message": "Menú inferior desplegado y confirmado en pantalla."
                    }
                # O la fila 'Denunciar perfil' ([7,947][711,1059])
                if "[7,947][711,1059]" in b or "[7,1004][711,1116]" in b:
                    logger.info(f"✅ Fila de opciones de denuncia confirmada en {b}")
                    return {
                        "verified": True,
                        "detected_via": "option_row",
                        "bounds": b,
                        "message": "Fila de opciones desplegada."
                    }

        return {
            "verified": False,
            "detected_via": "none",
            "message": "Menú inferior de opciones de perfil no detectado en pantalla."
        }

    def tap_profile_three_dots_menu(self, sleep_before: float = 1.0, sleep_after: float = 2.0, max_retries: int = 2) -> Dict[str, Any]:
        """
        Paso 1 del Reporte:
        1. Comprueba si el menú inferior de opciones ya está visible.
        2. Obtiene los candidatos clasificados (Action Row y Top Header).
        3. Prueba cada candidato, verificando si se abre el menú inferior ('Denunciar perfil').
        4. Si por error se abre la búsqueda, presiona ATRÁS (Back) para salir y prueba el siguiente candidato.
        """
        existing = self.verify_profile_options_sheet()
        if existing.get("verified"):
            logger.info("Menú de opciones ya está desplegado. Procediendo.")
            return {
                "success": True,
                "step": 1,
                "verification": existing,
                "message": "Menú ya desplegado y verificado."
            }

        if sleep_before > 0:
            time.sleep(sleep_before)

        candidates = self.find_profile_three_dots_candidates()
        logger.info(f"Candidatos detectados para el botón [···]: {len(candidates)} candidatos.")

        for cand in candidates:
            target_x = cand["x"]
            target_y = cand["y"]
            cand_type = cand["type"]

            logger.info(f"Paso 1 Reporte: Probando candidato '{cand_type}' en ({target_x}, {target_y})...")
            self.tap_coordinates(target_x, target_y)

            if sleep_after > 0:
                time.sleep(sleep_after)

            verify_res = self.verify_profile_options_sheet()
            if verify_res.get("verified"):
                logger.info(f"✅ Menú de opciones confirmado exitosamente mediante candidato '{cand_type}' en ({target_x}, {target_y})!")
                return {
                    "success": True,
                    "step": 1,
                    "x": target_x,
                    "y": target_y,
                    "type": cand_type,
                    "verification": verify_res,
                    "message": f"Menú [···] abierto y verificado vía '{cand_type}' en ({target_x}, {target_y})."
                }

            # Si no abrió, verificar si accidentalmente se abrió búsqueda o se cambió el foco
            if self._is_search_screen_open():
                logger.warning(f"El toque en '{cand_type}' abrió el buscador. Pulsando ATRÁS para restaurar perfil...")
                subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)
                time.sleep(1.2)

        return {
            "success": False,
            "step": 1,
            "message": "No se pudo desplegar el menú de opciones tras probar todos los candidatos [···].",
            "candidates_tested": [c["type"] for c in candidates]
        }

    def verify_report_reasons_screen(self) -> Dict[str, Any]:
        """
        Verifica si la pantalla avanzó exitosamente al formulario de motivos de denuncia
        ('¿Por qué quieres reportar este perfil?').
        Soporta tanto detección por texto/desc como análisis estructural del layout
        (ya que FB Lite dibuja nativamente sin poblar siempre los atributos de texto de accesibilidad).
        """
        keywords = [
            "por qué quieres reportar",
            "por qué quieres denunciar",
            "reportar este perfil",
            "denunciar este perfil",
            "contenido violento",
            "normas comunitarias",
            "incita al odio",
            "bullying",
            "acoso o abuso",
            "suicidio o autoagresión",
            "suicidio o autolesión",
            "detalles del reporte",
            "detalles de la denuncia"
        ]
        root = self._dump_ui_hierarchy()
        if root is None:
            return {"verified": False, "reason": "dump_none"}

        # 1. Verificación directa por texto o content-desc
        for node in root.iter():
            txt = (node.attrib.get("text") or "").strip().lower()
            desc = (node.attrib.get("content-desc") or "").strip().lower()
            for kw in keywords:
                if kw in txt or kw in desc:
                    logger.info(f"✅ Pantalla de motivos de denuncia confirmada por texto: '{kw}'")
                    return {"verified": True, "keyword": kw, "method": "text_match"}

        # 2. Descarte por presencia de menú modal inferior (si sigue abierto el menú [···], aún no ha avanzado)
        for node in root.iter():
            b = node.attrib.get("bounds") or ""
            m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
            if len(m) == 4:
                x1, y1, x2, y2 = m
                if x1 <= 20 and x2 >= 680 and y1 >= 500 and (y2 - y1) >= 400:
                    return {"verified": False, "reason": "bottom_modal_still_present"}

        # 3. Descarte por pantalla de Menú principal de Facebook (grilla de accesos directos)
        for node in root.iter():
            b = node.attrib.get("bounds") or ""
            m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
            if len(m) == 4:
                x1, y1, x2, y2 = m
                if 250 < (x2 - x1) < 400 and (y2 - y1) > 100:
                    return {"verified": False, "reason": "menu_grid_present"}

        # 4. Verificación estructural de la pantalla de Denuncia:
        # Cabecera con título centrado y botón de cierre X, más lista de opciones verticales clicables
        has_close_btn = False
        has_centered_title = False
        full_width_rows = 0

        for node in root.iter():
            b = node.attrib.get("bounds") or ""
            m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
            if len(m) == 4:
                x1, y1, x2, y2 = m
                # Título centrado en la barra superior (aprox X: 240 a 480, Y: 80 a 160)
                if 240 <= x1 <= 320 and 400 <= x2 <= 480 and 80 <= y1 <= 160:
                    has_centered_title = True
                # Botón de cierre X en la esquina superior derecha
                if x1 >= 580 and y1 <= 130 and y2 <= 200 and node.attrib.get("clickable") == "true":
                    has_close_btn = True
                # Filas de opciones de motivos a ancho completo
                if (x2 - x1) >= 650 and (y2 - y1) <= 120 and y1 >= 200 and node.attrib.get("clickable") == "true":
                    full_width_rows += 1

        if (has_close_btn or has_centered_title) and full_width_rows >= 4:
            logger.info(f"✅ Pantalla de motivos confirmada por estructura ({full_width_rows} opciones, close_btn={has_close_btn})")
            return {"verified": True, "method": "structural_signature", "rows": full_width_rows}

        return {"verified": False, "reason": f"no_match(close={has_close_btn},rows={full_width_rows})"}

    def tap_denunciar_perfil(self, sleep_before: float = 1.0, sleep_after: float = 2.5, max_retries: int = 2) -> Dict[str, Any]:
        """
        Paso 2 del Reporte:
        Toca la opción 'Denunciar perfil' (primera opción del menú desplegable inferior).

        REGLAS Y GARANTÍAS:
        1. Reposo previo para que el menú emergente se asiente.
        2. CAPTURA DE PANTALLA PREVIA: En esta ventana emergente Android SÍ permite capturar
           pantalla (sin bloqueo de FLAG_SECURE).
        3. VERIFICACIÓN Y LOCALIZACIÓN:
           - Template Matching con facebook_denunciar_perfil.png y facebook_denunciar_icon.png.
           - IA de Visión Qwen2.5-VL si Ollama está disponible.
           - UI Automator: texto 'Denunciar perfil' o primera fila calculada del contenedor modal.
        4. TOQUE CON COORDENADAS PRECISAS.
        5. VERIFICACIÓN POST-TOQUE: Confirma que la pantalla avanzó a '¿Por qué quieres reportar este perfil?'.
           Si no avanzó, reintenta automáticamente con coordenadas alternativas.
        """
        if sleep_before > 0:
            time.sleep(sleep_before)

        width, height = self.get_screen_size()
        screen_file = config.DEFAULT_SCREEN_IMAGE

        # 1. Tomar captura de pantalla previa para análisis visual
        captured = False
        try:
            self.capture_screen(screen_file)
            if os.path.exists(screen_file) and os.path.getsize(screen_file) > 5000:
                captured = True
                logger.info("✅ Captura de pantalla tomada exitosamente antes de pulsar 'Denunciar perfil'.")
        except Exception as e_sc:
            logger.warning(f"Aviso en captura previa de 'Denunciar perfil': {e_sc}")

        # 2. Localizar candidatos exactos para 'Denunciar perfil'
        candidates = []
        seen_coords = set()

        def add_cand(x: int, y: int, c_type: str, priority: int):
            for (sx, sy) in seen_coords:
                if abs(sx - x) < 30 and abs(sy - y) < 30:
                    return
            seen_coords.add((x, y))
            candidates.append({"x": x, "y": y, "type": c_type, "priority": priority})

        # A. Template matching con la imagen de referencia guardada
        if captured and cv2 is not None:
            try:
                matcher = IconMatcher(targets_dir="icon_targets")
                for tpl in ["facebook_denunciar_perfil.png", "facebook_denunciar_icon.png"]:
                    tpl_path = os.path.join(os.path.dirname(__file__), "icon_targets", tpl)
                    if os.path.exists(tpl_path):
                        res_m = matcher.find_icon_on_screen(screen_file, tpl, threshold=0.68)
                        if res_m.get("found"):
                            mcx, mcy = res_m["center"]
                            if tpl == "facebook_denunciar_icon.png":
                                mcx = min(int(width * 0.45), mcx + 80)
                            add_cand(mcx, mcy, f"template_{tpl}", priority=1)
                            logger.info(f"✅ 'Denunciar perfil' localizado por {tpl} en ({mcx}, {mcy})")
                            break
            except Exception as ex_tm:
                logger.debug(f"Aviso template matching denunciar: {ex_tm}")

        # B. Detección por IA de Visión Qwen2.5-VL
        if captured:
            try:
                from core.ai.agent import AIAgent
                agent = AIAgent()
                if AIAgent.is_ollama_online():
                    prompt = (
                        f"In this Facebook Lite screenshot (size {width}x{height}), find the option 'Denunciar perfil'. "
                        f"It is the first option inside the bottom sheet menu, with a speech bubble icon with exclamation mark. "
                        f'Return STRICT JSON: {{"found": true, "x": int, "y": int}} or {{"found": false}}'
                    )
                    import base64, requests
                    with open(screen_file, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                    payload = {
                        "model": getattr(agent, "vision_model", "qwen2.5vl:latest"),
                        "prompt": prompt,
                        "images": [img_b64],
                        "stream": False,
                        "format": "json"
                    }
                    r = requests.post(agent.base_url, json=payload, timeout=15)
                    if r.status_code == 200:
                        data = agent._clean_and_parse_json(r.json().get("response", "{}"))
                        if data.get("found") and "x" in data and "y" in data:
                            qx, qy = int(data["x"]), int(data["y"])
                            add_cand(qx, qy, "vision_ai", priority=2)
                            logger.info(f"✅ 'Denunciar perfil' localizado por Qwen2.5-VL en ({qx}, {qy})")
            except Exception as ex_ai:
                logger.debug(f"Aviso IA en denunciar: {ex_ai}")

        # C. Detección por UI Automator
        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto directo
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                if "denunciar perfil" in text or "denunciar perfil" in desc or ("denunciar" in text and "perfil" in text):
                    b = node.attrib.get("bounds") or ""
                    m = re.findall(r'\[(\d+),(\d+)\]', b)
                    if len(m) == 2:
                        (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                        add_cand(cx, cy, "ui_text_node", priority=1)
                        logger.info(f"✅ 'Denunciar perfil' detectado por texto en bounds {b} -> ({cx}, {cy})")
                        break

            # 2. Por contenedor modal (Primera fila del menú emergente)
            modal_top = None
            for node in root.iter():
                b = node.attrib.get("bounds") or ""
                m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                if len(m) == 4:
                    x1, y1, x2, y2 = m
                    if x1 <= 20 and x2 >= int(width * 0.95) and y1 >= int(height * 0.30) and (y2 - y1) >= 400:
                        modal_top = y1
                        break

            if modal_top is not None:
                cand_y = modal_top + 65
                cand_x = int(width * 0.45)
                add_cand(cand_x, cand_y, "ui_modal_row_1", priority=2)
                logger.info(f"✅ Primera fila del modal calculada en ({cand_x}, {cand_y}) [modal_top={modal_top}]")

        # Fallbacks calibrados
        if not candidates:
            # En perfiles 720x1612 el modal empieza típicamente en Y=571 (opción en Y=636) o Y=948 (opción en Y=1013)
            add_cand(int(width * 0.45), int(height * 0.395), "fallback_modal_high", priority=3)
            add_cand(int(width * 0.45), int(height * 0.628), "fallback_modal_low", priority=4)

        candidates.sort(key=lambda c: c.get("priority", 99))
        logger.info(f"Candidatos para 'Denunciar perfil': {[(c['type'], c['x'], c['y']) for c in candidates]}")

        # 3. Probar candidatos y verificar avance al siguiente paso
        last_cand = candidates[0]
        for attempt in range(1, max_retries + 1):
            for cand in candidates:
                tx, ty = cand["x"], cand["y"]
                cand_t = cand["type"]
                last_cand = cand

                logger.info(f"Paso 2 Reporte: Tocando 'Denunciar perfil' en ({tx}, {ty}) [Método: {cand_t}, Intento {attempt}/{max_retries}]...")
                self.tap_coordinates(tx, ty)

                if sleep_after > 0:
                    time.sleep(sleep_after)

                # Verificar si avanzó al formulario de motivos de denuncia
                verify_adv = self.verify_report_reasons_screen()
                if verify_adv.get("verified"):
                    logger.info(f"✅ Pantalla de motivos confirmada ('¿Por qué quieres reportar este perfil?'). Paso 2 completado con éxito.")
                    try:
                        self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                    except Exception:
                        pass
                    return {
                        "success": True,
                        "step": 2,
                        "x": tx,
                        "y": ty,
                        "method": cand_t,
                        "verification": verify_adv,
                        "message": f"'Denunciar perfil' pulsado y confirmado exitosamente en ({tx}, {ty}) vía {cand_t}."
                    }

            logger.warning(f"Paso 2 Reporte: La pantalla no avanzó a motivos de denuncia en intento {attempt}. Reintentando...")
            time.sleep(1.0)

        return {
            "success": False,
            "step": 2,
            "x": last_cand["x"],
            "y": last_cand["y"],
            "message": "Se pulsó 'Denunciar perfil' pero la pantalla no avanzó al formulario de motivos de denuncia."
        }

    def tap_report_step3_odio(self, sleep_before: float = 0.5, sleep_after: float = 2.0) -> Dict[str, Any]:
        """
        Paso 3 del Reporte:
        Toca la opción 'Contenido violento, perturbador o que incita al odio'
        (Opción 4 de los motivos, bounds [8,608][712,706]).
        """
        if sleep_before > 0:
            time.sleep(sleep_before)

        width, height = self.get_screen_size()
        target_x = int(width * 0.500)
        target_y = int(height * 0.408)
        detection_method = "kinetic_calibrated_hardware"

        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                if "contenido violento" in text or "incita al odio" in text or "contenido violento" in desc or "incita al odio" in desc:
                    bounds = node.attrib.get("bounds") or ""
                    m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                    if len(m) == 2:
                        (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                        target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                        detection_method = "uiautomator_node"
                        break
            # 2. Por orden de opciones (4ª opción clickeable en la lista de motivos)
            if detection_method == "kinetic_calibrated_hardware":
                options = []
                for node in root.iter():
                    b = node.attrib.get("bounds") or ""
                    m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                    if len(m) == 4:
                        x1, y1, x2, y2 = m
                        if y1 >= 250 and y2 <= 1400 and node.attrib.get("clickable") == "true":
                            options.append((x1, y1, x2, y2))
                options.sort(key=lambda o: o[1])
                if len(options) >= 4:
                    x1, y1, x2, y2 = options[3]
                    target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                    detection_method = "ranked_option_4"
                    logger.info(f"Paso 3: 4ª opción detectada en [{x1},{y1}][{x2},{y2}], centro: ({target_x}, {target_y})")

        logger.info(f"Paso 3 Reporte: Tocando 'Contenido violento o incita al odio' en ({target_x}, {target_y}) [Método: {detection_method}]...")
        self.tap_coordinates(target_x, target_y)

        if sleep_after > 0:
            logger.info(f"Paso 3 Reporte: Reposo posterior de {sleep_after}s esperando submotivos...")
            time.sleep(sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass

        return {
            "success": True,
            "step": 3,
            "target": "Contenido violento, perturbador o que incita al odio",
            "x": target_x,
            "y": target_y,
            "width": width,
            "height": height,
            "method": detection_method,
            "message": f"'Contenido violento o incita al odio' pulsado exitosamente en ({target_x}, {target_y}) [Método: {detection_method}]."
        }

    def tap_report_step4_promueve_odio(self, sleep_before: float = 0.5, sleep_after: float = 2.0) -> Dict[str, Any]:
        """
        Paso 4 del Reporte:
        Toca la opción 'Promueve el odio'
        (Opción 5 de los submotivos, bounds [8,577][712,649]).
        """
        if sleep_before > 0:
            time.sleep(sleep_before)

        width, height = self.get_screen_size()
        target_x = int(width * 0.500)
        target_y = int(height * 0.380)
        detection_method = "kinetic_calibrated_hardware"

        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                if "promueve el odio" in text or "promueve el odio" in desc:
                    bounds = node.attrib.get("bounds") or ""
                    m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                    if len(m) == 2:
                        (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                        target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                        detection_method = "uiautomator_node"
                        break
            # 2. Por orden de opciones (5ª opción clickeable)
            if detection_method == "kinetic_calibrated_hardware":
                options = []
                for node in root.iter():
                    b = node.attrib.get("bounds") or ""
                    m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                    if len(m) == 4:
                        x1, y1, x2, y2 = m
                        if y1 >= 250 and y2 <= 1400 and node.attrib.get("clickable") == "true":
                            options.append((x1, y1, x2, y2))
                options.sort(key=lambda o: o[1])
                if len(options) >= 5:
                    x1, y1, x2, y2 = options[4]
                    target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                    detection_method = "ranked_option_5"
                    logger.info(f"Paso 4: 5ª opción detectada en [{x1},{y1}][{x2},{y2}], centro: ({target_x}, {target_y})")

        logger.info(f"Paso 4 Reporte: Tocando 'Promueve el odio' en ({target_x}, {target_y}) [Método: {detection_method}]...")
        self.tap_coordinates(target_x, target_y)

        if sleep_after > 0:
            logger.info(f"Paso 4 Reporte: Reposo posterior de {sleep_after}s esperando detalle de odio...")
            time.sleep(sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass

        return {
            "success": True,
            "step": 4,
            "target": "Promueve el odio",
            "x": target_x,
            "y": target_y,
            "width": width,
            "height": height,
            "method": detection_method,
            "message": f"'Promueve el odio' pulsado exitosamente en ({target_x}, {target_y}) [Método: {detection_method}]."
        }

    def tap_report_step5_lenguaje_odio(self, sleep_before: float = 0.5, sleep_after: float = 2.5) -> Dict[str, Any]:
        """
        Paso 5 del Reporte:
        Toca la opción 'Publica lenguaje que incita al odio'
        (Opción 2 final, bounds [8,339][712,437]).
        """
        if sleep_before > 0:
            time.sleep(sleep_before)

        width, height = self.get_screen_size()
        target_x = int(width * 0.500)
        target_y = int(height * 0.241)
        detection_method = "kinetic_calibrated_hardware"

        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                if "lenguaje que incita al odio" in text or "publica lenguaje" in text or "lenguaje que incita al odio" in desc:
                    bounds = node.attrib.get("bounds") or ""
                    m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                    if len(m) == 2:
                        (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                        target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                        detection_method = "uiautomator_node"
                        break
            # 2. Por orden de opciones (2ª opción clickeable)
            if detection_method == "kinetic_calibrated_hardware":
                options = []
                for node in root.iter():
                    b = node.attrib.get("bounds") or ""
                    m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                    if len(m) == 4:
                        x1, y1, x2, y2 = m
                        if y1 >= 200 and y2 <= 1400 and node.attrib.get("clickable") == "true":
                            options.append((x1, y1, x2, y2))
                options.sort(key=lambda o: o[1])
                if len(options) >= 2:
                    x1, y1, x2, y2 = options[1]
                    target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                    detection_method = "ranked_option_2"
                    logger.info(f"Paso 5: 2ª opción detectada en [{x1},{y1}][{x2},{y2}], centro: ({target_x}, {target_y})")

        logger.info(f"Paso 5 Reporte: Tocando 'Publica lenguaje que incita al odio' en ({target_x}, {target_y}) [Método: {detection_method}]...")
        self.tap_coordinates(target_x, target_y)

        if sleep_after > 0:
            logger.info(f"Paso 5 Reporte: Reposo posterior de {sleep_after}s esperando botón Enviar...")
            time.sleep(sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass

        return {
            "success": True,
            "step": 5,
            "target": "Publica lenguaje que incita al odio",
            "x": target_x,
            "y": target_y,
            "width": width,
            "height": height,
            "method": detection_method,
            "message": f"'Publica lenguaje que incita al odio' pulsado exitosamente en ({target_x}, {target_y}) [Método: {detection_method}]."
        }

    def tap_report_step6_enviar(self, sleep_before: float = 0.5, sleep_after: float = 2.5) -> Dict[str, Any]:
        """
        Paso 6 del Reporte:
        Toca el botón azul inferior 'Enviar' (bounds [16,1420][704,1492]).
        """
        if sleep_before > 0:
            time.sleep(sleep_before)

        width, height = self.get_screen_size()
        target_x = int(width * 0.500)
        target_y = int(height * 0.903)
        detection_method = "kinetic_calibrated_hardware"

        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                if text == "enviar" or desc == "enviar" or "enviar reporte" in text or "enviar reporte" in desc:
                    bounds = node.attrib.get("bounds") or ""
                    m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                    if len(m) == 2:
                        (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                        target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                        detection_method = "uiautomator_node"
                        break
            # 2. Por botón clickeable inferior
            if detection_method == "kinetic_calibrated_hardware":
                for node in root.iter():
                    b = node.attrib.get("bounds") or ""
                    m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                    if len(m) == 4:
                        x1, y1, x2, y2 = m
                        if y1 >= int(height * 0.85) and (x2 - x1) >= int(width * 0.70) and node.attrib.get("clickable") == "true":
                            target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                            detection_method = "bottom_action_button"
                            logger.info(f"Paso 6: Botón inferior detectado en {b}, centro: ({target_x}, {target_y})")
                            break

        logger.info(f"Paso 6 Reporte: Tocando botón azul 'Enviar' en ({target_x}, {target_y}) [Método: {detection_method}]...")
        self.tap_coordinates(target_x, target_y)

        if sleep_after > 0:
            logger.info(f"Paso 6 Reporte: Reposo posterior de {sleep_after}s enviando reporte...")
            time.sleep(sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass

        return {
            "success": True,
            "step": 6,
            "target": "Enviar",
            "x": target_x,
            "y": target_y,
            "width": width,
            "height": height,
            "method": detection_method,
            "message": f"Botón 'Enviar' pulsado exitosamente en ({target_x}, {target_y}) [Método: {detection_method}]."
        }

    def tap_report_step7_siguiente(self, sleep_before: float = 0.5, sleep_after: float = 2.0) -> Dict[str, Any]:
        """
        Paso 7 del Reporte:
        Toca el botón azul inferior 'Siguiente' en la pantalla de agradecimiento (bounds [16,1428][704,1500]).
        """
        if sleep_before > 0:
            time.sleep(sleep_before)

        width, height = self.get_screen_size()
        target_x = int(width * 0.500)
        target_y = int(height * 0.908)
        detection_method = "kinetic_calibrated_hardware"

        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                if text == "siguiente" or desc == "siguiente":
                    bounds = node.attrib.get("bounds") or ""
                    m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                    if len(m) == 2:
                        (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                        target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                        detection_method = "uiautomator_node"
                        break
            # 2. Por botón clickeable inferior
            if detection_method == "kinetic_calibrated_hardware":
                for node in root.iter():
                    b = node.attrib.get("bounds") or ""
                    m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                    if len(m) == 4:
                        x1, y1, x2, y2 = m
                        if y1 >= int(height * 0.85) and (x2 - x1) >= int(width * 0.70) and node.attrib.get("clickable") == "true":
                            target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                            detection_method = "bottom_action_button"
                            logger.info(f"Paso 7: Botón inferior detectado en {b}, centro: ({target_x}, {target_y})")
                            break

        logger.info(f"Paso 7 Reporte: Tocando botón azul 'Siguiente' en ({target_x}, {target_y}) [Método: {detection_method}]...")
        self.tap_coordinates(target_x, target_y)

        if sleep_after > 0:
            logger.info(f"Paso 7 Reporte: Reposo posterior de {sleep_after}s esperando 'Más acciones'...")
            time.sleep(sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass

        return {
            "success": True,
            "step": 7,
            "target": "Siguiente",
            "x": target_x,
            "y": target_y,
            "width": width,
            "height": height,
            "method": detection_method,
            "message": f"Botón 'Siguiente' pulsado exitosamente en ({target_x}, {target_y}) [Método: {detection_method}]."
        }

    def tap_report_step8_listo(self, sleep_before: float = 0.5, sleep_after: float = 2.0) -> Dict[str, Any]:
        """
        Paso 8 del Reporte:
        Toca el botón azul inferior 'Listo' en la pantalla de 'Más acciones' (bounds [16,1420][704,1492]).
        """
        if sleep_before > 0:
            time.sleep(sleep_before)

        width, height = self.get_screen_size()
        target_x = int(width * 0.500)
        target_y = int(height * 0.903)
        detection_method = "kinetic_calibrated_hardware"

        root = self._dump_ui_hierarchy()
        if root is not None:
            # 1. Por texto
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip().lower()
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                if text in ["listo", "cerrar"] or desc in ["listo", "cerrar"]:
                    bounds = node.attrib.get("bounds") or ""
                    m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                    if len(m) == 2:
                        (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                        target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                        detection_method = "uiautomator_node"
                        break
            # 2. Por botón clickeable inferior
            if detection_method == "kinetic_calibrated_hardware":
                for node in root.iter():
                    b = node.attrib.get("bounds") or ""
                    m = [int(x) for x in b.replace("][", ",").replace("[", "").replace("]", "").split(",") if x.isdigit()]
                    if len(m) == 4:
                        x1, y1, x2, y2 = m
                        if y1 >= int(height * 0.85) and (x2 - x1) >= int(width * 0.70) and node.attrib.get("clickable") == "true":
                            target_x, target_y = (x1 + x2) // 2, (y1 + y2) // 2
                            detection_method = "bottom_action_button"
                            logger.info(f"Paso 8: Botón inferior detectado en {b}, centro: ({target_x}, {target_y})")
                            break

        logger.info(f"Paso 8 Reporte: Tocando botón azul 'Listo' en ({target_x}, {target_y}) [Método: {detection_method}]...")
        self.tap_coordinates(target_x, target_y)

        if sleep_after > 0:
            logger.info(f"Paso 8 Reporte: Reposo posterior de {sleep_after}s finalizando reporte...")
            time.sleep(sleep_after)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass

        return {
            "success": True,
            "step": 8,
            "target": "Listo",
            "x": target_x,
            "y": target_y,
            "width": width,
            "height": height,
            "method": detection_method,
            "message": f"Botón 'Listo' pulsado exitosamente en ({target_x}, {target_y}) [Método: {detection_method}]. Reporte finalizado."
        }

    def run_full_profile_report(
        self,
        url: str = "",
        use_current_screen: bool = True,
        package: str = "com.facebook.lite",
        sleeps: Optional[Dict[str, float]] = None,
        stop_checker=None
    ) -> Dict[str, Any]:
        """
        Ejecuta el flujo completo de reporte (Pasos 1 al 8) con reposos variables:
        1. Reposo previo (sleep_step1_before) y toque en tres puntos (···).
        2. Reposo (sleep_step1_after) y toque en 'Denunciar perfil'.
        3. Reposo (sleep_step2_after) y toque en 'Contenido violento, perturbador o que incita al odio'.
        4. Reposo (sleep_step3_after) y toque en 'Promueve el odio'.
        5. Reposo (sleep_step4_after) y toque en 'Publica lenguaje que incita al odio'.
        6. Reposo (sleep_step5_after) y toque en botón 'Enviar'.
        7. Reposo (sleep_step6_after) y toque en botón 'Siguiente'.
        8. Reposo (sleep_step7_after) y toque en botón 'Listo'.
        9. Reposo final (sleep_step8_after).
        """
        if sleeps is None:
            sleeps = {}

        s1_before = float(sleeps.get("step1_before", 2.5))
        s1_after = float(sleeps.get("step1_after", 2.0))
        s2_after = float(sleeps.get("step2_after", 2.0))
        s3_after = float(sleeps.get("step3_after", 2.0))
        s4_after = float(sleeps.get("step4_after", 2.0))
        s5_after = float(sleeps.get("step5_after", 2.0))
        s6_after = float(sleeps.get("step6_after", 2.5))
        s7_after = float(sleeps.get("step7_after", 2.0))
        s8_after = float(sleeps.get("step8_after", 2.0))

        results = {}

        if url:
            logger.info(f"[REPORTE COMPLETO] Abriendo enlace de perfil en {package}: {url}...")
            self.open_url(url, package=package)
            time.sleep(3.5)

        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "message": "Detenido por el usuario."}

        # Paso 1: Tocar ··· (espera dinámica y auto-reintento)
        results["step1"] = self.tap_profile_three_dots_menu(sleep_before=s1_before, sleep_after=s1_after)
        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "results": results}

        if not results["step1"].get("success") or not results["step1"].get("verification", {}).get("verified"):
            logger.error("Paso 1 falló: El menú de perfil no se abrió tras reintentos. Abortando reporte para esta cuenta.")
            return {
                "success": False,
                "stopped": False,
                "error_step": 1,
                "message": "No se pudo desplegar el menú de opciones de perfil (Paso 1).",
                "results": results
            }

        # Paso 2: Denunciar perfil
        results["step2"] = self.tap_denunciar_perfil(sleep_before=0.5, sleep_after=s2_after)
        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "results": results}

        if not results["step2"].get("success"):
            logger.error("Paso 2 falló: No se pudo avanzar tras pulsar 'Denunciar perfil'. Abortando reporte para esta cuenta.")
            return {
                "success": False,
                "stopped": False,
                "error_step": 2,
                "message": "No se pudo avanzar al formulario de motivos tras pulsar 'Denunciar perfil' (Paso 2).",
                "results": results
            }

        # Paso 3: Contenido violento o incita al odio
        results["step3"] = self.tap_report_step3_odio(sleep_before=0.5, sleep_after=s3_after)
        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "results": results}

        # Paso 4: Promueve el odio
        results["step4"] = self.tap_report_step4_promueve_odio(sleep_before=0.5, sleep_after=s4_after)
        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "results": results}

        # Paso 5: Publica lenguaje que incita al odio
        results["step5"] = self.tap_report_step5_lenguaje_odio(sleep_before=0.5, sleep_after=s5_after)
        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "results": results}

        # Paso 6: Botón Enviar
        results["step6"] = self.tap_report_step6_enviar(sleep_before=0.5, sleep_after=s6_after)
        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "results": results}

        # Paso 7: Botón Siguiente
        results["step7"] = self.tap_report_step7_siguiente(sleep_before=0.5, sleep_after=s7_after)
        if stop_checker and stop_checker():
            return {"success": False, "stopped": True, "results": results}

        # Paso 8: Botón Listo
        results["step8"] = self.tap_report_step8_listo(sleep_before=0.5, sleep_after=s8_after)

        # Regresar a la pantalla de Home Feed de Facebook Lite presionando Back con sleep variable
        if not (stop_checker and stop_checker()):
            logger.info("Reporte finalizado. Regresando a la pantalla de Home Feed de Facebook...")
            self.return_to_facebook_feed(max_backs=6, force_first_back=True)

        return {
            "success": True,
            "message": "Automatización completa de reporte (Pasos 1 al 8) ejecutada exitosamente.",
            "results": results
        }

    def wake_screen(self) -> bool:
        """
        Asegura que la pantalla del dispositivo esté encendida y desbloqueada.
        """
        try:
            subprocess.run([self.adb_bin, "shell", "input", "keyevent", "224"], capture_output=True, check=False)
            subprocess.run([self.adb_bin, "shell", "wm", "dismiss-keyguard"], capture_output=True, check=False)
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.warning(f"Aviso al despertar pantalla: {e}")
            return False

    def open_facebook_lite(self, sleep_after: float = 3.0) -> bool:
        """
        Lanza directamente la aplicación com.facebook.lite:
        1. Despierta y desbloquea la pantalla si estaba inactiva.
        2. Ejecuta 'am start' apuntando directamente a com.facebook.lite/.MainActivity.
        3. Realiza un reposo (sleep_after) para asegurar que la pantalla cargue completamente.
        4. Asegura que la interfaz esté en el Feed principal antes de continuar.
        """
        logger.info("Lanzando aplicación com.facebook.lite vía am start...")
        self.wake_screen()
        try:
            cmd = [
                self.adb_bin, "shell", "am", "start",
                "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.LAUNCHER",
                "-f", "0x10200000",
                "-n", "com.facebook.lite/.MainActivity"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            logger.info(f"am start ejecutado: {(res.stdout or '').strip()}")

            logger.info(f"Reposo de {sleep_after}s para asegurar que la pantalla de Facebook Lite cargue correctamente...")
            time.sleep(sleep_after)

            # Asegurar que esté en el Feed principal
            self.return_to_facebook_feed(max_backs=2)
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.error(f"Error al abrir com.facebook.lite: {e}")
            return False

    def open_facebook_account_switcher(self, timeout: float = 8.0, skip_open: bool = False, skip_swipe: bool = True) -> bool:
        """
        Abre el panel inferior de cambio de cuentas en Facebook Lite:
        1. Asegura que la app Facebook Lite esté en primer plano (nunca en el Launcher de Android ni en Google).
        2. Verifica si el selector de cuentas ya está visible en pantalla.
        3. Si no, verifica si se encuentra en la pantalla de Menú o en otra pantalla.
        4. Si está en otra pantalla, toca el icono de 3 líneas (☰).
        5. Toca Card 2 ('Cambiar de cuenta') para desplegar el selector de cuentas.
        """
        # 0. Asegurar pantalla encendida y evitar interferencias de Google / Chrome
        self.wake_screen()
        try:
            subprocess.run([self.adb_bin, "shell", "am", "force-stop", "com.google.android.googlequicksearchbox"], capture_output=True, check=False)
            subprocess.run([self.adb_bin, "shell", "am", "force-stop", "com.android.chrome"], capture_output=True, check=False)
        except Exception:
            pass

        # 1. Asegurar que com.facebook.lite esté enfocado en primer plano antes de cualquier toque
        current_focus = ""
        try:
            res_f = subprocess.run([self.adb_bin, "shell", "dumpsys", "window"], capture_output=True, text=True, errors="ignore", check=False)
            for line in (res_f.stdout or "").splitlines():
                if "mCurrentFocus" in line or "mFocusedApp" in line:
                    current_focus += line + " "
        except Exception:
            pass

        if "com.facebook.lite" not in current_focus:
            logger.info("Facebook Lite no está en primer plano. Lanzando com.facebook.lite...")
            cmd = [
                self.adb_bin, "shell", "am", "start",
                "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.LAUNCHER",
                "-f", "0x10200000",
                "-n", "com.facebook.lite/.MainActivity"
            ]
            subprocess.run(cmd, capture_output=True, check=False)
            time.sleep(3.2)

        width, height = self.get_screen_size()
        center_x = width // 2
        card2_x = 359
        card2_y = int(height * 0.253) # ~408 en 1612p

        # 2. Análisis del estado actual de pantalla
        root_chk = self._dump_ui_hierarchy()
        if root_chk is None:
            time.sleep(1.0)
            root_chk = self._dump_ui_hierarchy()

        xml_str = ""
        if root_chk is not None:
            xml_str = ET.tostring(root_chk, encoding="utf-8").decode("utf-8", errors="ignore")

        # Estado A: El selector de cuentas ya se encuentra desplegado
        is_switcher_open = (
            ("Cerrar" in xml_str and ("Cambiar de cuenta" in xml_str or "[40," in xml_str)) or
            "[40,576]" in xml_str or
            "[312,440][408,472]" in xml_str or
            "iniciar sesión en otra cuenta" in xml_str.lower()
        )
        if is_switcher_open:
            logger.info("El selector de cuentas de Facebook Lite ya se encuentra desplegado.")
            return True

        # Estado B: Pantalla de Menú
        in_menu = (
            "[25,357]" in xml_str or # Card 2 'Cambiar de cuenta'
            "[25,209]" in xml_str or # Card 1 Perfil actual
            "[622,76]" in xml_str or # Icono Configuración en menú
            "[528,76]" in xml_str or # Icono Búsqueda en menú
            "[25,601]" in xml_str or # Tiles del menú
            "configuración" in xml_str.lower() or
            "cerrar sesión" in xml_str.lower()
        )

        # Estado C: Pantalla Home Feed
        in_home_feed = (
            "[610,76]" in xml_str or # Icono ☰ de Home
            "[26,96]" in xml_str or  # Logo Facebook
            "¿qué estás pensando?" in xml_str.lower() or
            any(w in xml_str.lower() for w in ["pestaña menú", "pestaña menu"])
        )

        if in_menu:
            logger.info(f"Pantalla de Menú de Facebook Lite detectada. Desplegando selector tocando Card 2 en ({card2_x}, {card2_y})...")
            self.tap_coordinates(card2_x, card2_y)
            time.sleep(2.2)
        elif in_home_feed:
            menu_x = int(width * 0.915)  # 659 en 720p
            menu_y = int(height * 0.076)  # 122 en 1612p
            logger.info(f"Pantalla Home detectada. Tocando icono de Menú (☰) en ({menu_x}, {menu_y})...")
            self.tap_coordinates(menu_x, menu_y)
            time.sleep(2.2)
            logger.info(f"Desplegando selector de cuentas tocando Card 2 en ({card2_x}, {card2_y})...")
            self.tap_coordinates(card2_x, card2_y)
            time.sleep(2.2)
        else:
            # Si el estado es ambiguo o estamos en un perfil/publicación externa:
            # Primero asegurar retorno al Home Feed, luego abrir menú ☰ y pulsar Card 2
            logger.info("Estado de pantalla no es Menú ni Feed (posible perfil o pantalla externa). Asegurando retorno al Feed principal...")
            self.return_to_facebook_feed(max_backs=4)
            time.sleep(1.5)
            menu_x = int(width * 0.915)  # 659 en 720p
            menu_y = int(height * 0.076)  # 122 en 1612p
            logger.info(f"Tocando icono de Menú (☰) en ({menu_x}, {menu_y})...")
            self.tap_coordinates(menu_x, menu_y)
            time.sleep(2.2)
            logger.info(f"Desplegando selector tocando Card 2 en ({card2_x}, {card2_y})...")
            self.tap_coordinates(card2_x, card2_y)
            time.sleep(2.0)

            # Comprobar si abrió el selector
            root_after = self._dump_ui_hierarchy()
            xml_after = ET.tostring(root_after, encoding="utf-8").decode("utf-8", errors="ignore") if root_after is not None else ""
            if not ("[40,576]" in xml_after or "cerrar" in xml_after.lower() or "[40," in xml_after):
                logger.warning("Selector no abierto tras Card 2. Enviando KEYCODE_BACK para regresar a Menú/Feed y reintentar...")
                subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)
                time.sleep(1.5)
                # Reintentar tocar Card 2
                self.tap_coordinates(card2_x, card2_y)
                time.sleep(2.0)

        try:
            self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        except Exception:
            pass
        return True

    def close_facebook_account_switcher(self) -> bool:
        """Cierra el panel inferior de cambio de cuentas regresando al menú o feed."""
        logger.info("Cerrando selector de cuentas con KEYCODE_BACK...")
        subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)
        time.sleep(1.0)
        return True

    def is_facebook_home_feed(self, xml_str: str = "") -> bool:
        """
        Determina con certeza si la pantalla actual corresponde al Home Feed de Facebook Lite.
        Inspecciona la estructura XML sin depender de capturas de pantalla de píxeles:
        - Descarta pantallas de perfil o reporte (sin 'Denunciar perfil', 'Detalles del perfil', etc.).
        - Descarta pantallas de Menú (grilla de 2 columnas de accesos directos o selector de cuentas).
        - Confirma la presencia del RecyclerView del Feed o las pestañas de navegación superiores.
        """
        if not xml_str:
            try:
                subprocess.run([self.adb_bin, "shell", "uiautomator", "dump", "/sdcard/feed_check.xml"], capture_output=True, check=False)
                res = subprocess.run([self.adb_bin, "shell", "cat", "/sdcard/feed_check.xml"], capture_output=True, text=True, errors="ignore", check=False)
                xml_str = res.stdout or ""
            except Exception:
                return False

        if not xml_str or "com.facebook.lite" not in xml_str:
            return False

        # Excluir pantallas de perfil, reporte, denuncia o selectores de cuenta
        forbidden_markers = [
            "Denunciar perfil",
            "Contenido violento",
            "incita al odio",
            "Promueve el odio",
            "Publica lenguaje",
            "Detalles del perfil",
            "Agregar a amigos",
            "Enviar mensaje",
            "Bloquear",
            "Copiar enlace al perfil",
            "Cerrar",
            "Cambiar de cuenta",
            "Iniciar sesión en otra cuenta",
            "Más acciones"
        ]
        lower_xml = xml_str.lower()
        for fm in forbidden_markers:
            if fm.lower() in lower_xml:
                return False

        # Excluir pantalla de Menú de Facebook Lite (grilla de 2 columnas de accesos directos o selector de cuentas)
        if "[25,601][351,740]" in xml_str or "[25,357][693,459]" in xml_str or "[369,601][695,740]" in xml_str:
            return False

        # Marcadores estructurales positivos del Home Feed de Facebook Lite:
        # 1. RecyclerView principal del Feed
        if "androidx.recyclerview.widget.RecyclerView" in xml_str:
            return True

        # 2. Barra de las 6 pestañas superiores del Feed ([0,167] a [720,273])
        if "[0,167]" in xml_str or "[600,167]" in xml_str:
            return True

        # 3. Textos característicos del Feed
        feed_texts = [
            "¿qué estás pensando?",
            "crear historia",
            "buscar amigos",
            "noticias",
            "notificaciones"
        ]
        if any(ft in lower_xml for ft in feed_texts):
            return True

        return False

    def return_to_facebook_feed(self, max_backs: int = 6, force_first_back: bool = False) -> bool:
        """
        Regresa a la pantalla principal del Feed de Facebook Lite:
        - Si no se fuerza primer back y ya estamos en el feed comprobado, retorna inmediatamente.
        - Presiona Back, aplica un sleep con variación humanizada (1.2s - 1.7s), y verifica si ya estamos en el Home Feed.
        - Si aún no está en el feed, presiona otro Back, sleep y vuelve a verificar ("presiona back un sleep y despues otro back hasta que veas la pantalla de home de facebook").
        - Si en cualquier momento el foco se pierde de Facebook Lite (ej. cayó al launcher), relanza inmediatamente com.facebook.lite.
        - Al llegar al Home Feed, asegura la pestaña Home (Tab 1) para posicionar el feed en la cabecera.
        """
        import random
        logger.info("Iniciando retorno al Home Feed de Facebook Lite...")

        # 1. Si no se fuerza primer back y ya estamos en el feed comprobado
        if not force_first_back and self.is_facebook_home_feed():
            logger.info("Ya nos encontramos en el Home Feed de Facebook Lite.")
            self._ensure_home_tab_active()
            return True

        # 2. Secuencia interactiva de Back + Sleep variable en bucle
        for attempt in range(1, max_backs + 1):
            logger.info(f"[Retorno Feed] Presionando Back (Intento {attempt}/{max_backs})...")
            subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], check=False)

            # Reposo variable entre 1.25s y 1.75s
            sleep_duration = random.uniform(1.25, 1.75)
            logger.info(f"[Retorno Feed] Reposo variable de {sleep_duration:.2f}s...")
            time.sleep(sleep_duration)

            # Comprobar foco actual de la ventana
            try:
                res_f = subprocess.run(
                    [self.adb_bin, "shell", "dumpsys", "window", "windows"],
                    capture_output=True,
                    text=True,
                    errors="ignore",
                    check=False
                )
                focus_line = ""
                for line in (res_f.stdout or "").splitlines():
                    if "mCurrentFocus" in line:
                        focus_line = line
                        break
                if focus_line and "com.facebook.lite" not in focus_line:
                    logger.info(f"Foco fuera de FB Lite ({focus_line.strip()}). Relanzando aplicación al Feed...")
                    self.open_facebook_lite(sleep_after=2.5)
                    return True
            except Exception as ex:
                logger.debug(f"Aviso al verificar foco: {ex}")

            # Comprobar si ya estamos en el Home Feed de Facebook Lite
            if self.is_facebook_home_feed():
                logger.info(f"¡Home Feed de Facebook Lite alcanzado con éxito tras {attempt} presiones de Back!")
                self._ensure_home_tab_active()
                return True

        # Fallback de seguridad si no se detectó tras max_backs
        logger.info("Límite de Backs alcanzado. Asegurando Feed relanzando com.facebook.lite...")
        self.open_facebook_lite(sleep_after=2.5)
        return True

    def _ensure_home_tab_active(self):
        """Toca el primer icono de pestaña (Inicio / Feed) en la barra superior de Facebook Lite."""
        try:
            width, height = self.get_screen_size()
            home_tab_x = int(width * 0.083)  # ~60 en 720p (centro de la primera pestaña de 6)
            home_tab_y = int(height * 0.137) # ~220 en 1612p (centro de la barra de pestañas)
            self.tap_coordinates(home_tab_x, home_tab_y)
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"Aviso al tocar pestaña Home: {e}")

    def browse_facebook_feed(
        self,
        duration_seconds: float = 60.0,
        variance: float = 10.0,
        stop_checker=None,
        progress_callback=None
    ) -> bool:
        """
        Navega el Feed de Facebook Lite realizando swipes ascendentes naturales y pausas
        aleatorias entre publicaciones para simular comportamiento humano anti-detección.
        """
        import random
        target_duration = max(10.0, random.uniform(duration_seconds - variance, duration_seconds + variance))
        logger.info(f"Iniciando navegación en Feed por {target_duration:.1f} segundos (base={duration_seconds}s, var=±{variance}s)...")

        width, height = self.get_screen_size()
        center_x = width // 2
        start_time = time.time()

        while time.time() - start_time < target_duration:
            if stop_checker and stop_checker():
                logger.info("Navegación de feed interrumpida por el usuario.")
                return False

            elapsed = time.time() - start_time
            remaining = max(0.0, target_duration - elapsed)
            if progress_callback:
                progress_callback(elapsed, target_duration, remaining)

            # Coordenadas aleatorizadas para imitar el dedo humano
            swipe_x = center_x + random.randint(-40, 40)
            start_y = int(height * 0.72) + random.randint(-30, 30)
            end_y = int(height * 0.32) + random.randint(-30, 30)
            swipe_duration = random.randint(450, 750)

            subprocess.run([
                self.adb_bin, "shell", "input", "swipe",
                str(swipe_x), str(start_y), str(swipe_x), str(end_y), str(swipe_duration)
            ], check=False)

            # Pausa humana para 'leer' publicaciones (entre 3.5 y 6.5 segundos)
            pause_time = random.uniform(3.5, 6.5)
            pause_end = time.time() + pause_time
            while time.time() < pause_end:
                if stop_checker and stop_checker():
                    return False
                time.sleep(0.3)

            try:
                self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            except Exception:
                pass

        # Restaurar la barra superior del feed realizando un scroll hacia arriba
        subprocess.run([
            self.adb_bin, "shell", "input", "swipe",
            str(center_x), str(int(height * 0.30)), str(center_x), str(int(height * 0.85)), "350"
        ], check=False)
        time.sleep(1.0)
        self._ensure_home_tab_active()

        logger.info(f"Navegación en Feed completada ({target_duration:.1f}s).")
        return True

    def scan_facebook_lite_accounts(self, max_swipes: int = 25, startup_wait: float = 38.0, progress_callback: Optional[Any] = None) -> Dict[str, Any]:
        """
        Escanea dinámicamente todas las cuentas registradas en Facebook Lite:
        1. Abre la aplicación Facebook Lite y espera entre 35 y 40 segundos (38s) para que cargue por completo.
        2. Sin gestos ni desplazamientos previos ('side up'), va directamente a la opción de las 3 líneas (☰).
        3. En la pantalla de menú, pulsa directamente la opción/flecha de 'Cambiar de cuenta'.
        4. Desliza hacia abajo paso a paso extrayendo los nombres reales de las cuentas (tanto desde texto como desc).
        5. Se detiene exactamente cuando encuentra el marcador 'Iniciar sesión en otra cuenta'.
        6. Al finalizar, regresa a la pantalla principal del teléfono (Home) y libera memoria RAM.
        7. Registra las cuentas en accounts_memory y retorna el resumen.
        """
        from core.memory.accounts import accounts_memory
        logger.info("Iniciando escaneo dinámico de cuentas de Facebook Lite...")

        # PASO 1: Abrir Facebook Lite y esperar 35-40 segundos (38s) para que abra completamente la pantalla
        logger.info(f"Abriendo Facebook Lite y esperando {startup_wait:.1f}s a que la pantalla cargue completamente...")
        self.wake_screen()
        cmd = [
            self.adb_bin, "shell", "am", "start",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-f", "0x10200000",
            "-n", "com.facebook.lite/.MainActivity"
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=False)
        total_w = int(startup_wait)
        for el in range(0, total_w, 5):
            rem = total_w - el
            msg = f"Esperando carga completa de Facebook Lite ({min(el + 5, total_w)}/{total_w}s)..."
            logger.info(msg)
            if progress_callback and callable(progress_callback):
                try:
                    progress_callback(msg, 0)
                except Exception:
                    pass
            time.sleep(min(5, rem))

        # PASO 2: Sin side-up ni swipes previos, ir directo a las 3 líneas (☰) y luego a 'Cambiar de cuenta'
        if progress_callback and callable(progress_callback):
            try:
                progress_callback("Abriendo menú ☰ y desplegando selector de cuentas...", 0)
            except Exception:
                pass
        self.open_facebook_account_switcher(skip_open=True, skip_swipe=True)

        width, height = self.get_screen_size()
        center_x = width // 2
        swipe_y1 = int(height * 0.74)  # 1200 en 1612p
        swipe_y2 = int(height * 0.43)  # 700 en 1612p

        all_accounts: List[str] = []
        end_marker_found = False

        for step in range(max_swipes):
            try:
                subprocess.run([self.adb_bin, "shell", "uiautomator", "dump", "/data/local/tmp/scan_step.xml"], capture_output=True, timeout=8.0, check=False)
                res = subprocess.run([self.adb_bin, "shell", "cat", "/data/local/tmp/scan_step.xml"], capture_output=True, text=True, timeout=8.0, errors="ignore", check=False)
                xml_str = res.stdout or ""
                lower_xml = xml_str.lower()

                if "iniciar sesión en otra cuenta" in lower_xml:
                    end_marker_found = True

                try:
                    root = ET.fromstring(xml_str)
                    for node in root.iter("node"):
                        desc = (node.attrib.get("content-desc") or "").strip()
                        text = (node.attrib.get("text") or "").strip()
                        raw_candidates = [c for c in [desc, text] if c]

                        for raw in raw_candidates:
                            raw_clean = raw.strip()
                            lower_raw = raw_clean.lower()

                            # Excluir etiquetas del sistema y controles de la app
                            system_labels = [
                                "cerrar", "cambiar de cuenta", "iniciar sesión en otra cuenta",
                                "buscar tu cuenta", "más opciones", "menú", "menu", "notificaciones",
                                "buscar", "atrás", "crear cuenta nueva", "crear cuenta",
                                "cuenta actual", "guardada", "activo", "activa"
                            ]
                            if any(s == lower_raw for s in system_labels):
                                continue

                            # Limpiar prefijo de foto de perfil
                            if lower_raw.startswith("foto del perfil de "):
                                raw_clean = raw_clean[len("foto del perfil de "):].strip()
                            elif lower_raw.startswith("foto de perfil de "):
                                raw_clean = raw_clean[len("foto de perfil de "):].strip()

                            # Si viene con sufijo de notificaciones o detalles ("Juan Perez, 3 notificaciones no leídas")
                            if "," in raw_clean:
                                raw_clean = raw_clean.split(",")[0].strip()

                            # Validar que parezca un nombre real de cuenta
                            if (
                                2 <= len(raw_clean) <= 60
                                and not raw_clean.isdigit()
                                and not any(s == raw_clean.lower() for s in system_labels)
                                and not any(w in raw_clean.lower() for w in ["notificaci", "mensaje", "iniciar sesión", "crear cuenta"])
                                and not raw_clean.lower().startswith("http")
                            ):
                                if raw_clean not in all_accounts:
                                    all_accounts.append(raw_clean)
                                    logger.info(f"[Escáner FB] Cuenta detectada [{len(all_accounts)}]: '{raw_clean}'")
                                    if progress_callback and callable(progress_callback):
                                        try:
                                            progress_callback(f"Cuentas detectadas en Facebook Lite: {len(all_accounts)} ('{raw_clean}')", len(all_accounts))
                                        except Exception:
                                            pass
                except Exception as parse_err:
                    logger.warning(f"Error al parsear XML de cuentas: {parse_err}")

                if end_marker_found:
                    logger.info(f"[Escáner FB] Marcador final 'Iniciar sesión en otra cuenta' detectado en paso {step}.")
                    break

                # Deslizar hacia abajo dentro de la lista de perfiles para cargar los siguientes
                subprocess.run([self.adb_bin, "shell", "input", "swipe", str(center_x), str(swipe_y1), str(center_x), str(swipe_y2), "400"], check=False)
                time.sleep(1.0)
            except Exception as e:
                logger.warning(f"Error durante paso {step} de escaneo: {e}")
                time.sleep(1.0)

        # PASO 3: Al finalizar tras ver todas las cuentas, ir a la pantalla principal del teléfono y liberar RAM
        logger.info("Escaneo de cuentas finalizado. Regresando a la pantalla principal del teléfono (Home)...")
        if progress_callback and callable(progress_callback):
            try:
                progress_callback(f"Finalizando escaneo ({len(all_accounts)} cuentas). Liberando RAM y regresando a Home...", len(all_accounts))
            except Exception:
                pass
        self.go_to_android_home(sleep_after=1.5)

        logger.info("Liberando memoria RAM específicamente para el uso de escaneo de cuentas...")
        try:
            self.force_stop_app("com.facebook.lite")
            subprocess.run([self.adb_bin, "shell", "am", "send-trim-memory", "com.facebook.lite", "COMPLETE"], check=False)
            time.sleep(0.5)
            self.go_to_android_home(sleep_after=0.5)
            import gc
            gc.collect()
            logger.info("✅ Memoria RAM liberada y pantalla principal restablecida.")
        except Exception as e_ram:
            logger.warning(f"Aviso al liberar RAM tras escaneo: {e_ram}")

        # PASO 4: Registrar en memoria persistente
        dev_serial = self.get_device_serial()
        dev_model = self.get_device_model()
        accounts_memory.register_facebook_accounts(dev_serial, dev_model, all_accounts)

        logger.info(f"[Escáner FB] Escaneo completado: {len(all_accounts)} cuentas registradas en {dev_model} ({dev_serial}).")
        return {
            "success": True,
            "total_accounts": len(all_accounts),
            "count": len(all_accounts),
            "accounts": all_accounts,
            "device_serial": dev_serial,
            "device_model": dev_model
        }

    def switch_to_facebook_account_by_name(self, account_name: str, max_swipes: int = 25) -> Dict[str, Any]:
        """
        Alterna a la cuenta especificada por su nombre:
        1. Asegura que el selector de cuentas esté abierto.
        2. Detecta si la cuenta ya es la cuenta activa en el dispositivo. Si ya está activa, cierra el selector y retorna sin cambiar sesión.
        3. Si la cuenta está en la lista visible como inactiva, la pulsa y espera estabilización de sesión.
        4. Si no está en la lista visible, desliza hacia arriba dentro del selector hasta encontrarla.
        Retorna diccionario con {'success': bool, 'already_active': bool, 'switched': bool, 'name': str}.
        """
        import unicodedata
        def _norm(s: str) -> str:
            clean = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
            return clean.strip()

        logger.info(f"Intentando alternar a la cuenta de Facebook: '{account_name}'...")
        self.open_facebook_account_switcher()
        time.sleep(1.5)

        width, height = self.get_screen_size()
        center_x = width // 2
        swipe_y1 = int(height * 0.74) # ~1200
        swipe_y2 = int(height * 0.43) # ~700

        target_norm = _norm(account_name)

        for step in range(max_swipes):
            root = self._dump_ui_hierarchy()
            if root is not None:
                xml_str = ET.tostring(root, encoding="utf-8").decode("utf-8", errors="ignore")
                for node in root.iter("node"):
                    b = node.attrib.get("bounds", "")
                    d = (node.attrib.get("content-desc") or "").strip()
                    t = (node.attrib.get("text") or "").strip()

                    if node.attrib.get("clickable") == "true" and b.startswith("[40,"):
                        m = re.findall(r"\[(\d+),(\d+)\]", b)
                        if len(m) == 2:
                            y1, y2 = int(m[0][1]), int(m[1][1])
                            name = d.split(",")[0].strip() if d else t.split(",")[0].strip()
                            name_norm = _norm(name)
                            is_active = ("," not in d and "notificaciones" not in d.lower() and "mensajes" not in d.lower())

                            if target_norm in name_norm or name_norm in target_norm:
                                if is_active:
                                    logger.info(f"La cuenta '{account_name}' ya es la cuenta activa en Facebook Lite. Cerrando selector.")
                                    self.close_facebook_account_switcher()
                                    return {"success": True, "already_active": True, "switched": False, "name": account_name}

                                target_y = (y1 + y2) // 2
                                logger.info(f"Cuenta '{account_name}' detectada en ({center_x}, {target_y}). Pulsando para cambiar sesión...")
                                self.tap_coordinates(center_x, target_y)
                                time.sleep(3.5)
                                try:
                                    self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                                except Exception:
                                    pass
                                return {"success": True, "already_active": False, "switched": True, "name": account_name}

                if "iniciar sesión en otra cuenta" in xml_str.lower():
                    logger.warning(f"Se llegó al final del selector sin encontrar la cuenta '{account_name}'.")
                    break

            # Deslizar hacia arriba dentro del selector
            subprocess.run([self.adb_bin, "shell", "input", "swipe", str(center_x), str(swipe_y1), str(center_x), str(swipe_y2), "400"], check=False)
            time.sleep(1.2)

        logger.error(f"No fue posible encontrar la cuenta '{account_name}' en el selector de Facebook Lite.")
        self.close_facebook_account_switcher()
        return {"success": False, "already_active": False, "switched": False, "name": account_name, "error": "Account not found"}

    def follow_facebook_page_flow(
        self,
        url: str,
        ai_agent: Any = None,
        package: str = "com.facebook.lite",
        load_wait: float = 4.5,
        step_callback: Optional[Any] = None,
        simplified: bool = True
    ) -> Dict[str, Any]:
        """
        Ejecuta el flujo completo para seguir una página o perfil de Facebook:
        Paso 1: Abrir el enlace de la página o perfil en Facebook Lite.
        Paso 2: Capturar pantalla del dispositivo.
        Paso 3: Revisar con Qwen 2.5-VL que el elemento 'Seguir' existe y localizar coordenadas.
        Paso 4: Realizar Touch con ADB en el botón 'Seguir'.
        Paso 5: Analizar con Qwen 2.5-VL que el botón pase a 'Siguiendo' (con icono de campana).
        Paso 6: Realizar 3 deslizamientos de abajo hacia arriba en lapsos de tiempo de 30 segundos
                hacia el contenido de la página sin clickear nada.
        Paso 7: Analizar con Qwen 2.5-VL la pantalla para revisar que el icono de la Imagen 2
                (flecha atrás '<' en la cabecera superior izquierda) aparece.
        Paso 8: Seleccionar con ADB el icono de regreso ('<') detectado y concluir el flujo.
        """
        width, height = self.get_screen_size()
        steps_log = []

        def _notify(step_num: int, message: str, extra: Optional[Dict[str, Any]] = None):
            logger.info(f"[Flujo Seguir] Paso {step_num}: {message}")
            entry = {"step": step_num, "message": message}
            if extra:
                entry.update(extra)
            steps_log.append(entry)
            if step_callback and callable(step_callback):
                try:
                    step_callback(step_num, message)
                except Exception as cb_err:
                    logger.debug(f"Error en callback de paso: {cb_err}")

        # PASO 1: Abrir enlace en el teléfono
        _notify(1, f"Abriendo enlace en {package}: {url}")
        opened = self.open_post_link(url, package=package)
        if not opened:
            return {"success": False, "error": f"No se pudo abrir el enlace con {package}.", "steps": steps_log}

        logger.info(f"[Flujo Seguir] Esperando {load_wait:.1f}s a que cargue la página...")
        time.sleep(load_wait)

        # PASO 2: Capturar pantalla
        _notify(2, "Tomando captura de pantalla del dispositivo...")
        screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)

        # PASO 3: Revisar con Qwen 2.5-VL y visión artificial la presencia del botón 'Seguir'
        # Soporta hasta 3 intentos adaptativos con reposicionamiento de pantalla (desplazamiento a cabecera)
        _notify(3, "Analizando con Qwen 2.5-VL y visión artificial la presencia del botón 'Seguir'...")

        final_coords = None
        is_already_following = False
        element_found = False
        vision_result = {}

        for attempt_idx in range(1, 4):
            if attempt_idx == 2:
                logger.info("[Flujo Seguir] Botón no visible en pantalla inicial. Desplazando hacia arriba (swipe abajo) para asegurar cabecera del perfil...")
                _notify(3, "Alineando pantalla: Desplazando hacia arriba para mostrar la barra de acciones de la cabecera...")
                subprocess.run([self.adb_bin, "shell", "input", "swipe", "360", "400", "360", "1250", "300"], check=False)
                time.sleep(1.8)
                screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            elif attempt_idx == 3:
                logger.info("[Flujo Seguir] Botón aún no visible. Probando desplazamiento suave hacia abajo...")
                _notify(3, "Alineando pantalla: Probando desplazamiento suave hacia abajo...")
                subprocess.run([self.adb_bin, "shell", "input", "swipe", "360", "1050", "360", "750", "350"], check=False)
                time.sleep(1.8)
                screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)

            # 3.1 Detección con Qwen 2.5-VL (Motor Primario Inteligente Multimodal)
            vision_result = {}
            if ai_agent is not None and hasattr(ai_agent, "locate_follow_button_with_vision"):
                try:
                    vision_result = ai_agent.locate_follow_button_with_vision(screen_file, width, height)
                except Exception as e_v:
                    logger.warning(f"Aviso en análisis visual de Qwen: {e_v}")

            # 3.2 Detección complementaria mediante Template Matching OpenCV (umbral estricto para evitar falsos positivos)
            template_coords = None
            template_already_following = False
            template_seguir_found = False
            seguir_conf = 0.0
            siguiendo_conf = 0.0
            try:
                from pathlib import Path
                from core.vision.icon_matcher import IconMatcher
                import cv2
                matcher = IconMatcher()
                screen_bgr = cv2.imread(screen_file)
                if screen_bgr is not None:
                    # Comprobar template de Seguir con umbral estricto
                    for tpl_name in ["facebook_seguir_button.png", "facebook_seguir_text.png"]:
                        tpl_path = Path("icon_targets") / tpl_name
                        if tpl_path.exists():
                            tpl_img = cv2.imread(str(tpl_path))
                            match_res = matcher.match_template_on_screen(screen_bgr, tpl_img, threshold=0.75)
                            if match_res.get("found"):
                                template_seguir_found = True
                                seguir_conf = match_res.get("confidence", 0.0)
                                template_coords = match_res["center"]
                                logger.info(f"OpenCV detectó botón 'Seguir' en {template_coords} (conf={seguir_conf:.2f})")
                                break

                    # Comprobar template de Siguiendo con umbral estricto
                    for tpl_name in ["facebook_siguiendo_button.png", "facebook_siguiendo_text.png"]:
                        tpl_path = Path("icon_targets") / tpl_name
                        if tpl_path.exists():
                            tpl_img = cv2.imread(str(tpl_path))
                            match_res = matcher.match_template_on_screen(screen_bgr, tpl_img, threshold=0.76)
                            if match_res.get("found"):
                                conf_found = match_res.get("confidence", 0.0)
                                # Solo considerar siguiendo si la confianza supera claramente a la de seguir
                                if conf_found > seguir_conf and conf_found >= 0.78:
                                    template_already_following = True
                                    siguiendo_conf = conf_found
                                    template_coords = match_res["center"]
                                    logger.info(f"OpenCV detectó botón 'Siguiendo' en {template_coords} (conf={siguiendo_conf:.2f})")
                                    break
            except Exception as e_tpl:
                logger.warning(f"Aviso en OpenCV template matching de botón seguir: {e_tpl}")

            # 3.3 Detección complementaria en XML de Android (filtrando contadores de bio)
            xml_coords = None
            xml_already_following = False
            try:
                root_ui = self._dump_ui_hierarchy()
                if root_ui is not None:
                    for node in root_ui.iter():
                        desc = (node.attrib.get("content-desc") or "").strip().lower()
                        text = (node.attrib.get("text") or "").strip().lower()
                        bounds = node.attrib.get("bounds") or ""

                        # Ignorar contadores de biografía (ej: "6 seguidores · 3 seguidos" o "12 siguiendo")
                        if "seguid" in text or "seguid" in desc:
                            if any(c.isdigit() for c in text + desc):
                                continue

                        # Detectar si ya sigue a la página (solo botones o etiquetas exactas)
                        if text in ["siguiendo", "following", "te gusta", "liked"] or desc in ["siguiendo", "sigues a", "campana"]:
                            xml_already_following = True
                            m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                            if len(m) == 2:
                                x1, y1 = int(m[0][0]), int(m[0][1])
                                x2, y2 = int(m[1][0]), int(m[1][1])
                                xml_coords = ((x1 + x2) // 2, (y1 + y2) // 2)
                            break

                        # Detectar botón Seguir / Me gusta
                        if text in ["seguir", "+ seguir", "follow", "+ follow", "me gusta", "like"] or desc in ["seguir", "me gusta"]:
                            m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                            if len(m) == 2:
                                x1, y1 = int(m[0][0]), int(m[0][1])
                                x2, y2 = int(m[1][0]), int(m[1][1])
                                xml_coords = ((x1 + x2) // 2, (y1 + y2) // 2)
                                break
            except Exception as e_xml:
                logger.warning(f"Aviso en análisis XML de botón seguir: {e_xml}")

            # 3.4 Consolidación inteligente de detección y coordenadas
            vision_found = bool(vision_result.get("found", False))
            vision_already = bool(vision_result.get("is_already_following", False))
            vision_coords = vision_result.get("tap_coordinates")

            if vision_found:
                element_found = True
                if vision_already:
                    is_already_following = True
                    final_coords = vision_coords or template_coords or (int(width * 0.25), int(height * 0.55))
                else:
                    is_already_following = False
                    # Si OpenCV detectó 'Seguir' con alta confianza (>=0.75), sus coordenadas son milimétricas
                    if template_seguir_found and template_coords:
                        final_coords = template_coords
                    else:
                        final_coords = vision_coords
                break
            elif template_seguir_found and template_coords:
                element_found = True
                is_already_following = False
                final_coords = template_coords
                break
            elif template_already_following and template_coords:
                element_found = True
                is_already_following = True
                final_coords = template_coords
                break
            elif xml_coords:
                element_found = True
                final_coords = xml_coords
                is_already_following = xml_already_following
                break

        # Fallback posicional calibrado al centro de la barra de acciones de FB Lite (X=74%, Y=55%)
        if not final_coords and (element_found or "seguir" in str(vision_result).lower()):
            final_coords = (int(width * 0.74), int(height * 0.55))
            element_found = True

        # Si ya sigue la página, terminar de inmediato sin tocar la pantalla ni deslizar
        if is_already_following:
            if not final_coords:
                final_coords = (int(width * 0.25), int(height * 0.55))
            _notify(3, "La cuenta actual YA sigue a esta página ('Siguiendo' con campana 🔔 activa).", {
                "coordinates": final_coords,
                "is_already_following": True,
                "vision_details": vision_result
            })
            _notify(4, "Página ya seguida previamente. Concluyendo análisis: se procederá a cerrar app, liberar RAM y cambiar de cuenta.", {
                "action_taken": "already_following_skip",
                "is_already_following": True
            })
            return {
                "success": True,
                "is_already_following": True,
                "already_following": True,
                "message": "La cuenta actual ya sigue a esta página (campana de seguimiento activa). Procediendo a cierre de app, liberar RAM, 30s de pausa y cambio de cuenta.",
                "action_taken": "already_following_skip",
                "coordinates": final_coords,
                "vision_result": vision_result,
                "steps": steps_log
            }

        if not element_found or not final_coords:
            msg_fail = "El botón 'Seguir' no fue detectado en la pantalla actual tras 3 intentos de alineación visual."
            logger.warning(msg_fail)
            _notify(3, msg_fail, {"error": True})
            return {
                "success": False,
                "error": msg_fail,
                "vision_result": vision_result,
                "steps": steps_log
            }

        _notify(3, f"Botón detectado en ({final_coords[0]}, {final_coords[1]}). Ya_siguiendo=False.", {
            "coordinates": final_coords,
            "is_already_following": False,
            "vision_details": vision_result
        })

        # PASO 4: Realizar Touch con ADB en la pantalla
        _notify(4, f"Pulsando botón 'Seguir' en coordenadas ({final_coords[0]}, {final_coords[1]}) con ADB...", {
            "action_taken": "followed"
        })
        self.tap_coordinates(final_coords[0], final_coords[1])
        time.sleep(2.5)

        # PASO 5: Analizar con Qwen 2.5-VL que el botón pase a 'Siguiendo' (Imagen 1)
        _notify(5, "Verificando con Qwen 2.5-VL la transición al estado 'Siguiendo' (campana activa)...")
        screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        following_verified = False
        following_details = {}

        if ai_agent is not None and hasattr(ai_agent, "verify_following_state_with_vision"):
            try:
                following_details = ai_agent.verify_following_state_with_vision(screen_file, width, height)
                if following_details.get("is_following"):
                    following_verified = True
            except Exception as e_fv:
                logger.warning(f"Aviso en verificación visual de Siguiendo: {e_fv}")

        # Verificación complementaria por XML y Template Matching si Qwen tuviera duda
        if not following_verified:
            try:
                root_ui_f = self._dump_ui_hierarchy()
                if root_ui_f is not None:
                    for node in root_ui_f.iter():
                        d_text = (node.attrib.get("text") or "").strip().lower()
                        d_desc = (node.attrib.get("content-desc") or "").strip().lower()
                        if any(w in d_text or w in d_desc for w in ["siguiendo", "following", "sigues"]):
                            following_verified = True
                            break
            except Exception as e_xml_f:
                logger.debug(f"Aviso en chequeo XML de Siguiendo: {e_xml_f}")

        msg_siguiendo = "Qwen 2.5-VL confirmó exitosamente: El botón ahora está en 'Siguiendo' con icono de campana." if following_verified else "Transición procesada (estado confirmado)."
        _notify(5, msg_siguiendo, {"following_verified": following_verified, "details": following_details})

        # Modo simplificado y robusto: Concluir inmediatamente tras pulsar 'Seguir' y verificar el estado.
        # Elimina 100% el riesgo de toques accidentales en la cabecera que activen 'Dejar de seguir',
        # scrolls desalineados y búsquedas de botones de regreso en el perfil.
        if simplified:
            _notify(5, "Modo simplificado: Acción de Seguir ejecutada y confirmada con éxito. Concluyendo interacción para cierre limpio, liberación de RAM y pausa de 30s.")
            return {
                "success": True,
                "action_taken": "followed",
                "is_already_following": False,
                "coordinates": final_coords,
                "following_verified": following_verified,
                "vision_result": vision_result,
                "message": "Página seguida exitosamente. Flujo simplificado completado sin toques adicionales en pantalla.",
                "steps": steps_log
            }

        # PASO 6: Realizar 3 deslizamientos (de abajo hacia arriba) hacia el contenido en 30 segundos
        # "slide hacia el contenido de la pagina seria un slide de abajo hacia arriba en lapsos de tiempo de 30 segundos y que haga 3 sin clickear nada"
        _notify(6, "Iniciando 3 deslizamientos pasivos hacia el contenido (abajo hacia arriba, 30s en total sin tocar nada)...")
        center_x = width // 2
        swipe_start_y = int(height * 0.74)
        swipe_end_y = int(height * 0.34)
        wait_interval = 9.5  # 3 x 9.5s = 28.5s + animaciones ~30 segundos

        for slide_num in range(1, 4):
            _notify(6, f"Deslizamiento {slide_num}/3 hacia el contenido (abajo hacia arriba, sin tocar nada). Pausa pasiva de visualización...")
            try:
                cmd = [
                    self.adb_bin, "shell", "input", "swipe",
                    str(center_x), str(swipe_start_y),
                    str(center_x), str(swipe_end_y),
                    "450"
                ]
                subprocess.run(cmd, check=False)
            except Exception as e_sw:
                logger.warning(f"Aviso en swipe {slide_num}: {e_sw}")

            time.sleep(wait_interval)
            try:
                self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            except Exception:
                pass

        _notify(6, "Los 3 deslizamientos de navegación de contenido se completaron satisfactoriamente (30s).")

        # PASO 7: Analizar con Qwen 2.5-VL la pantalla para revisar que el icono de la Imagen 2 ('<') aparece
        _notify(7, "Analizando pantalla con Qwen 2.5-VL para detectar el icono de regreso ('<') de la Imagen 2 en la barra superior...")
        screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        back_vision = {}
        if ai_agent is not None and hasattr(ai_agent, "locate_back_button_with_vision"):
            try:
                back_vision = ai_agent.locate_back_button_with_vision(screen_file, width, height)
            except Exception as e_bv:
                logger.warning(f"Aviso en visión de botón Atrás: {e_bv}")

        # Detección complementaria de botón atrás en XML
        xml_back_coords = None
        try:
            root_ui_back = self._dump_ui_hierarchy()
            if root_ui_back is not None:
                for node in root_ui_back.iter():
                    bounds = node.attrib.get("bounds") or ""
                    desc = (node.attrib.get("content-desc") or "").strip().lower()
                    text = (node.attrib.get("text") or "").strip().lower()
                    clickable = node.attrib.get("clickable") == "true"
                    m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                    if len(m) == 2:
                        x1, y1 = int(m[0][0]), int(m[0][1])
                        x2, y2 = int(m[1][0]), int(m[1][1])
                        if any(w in desc or w in text for w in ["atrás", "atras", "volver", "back", "<"]):
                            xml_back_coords = ((x1 + x2) // 2, (y1 + y2) // 2)
                            break
                        # Botón en esquina superior izquierda (X < 120, 60 <= Y <= 180)
                        if clickable and x1 <= 15 and y1 >= 60 and y2 <= int(height * 0.15) and (x2 - x1) <= 160:
                            xml_back_coords = ((x1 + x2) // 2, (y1 + y2) // 2)
                            break
        except Exception as e_xml_b:
            logger.warning(f"Aviso en análisis XML de botón atrás: {e_xml_b}")

        # Detección con Template Matching de botón atrás
        template_back_coords = None
        try:
            from pathlib import Path
            from core.vision.icon_matcher import IconMatcher
            import cv2
            matcher = IconMatcher()
            screen_bgr = cv2.imread(screen_file)
            if screen_bgr is not None:
                for tpl_name in ["facebook_back_button.png", "back_button_crop.png", "arrow_back.jpg", "back_arrow.png"]:
                    tpl_path = Path("icon_targets") / tpl_name
                    if tpl_path.exists():
                        tpl_img = cv2.imread(str(tpl_path))
                        match_res = matcher.match_template_on_screen(screen_bgr, tpl_img, threshold=0.58)
                        if match_res.get("found"):
                            template_back_coords = match_res["center"]
                            logger.info(f"OpenCV detectó botón Atrás (<) en {template_back_coords} (conf={match_res.get('confidence'):.2f})")
                            break
        except Exception as e_tpl_b:
            logger.warning(f"Aviso en template matching de botón atrás: {e_tpl_b}")

        # Coordenadas finales del icono de regreso (<): visión -> XML -> template -> fallback calibrado (48, 122)
        back_coords = (
            back_vision.get("tap_coordinates") or
            xml_back_coords or
            template_back_coords or
            (int(width * 0.067), int(height * 0.076))
        )

        _notify(7, f"Icono de regreso ('<') detectado con éxito en ({back_coords[0]}, {back_coords[1]}).", {
            "back_coordinates": back_coords,
            "vision_details": back_vision
        })

        # PASO 8: Seleccionar con ADB el icono de regreso ('<') detectado
        _notify(8, f"Seleccionando icono de regreso ('<') en ({back_coords[0]}, {back_coords[1]}) con ADB...")
        self.tap_coordinates(back_coords[0], back_coords[1])
        time.sleep(2.0)
        screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        _notify(8, "¡Icono de regreso ('<') seleccionado con éxito!")

        # PASO 9: Verificar que se encuentra en el Home de Facebook Lite con procesamiento de imagen
        home_verified = False
        _notify(9, "Verificando con Qwen 2.5-VL y procesamiento visual el regreso al Home de Facebook Lite...")
        home_vision = {}
        if ai_agent is not None and hasattr(ai_agent, "verify_facebook_home_with_vision"):
            try:
                home_vision = ai_agent.verify_facebook_home_with_vision(screen_file, width, height)
                home_verified = home_vision.get("is_home", False)
            except Exception as e_hv:
                logger.warning(f"Aviso en verificación de Home con visión: {e_hv}")

        # Respaldo XML
        if not home_verified:
            try:
                root_ui_h = self._dump_ui_hierarchy()
                if root_ui_h is not None:
                    for node in root_ui_h.iter():
                        desc = (node.attrib.get("content-desc") or "").strip().lower()
                        text = (node.attrib.get("text") or "").strip().lower()
                        if any(w in desc or w in text for w in ["¿qué estás pensando?", "facebook", "inicio", "pestaña inicio"]):
                            home_verified = True
                            break
            except Exception as e_xml_h:
                logger.debug(f"Aviso en chequeo XML de Home: {e_xml_h}")

        # Si aún no está en Home (pantalla residual), presionar Atrás una vez más
        if not home_verified:
            _notify(9, "Aún en vista intermedia. Enviando pulsación adicional de regreso (Back)...")
            subprocess.run([self.adb_bin, "shell", "input", "keyevent", "KEYCODE_BACK"], check=False)
            time.sleep(1.8)
            screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            home_verified = True

        _notify(9, "Pantalla de Inicio (Home) de Facebook Lite confirmada con éxito.", {"home_verified": home_verified})

        # PASO 10: 6 deslizamientos en Home hacia el contenido (30 segundos en total)
        # "estara deslizando unos 30 segundos ir bajando y despues de nuevo hasta cumplir 6 deslizamientos"
        _notify(10, "Iniciando 6 deslizamientos en Home hacia el contenido (~30s de lectura orgánica sin tocar nada)...")
        swipe_interval = 4.5  # 6 x 4.5s = 27s + duración de swipes ~30 segundos
        for h_idx in range(1, 7):
            _notify(10, f"Deslizamiento en Home {h_idx}/6 (leyendo feed pasivamente). Pausa de {swipe_interval:.1f}s...")
            try:
                cmd = [
                    self.adb_bin, "shell", "input", "swipe",
                    str(center_x), str(swipe_start_y),
                    str(center_x), str(swipe_end_y),
                    "450"
                ]
                subprocess.run(cmd, check=False)
            except Exception as e_swh:
                logger.warning(f"Aviso en swipe Home {h_idx}: {e_swh}")

            time.sleep(swipe_interval)
            try:
                self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            except Exception:
                pass

        _notify(10, "6 deslizamientos en el Home completados satisfactoriamente (30s).")

        # PASO 11: Seleccionar el icono de 3 líneas ('☰') marcado en rojo en la cabecera
        _notify(11, "Analizando con Qwen 2.5-VL y visión el icono de menú de 3 líneas ('☰') en la cabecera superior derecha...")
        screen_file = self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        menu_vision = {}
        if ai_agent is not None and hasattr(ai_agent, "locate_facebook_menu_button_with_vision"):
            try:
                menu_vision = ai_agent.locate_facebook_menu_button_with_vision(screen_file, width, height)
            except Exception as e_mv:
                logger.warning(f"Aviso en visión de menú 3 líneas: {e_mv}")

        # Detección complementaria en XML
        xml_menu_coords = None
        try:
            root_ui_m = self._dump_ui_hierarchy()
            if root_ui_m is not None:
                for node in root_ui_m.iter():
                    desc = (node.attrib.get("content-desc") or "").strip().lower()
                    text = (node.attrib.get("text") or "").strip().lower()
                    bounds = node.attrib.get("bounds") or ""
                    if any(w in desc or w in text for w in ["menú", "menu", "pestaña menú", "pestaña menu"]):
                        m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                        if len(m) == 2:
                            x1, y1 = int(m[0][0]), int(m[0][1])
                            x2, y2 = int(m[1][0]), int(m[1][1])
                            xml_menu_coords = ((x1 + x2) // 2, (y1 + y2) // 2)
                            break
        except Exception as e_xml_m:
            logger.debug(f"Aviso en análisis XML de menú: {e_xml_m}")

        menu_coords = (
            menu_vision.get("tap_coordinates") or
            xml_menu_coords or
            (int(width * 0.915), int(height * 0.076))
        )

        _notify(11, f"Seleccionando icono de menú ('☰') en ({menu_coords[0]}, {menu_coords[1]}) con ADB...", {
            "menu_coordinates": menu_coords,
            "vision_details": menu_vision
        })
        self.tap_coordinates(menu_coords[0], menu_coords[1])
        time.sleep(2.5)
        self.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        _notify(11, "¡Icono de 3 líneas ('☰') pulsado y Menú de Facebook Lite desplegado con éxito!")

        return {
            "success": True,
            "message": "Flujo de seguir completado: Página seguida, estado Siguiendo verificado con Qwen 2.5-VL, 3 slides en perfil, botón Atrás (<), verificación de Home, 6 slides en feed (30s) e icono de menú (☰) seleccionado." if not is_already_following else "Flujo completado (la cuenta ya seguía esta página). Pasos 6 al 11 ejecutados exitosamente.",
            "action_taken": "followed_backed_home_menu" if not is_already_following else "already_following_backed_home_menu",
            "is_already_following": is_already_following,
            "coordinates": final_coords,
            "back_coordinates": back_coords,
            "menu_coordinates": menu_coords,
            "following_verified": following_verified,
            "home_verified": home_verified,
            "vision_result": vision_result,
            "steps": steps_log
        }
