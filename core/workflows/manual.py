"""
manual_automation.py - Módulo de Automatización Manual con Lista Predefinida (Sin IA)
Ejecuta la rotación estricta de comentarios predefinidos pegados por el usuario,
cuenta por cuenta, utilizando exclusivamente interacciones directas ADB/OpenCV
sin invocar Ollama ni registrar memoria de IA.
"""

import logging
import time
import os
import shutil
from typing import List, Optional, Callable, Dict, Any
import cv2

import config
from core.adb.controller import AndroidController
from core.memory.accounts import accounts_memory

logger = logging.getLogger("AndroidAutomator")


def run_manual_preset_automation(
    url: str,
    pasted_comments: List[str],
    accounts_to_use: int = 0,
    do_like: bool = True,
    do_comment: bool = True,
    repeat_cycles: bool = False,
    max_cycles: int = 1,
    package_name: str = "",
    media_type: str = "post",
    cooldown_seconds: int = 0,
    clear_ram_between_cycles: bool = True,
    feed_browsing: bool = False,
    auto_restart_on_failure: bool = False,
    restart_attempt: int = 0,
    max_restarts: int = 3,
    check_abort: Optional[Callable[[], bool]] = None,
    broadcast_fn: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    emit_step_fn: Optional[Callable[[int, str, bool, Optional[str], Optional[str]], None]] = None,
) -> bool:
    """
    Ejecuta el ciclo de interacción cuenta por cuenta consumiendo comentarios de la lista predefinida.
    NO invoca ningún modelo de lenguaje ni visión por IA (Ollama).
    """
    def _is_aborted() -> bool:
        return check_abort() if check_abort else False

    def _broadcast(event_type: str, data: Dict[str, Any]) -> None:
        if broadcast_fn:
            try:
                broadcast_fn(event_type, data)
            except Exception as e:
                logger.warning(f"Error emitiendo broadcast SSE ({event_type}): {e}")

    current_acc_state = {"idx": 1, "total": 1, "label": ""}

    def _emit(step_num: int, message: str, is_error: bool = False, current_comment: Optional[str] = None, account_label: Optional[str] = None) -> None:
        lbl = account_label or current_acc_state["label"]
        if emit_step_fn:
            try:
                emit_step_fn(step_num, message, is_error, current_comment, lbl, current_acc_state["idx"], current_acc_state["total"])
            except TypeError:
                emit_step_fn(step_num, message, is_error, current_comment, lbl)
        else:
            if is_error:
                logger.error(f"[PRESET MANUAL ERROR] {message}")
            else:
                logger.info(f"[PRESET MANUAL STEP {step_num}] {message}")

    def _handle_abort(controller: AndroidController) -> None:
        logger.info("Automatización manual cancelada por el usuario. Retornando a pantalla de inicio...")
        try:
            controller.go_to_android_home(sleep_after=1.0)
        except Exception:
            pass
        _emit(99, "Automatización con Lista Predefinida detenida por el usuario.")

    try:
        controller = AndroidController()
        target_package = package_name.strip() if package_name and package_name.strip() else config.PACKAGE_NAME
        if not target_package or target_package == "com.instagram.android":
            target_package = "com.instagram.lite"

        if _is_aborted():
            _handle_abort(controller)
            return False

        dev_serial = controller.get_device_serial()
        dev_model = controller.get_device_model()

        # Consultar cuentas en memoria
        dev_summary = accounts_memory.get_device_summary(dev_serial)
        total_detected = dev_summary.get("total_accounts", 0)
        if total_detected < 1:
            total_detected = 11
            accounts_memory.get_or_register_device(dev_serial, dev_model, total_detected)

        if accounts_to_use > 0:
            accounts_count = min(accounts_to_use, total_detected)
        else:
            accounts_count = total_detected

        avail_accounts = accounts_memory.get_available_accounts_for_post(dev_serial, url, accounts_count)

        active_preset_comments = [c.strip() for c in pasted_comments if c and c.strip()]
        if do_comment and not active_preset_comments:
            _emit(-1, "Error: La lista de comentarios predefinidos está vacía. Pega tus comentarios antes de iniciar.", is_error=True)
            return False

        total_consumed_comments = 0
        cycle_round = 1
        content_label = "Reel" if media_type == "reel" else "publicación"

        logger.info(f"=== INICIANDO SCRIPT MANUAL CON LISTA PREDEFINIDA ({accounts_count} cuentas, {len(active_preset_comments)} comentarios cargados) ===")

        while True:
            if _is_aborted():
                _handle_abort(controller)
                return False

            logger.info(f"=== RONDA {cycle_round} (LISTA PREDEFINIDA - {accounts_count} CUENTAS) ===")

            for acc_idx in range(accounts_count):
                if _is_aborted():
                    _handle_abort(controller)
                    return False

                target_acc = avail_accounts[acc_idx] if acc_idx < len(avail_accounts) else {"index": acc_idx, "label": f"Cuenta {acc_idx+1}"}
                acc_label = target_acc.get("label", f"Cuenta {acc_idx+1}")
                if target_acc.get("username") and not target_acc.get("username").startswith("cuenta_"):
                    acc_label += f" (@{target_acc.get('username')})"

                account_banner = f"Cuenta {acc_idx + 1}/{accounts_count}: [{acc_label}]"
                current_acc_state["idx"] = acc_idx + 1
                current_acc_state["total"] = accounts_count
                current_acc_state["label"] = acc_label
                logger.info(f"--- [LISTA PREDEFINIDA] {account_banner} ---")

                # Paso 1: Abrir URL o usar pantalla actual
                cleaned_url = url.strip() if url else ""
                if cleaned_url and cleaned_url.lower() not in ["current", "none", "actual", "skip"]:
                    _emit(1, f"{account_banner}: Abriendo enlace en {target_package}...", account_label=acc_label)
                    opened = controller.open_post_link(cleaned_url, package=target_package)
                    if not opened:
                        raise RuntimeError(f"Fallo al abrir el enlace {cleaned_url} en {target_package}.")
                    _emit(1, f"{account_banner}: Esperando carga completa y estabilización de contenido...", account_label=acc_label)
                    screen_file = controller.wait_for_instagram_content_load(min_wait=7.5, max_wait=20.0, check_interval=1.5, ai_agent=None)
                else:
                    _emit(1, f"{account_banner}: Usando el {content_label} actual en pantalla...", account_label=acc_label)
                    time.sleep(1.0)
                    screen_file = controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

                if _is_aborted():
                    _handle_abort(controller)
                    return False

                try:
                    shutil.copyfile(screen_file, "current_post_screen.png")
                except Exception:
                    pass

                # Paso 2: Análisis de pantalla y detección de botones (OpenCV template / geometría híbrida)
                btn_coords = controller.detect_action_buttons(screen_path=screen_file, ai_agent=None, media_type=media_type)
                if btn_coords.get("media_type"):
                    media_type = btn_coords.get("media_type")
                    content_label = "Reel" if media_type == "reel" else "publicación"
                detected_layout_name = btn_coords.get("layout_type", media_type)
                logger.info(f"{account_banner}: Layout activo determinado por visión: {detected_layout_name} ({content_label})")

                if do_like:
                    _emit(2, f"{account_banner}: Verificando botón de Me gusta (Like)...", account_label=acc_label)
                    if btn_coords.get("already_liked"):
                        _emit(2, f"{account_banner}: El {content_label} ya tiene Like. Manteniendo reacción.", account_label=acc_label)
                    else:
                        like_pt = btn_coords.get("like")
                        if not like_pt:
                            width, height = controller.get_screen_size()
                            if media_type == "reel":
                                like_pt = (int(width * 0.938), int(height * 0.404))
                            else:
                                like_pt = (int(width * 0.071), int(height * 0.797))
                        _emit(2, f"{account_banner}: Pulsando corazón para dar Like...", account_label=acc_label)
                        controller.tap_coordinates(*like_pt)
                        time.sleep(config.LIKE_DELAY_SECONDS)
                else:
                    _emit(2, f"{account_banner}: Acción de Like omitida por configuración.", account_label=acc_label)

                if _is_aborted():
                    _handle_abort(controller)
                    return False

                # Paso 3: Consumir comentario del banco rotativo predefinido
                if do_comment:
                    if not active_preset_comments:
                        logger.info(f"{account_banner}: Banco de comentarios rotativos completamente consumido.")
                        _emit(4, f"{account_banner}: Lista predefinida completada (0 comentarios restantes). Finalizando.", account_label=acc_label)
                        break

                    # Consumir el comentario de la lista
                    comment_text = active_preset_comments.pop(0).strip()
                    total_consumed_comments += 1

                    # Actualizar textarea en el frontend en tiempo real
                    _broadcast("comment_bank_updated", {
                        "platform": "instagram",
                        "consumed_comment": comment_text,
                        "remaining_comments": list(active_preset_comments)
                    })
                    logger.info(f"{account_banner}: Comentario consumido de la lista: '{comment_text}' ({len(active_preset_comments)} restantes)")

                    # Mostrar preview en la tarjeta de comentario activo con origen BANCO ROTATIVO
                    arch_name = f"Lista Predefinida #{total_consumed_comments}"
                    _broadcast("comment_generated", {
                        "comment": comment_text,
                        "archetype": {"name": arch_name},
                        "origin": "preset_list",
                        "account": account_banner
                    })
                    _emit(3, f"{account_banner}: Comentario seleccionado ({arch_name}): \"{comment_text}\"", current_comment=comment_text, account_label=acc_label)
                    time.sleep(0.8)

                    # Paso 4: Abrir panel de comentarios
                    _emit(4, f"{account_banner}: Localizando botón de comentarios...", account_label=acc_label)
                    comment_pt = btn_coords.get("comment")
                    if not comment_pt:
                        screen_file = controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                        comment_pt = controller.locate_comment_button(screen_path=screen_file, ai_agent=None, media_type=media_type)
                    _emit(4, f"{account_banner}: Pulsando icono para abrir panel de comentarios...", account_label=acc_label)
                    controller.tap_coordinates(*comment_pt)
                    time.sleep(2.5)

                    if _is_aborted():
                        _handle_abort(controller)
                        return False

                    # Paso 5: Escribir comentario de forma natural por ADB
                    _emit(5, f"{account_banner}: Escribiendo comentario en el campo de texto...", current_comment=comment_text, account_label=acc_label)
                    typed = controller.type_comment_naturally(comment_text, stop_checker=_is_aborted)
                    if _is_aborted():
                        _handle_abort(controller)
                        return False
                    if not typed:
                        raise RuntimeError("No se pudo escribir el comentario en la caja de texto.")
                    time.sleep(1.5)

                    # Paso 6: Pulsar botón Publicar y cerrar teclado/panel
                    _emit(6, f"{account_banner}: Pulsando 'Publicar' para enviar comentario...", current_comment=comment_text, account_label=acc_label)
                    published = controller.tap_publish_comment(sleep_after=3.5)
                    if not published:
                        logger.warning("Botón 'Publicar' no respondió o comentario ya enviado.")
                    controller.dismiss_keyboard_and_drawer(keyboard_wait=1.2, inter_pause=1.5, drawer_wait=2.0)
                    _emit(6, f"{account_banner}: Comentario publicado con éxito.", current_comment=comment_text, account_label=acc_label)

                    # Registrar interacción en memoria de cuentas (sin IA)
                    accounts_memory.record_interaction(
                        device_serial=dev_serial,
                        account_index=target_acc.get("index", acc_idx),
                        post_url=cleaned_url,
                        comment_text=comment_text,
                        archetype_name="Lista Predefinida",
                        action_type="like_and_comment" if do_like else "comment_only"
                    )

                if _is_aborted():
                    _handle_abort(controller)
                    return False

                # Paso 7: Regresar al feed y asegurar Inicio (Home, Slot 1)
                _emit(7, f"{account_banner}: Regresando al feed...", account_label=acc_label)
                arrow_res = controller.locate_and_tap_icon("back_arrow", sleep_after=2.0)
                if not arrow_res.get("found"):
                    arrow_res = controller.locate_and_tap_icon("arrow_back", sleep_after=2.0)
                if not arrow_res.get("found"):
                    controller.press_back(sleep_after=1.5)

                time.sleep(1.0)
                _emit(7, f"{account_banner}: Asegurando posición en Inicio (Feed, Slot 1)...", account_label=acc_label)
                controller.verify_and_tap_nav_bar(slot_index=1, sleep_after=2.0)
                time.sleep(1.0)

                if _is_aborted():
                    _handle_abort(controller)
                    return False

                # Paso 8: Feed browsing suave si está activo
                if feed_browsing:
                    _emit(8, f"{account_banner}: Navegando en el feed suavemente (20s)...", account_label=acc_label)
                    controller.browse_feed_naturally(duration_seconds=20.0, stop_checker=_is_aborted)
                    if _is_aborted():
                        _handle_abort(controller)
                        return False

                # Paso 9: Cambiar de cuenta si hay más cuentas por procesar
                if acc_idx < accounts_count - 1 and (not do_comment or len(active_preset_comments) > 0):
                    time.sleep(1.0)
                    _emit(9, f"{account_banner}: Tocando Perfil (Slot 5) para cambiar de cuenta...", account_label=acc_label)
                    nav_prof = controller.verify_and_tap_nav_bar(slot_index=5, sleep_after=2.5)
                    if not nav_prof.get("success"):
                        raise RuntimeError("No se pudo tocar el icono de Perfil en la barra de navegación.")

                    if _is_aborted():
                        _handle_abort(controller)
                        return False

                    time.sleep(1.5)
                    _emit(10, "Abriendo menú desplegable de cuentas ('v')...", account_label=acc_label)
                    opened_sheet = controller.open_account_dropdown_sheet(sleep_after=2.0)
                    if not opened_sheet:
                        raise RuntimeError("No se localizó el menú desplegable de cuentas ('v').")

                    if _is_aborted():
                        _handle_abort(controller)
                        return False

                    time.sleep(1.5)
                    next_idx = (acc_idx + 1) % accounts_count
                    _emit(11, f"Cambiando a cuenta siguiente (Fila {next_idx + 1} de {accounts_count})...", account_label=acc_label)
                    switched = controller.switch_to_account_by_index(next_idx)
                    if not switched:
                        raise RuntimeError(f"Fallo al cambiar a la cuenta fila {next_idx + 1}.")

                    if _is_aborted():
                        _handle_abort(controller)
                        return False

                    # Salir de perfil inmediatamente hacia Inicio (Home, Slot 1)
                    time.sleep(2.0)
                    _emit(12, f"Cuenta #{next_idx + 1} activa. Pasando de Perfil a Inicio (Home, Slot 1)...", account_label=acc_label)
                    controller.verify_and_tap_nav_bar(slot_index=1, sleep_after=2.5)
                    time.sleep(1.5)

                # Liberación de RAM entre cuentas si está activa
                if clear_ram_between_cycles:
                    _emit(13, f"{account_banner}: Liberando memoria RAM y cerrando apps recientes...", account_label=acc_label)
                    controller.clear_recent_apps(packages=[target_package], sleep_after=1.5)

                # Cooldown antibloqueo si está configurado
                if cooldown_seconds > 0 and (acc_idx + 1 < accounts_count or repeat_cycles or cycle_round < max_cycles):
                    _emit(14, f"Enfriamiento antibloqueo activo ({cooldown_seconds}s)...", account_label=acc_label)
                    for rem in range(cooldown_seconds, 0, -1):
                        if _is_aborted():
                            _handle_abort(controller)
                            return False
                        _emit(14, f"⏳ Tiempo de reposo: faltan {rem}s para siguiente cuenta...", account_label=acc_label)
                        time.sleep(1.0)

            # Fin de la ronda
            if not repeat_cycles or (do_comment and len(active_preset_comments) == 0):
                motivo = "Comentarios de la lista agotados" if (do_comment and len(active_preset_comments) == 0) else f"{accounts_count} cuentas procesadas"
                _emit(100, f"¡AUTOMATIZACIÓN CON LISTA PREDEFINIDA FINALIZADA! ({motivo}).")
                controller.ensure_in_home_feed(sleep_after=1.5)
                controller.go_to_android_home(sleep_after=2.0)
                break
            else:
                cycle_round += 1

        return True

    except Exception as e:
        if _is_aborted():
            _handle_abort(controller)
            return False
        elif auto_restart_on_failure and restart_attempt < max_restarts:
            logger.warning(f"Error en automatización con lista predefinida: {e}. Auto-reinicio {restart_attempt + 1}/{max_restarts}...")
            _emit(98, f"⚠️ Error ({e}). Auto-reinicio activo ({restart_attempt + 1}/{max_restarts})...")
            try:
                controller.clear_recent_apps(packages=[target_package], sleep_after=2.0)
                controller.go_to_android_home(sleep_after=2.0)
            except Exception:
                pass
            time.sleep(5.0)
            if not _is_aborted():
                return run_manual_preset_automation(
                    url=url, pasted_comments=pasted_comments, accounts_to_use=accounts_to_use,
                    do_like=do_like, do_comment=do_comment, repeat_cycles=repeat_cycles,
                    max_cycles=max_cycles, package_name=package_name, media_type=media_type,
                    cooldown_seconds=cooldown_seconds, clear_ram_between_cycles=clear_ram_between_cycles,
                    feed_browsing=feed_browsing, auto_restart_on_failure=auto_restart_on_failure,
                    restart_attempt=restart_attempt + 1, max_restarts=max_restarts,
                    check_abort=check_abort, broadcast_fn=broadcast_fn, emit_step_fn=emit_step_fn
                )
        else:
            err_msg = f"DETENCIÓN POR ERROR (AUTOMATIZACIÓN PREDEFINIDA): {e}"
            logger.error(err_msg)
            _emit(-1, err_msg, is_error=True)
            return False
