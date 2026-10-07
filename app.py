"""
app.py - Web Control Dashboard and REST API for Android Social Media Automator.
"""

import json
import logging
import os
import queue
import re
import base64
import io
import math
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import requests
from flask import Flask, Response, jsonify, render_template, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

import config
from core.adb.controller import AndroidController, find_adb_binary, get_controller
from core.ai.agent import AIAgent
from config import logger
from core.vision.icon_matcher import IconMatcher
from core.memory.accounts import accounts_memory
from core.memory.store import memory_store
from core.workflows.manual import run_manual_preset_automation
from web.routes.memory import memory_bp
from web.routes.comments import comments_bp
from web.routes.accounts import accounts_bp
from web.routes.icons import icons_bp

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.register_blueprint(memory_bp)
app.register_blueprint(comments_bp)
app.register_blueprint(accounts_bp)
app.register_blueprint(icons_bp)

from web.events import broadcast_event, log_queue, recent_logs, recent_logs_lock


class QueueLogHandler(logging.Handler):
    """Custom logging handler that dispatches logs to web clients via SSE queue."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = {
                "type": "log",
                "level": record.levelname,
                "message": self.format(record),
                "timestamp": time.strftime("%H:%M:%S", time.localtime(record.created)),
            }
            with recent_logs_lock:
                recent_logs.append(log_entry)
                if len(recent_logs) > 200:
                    recent_logs.pop(0)

            log_queue.put_nowait(log_entry)
        except Exception:
            pass


queue_handler = QueueLogHandler()
queue_handler.setFormatter(logging.Formatter(config.LOG_FORMAT, config.LOG_DATE_FORMAT))
logger.addHandler(queue_handler)


# --- Automation State & Background Worker ---
class AutomationManager:
    """Manages the background execution of feed analysis and device automation."""

    def __init__(self) -> None:
        self.state: str = "IDLE"  # IDLE, RUNNING, STOPPING, ERROR
        self.current_iteration: int = 0
        self.max_iterations: int = config.MAX_ITERATIONS
        self.latest_result: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.stop_requested: bool = False
        self.worker_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self.state == "RUNNING"

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "current_iteration": self.current_iteration,
                "max_iterations": self.max_iterations,
                "latest_result": self.latest_result,
                "last_error": self.last_error,
            }

    def start(self, package_name: str, max_iterations: int, scroll_wait: float) -> bool:
        with self.lock:
            if self.state == "RUNNING":
                return False
            self.state = "RUNNING"
            self.stop_requested = False
            self.last_error = None
            self.current_iteration = 0
            self.max_iterations = max_iterations

            self.worker_thread = threading.Thread(
                target=self._run_automation,
                args=(package_name, max_iterations, scroll_wait),
                daemon=True,
            )
            self.worker_thread.start()
            return True

    def stop(self) -> None:
        with self.lock:
            if self.state == "RUNNING":
                self.stop_requested = True
                self.state = "STOPPING"
                logger.info("Stop requested by user. Waiting for current operation to complete...")

    def _broadcast_event(self, event_type: str, data: Any) -> None:
        try:
            log_queue.put_nowait({"type": event_type, "data": data})
        except Exception:
            pass

    def _run_automation(self, package_name: str, max_iterations: int, scroll_wait: float) -> None:
        logger.info(f"Starting automation background task for package: {package_name}")
        self._broadcast_event("status_change", {"state": "RUNNING"})

        try:
            controller = AndroidController()
        except Exception as e:
            err = f"ADB Initialization failed: {e}"
            logger.error(err)
            with self.lock:
                self.state = "ERROR"
                self.last_error = err
            self._broadcast_event("status_change", {"state": "ERROR", "error": err})
            return

        ai_agent = AIAgent(
            base_url=config.OLLAMA_BASE_URL,
            vision_model=config.VISION_MODEL,
            text_model=config.TEXT_MODEL,
        )

        try:
            # 1. Launch target app
            logger.info(f"Launching app: {package_name}")
            controller.open_app(package_name)
            time.sleep(config.APP_STARTUP_DELAY_SECONDS)

            all_results: List[Dict[str, Any]] = []

            # 2. Main Iteration Loop
            for i in range(1, max_iterations + 1):
                with self.lock:
                    if self.stop_requested:
                        logger.info("Automation gracefully halted by user request.")
                        break
                    self.current_iteration = i

                logger.info(f"--- Running Iteration {i}/{max_iterations} ---")
                self._broadcast_event("iteration_update", {"iteration": i, "max_iterations": max_iterations})

                # A. Screen Capture & UI Text
                screen_path = controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                ui_text = controller.get_ui_text()
                self._broadcast_event("screen_updated", {"timestamp": time.time()})

                # B. Vision Model Inference
                vision_result = ai_agent.analyze_screen_vision(screen_path, ui_text)
                posts = vision_result.get("posts", [])
                text_analysis: Dict[str, Any] = {"analysis": []}

                # C. Text Model Inference
                if posts:
                    text_analysis = ai_agent.analyze_post_data(vision_result)

                # Record results
                iteration_payload = {
                    "iteration": i,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "vision_data": vision_result,
                    "text_analysis": text_analysis,
                }
                all_results.append(iteration_payload)

                with self.lock:
                    self.latest_result = iteration_payload

                # Broadcast new analysis cards to UI
                self._broadcast_event("new_analysis", iteration_payload)

                # Handle clipboard action if requested
                for item in text_analysis.get("analysis", []):
                    if str(item.get("action", "")).strip().lower() == "copy":
                        snippet = f"@{item.get('author')}: {item.get('topic')}"
                        controller.copy_to_clipboard(snippet)

                # Check scroll decision
                should_scroll = vision_result.get("should_scroll", True)
                if not should_scroll:
                    logger.info("AI determined 'should_scroll' is False. Ending feed scan.")
                    break

                if i < max_iterations:
                    logger.info(f"Scrolling down. Waiting {scroll_wait}s for feed reload...")
                    controller.swipe_down()
                    time.sleep(scroll_wait)

            # 3. Write final results to file
            with open(config.OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(all_results, f, indent=2, ensure_ascii=False)
            logger.info(f"Successfully saved {len(all_results)} iterations to '{config.OUTPUT_FILE}'.")

            with self.lock:
                self.state = "IDLE"
            self._broadcast_event("status_change", {"state": "IDLE"})

        except Exception as e:
            err = f"Automation runtime error: {e}"
            logger.error(err)
            with self.lock:
                self.state = "ERROR"
                self.last_error = err
            self._broadcast_event("status_change", {"state": "ERROR", "error": err})


manager = AutomationManager()


# --- Multi-Account Dynamic Cycle Manager ---
class MultiAccountCycleManager:
    """
    Manages the multi-account interaction cycle across all registered accounts
    dynamically discovered on the connected Android phone.
    Enforces strict error stopping: halts immediately if any action fails.
    """

    def __init__(self) -> None:
        self.is_running: bool = False
        self.stop_requested: bool = False
        self.current_account_idx: int = 0
        self.total_accounts: int = 0
        self.current_step: int = 0
        self.current_step_msg: str = ""
        self.last_error: Optional[str] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.current_comment: str = ""
        self.current_account_label: str = ""
        self.comment_mode: str = "auto"
        self.pasted_comments: List[str] = []
        self.lock = threading.Lock()

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "is_running": self.is_running,
                "current_account_idx": self.current_account_idx,
                "total_accounts": self.total_accounts,
                "current_step": self.current_step,
                "current_step_msg": self.current_step_msg,
                "last_error": self.last_error,
                "comment_mode": self.comment_mode,
            }

    def stop(self) -> None:
        with self.lock:
            self.stop_requested = True
        logger.info("Solicitud de detención del ciclo multi-cuenta recibida.")

        # Disparar retorno inmediato a Android Home en hilo de fondo para no bloquear
        def _send_home_safely():
            try:
                ctrl = AndroidController()
                ctrl.go_to_android_home(sleep_after=1.0)
                logger.info("Dispositivo regresado exitosamente a Android Home tras orden de detención.")
            except Exception as e:
                logger.warning(f"No se pudo enviar a Home tras detención: {e}")

        threading.Thread(target=_send_home_safely, daemon=True).start()

        # Notificar a la UI por SSE de inmediato
        manager._broadcast_event("cycle_step", {
            "account_idx": self.current_account_idx,
            "total_accounts": self.total_accounts,
            "step": 99,
            "message": "Automatización detenida. El dispositivo regresó a la pantalla principal.",
            "is_error": False,
        })

    def start(
        self,
        url: str,
        tone: str = "positive",
        custom_comment: str = "",
        do_like: bool = True,
        do_comment: bool = True,
        repeat_cycles: bool = False,
        vision_model: str = "",
        text_model: str = "",
        max_cycles: int = 1,
        accounts_to_use: int = 0,
        narrative: str = "a_favor",
        manual_context: str = "",
        is_contest: bool = False,
        contest_requirement: str = "",
        teams_or_topic: str = "",
        package_name: str = "",
        media_type: str = "post",
        comment_mode: str = "auto",
        pasted_comments: Optional[List[str]] = None,
        cooldown_seconds: int = 0,
        clear_ram_between_cycles: bool = True,
        feed_browsing: bool = False,
        auto_restart_on_failure: bool = False,
    ) -> bool:
        with self.lock:
            if self.is_running:
                return False
            self.is_running = True
            self.stop_requested = False
            self.last_error = None
            self.current_account_idx = 0
            self.total_accounts = 0
            self.current_step = 0
            self.current_step_msg = "Iniciando ciclo multi-cuenta..."
            self.max_cycles = max(1, int(max_cycles))
            self.accounts_to_use = max(0, int(accounts_to_use))
            self.narrative = narrative or "a_favor"
            self.manual_context = manual_context
            self.is_contest = is_contest
            self.contest_requirement = contest_requirement
            self.teams_or_topic = teams_or_topic
            self.comment_mode = comment_mode or "auto"
            self.pasted_comments = [c.strip() for c in (pasted_comments or []) if c.strip()]
            self.generated_comments_history = []
            self.package_name = package_name or config.PACKAGE_NAME
            self.media_type = media_type or "post"

        self.worker_thread = threading.Thread(
            target=self._run_cycle,
            args=(
                url, tone, custom_comment, do_like, do_comment, repeat_cycles,
                vision_model, text_model, self.max_cycles, self.accounts_to_use, self.narrative,
                manual_context, is_contest, contest_requirement, teams_or_topic, self.package_name,
                self.media_type, self.comment_mode, self.pasted_comments,
                cooldown_seconds, clear_ram_between_cycles, feed_browsing, auto_restart_on_failure
            ),
            daemon=True,
        )
        self.worker_thread.start()
        return True

    def start_preset(
        self,
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
    ) -> bool:
        """Inicia la automatización con lista predefinida (sin IA, delegada a manual_automation.py)."""
        with self.lock:
            if self.is_running:
                return False
            self.is_running = True
            self.stop_requested = False
            self.last_error = None
            self.current_account_idx = 1
            self.total_accounts = max(1, int(accounts_to_use))
            self.current_step = 1
            self.current_step_msg = "Iniciando automatización con lista predefinida..."
            self.current_comment = ""
            self.current_account_label = ""
            self.max_cycles = max(1, int(max_cycles))
            self.accounts_to_use = max(0, int(accounts_to_use))
            self.comment_mode = "preset_list"
            self.pasted_comments = [c.strip() for c in (pasted_comments or []) if c.strip()]
            self.package_name = package_name or config.PACKAGE_NAME
            self.media_type = media_type or "post"

        def _worker():
            try:
                def _emit(step_num, message, is_error=False, current_comment=None, account_label=None, account_idx=None, total_accounts=None):
                    with self.lock:
                        self.current_step = step_num
                        self.current_step_msg = message
                        if current_comment is not None:
                            self.current_comment = current_comment
                        if account_label is not None:
                            self.current_account_label = account_label
                        if account_idx is not None:
                            self.current_account_idx = account_idx
                        if total_accounts is not None:
                            self.total_accounts = total_accounts
                        if is_error:
                            self.last_error = message
                    manager._broadcast_event("cycle_step", {
                        "account_idx": self.current_account_idx,
                        "total_accounts": self.total_accounts,
                        "account_label": self.current_account_label,
                        "current_comment": self.current_comment,
                        "step": step_num,
                        "message": message,
                        "is_error": is_error,
                    })

                run_manual_preset_automation(
                    url=url,
                    pasted_comments=self.pasted_comments,
                    accounts_to_use=self.accounts_to_use,
                    do_like=do_like,
                    do_comment=do_comment,
                    repeat_cycles=repeat_cycles,
                    max_cycles=max_cycles,
                    package_name=self.package_name,
                    media_type=self.media_type,
                    cooldown_seconds=cooldown_seconds,
                    clear_ram_between_cycles=clear_ram_between_cycles,
                    feed_browsing=feed_browsing,
                    auto_restart_on_failure=auto_restart_on_failure,
                    check_abort=lambda: self.stop_requested,
                    broadcast_fn=manager._broadcast_event,
                    emit_step_fn=_emit,
                )
            finally:
                with self.lock:
                    self.is_running = False
                    self.stop_requested = False
                logger.info("Hilo de automatización con lista predefinida finalizado completamente.")

        self.worker_thread = threading.Thread(target=_worker, daemon=True)
        self.worker_thread.start()
        return True

    # Alias para retrocompatibilidad
    start_automatizacion_manual = start_preset

    def _run_cycle(
        self,
        url: str,
        tone: str,
        custom_comment: str,
        do_like: bool,
        do_comment: bool,
        repeat_cycles: bool,
        v_model: str,
        t_model: str,
        max_cycles: int = 1,
        accounts_to_use: int = 0,
        narrative: str = "a_favor",
        manual_context: str = "",
        is_contest: bool = False,
        contest_requirement: str = "",
        teams_or_topic: str = "",
        package_name: str = "",
        media_type: str = "post",
        comment_mode: str = "auto",
        pasted_comments: Optional[List[str]] = None,
        cooldown_seconds: int = 0,
        clear_ram_between_cycles: bool = True,
        feed_browsing: bool = False,
        auto_restart_on_failure: bool = False,
        restart_attempt: int = 0,
        max_restarts: int = 3,
    ) -> None:
        def check_abort() -> bool:
            with self.lock:
                return self.stop_requested

        def handle_abort() -> None:
            logger.info("Ciclo interrumpido por el usuario. Asegurando pantalla principal de Android y finalizando automatización...")
            try:
                controller.go_to_android_home(sleep_after=1.0)
            except Exception:
                pass
            emit_step(99, "Automatización detenida por el usuario. El teléfono regresó a la pantalla de inicio.")

        def emit_step(
            step_num: int,
            message: str,
            is_error: bool = False,
            current_comment: Optional[str] = None,
            account_label: Optional[str] = None,
        ) -> None:
            with self.lock:
                self.current_step = step_num
                self.current_step_msg = message
                if current_comment is not None:
                    self.current_comment = current_comment
                if account_label is not None:
                    self.current_account_label = account_label
                if is_error:
                    self.last_error = message
            manager._broadcast_event("cycle_step", {
                "account_idx": self.current_account_idx,
                "total_accounts": self.total_accounts,
                "account_label": self.current_account_label,
                "current_comment": self.current_comment,
                "device_serial": self.device_serial,
                "device_model": self.device_model,
                "step": step_num,
                "message": message,
                "is_error": is_error,
            })
            if is_error:
                logger.error(f"[CYCLE ERROR] {message}")
            else:
                logger.info(f"[CYCLE STEP {step_num}] {message}")

        try:
            controller = AndroidController()
            ai_agent = AIAgent(
                base_url=config.OLLAMA_BASE_URL,
                vision_model=v_model or config.VISION_MODEL,
                text_model=t_model or config.TEXT_MODEL,
            )
            target_package = package_name.strip() if package_name and package_name.strip() else config.PACKAGE_NAME
            if not target_package or target_package == "com.instagram.android":
                target_package = "com.instagram.lite"

            if check_abort():
                handle_abort()
                return

            dev_serial = controller.get_device_serial()
            dev_model = controller.get_device_model()

            # Consultar cuentas previamente analizadas en la memoria persistente (sin re-escanear)
            dev_summary = accounts_memory.get_device_summary(dev_serial)
            total_detected = dev_summary.get("total_accounts", 0)
            if total_detected < 1:
                total_detected = 11
                accounts_memory.get_or_register_device(dev_serial, dev_model, total_detected)

            logger.info(f"[CICLO MULTI-CUENTA] Usando {total_detected} cuentas en memoria para {dev_model} ({dev_serial}).")

            if accounts_to_use > 0:
                accounts_count = min(accounts_to_use, total_detected)
            else:
                accounts_count = total_detected

            with self.lock:
                self.total_accounts = accounts_count
                self.device_serial = dev_serial
                self.device_model = dev_model

            # Obtener cuentas disponibles sin repetición para este post
            avail_accounts = accounts_memory.get_available_accounts_for_post(dev_serial, url, accounts_count)

            logger.info(f"Ciclo listo: {total_detected} registradas ({dev_model} - {dev_serial}). Operando {accounts_count} cuentas con narrativa '{narrative}'.")

            if check_abort():
                handle_abort()
                return

            active_preset_comments = list(pasted_comments or [])
            total_consumed_comments = 0
            cycle_round = 1
            while True:
                if check_abort():
                    handle_abort()
                    return

                content_label = "Reel" if media_type == "reel" else "publicación"
                logger.info(f"=== INICIANDO RONDA {cycle_round} DEL CICLO MULTI-CUENTA ({accounts_count} CUENTAS, {content_label.upper()}) ===")

                for acc_idx in range(accounts_count):
                    if check_abort():
                        handle_abort()
                        return

                    target_acc = avail_accounts[acc_idx] if acc_idx < len(avail_accounts) else {"index": acc_idx, "label": f"Cuenta {acc_idx+1}"}
                    acc_label = target_acc.get("label", f"Cuenta {acc_idx+1}")
                    if target_acc.get("username") and not target_acc.get("username").startswith("cuenta_"):
                        acc_label += f" (@{target_acc.get('username')})"

                    with self.lock:
                        self.current_account_idx = acc_idx + 1
                        self.current_account_label = acc_label
                        self.current_comment = ""

                    account_banner = f"Cuenta {acc_idx + 1}/{accounts_count}: [{acc_label}]"
                    logger.info(f"--- [PROCESANDO {account_banner}] ---")

                    # Paso 1: Abrir enlace de la publicación en el teléfono (si se dio URL) o usar pantalla actual y tomar captura
                    cleaned_url = url.strip() if url else ""
                    if cleaned_url and cleaned_url.lower() not in ["current", "none", "actual", "skip"]:
                        emit_step(1, f"{account_banner}: Abriendo enlace en {target_package}...")
                        opened = controller.open_post_link(cleaned_url, package=target_package)
                        if not opened:
                            raise RuntimeError(f"Fallo al abrir el enlace {cleaned_url} en {target_package}.")
                        emit_step(1, f"{account_banner}: Esperando carga completa y estabilización de contenido...")
                        screen_file = controller.wait_for_instagram_content_load(min_wait=7.5, max_wait=20.0, check_interval=1.5, ai_agent=ai_agent)
                    else:
                        emit_step(1, f"{account_banner}: Usando el {content_label} actual en pantalla...")
                        time.sleep(1.0)
                        screen_file = controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)

                    if check_abort():
                        handle_abort()
                        return

                    try:
                        shutil.copyfile(screen_file, "current_post_screen.png")
                    except Exception:
                        pass

                    # Paso 2: Análisis dinámico de pantalla y layout (Reel vertical rail vs Post horizontal row)
                    emit_step(2, f"{account_banner}: Analizando pantalla con IA para resolver layout y botones de acción...")
                    btn_coords = controller.detect_action_buttons(screen_path=screen_file, ai_agent=ai_agent, media_type=media_type)

                    # Reajustar dinámicamente el formato si la pantalla difiere del tipo predefinido
                    if btn_coords.get("media_type"):
                        media_type = btn_coords.get("media_type")
                        content_label = "Reel" if media_type == "reel" else "publicación"
                    detected_layout_name = btn_coords.get("layout_type", media_type)
                    logger.info(f"{account_banner}: Layout activo determinado por visión: {detected_layout_name} ({content_label})")

                    if do_like:
                        if btn_coords.get("already_liked"):
                            emit_step(3, f"{account_banner}: El {content_label} ya tiene 'Me gusta'. Manteniendo reacción.")
                        else:
                            like_pt = btn_coords.get("like")
                            if not like_pt:
                                width, height = controller.get_screen_size()
                                if media_type == "reel":
                                    like_pt = (int(width * 0.938), int(height * 0.404))
                                else:
                                    like_pt = (int(width * 0.071), int(height * 0.797))
                            emit_step(3, f"{account_banner}: Pulsando corazón para dar Like...")
                            controller.tap_coordinates(*like_pt)
                            time.sleep(config.LIKE_DELAY_SECONDS)
                    else:
                        emit_step(3, f"{account_banner}: Acción de Like omitida por configuración.")

                    if check_abort():
                        handle_abort()
                        return

                    # Paso 3: Obtener contexto de la publicación (protegiendo Reels de swipe involuntario)
                    allow_caption_expand = btn_coords.get("allow_caption_expand", (media_type != "reel"))
                    if not allow_caption_expand or media_type == "reel":
                        emit_step(4, f"{account_banner}: {content_label.capitalize()} detectado: Omitiendo 'ver más' para proteger la pantalla de cambios involuntarios...")
                    else:
                        emit_step(4, f"{account_banner}: Desplazando un poco hacia abajo y expandiendo descripción ('... más')...")
                        controller.expand_caption_more(ai_agent=ai_agent, scroll_first=True, scroll_amount=220, media_type="post")

                    # Obtener contexto textual y visual completo
                    if manual_context:
                        caption_text = manual_context.strip()
                        logger.info(f"Usando contexto manual de publicación: {caption_text[:80]}...")
                    else:
                        ui_text = controller.get_ui_text()
                        caption_text = ui_text[:400].strip() if ui_text else f"{content_label.capitalize()} destacado en redes sociales"

                    if is_contest or contest_requirement:
                        contest_info = {
                            "is_contest": True,
                            "contest_type": "pronostico" if any(w in (contest_requirement + caption_text).lower() for w in ["pronostico", "pronóstico", "marcador", "clásico", "clasico"]) else "sorteo",
                            "contest_requirement": contest_requirement or "Comentar según dinámica o pronóstico",
                            "teams_or_topic": teams_or_topic or "Dinámica interactiva",
                            "sample_comments": ["2-1", "1-0", "1-1", "Gana la U 2-0"]
                        }
                    else:
                        contest_info = None

                    emit_step(4, f"{account_banner}: Contexto visual y textual completo obtenido.")

                    if check_abort():
                        handle_abort()
                        return

                    # Paso 4: Abrir panel de comentarios
                    if do_comment:
                        emit_step(5, f"{account_banner}: Localizando botón de comentarios...")
                        comment_pt = btn_coords.get("comment")
                        if not comment_pt:
                            screen_file = controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                            comment_pt = controller.locate_comment_button(screen_path=screen_file, ai_agent=ai_agent, media_type=media_type)
                        emit_step(5, f"{account_banner}: Pulsando botón de comentarios para abrir panel...")
                        controller.tap_coordinates(*comment_pt)
                        time.sleep(2.5)

                        if check_abort():
                            handle_abort()
                            return

                        # Generación de comentario con IA y memoria persistente
                        arch_name = "Humano"
                        if custom_comment:
                            comment_text = custom_comment
                            arch = {"name": "Personalizado"}
                            arch_name = "Personalizado"
                            vis_elem = "manual"
                            arch_label = "[Personalizado]"
                            ai_agent.record_agent_memory(
                                caption=caption_text,
                                narrative=narrative,
                                archetype=arch,
                                comment=comment_text,
                                account=account_banner,
                                visual_element="manual",
                            )
                        elif comment_mode == "preset_list":
                            if active_preset_comments:
                                # Consumir y borrar el comentario del banco para que no se repita
                                comment_text = active_preset_comments.pop(0).strip()
                                total_consumed_comments += 1
                                with self.lock:
                                    self.pasted_comments = list(active_preset_comments)

                                # Emitir evento SSE para actualizar textarea y contador en tiempo real
                                manager._broadcast_event("comment_bank_updated", {
                                    "platform": "instagram",
                                    "consumed_comment": comment_text,
                                    "remaining_comments": list(active_preset_comments)
                                })
                                logger.info(f"{account_banner}: Comentario consumido y eliminado del banco: '{comment_text}' ({len(active_preset_comments)} restantes)")

                                arch_name = f"Lista Predefinida #{total_consumed_comments}"
                                arch = {"name": arch_name}
                                vis_elem = "lista_predefinida"
                                arch_label = f"[Lista #{total_consumed_comments}]"
                                ai_agent.record_agent_memory(
                                    caption=caption_text,
                                    narrative=narrative,
                                    archetype=arch,
                                    comment=comment_text,
                                    account=account_banner,
                                    visual_element="lista_predefinida",
                                )
                            else:
                                # Banco agotado: generar con IA para no repetir ningún comentario anterior
                                logger.info(f"{account_banner}: Banco de comentarios rotativos agotado. Generando nuevo comentario con IA para no repetir...")
                                emit_step(5, f"{account_banner}: Banco rotativo agotado. Generando nuevo comentario con IA...")
                                comment_text = ai_agent.generate_post_comment(
                                    caption=caption_text,
                                    author="",
                                    tone=tone,
                                    archetype_id=None,
                                    recent_comments=self.generated_comments_history,
                                    narrative=narrative,
                                    contest_info=contest_info,
                                    image_path="current_post_screen.png"
                                )
                                arch = getattr(ai_agent, "last_archetype", None)
                                vis_elem = getattr(ai_agent, "last_visual_element", "el entorno")
                                arch_name = arch.get("name", "Humano") if isinstance(arch, dict) else str(arch or "Humano")
                                arch_label = f"[{arch_name}] · Fallback IA"
                        else:
                            arch_id = None
                            if tone == "auto" or not tone:
                                arch_id = None  # Human Archetype with persistent memory
                            elif str(tone).isdigit():
                                arch_id = int(tone)
                            else:
                                arch_id = tone

                            comment_text = ai_agent.generate_post_comment(
                                caption=caption_text,
                                author="",
                                tone=tone,
                                archetype_id=arch_id,
                                recent_comments=self.generated_comments_history,
                                narrative=narrative,
                                contest_info=contest_info,
                                image_path="current_post_screen.png",
                                reference_comments=pasted_comments if pasted_comments else None
                            )
                            arch = getattr(ai_agent, "last_archetype", None)
                            vis_elem = getattr(ai_agent, "last_visual_element", "el entorno")
                            arch_name = arch.get("name", "Humano") if isinstance(arch, dict) else str(arch or "Humano")
                            arch_label = f"[{arch_name}] · '{vis_elem}'"

                        if not comment_text:
                            raise RuntimeError("No se pudo generar un comentario válido con la IA.")

                        self.generated_comments_history.append(comment_text)

                        with self.lock:
                            self.current_comment = comment_text
                        manager._broadcast_event("comment_generated", {
                            "comment": comment_text,
                            "archetype": arch,
                            "account": account_banner,
                            "contest_info": contest_info
                        })
                        emit_step(6, f"{account_banner}: Comentario verificado ({arch_label}): \"{comment_text}\"", current_comment=comment_text, account_label=acc_label)
                        time.sleep(1.0)

                        if check_abort():
                            handle_abort()
                            return

                        # Paso 5: Enfocar campo y escribir comentario con ritmo humano
                        emit_step(7, f"{account_banner}: Escribiendo comentario de forma natural...")
                        typed = controller.type_comment_naturally(comment_text, stop_checker=check_abort)
                        if check_abort():
                            handle_abort()
                            return
                        if not typed:
                            raise RuntimeError("Fallo al inyectar el comentario en la caja de texto.")
                        time.sleep(1.5)

                        if check_abort():
                            handle_abort()
                            return

                        # Paso 6: Pulsar 'Publicar' y cerrar panel de comentarios
                        emit_step(8, f"{account_banner}: Localizando botón 'Publicar' y enviando comentario...")
                        published = controller.tap_publish_comment(sleep_after=3.5)
                        if not published:
                            logger.warning("Botón 'Publicar' no respondió o comentario ya enviado.")
                        controller.dismiss_keyboard_and_drawer(keyboard_wait=1.2, inter_pause=1.5, drawer_wait=2.0)
                        emit_step(8, f"{account_banner}: Comentario publicado y panel cerrado exitosamente.", current_comment=comment_text, account_label=acc_label)
                        # Registrar en memoria persistente del dispositivo
                        accounts_memory.record_interaction(
                            device_serial=dev_serial,
                            account_index=target_acc.get("index", acc_idx),
                            post_url=cleaned_url,
                            comment_text=comment_text,
                            archetype_name=arch_name if isinstance(arch_name, str) else "",
                            action_type="like_and_comment"
                        )

                    if check_abort():
                        handle_abort()
                        return

                    # Paso 9: Flecha atrás ('<-') para volver al feed
                    emit_step(10, f"{account_banner}: Tocando flecha de retroceso ('<-') para salir al feed...")
                    arrow_res = controller.locate_and_tap_icon("back_arrow", sleep_after=2.0)
                    if not arrow_res.get("found"):
                        arrow_res = controller.locate_and_tap_icon("arrow_back", sleep_after=2.0)
                    if not arrow_res.get("found"):
                        logger.warning("Plantilla flecha no encontrada, enviando KEYCODE_BACK...")
                        controller.press_back(sleep_after=1.5)

                    time.sleep(1.0)
                    try:
                        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                    except Exception:
                        pass

                    # ASEGURAR QUE VUELVA A HOME (Slot 1) Y NUNCA SE QUEDE EN PERFIL:
                    emit_step(10, f"{account_banner}: Asegurando posición en Inicio (Feed, Slot 1)...")
                    controller.verify_and_tap_nav_bar(slot_index=1, sleep_after=2.0)
                    time.sleep(1.0)
                    try:
                        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                    except Exception:
                        pass

                    if check_abort():
                        handle_abort()
                        return

                    # Paso 10: Navegación orgánica en el feed (~32s) si está activa
                    if feed_browsing:
                        emit_step(11, f"{account_banner}: Navegando en el feed suavemente y poco a poco (32 segundos, sin clics ni likes)...")
                        controller.browse_feed_naturally(duration_seconds=32.0, stop_checker=check_abort)
                        if check_abort():
                            handle_abort()
                            return
                        time.sleep(1.0)
                        try:
                            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                        except Exception:
                            pass
                    else:
                        logger.info(f"{account_banner}: Navegación suave en feed omitida (opción desactivada).")

                    if check_abort():
                        handle_abort()
                        return

                    # Pasos 11-14: Cambiar a la siguiente cuenta solo si quedan más cuentas por procesar
                    if acc_idx < accounts_count - 1:
                        time.sleep(1.0)
                        emit_step(12, f"{account_banner}: Tocando Perfil (Slot 5 en barra inferior)...")
                        nav_prof = controller.verify_and_tap_nav_bar(slot_index=5, sleep_after=2.5)
                        if not nav_prof.get("success"):
                            raise RuntimeError("No se pudo tocar el icono de Perfil en la barra de navegación.")

                        time.sleep(1.5)
                        try:
                            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                        except Exception:
                            pass

                        if check_abort():
                            handle_abort()
                            return

                        # Paso 12: Abrir selector de cuentas ('v' o atajo nativo)
                        emit_step(13, f"{account_banner}: Abriendo desplegable de cuentas ('v')...")
                        opened_sheet = controller.open_account_dropdown_sheet(sleep_after=2.0)
                        if not opened_sheet:
                            raise RuntimeError("No se localizó el menú desplegable de cuentas ('v') en el perfil.")

                        time.sleep(1.5)
                        try:
                            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                        except Exception:
                            pass

                        if check_abort():
                            handle_abort()
                            return

                        # Paso 13: Seleccionar siguiente cuenta y esperar carga
                        next_acc_idx = (acc_idx + 1) % total_detected
                        emit_step(14, f"Cambiando a la siguiente cuenta (Fila {next_acc_idx + 1} de {total_detected})...")
                        switched = controller.switch_to_account_by_index(next_acc_idx)
                        if not switched:
                            raise RuntimeError(f"Fallo al cambiar a la cuenta {next_acc_idx + 1}.")

                        if check_abort():
                            handle_abort()
                            return

                        # Paso 14: SALIR DE PERFIL INMEDIATAMENTE: pasar a Home (Slot 1)
                        time.sleep(2.0)
                        try:
                            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                        except Exception:
                            pass
                        emit_step(15, f"Cuenta {next_acc_idx + 1}: Saliendo de Perfil a Inicio (Home, Slot 1)...")
                        controller.verify_and_tap_nav_bar(slot_index=1, sleep_after=2.5)
                        time.sleep(1.5)
                        try:
                            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                        except Exception:
                            pass
                        if feed_browsing:
                            emit_step(15, f"Cuenta {next_acc_idx + 1}: Navegando feed suavemente (15s)...")
                            controller.browse_feed_naturally(duration_seconds=15.0, stop_checker=check_abort)
                            if check_abort():
                                handle_abort()
                                return
                    else:
                        logger.info(f"{account_banner}: Última cuenta del ciclo procesada ({acc_idx + 1}/{accounts_count}). Regresando al feed principal.")
                        controller.ensure_in_home_feed(sleep_after=1.5)

                    # Paso 15: Volver a la pantalla principal de Android
                    emit_step(16, f"Regresando a la pantalla principal de Android (Launcher)...")
                    controller.go_to_android_home(sleep_after=1.5)

                    # Rutina de Recientes & Liberación de memoria RAM para evitar congelamientos en vueltas posteriores
                    if clear_ram_between_cycles:
                        emit_step(17, f"{account_banner}: Cerrando apps recientes y liberando memoria RAM del teléfono...")
                        controller.clear_recent_apps(packages=[target_package], sleep_after=1.5)

                    # Tiempo de reposo antibloqueo antes de continuar a la siguiente cuenta o vuelta
                    if cooldown_seconds > 0 and (acc_idx + 1 < accounts_count or repeat_cycles or cycle_round < max_cycles):
                        emit_step(18, f"Iniciando tiempo de reposo antibloqueo ({cooldown_seconds}s)...")
                        for rem in range(cooldown_seconds, 0, -1):
                            if check_abort():
                                handle_abort()
                                return
                            emit_step(18, f"⏳ Tiempo de reposo: faltan {rem}s para enfriar el dispositivo y proteger la sesión...")
                            time.sleep(1.0)

                    logger.info(f"--- Completada iteración para {account_banner} con éxito ---")

                if not repeat_cycles and cycle_round >= max_cycles:
                    emit_step(100, f"¡AUTOMATIZACIÓN COMPLETADA! Se cumplieron {cycle_round} ronda(s) en las {accounts_count} cuentas seleccionadas.")
                    break
                else:
                    cycle_round += 1
                    logger.info(f"Pasando a ronda {cycle_round} (Límite: {max_cycles})...")

        except Exception as e:
            if check_abort():
                handle_abort()
            elif auto_restart_on_failure and restart_attempt < max_restarts:
                logger.warning(f"Error detectado en ciclo: {e}. Auto-reinicio {restart_attempt + 1}/{max_restarts} activado.")
                emit_step(
                    98,
                    f"⚠️ Error ({e}). Auto-reinicio activo: Liberando RAM y recomenzando desde Cuenta 1 (Reintento {restart_attempt + 1}/{max_restarts})..."
                )
                try:
                    controller.clear_recent_apps(packages=[target_package], sleep_after=2.0)
                    controller.go_to_android_home(sleep_after=2.0)
                except Exception:
                    pass
                time.sleep(5.0)
                if not check_abort():
                    return self._run_cycle(
                        url=url, tone=tone, custom_comment=custom_comment, do_like=do_like,
                        do_comment=do_comment, repeat_cycles=repeat_cycles,
                        v_model=v_model, t_model=t_model, max_cycles=max_cycles,
                        accounts_to_use=accounts_to_use, narrative=narrative,
                        manual_context=manual_context, is_contest=is_contest,
                        contest_requirement=contest_requirement, teams_or_topic=teams_or_topic,
                        package_name=package_name, media_type=media_type,
                        comment_mode=comment_mode, pasted_comments=pasted_comments,
                        cooldown_seconds=cooldown_seconds, clear_ram_between_cycles=clear_ram_between_cycles,
                        feed_browsing=feed_browsing, auto_restart_on_failure=auto_restart_on_failure,
                        restart_attempt=restart_attempt + 1, max_restarts=max_restarts
                    )
            else:
                err_msg = f"DETENCIÓN INMEDIATA POR ERROR: {e}"
                logger.error(err_msg)
                emit_step(-1, err_msg, is_error=True)
        finally:
            if restart_attempt == 0:
                with self.lock:
                    self.is_running = False
                    self.stop_requested = False
                logger.info("Hilo de ciclo multi-cuenta finalizado completamente.")



cycle_manager = MultiAccountCycleManager()


class FacebookReportCycleManager:
    """
    Gestiona el ciclo multi-cuenta de reportes en Facebook Lite:
    - Detección dinámica de todas las cuentas del dispositivo (hasta el marcador 'Iniciar sesión en otra cuenta').
    - Selección de cuentas sin repetición mediante accounts_memory.
    - Refresco automático de memoria cada 3 o 4 cuentas para máxima precisión.
    - Navegación anti-detección en el feed (~60s variable) entre cuentas.
    """
    def __init__(self):
        self.is_running = False
        self.stop_requested = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.current_round = 0
        self.total_rounds = 0
        self.current_account = ""
        self.device_serial = ""
        self.device_model = ""
        self.status_message = "Inactivo"
        self.feed_remaining_seconds = 0.0
        self.target_url = ""
        self.accounts_used_this_session: List[str] = []
        self.refresh_interval = 3

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "is_running": self.is_running,
                "current_round": self.current_round,
                "total_rounds": self.total_rounds,
                "current_account": self.current_account,
                "device_serial": self.device_serial,
                "device_model": self.device_model,
                "status_message": self.status_message,
                "feed_remaining_seconds": round(self.feed_remaining_seconds, 1),
                "target_url": self.target_url,
                "accounts_used_this_session": list(self.accounts_used_this_session),
                "refresh_interval": self.refresh_interval
            }

    def stop(self) -> None:
        with self.lock:
            self.stop_requested = True
            self.status_message = "Detención solicitada por el usuario..."
        logger.info("[FB MultiAccount] Detención solicitada.")

    def start_cycle(
        self,
        target_url: str,
        accounts_limit: int = 0,
        feed_browse_seconds: float = 60.0,
        feed_variance: float = 10.0,
        sleeps: Optional[Dict[str, float]] = None,
        refresh_interval: int = 3
    ) -> Dict[str, Any]:
        with self.lock:
            if self.is_running:
                return {"success": False, "error": "El ciclo multi-cuenta de Facebook Lite ya se encuentra activo."}
            self.is_running = True
            self.stop_requested = False
            self.target_url = target_url.strip()
            self.current_round = 0
            self.accounts_used_this_session = []
            self.refresh_interval = max(2, min(5, refresh_interval or 3))
            self.status_message = "Iniciando ciclo multi-cuenta en Facebook Lite..."

        self.thread = threading.Thread(
            target=self._run_cycle,
            args=(self.target_url, accounts_limit, feed_browse_seconds, feed_variance, sleeps),
            daemon=True
        )
        self.thread.start()
        return {"success": True, "message": "Ciclo multi-cuenta iniciado correctamente."}

    def _run_cycle(
        self,
        target_url: str,
        accounts_limit: int,
        feed_browse_seconds: float,
        feed_variance: float,
        sleeps: Optional[Dict[str, float]]
    ):
        try:
            ctrl = AndroidController()
            dev_serial = ctrl.get_device_serial()
            dev_model = ctrl.get_device_model()
            with self.lock:
                self.device_serial = dev_serial
                self.device_model = dev_model

            # 1. Comprobar cuentas en memoria o realizar escaneo dinámico inicial
            summary = accounts_memory.get_device_summary(dev_serial)
            known_fb = summary.get("facebook_accounts", [])
            if not known_fb:
                self._update_status(f"Escaneando cuentas registradas en {dev_model}...")
                scan_res = ctrl.scan_facebook_lite_accounts()
                known_names = scan_res.get("accounts", [])
                accounts_memory.register_facebook_accounts(dev_serial, dev_model, known_names)
            else:
                known_names = [a.get("username") or a.get("label") for a in known_fb]

            # 2. Filtrar cuentas que AÚN NO han reportado este perfil
            avail = accounts_memory.get_available_facebook_accounts_for_url(dev_serial, target_url, limit=accounts_limit)
            if not avail:
                raise RuntimeError(f"Todas las cuentas registradas ({len(known_names)}) ya reportaron este enlace.")

            total_to_run = len(avail)
            with self.lock:
                self.total_rounds = total_to_run

            round_num = 0
            while not self.stop_requested and round_num < total_to_run:
                round_num += 1


                # Obtener la siguiente cuenta disponible para esta ronda
                current_unreported = accounts_memory.get_available_facebook_accounts_for_url(dev_serial, target_url)
                if not current_unreported:
                    self._update_status("No hay más cuentas disponibles sin repetir para este perfil.")
                    break

                target_acc = current_unreported[0]
                acc_name = target_acc.get("username") or target_acc.get("label")

                with self.lock:
                    self.current_round = round_num
                    self.current_account = acc_name
                    self.accounts_used_this_session.append(acc_name)

                self._update_status(f"[Ronda {round_num}/{total_to_run}] Alternando a cuenta: '{acc_name}'...")
                if self.stop_requested:
                    break

                # Alternar cuenta en Facebook Lite
                switched = ctrl.switch_to_facebook_account_by_name(acc_name)
                if not switched:
                    logger.warning(f"No fue posible alternar a '{acc_name}', avanzando a siguiente cuenta...")
                    continue

                if self.stop_requested:
                    break

                # Reportar el perfil con los 8 pasos
                self._update_status(f"[Ronda {round_num}/{total_to_run}] Abriendo enlace y esperando carga de perfil con '{acc_name}'...")
                rep_res = ctrl.run_full_profile_report(
                    url=target_url,
                    package="com.facebook.lite",
                    sleeps=sleeps,
                    stop_checker=lambda: self.stop_requested
                )

                if self.stop_requested:
                    break

                if not rep_res.get("success"):
                    logger.warning(f"Reporte falló o no se completó para '{acc_name}': {rep_res.get('message')}. Avanzando al feed...")
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Menú de perfil no disponible para '{acc_name}'. Regresando al feed...")
                    ctrl.return_to_facebook_feed()
                    continue

                # Registrar reporte exitoso en el banco de memoria
                accounts_memory.record_facebook_report(dev_serial, acc_name, target_url)
                logger.info(f"Reporte de '{acc_name}' registrado exitosamente en memoria para {target_url}.")

                # Navegar Feed si aún faltan cuentas
                if round_num < total_to_run and not self.stop_requested:
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Regresando a Feed principal de Facebook...")
                    ctrl.return_to_facebook_feed()

                    def _feed_cb(elapsed, total, remaining):
                        with self.lock:
                            self.feed_remaining_seconds = remaining
                            self.status_message = f"[Ronda {round_num}/{total_to_run}] Navegando feed anti-detección: {remaining:.0f}s restantes"
                        manager._broadcast_event("screen_updated", {"timestamp": time.time()})

                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Navegando feed (~{feed_browse_seconds:.0f}s variables)...")
                    ctrl.browse_facebook_feed(
                        duration_seconds=feed_browse_seconds,
                        variance=feed_variance,
                        stop_checker=lambda: self.stop_requested,
                        progress_callback=_feed_cb
                    )
                    with self.lock:
                        self.feed_remaining_seconds = 0.0

        except Exception as e:
            logger.error(f"Error en ciclo multi-cuenta Facebook: {e}")
            self._update_status(f"Error en ciclo: {e}")
        finally:
            with self.lock:
                self.is_running = False
                if not self.stop_requested and self.current_round >= self.total_rounds and self.total_rounds > 0:
                    self.status_message = f"Ciclo finalizado con éxito ({self.current_round} cuentas procesadas)."
                elif self.stop_requested:
                    self.status_message = "Ciclo detenido por el usuario."
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

    def _update_status(self, msg: str):
        with self.lock:
            self.status_message = msg
        logger.info(f"[FB MultiAccount] {msg}")
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})


fb_cycle_manager = FacebookReportCycleManager()


# =====================================================================
# REQUERIMIENTO OBLIGATORIO: REVISIÓN DE CUENTAS DE FACEBOOK LITE AL INICIAR SERVIDOR
# =====================================================================
fb_accounts_requirement = {
    "reviewed": False,
    "scanning": False,
    "total_accounts": 0,
    "accounts": [],
    "error": None,
    "message": "Requerimiento obligatorio: presiona 'Revisar Cuentas FB' para revisar las cuentas antes de iniciar la automatización.",
    "lock": threading.Lock()
}

def execute_facebook_accounts_review(force: bool = False) -> Dict[str, Any]:
    """
    Ejecuta la revisión obligatoria de cuentas de Facebook Lite en el dispositivo conectado.
    Al concluir, registra las cuentas en accounts_memory y habilita el uso de la automatización.
    """
    with fb_accounts_requirement["lock"]:
        if fb_accounts_requirement["scanning"]:
            return {
                "success": False,
                "already_scanning": True,
                "message": "La revisión de cuentas en Facebook Lite ya se encuentra en progreso."
            }
        fb_accounts_requirement["scanning"] = True
        fb_accounts_requirement["message"] = "Revisando cuentas en Facebook Lite (requerimiento obligatorio)..."
        fb_accounts_requirement["error"] = None

    manager._broadcast_event("fb_requirement_status", {
        "reviewed": False,
        "scanning": True,
        "message": "Revisando cuentas en Facebook Lite antes de habilitar la automatización...",
        "count": 0
    })

    def _worker():
        try:
            ctrl = AndroidController()
            dev_serial = ctrl.get_device_serial()
            dev_model = ctrl.get_device_model()

            def _progress(msg, count=0):
                with fb_accounts_requirement["lock"]:
                    fb_accounts_requirement["message"] = msg
                    if count > 0:
                        fb_accounts_requirement["total_accounts"] = count
                manager._broadcast_event("fb_requirement_status", {
                    "reviewed": False,
                    "scanning": True,
                    "message": msg,
                    "count": count
                })

            logger.info(f"[Startup/Requisito] Iniciando escaneo dinámico de cuentas en Facebook Lite en {dev_model} ({dev_serial})...")
            res = ctrl.scan_facebook_lite_accounts(progress_callback=_progress)
            accounts = res.get("accounts", [])
            total = res.get("total_accounts", len(accounts))

            with fb_accounts_requirement["lock"]:
                fb_accounts_requirement["reviewed"] = True
                fb_accounts_requirement["scanning"] = False
                fb_accounts_requirement["total_accounts"] = total
                fb_accounts_requirement["accounts"] = accounts
                fb_accounts_requirement["message"] = f"✅ Requerimiento completado: {total} cuentas detectadas en Facebook Lite. Automatización habilitada."
                fb_accounts_requirement["error"] = None

            manager._broadcast_event("fb_requirement_status", {
                "reviewed": True,
                "scanning": False,
                "total_accounts": total,
                "accounts": accounts,
                "message": f"Revisión completada: {total} cuentas detectadas en Facebook Lite. Automatización habilitada."
            })
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})
            logger.info(f"[Startup/Requisito] ✅ Requerimiento completado: {total} cuentas verificadas en Facebook Lite.")
        except Exception as e:
            logger.error(f"[Startup/Requisito] Error al revisar cuentas de Facebook Lite: {e}")
            with fb_accounts_requirement["lock"]:
                fb_accounts_requirement["scanning"] = False
                fb_accounts_requirement["reviewed"] = False
                fb_accounts_requirement["error"] = str(e)
                fb_accounts_requirement["message"] = f"Aviso al revisar cuentas: {e}"
            manager._broadcast_event("fb_requirement_status", {
                "reviewed": False,
                "scanning": False,
                "error": str(e),
                "message": f"Aviso al revisar cuentas: {e}"
            })

    t = threading.Thread(target=_worker, daemon=True, name="FBAccountsReviewWorker")
    t.start()
    return {"success": True, "message": "Revisión de cuentas en Facebook Lite iniciada."}


def _startup_facebook_accounts_daemon():
    """
    Hilo de arranque del servidor:
    Espera a que el servidor Flask y ADB estén activos y dispara automáticamente la revisión de cuentas.
    """
    logger.info("[Startup] Hilo de revisión de cuentas en Facebook Lite esperando conexión ADB...")
    time.sleep(3.5)
    try:
        ctrl = AndroidController()
        serial = ctrl.get_device_serial()
        if serial:
            logger.info(f"[Startup] Dispositivo detectado ({serial}). Ejecutando revisión obligatoria de cuentas en Facebook Lite...")
            execute_facebook_accounts_review()
        else:
            logger.info("[Startup] No hay dispositivos ADB activos de inmediato. En espera de conexión para revisión...")
            for _ in range(12):
                time.sleep(3.0)
                ser = ctrl.get_device_serial()
                if ser:
                    logger.info(f"[Startup] Dispositivo conectado detectado ({ser}). Iniciando revisión obligatoria de cuentas...")
                    execute_facebook_accounts_review()
                    break
    except Exception as ex:
        logger.warning(f"[Startup] Excepción al verificar dispositivos en arranque: {ex}")


def start_facebook_accounts_startup_scan():
    """Lanza el hilo en segundo plano de verificación obligatoria de arranque."""
    t = threading.Thread(target=_startup_facebook_accounts_daemon, daemon=True, name="FBStartupScanner")
    t.start()


class FacebookFollowCycleManager:
    """
    Gestor del ciclo multi-cuenta de Seguir en Facebook Lite:
    1. Obtiene las cuentas del dispositivo y verifica cuáles AÚN NO han seguido a la página.
    2. Ejecuta ronda a ronda (de 1 en 1):
       a. Alterna a la cuenta correspondiente (si round > 1).
       b. Ejecuta follow_facebook_page_flow (Pasos 1 al 11).
       c. Registra en memoria que la cuenta siguió a la URL objetivo.
       d. Si quedan más cuentas, abre el menú (☰), espera y avanza a la siguiente.
    3. Emite eventos post_engage_step y actualiza la pantalla en tiempo real.
    4. Permite detener limpiamente el ciclo en cualquier momento.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.is_running = False
        self.stop_requested = False
        self.target_url = ""
        self.total_rounds = 0
        self.current_round = 0
        self.current_account = ""
        self.accounts_used_this_session: List[str] = []
        self.status_message = "Listo"
        self.thread: Optional[threading.Thread] = None
        self.device_serial = ""
        self.device_model = ""

    def start_cycle(
        self,
        target_url: str,
        accounts_limit: int = 5,
        delay_between_accounts: float = 18.0,
        package: str = "com.facebook.lite",
        vision_model: str = "qwen2.5vl:latest"
    ) -> Dict[str, Any]:
        with self.lock:
            if self.is_running:
                return {"success": False, "error": "El ciclo multi-cuenta de Seguir en Facebook Lite ya está activo."}

            # Validar requerimiento obligatorio: revisión de cuentas
            with fb_accounts_requirement["lock"]:
                if fb_accounts_requirement["scanning"]:
                    return {
                        "success": False,
                        "error": "El servidor está realizando la revisión obligatoria de cuentas en Facebook Lite. Espera a que concluya antes de iniciar la automatización."
                    }
                if not fb_accounts_requirement["reviewed"]:
                    return {
                        "success": False,
                        "error": "Requerimiento obligatorio: Se deben revisar cuántas cuentas hay en Facebook Lite antes de usar la automatización. Presiona 'Revisar Cuentas FB' para habilitarla.",
                        "need_scan": True
                    }

            self.is_running = True
            self.stop_requested = False
            self.target_url = target_url.strip()
            self.total_rounds = accounts_limit
            self.current_round = 0
            self.current_account = ""
            self.accounts_used_this_session = []
            self.status_message = "Iniciando ciclo multi-cuenta de Seguir..."

        self.thread = threading.Thread(
            target=self._run_cycle,
            args=(self.target_url, accounts_limit, delay_between_accounts, package, vision_model),
            daemon=True
        )
        self.thread.start()
        return {"success": True, "message": "Ciclo multi-cuenta de Seguir iniciado correctamente."}

    def stop_cycle(self) -> Dict[str, Any]:
        with self.lock:
            if not self.is_running:
                return {"success": True, "message": "El ciclo no se encuentra activo."}
            self.stop_requested = True
            self.status_message = "Detención solicitada por el usuario..."
        logger.info("[FB Follow Cycle] Solicitud de detención recibida.")
        manager._broadcast_event("post_engage_step", {
            "step": -1,
            "message": "Deteniendo ciclo de Seguir por solicitud del usuario..."
        })
        return {"success": True, "message": "Detención solicitada."}

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "is_running": self.is_running,
                "current_round": self.current_round,
                "total_rounds": self.total_rounds,
                "current_account": self.current_account,
                "accounts_used": list(self.accounts_used_this_session),
                "status_message": self.status_message,
                "target_url": self.target_url
            }

    def _update_status(self, msg: str, step: int = 0):
        with self.lock:
            self.status_message = msg
        logger.info(f"[FB Follow Cycle] {msg}")
        manager._broadcast_event("post_engage_step", {
            "step": step,
            "message": msg,
            "account_name": self.current_account,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "is_running": self.is_running
        })
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})

    def _run_cycle(
        self,
        target_url: str,
        accounts_limit: int,
        delay_between_accounts: float,
        package: str,
        vision_model: str
    ):
        try:
            ctrl = AndroidController()
            ai_agent = AIAgent(vision_model=vision_model)
            dev_serial = ctrl.get_device_serial()
            dev_model = ctrl.get_device_model()
            with self.lock:
                self.device_serial = dev_serial
                self.device_model = dev_model

            # 1. Comprobar cuentas conocidas en memoria (verificadas por el requerimiento inicial)
            summary = accounts_memory.get_device_summary(dev_serial)
            known_fb = summary.get("facebook_accounts", [])
            with fb_accounts_requirement["lock"]:
                needs_scan = not fb_accounts_requirement["reviewed"] or not known_fb

            if needs_scan:
                self._update_status(
                    "Requerimiento obligatorio: primero presiona 'Revisar Cuentas FB' en el panel para "
                    "revisar las cuentas de Facebook Lite. La automatización no puede iniciarse sin esa revisión.",
                    step=-1,
                )
                return

            known_names = [a.get("username") or a.get("label") for a in known_fb]

            # 2. Filtrar cuentas que AÚN NO han seguido esta página
            avail = accounts_memory.get_available_facebook_accounts_for_url(dev_serial, target_url, limit=accounts_limit)
            if not avail:
                msg_no_avail = f"Todas las cuentas registradas ({len(known_names)}) ya siguieron este enlace previamente."
                self._update_status(msg_no_avail, step=-1)
                return

            total_to_run = min(accounts_limit, len(avail)) if accounts_limit > 0 else len(avail)
            with self.lock:
                self.total_rounds = total_to_run

            round_num = 0
            while not self.stop_requested and round_num < total_to_run:
                round_num += 1

                # Obtener la siguiente cuenta sin usar para esta página
                current_unreported = accounts_memory.get_available_facebook_accounts_for_url(dev_serial, target_url)
                if not current_unreported:
                    self._update_status("No hay más cuentas disponibles sin repetir para esta página.", step=-1)
                    break

                target_acc = current_unreported[0]
                acc_name = target_acc.get("username") or target_acc.get("label")

                with self.lock:
                    self.current_round = round_num
                    self.current_account = acc_name
                    self.accounts_used_this_session.append(acc_name)

                self._update_status(f"[Ronda {round_num}/{total_to_run}] Preparando cuenta: '{acc_name}'...", step=0)

                # En Ronda 1 se usa la sesión activa actual; en Ronda 2 y posteriores se alterna de cuenta
                if round_num > 1:
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Cambiando a cuenta '{acc_name}'...", step=0)
                    switch_res = ctrl.switch_to_facebook_account_by_name(acc_name)

                    if isinstance(switch_res, dict) and not switch_res.get("success"):
                        logger.warning(f"Primer intento de cambiar a '{acc_name}' falló. Reintentando...")
                        ctrl.open_app(package)
                        time.sleep(2.0)
                        switch_res = ctrl.switch_to_facebook_account_by_name(acc_name)
                        if not (isinstance(switch_res, dict) and switch_res.get("success")):
                            self._update_status(f"[{acc_name}] No se encontró la cuenta en el selector. Saltando a la siguiente...", step=0)
                            continue

                    # Despues de cambiar la cuenta: limpiar RAM, cerrar aplicacion y esperar lapso de 30 segundos
                    did_switch = isinstance(switch_res, dict) and switch_res.get("switched")
                    if did_switch or not (isinstance(switch_res, dict) and switch_res.get("already_active")):
                        self._update_status(f"[{acc_name}] Cambio de cuenta realizado. Cerrando aplicación y liberando memoria RAM...", step=0)
                        try:
                            subprocess.run([ctrl.adb_bin, "shell", "am", "send-trim-memory", package, "COMPLETE"], capture_output=True, check=False)
                        except Exception:
                            pass
                        ctrl.force_stop_app(package)
                        ctrl.go_to_android_home(sleep_after=1.0)
                        import gc
                        gc.collect()

                        self._update_status(f"[{acc_name}] ⏳ Pausa de 30s de enfriamiento antes de abrir enlace...", step=0)
                        for rem in range(30, 0, -1):
                            if self.stop_requested:
                                break
                            self._update_status(f"[{acc_name}] ⏳ Pausa antibloqueo y RAM limpia: faltan {rem}s para abrir enlace...", step=0)
                            time.sleep(1.0)
                else:
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Iniciando con cuenta '{acc_name}' en Facebook Lite...", step=0)
                    time.sleep(1.0)

                if self.stop_requested:
                    break

                # Ejecutar el flujo completo de 11 pasos
                def _step_bridge(st: int, msg: str):
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] [{acc_name}] Paso {st}: {msg}", step=st)

                flow_res = ctrl.follow_facebook_page_flow(
                    url=target_url,
                    ai_agent=ai_agent,
                    package=package,
                    load_wait=4.5,
                    step_callback=_step_bridge
                )

                if self.stop_requested:
                    break

                is_alr = isinstance(flow_res, dict) and flow_res.get("is_already_following")
                if is_alr:
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] [{acc_name}] 🔔 Campana activa detectada: La cuenta ya seguía esta página.", step=3)
                    # 1. Registrar en memoria para no repetir
                    accounts_memory.record_facebook_follow(dev_serial, acc_name, target_url)
                    accounts_memory.record_facebook_report(dev_serial, acc_name, target_url)
                    logger.info(f"Acción 'Seguir' de '{acc_name}' registrada exitosamente en memoria para {target_url}.")

                    # 2. Cerrar app y liberar RAM
                    self._update_status(f"[{acc_name}] Cerrando aplicación y liberando memoria RAM...", step=0)
                    try:
                        subprocess.run([ctrl.adb_bin, "shell", "am", "send-trim-memory", package, "COMPLETE"], capture_output=True, check=False)
                    except Exception:
                        pass
                    ctrl.force_stop_app(package)
                    ctrl.go_to_android_home(sleep_after=1.0)
                    import gc
                    gc.collect()

                    # 3. Esperar lapso de 30 segundos
                    self._update_status(f"[{acc_name}] ⏳ Pausa de 30s de enfriamiento tras detectar seguimiento...", step=0)
                    for rem in range(30, 0, -1):
                        if self.stop_requested:
                            break
                        self._update_status(f"[{acc_name}] ⏳ Pausa antibloqueo y RAM limpia: faltan {rem}s para cambiar cuenta...", step=0)
                        time.sleep(1.0)

                    if round_num < total_to_run and not self.stop_requested:
                        self._update_status(f"[Ronda {round_num}/{total_to_run}] Procediendo al proceso de cambiar cuenta...", step=0)
                    continue

                if not (isinstance(flow_res, dict) and flow_res.get("success")):
                    err_msg = flow_res.get("error", "Fallo al ejecutar el flujo de seguimiento.") if isinstance(flow_res, dict) else "Error desconocido"
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] [{acc_name}] ⚠️ {err_msg}. Asegurando retorno para siguiente cuenta...", step=-1)
                    ctrl.return_to_facebook_feed(max_backs=4)
                    ctrl.force_stop_app(package)
                    ctrl.go_to_android_home(sleep_after=1.0)
                    time.sleep(2.0)
                    continue

                # Si NO seguía la página, se completó la acción de seguir exitosamente
                accounts_memory.record_facebook_follow(dev_serial, acc_name, target_url)
                accounts_memory.record_facebook_report(dev_serial, acc_name, target_url)
                logger.info(f"Acción 'Seguir' de '{acc_name}' registrada exitosamente en memoria para {target_url}.")

                self._update_status(f"[Ronda {round_num}/{total_to_run}] Cuenta '{acc_name}' completada con éxito. Cerrando aplicación y liberando memoria RAM...", step=5)

                # 1. Cerrar aplicación y liberar memoria RAM
                try:
                    subprocess.run([ctrl.adb_bin, "shell", "am", "send-trim-memory", package, "COMPLETE"], capture_output=True, check=False)
                except Exception:
                    pass
                ctrl.force_stop_app(package)
                ctrl.go_to_android_home(sleep_after=1.0)
                import gc
                gc.collect()

                # 2. Esperar lapso de 30 segundos de enfriamiento antibloqueo
                self._update_status(f"[{acc_name}] ⏳ Pausa de 30s de enfriamiento tras seguir...", step=5)
                for rem in range(30, 0, -1):
                    if self.stop_requested:
                        break
                    self._update_status(f"[{acc_name}] ⏳ Pausa antibloqueo y RAM limpia: faltan {rem}s para cambiar cuenta...", step=5)
                    time.sleep(1.0)

                # Si aún faltan cuentas por procesar y no se ha solicitado detener
                if round_num < total_to_run and not self.stop_requested:
                    self._update_status(f"[Ronda {round_num}/{total_to_run}] Procediendo al proceso de cambiar cuenta...", step=0)

            if not self.stop_requested:
                self._update_status(f"¡Ciclo multi-cuenta de Seguir completado exitosamente! ({round_num} cuentas procesadas).", step=100)
            else:
                self._update_status("Ciclo multi-cuenta de Seguir detenido por el usuario.", step=-1)

        except Exception as e:
            logger.error(f"Error en ciclo multi-cuenta de Seguir: {e}", exc_info=True)
            self._update_status(f"Error en ciclo multi-cuenta: {e}", step=-1)
        finally:
            with self.lock:
                self.is_running = False
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})


fb_follow_cycle_manager = FacebookFollowCycleManager()

# Gestor dedicado de interacción en Publicaciones y Reels de Facebook Lite
from core.workflows.facebook_engagement import fb_post_engagement_manager
fb_post_engagement_manager.set_broadcast_callback(manager._broadcast_event)


class ScrcpyDeviceManager:
    """
    Gestor multi-dispositivo con scrcpy y ADB:
    - Descubre todos los dispositivos Android conectados vía ADB.
    - Obtiene telemetría (batería, cargando, modelo, estado, PID de scrcpy).
    - Lanza y controla instancias de scrcpy en mosaico / grid calculado en base a la resolución (2560x1440).
    - Permite arranque / detención individual o en lote (todos).
    - Genera miniaturas en tiempo real con compresión optimizada y caché en memoria.
    - Envía eventos táctiles/teclas globales o individuales (Home, Back, Recientes, Despertar, etc.).
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.processes: Dict[str, subprocess.Popen] = {}
        self.thumbnail_cache: Dict[str, Tuple[float, bytes]] = {}
        self.cache_ttl: float = 2.0  # segundos de validez de caché para miniaturas
        self.adb_bin: str = find_adb_binary()
        self.scrcpy_bin: str = shutil.which("scrcpy") or "/usr/bin/scrcpy"

    def _get_display_resolution(self) -> Tuple[int, int]:
        """Detecta la resolución del monitor principal, fallback a 2560x1440."""
        try:
            out = subprocess.check_output("xdpyinfo | grep dimensions", shell=True, text=True, timeout=1.0)
            m = re.search(r'(\d+)x(\d+)\s+pixels', out)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return 2560, 1440

    def get_connected_devices(self) -> List[Dict[str, Any]]:
        """Devuelve la lista detallada de todos los dispositivos conectados por ADB."""
        try:
            res = subprocess.check_output([self.adb_bin, "devices", "-l"], text=True, timeout=5.0)
        except Exception as e:
            logger.error(f"Error ejecutando 'adb devices -l': {e}")
            return []

        lines = [l.strip() for l in res.strip().splitlines()[1:] if l.strip() and not l.startswith("*")]

        def _fetch_dev_info(line: str) -> Dict[str, Any]:
            parts = line.split()
            if not parts:
                return {}
            serial = parts[0]
            status = parts[1] if len(parts) > 1 else "unknown"
            model = "Android Device"
            product = ""
            transport_id = ""
            for p in parts[2:]:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1]
                elif p.startswith("product:"):
                    product = p.split(":", 1)[1]
                elif p.startswith("transport_id:"):
                    transport_id = p.split(":", 1)[1]

            battery = 100
            charging = True
            screen_on = True
            if status == "device":
                try:
                    bs = subprocess.check_output([self.adb_bin, "-s", serial, "shell", "dumpsys", "battery"], text=True, timeout=2.0)
                    m = re.search(r"level:\s*(\d+)", bs)
                    if m:
                        battery = int(m.group(1))
                    charging = ("USB powered: true" in bs) or ("AC powered: true" in bs)
                except Exception:
                    pass

                try:
                    pw = subprocess.check_output([self.adb_bin, "-s", serial, "shell", "dumpsys", "power"], text=True, timeout=2.0)
                    screen_on = "mHoldingDisplaySuspendBlocker=true" in pw or "Display Power: state=ON" in pw
                except Exception:
                    pass

            with self.lock:
                proc = self.processes.get(serial)
                is_running = proc is not None and proc.poll() is None
                pid = proc.pid if is_running else None
                if proc is not None and proc.poll() is not None:
                    del self.processes[serial]

            return {
                "serial": serial,
                "status": status,
                "model": model,
                "product": product,
                "transport_id": transport_id,
                "battery": battery,
                "charging": charging,
                "screen_on": screen_on,
                "scrcpy_running": is_running,
                "scrcpy_pid": pid
            }

        with ThreadPoolExecutor(max_workers=max(1, len(lines))) as ex:
            devices = list(ex.map(_fetch_dev_info, lines))

        devices = [d for d in devices if d.get("serial")]
        devices.sort(key=lambda x: x["serial"])
        return devices

    def calculate_grid_positions(self, total_devices: int, screen_w: int, screen_h: int) -> List[Dict[str, int]]:
        """Calcula posiciones (x, y, w, h) para acomodar N dispositivos en mosaico."""
        if total_devices <= 0:
            return []

        if total_devices == 1:
            cols, rows = 1, 1
        elif total_devices <= 2:
            cols, rows = 2, 1
        elif total_devices <= 4:
            cols, rows = 2, 2
        elif total_devices <= 6:
            cols, rows = 3, 2
        elif total_devices <= 8:
            cols, rows = 4, 2
        elif total_devices <= 12:
            cols, rows = 4, 3
        elif total_devices <= 15:
            cols, rows = 5, 3
        elif total_devices <= 18:
            cols, rows = 6, 3
        else:
            cols = int(math.ceil(math.sqrt(total_devices * (screen_w / screen_h))))
            rows = int(math.ceil(total_devices / cols))

        slot_w = screen_w // cols
        slot_h = screen_h // rows

        positions = []
        for i in range(total_devices):
            r = i // cols
            c = i % cols
            positions.append({
                "x": c * slot_w,
                "y": r * slot_h,
                "w": slot_w,
                "h": slot_h
            })
        return positions

    def launch_scrcpy(self, serial: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Lanza scrcpy para un dispositivo o para todos ('all').
        Opciones: max_size, bit_rate, stay_awake, turn_screen_off, no_audio.
        """
        options = options or {}
        max_size = options.get("max_size", 600)
        bit_rate = options.get("bit_rate", "2M")
        stay_awake = options.get("stay_awake", True)
        turn_screen_off = options.get("turn_screen_off", False)
        no_audio = options.get("no_audio", True)

        screen_w, screen_h = self._get_display_resolution()
        env = os.environ.copy()
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"
        if "WAYLAND_DISPLAY" not in env and os.path.exists("/run/user/1000/wayland-0"):
            env["WAYLAND_DISPLAY"] = "wayland-0"

        if serial == "all":
            devices = self.get_connected_devices()
            online_devs = [d for d in devices if d["status"] == "device"]
            if not online_devs:
                return {"success": False, "error": "No hay dispositivos autorizados conectados."}

            positions = self.calculate_grid_positions(len(online_devs), screen_w, screen_h)
            launched = []
            errors = []

            for idx, dev in enumerate(online_devs):
                d_serial = dev["serial"]
                pos = positions[idx]

                with self.lock:
                    if d_serial in self.processes and self.processes[d_serial].poll() is None:
                        try:
                            self.processes[d_serial].terminate()
                            self.processes[d_serial].wait(timeout=1.0)
                        except Exception:
                            pass

                cmd = [
                    self.scrcpy_bin,
                    "-s", d_serial,
                    f"--window-title=[{idx+1}/{len(online_devs)}] {d_serial} ({dev['model']})",
                    f"--window-x={pos['x']}",
                    f"--window-y={pos['y']}",
                    f"--window-width={pos['w']}",
                    f"--window-height={pos['h']}",
                    "-m", str(max_size),
                    "-b", str(bit_rate)
                ]
                if no_audio:
                    cmd.append("--no-audio")
                if stay_awake:
                    cmd.append("--stay-awake")
                if turn_screen_off:
                    cmd.append("--turn-screen-off")

                try:
                    proc = subprocess.Popen(
                        cmd,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    with self.lock:
                        self.processes[d_serial] = proc
                    launched.append({"serial": d_serial, "pid": proc.pid})
                    time.sleep(0.08)
                except Exception as ex:
                    errors.append({"serial": d_serial, "error": str(ex)})

            logger.info(f"🚀 Lanzadas {len(launched)} ventanas de scrcpy en mosaico ({screen_w}x{screen_h}).")
            return {
                "success": True,
                "count": len(launched),
                "launched": launched,
                "errors": errors
            }

        else:
            with self.lock:
                if serial in self.processes and self.processes[serial].poll() is None:
                    return {"success": True, "message": "Scrcpy ya está en ejecución", "pid": self.processes[serial].pid}

            cmd = [
                self.scrcpy_bin,
                "-s", serial,
                f"--window-title={serial}",
                "-m", str(max_size),
                "-b", str(bit_rate)
            ]
            if no_audio:
                cmd.append("--no-audio")
            if stay_awake:
                cmd.append("--stay-awake")
            if turn_screen_off:
                cmd.append("--turn-screen-off")

            try:
                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                with self.lock:
                    self.processes[serial] = proc
                logger.info(f"🚀 Lanzada ventana de scrcpy para {serial} (PID: {proc.pid})")
                return {"success": True, "serial": serial, "pid": proc.pid}
            except Exception as e:
                logger.error(f"Error lanzando scrcpy para {serial}: {e}")
                return {"success": False, "error": str(e)}

    def stop_scrcpy(self, serial: str) -> Dict[str, Any]:
        """Detiene scrcpy para un dispositivo o para todos ('all')."""
        if serial == "all":
            stopped = []
            with self.lock:
                for s, proc in list(self.processes.items()):
                    try:
                        proc.terminate()
                        proc.wait(timeout=1.0)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    stopped.append(s)
                self.processes.clear()

            try:
                subprocess.run(["pkill", "-f", "scrcpy"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            logger.info(f"⏹ Todas las ventanas de scrcpy ({len(stopped)}) han sido cerradas.")
            return {"success": True, "stopped_count": len(stopped)}

        else:
            with self.lock:
                proc = self.processes.pop(serial, None)
                if proc:
                    try:
                        proc.terminate()
                        proc.wait(timeout=1.0)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass

            try:
                subprocess.run(["pkill", "-f", f"scrcpy.*{serial}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            logger.info(f"⏹ Scrcpy detenido para {serial}")
            return {"success": True, "serial": serial}

    def get_preview_image(self, serial: str) -> bytes:
        """Captura o devuelve miniatura JPEG en caché para el dispositivo."""
        now = time.time()
        with self.lock:
            cached = self.thumbnail_cache.get(serial)
            if cached and (now - cached[0]) < self.cache_ttl:
                return cached[1]

        try:
            raw = subprocess.check_output(
                [self.adb_bin, "-s", serial, "exec-out", "screencap", "-p"],
                timeout=5.0
            )
            arr = np.frombuffer(raw, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                target_w = 260
                target_h = int(target_w * (h / w))
                thumb = cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_AREA)
                _, buf = cv2.imencode(".jpg", thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
                jpeg_bytes = buf.tobytes()
                with self.lock:
                    self.thumbnail_cache[serial] = (now, jpeg_bytes)
                return jpeg_bytes
        except Exception as e:
            logger.debug(f"Aviso capturando preview de {serial}: {e}")

        blank = np.zeros((460, 260, 3), dtype=np.uint8)
        blank[:] = (20, 24, 32)
        cv2.putText(blank, serial[:10], (25, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 140, 160), 2)
        cv2.putText(blank, "En espera / Standby", (30, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 120, 140), 1)
        _, buf = cv2.imencode(".jpg", blank, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
        return buf.tobytes()

    def send_control(self, serial: str, action: str) -> Dict[str, Any]:
        """Envía comandos de navegación/tecla a un dispositivo o a todos."""
        key_map = {
            "home": 3,
            "back": 4,
            "app_switch": 187,
            "power": 26,
            "wake": 224,
            "unlock": 82,
        }
        keycode = key_map.get(action)
        if not keycode:
            return {"success": False, "error": f"Acción desconocida '{action}'"}

        devices = [serial] if serial != "all" else [d["serial"] for d in self.get_connected_devices() if d["status"] == "device"]

        def _send(s: str):
            try:
                subprocess.run([self.adb_bin, "-s", s, "shell", "input", "keyevent", str(keycode)], timeout=2.0)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=max(1, len(devices))) as ex:
            list(ex.map(_send, devices))

        return {"success": True, "action": action, "targets": len(devices)}

    def clear_device_recents(self, serial: str) -> Dict[str, Any]:
        """Cierra apps recientes y vuelve al home en uno o todos los dispositivos."""
        devices = [serial] if serial != "all" else [d["serial"] for d in self.get_connected_devices() if d["status"] == "device"]

        def _clear(s: str):
            try:
                subprocess.run([self.adb_bin, "-s", s, "shell", "input", "keyevent", "187"], timeout=2.0)
                time.sleep(0.4)
                subprocess.run([self.adb_bin, "-s", s, "shell", "input", "keyevent", "3"], timeout=2.0)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=max(1, len(devices))) as ex:
            list(ex.map(_clear, devices))

        return {"success": True, "action": "clear_recents", "targets": len(devices)}


scrcpy_manager = ScrcpyDeviceManager()


def full_system_stop_and_cleanup_ram() -> Dict[str, Any]:
    """
    Detiene cualquier automatización activa (Facebook, Instagram o General),
    reinicia todas las variables de estado a valores iniciales limpios y ejecuta
    una limpieza profunda y agresiva de memoria RAM tanto en el dispositivo Android
    como en la PC local (descargando modelos de Ollama y recolectando basura en Python).
    """
    logger.info("🛑 [STOP GENERAL] Iniciando detención total, reinicio de tareas y liberación de memoria RAM...")

    # Detener ventanas scrcpy en ejecución
    try:
        scrcpy_manager.stop_scrcpy("all")
    except Exception as ex_sc:
        logger.debug(f"Aviso cerrando scrcpy en stop general: {ex_sc}")

    # 1. Señalizar detención inmediata y reiniciar estados en todos los gestores de automatización
    with fb_cycle_manager.lock:
        fb_cycle_manager.stop_requested = True
        fb_cycle_manager.is_running = False
        fb_cycle_manager.feed_remaining_seconds = 0.0
        fb_cycle_manager.current_round = 0
        fb_cycle_manager.total_rounds = 0
        fb_cycle_manager.current_account = ""
        fb_cycle_manager.status_message = "⏹ Automatización detenida. Memoria RAM liberada y sistema listo."

    with cycle_manager.lock:
        cycle_manager.stop_requested = True
        cycle_manager.is_running = False
        cycle_manager.current_step = 0
        cycle_manager.current_account_idx = 0
        cycle_manager.total_accounts = 0
        cycle_manager.current_step_msg = "⏹ Automatización detenida y memoria RAM liberada."

    with manager.lock:
        manager.stop_requested = True
        manager.state = "IDLE"
        manager.current_action = "Stopped"

    # 2. Notificar inmediatamente a la interfaz web mediante SSE/Eventos para refrescar botones y contadores
    try:
        manager._broadcast_event("status_change", {"state": "IDLE"})
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        manager._broadcast_event("cycle_step", {
            "account_idx": 0,
            "total_accounts": 0,
            "account_label": "",
            "step": 0,
            "message": "⏹ Sistema detenido. Memoria RAM liberada con éxito.",
            "is_error": False,
        })
    except Exception as ex_b:
        logger.debug(f"Aviso en broadcast: {ex_b}")

    # 3. Hilo asíncrono para limpieza agresiva de RAM y reinicio físico sin congelar el request HTTP
    def _do_deep_ram_cleanup():
        # A. Limpieza de memoria RAM en el dispositivo Android
        try:
            ctrl = AndroidController()
            heavy_apps = [
                "com.facebook.lite",
                "com.instagram.lite",
                "com.facebook.katana",
                "com.instagram.android",
                "com.android.chrome"
            ]
            for pkg in heavy_apps:
                try:
                    ctrl.force_stop_app(pkg)
                except Exception:
                    pass

            ctrl.clear_recent_apps(packages=heavy_apps, sleep_after=0.5)
            ctrl.go_to_android_home(sleep_after=0.5)
            logger.info("✅ RAM del dispositivo Android liberada exitosamente (apps cerradas y recientes despejadas).")
        except Exception as ex_adb:
            logger.warning(f"Aviso liberando RAM en teléfono: {ex_adb}")

        # B. Descargar modelos de IA de la memoria RAM / VRAM en Ollama (keep_alive=0)
        try:
            import requests
            models_to_unload = set([
                "qwen2.5vl:latest", "qwen3:latest", "qwen2.5:7b",
                "llama3", "llama3.2-vision",
                config.VISION_MODEL, config.TEXT_MODEL
            ])
            try:
                base_ol = config.OLLAMA_BASE_URL.replace("/api/generate", "")
                ps_res = requests.get(f"{base_ol}/api/ps", timeout=1.5)
                if ps_res.status_code == 200:
                    for item in ps_res.json().get("models", []):
                        m_name = item.get("name") or item.get("model")
                        if m_name:
                            models_to_unload.add(m_name)
            except Exception:
                pass

            for m in models_to_unload:
                if not m:
                    continue
                try:
                    requests.post("http://localhost:11434/api/generate", json={"model": m, "keep_alive": 0}, timeout=1.5)
                except Exception:
                    pass
            logger.info("✅ Modelos de IA descargados de memoria VRAM/RAM en Ollama (keep_alive=0).")
        except Exception as ex_ol:
            logger.debug(f"Aviso liberando memoria en Ollama: {ex_ol}")

        # C. Liberar memoria del proceso Python local (recolector de basura y buffers de frames)
        try:
            global latest_stream_frame
            if 'latest_stream_lock' in globals():
                with latest_stream_lock:
                    latest_stream_frame = None
            import gc
            gc.collect()
            logger.info("✅ Recolector de basura de Python ejecutado (gc.collect).")
        except Exception as ex_gc:
            logger.debug(f"Aviso en gc: {ex_gc}")

    threading.Thread(target=_do_deep_ram_cleanup, daemon=True).start()

    return {
        "success": True,
        "message": "Automatización detenida. Todo ha sido reiniciado y la memoria RAM fue liberada exitosamente."
    }



# --- Device & Ollama Health Checks ---
def check_device_status() -> Dict[str, Any]:
    try:
        controller = AndroidController()
        return {"connected": True, "error": None}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def check_ollama_status() -> Dict[str, Any]:
    try:
        base = config.OLLAMA_BASE_URL.replace("/api/generate", "")
        res = requests.get(f"{base}/api/tags", timeout=2)
        if res.status_code == 200:
            data = res.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            v_norm = config.VISION_MODEL.replace("-", "").replace(":latest", "").lower()
            t_norm = config.TEXT_MODEL.replace("-", "").replace(":latest", "").lower()
            has_vision = any(v_norm in m.replace("-", "").replace(":latest", "").lower() for m in models)
            has_text = any(t_norm in m.replace("-", "").replace(":latest", "").lower() for m in models)
            return {
                "online": True,
                "models": models,
                "has_vision": has_vision,
                "has_text": has_text,
                "vision_model": config.VISION_MODEL,
                "text_model": config.TEXT_MODEL,
            }
        return {"online": False, "error": f"Status code {res.status_code}"}
    except Exception as e:
        return {"online": False, "error": str(e)}


# --- Flask Routes ---
@app.route("/")
def index() -> str:
    """Renders the main dashboard."""
    return render_template("index.html")


@app.route("/api/models", methods=["GET"])
def api_get_models() -> Response:
    """
    Returns all installed local AI models from Ollama with capabilities,
    along with currently active models in config.
    """
    installed = AIAgent.list_installed_models(config.OLLAMA_BASE_URL)
    return jsonify({
        "success": True,
        "models": installed,
        "active_vision_model": config.VISION_MODEL,
        "active_text_model": config.TEXT_MODEL,
    })


@app.route("/api/models/select", methods=["POST"])
def api_select_model() -> Response:
    """
    Updates the active AI model(s) for Vision and/or Text generation.
    """
    data = request.get_json() or {}
    new_vision = data.get("vision_model", "").strip()
    new_text = data.get("text_model", "").strip()
    single_model = data.get("model", "").strip()

    updated = {}
    if single_model:
        models = AIAgent.list_installed_models(config.OLLAMA_BASE_URL)
        match = next((m for m in models if m["name"] == single_model), None)
        if match and match.get("is_vision"):
            config.VISION_MODEL = single_model
            updated["vision_model"] = single_model
        config.TEXT_MODEL = single_model
        updated["text_model"] = single_model

    if new_vision:
        config.VISION_MODEL = new_vision
        updated["vision_model"] = new_vision
    if new_text:
        config.TEXT_MODEL = new_text
        updated["text_model"] = new_text

    logger.info(f"AI Models updated: Vision='{config.VISION_MODEL}', Text='{config.TEXT_MODEL}'")
    manager._broadcast_event("models_updated", {
        "vision_model": config.VISION_MODEL,
        "text_model": config.TEXT_MODEL,
    })

    return jsonify({
        "success": True,
        "message": f"Modelos actualizados: Visión='{config.VISION_MODEL}' | Texto='{config.TEXT_MODEL}'",
        "active_vision_model": config.VISION_MODEL,
        "active_text_model": config.TEXT_MODEL,
        "updated": updated
    })


@app.route("/api/status", methods=["GET"])
def api_status() -> Response:
    """Returns combined status of device, Ollama, and automation manager."""
    dev_status = check_device_status()
    ollama_status = check_ollama_status()
    manager_status = manager.get_status()

    return jsonify({
        "device": dev_status,
        "ollama": ollama_status,
        "automation": manager_status,
        "config": {
            "package_name": config.PACKAGE_NAME,
            "max_iterations": config.MAX_ITERATIONS,
            "vision_model": config.VISION_MODEL,
            "text_model": config.TEXT_MODEL,
            "scroll_wait": config.SCROLL_WAIT_SECONDS,
        }
    })


@app.route("/api/start", methods=["POST"])
def api_start() -> Response:
    """Starts the automation loop in background."""
    data = request.get_json() or {}
    package_name = data.get("package_name", config.PACKAGE_NAME).strip() or config.PACKAGE_NAME
    max_iterations = int(data.get("max_iterations", config.MAX_ITERATIONS))
    scroll_wait = float(data.get("scroll_wait", config.SCROLL_WAIT_SECONDS))

    started = manager.start(package_name, max_iterations, scroll_wait)
    if started:
        return jsonify({"success": True, "message": "Automation started."})
    return jsonify({"success": False, "message": "Automation already running."}), 400


@app.route("/api/stop", methods=["POST"])
def api_stop() -> Response:
    """Requests stopping the background automation, resetting everything and freeing RAM."""
    res = full_system_stop_and_cleanup_ram()
    return jsonify(res)


@app.route("/api/screen", methods=["GET"])
def api_screen() -> Response:
    """Serves the latest screen capture image or realtime frame with no-cache headers."""
    global latest_stream_frame
    with latest_stream_lock:
        frame = latest_stream_frame
    if frame:
        resp = Response(frame, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    screen_file = os.path.abspath(config.DEFAULT_SCREEN_IMAGE)
    if os.path.exists(screen_file):
        resp = send_file(screen_file, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    # Fallback to on-demand capture if stream frame is not cached yet
    try:
        ctrl = AndroidController()
        f = ctrl.get_realtime_frame(quality=60, scale=0.5)
        if f:
            resp = Response(f, mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            return resp
    except Exception:
        pass

    return jsonify({"error": "No screenshot available"}), 404


latest_stream_frame = None
latest_stream_lock = threading.Lock()
stream_active = True

def _stream_capture_worker():
    global latest_stream_frame
    controller = None
    while stream_active:
        try:
            if controller is None:
                try:
                    controller = AndroidController()
                except Exception as e:
                    logger.debug(f"Stream worker waiting for ADB: {e}")
                    time.sleep(2.0)
                    continue

            frame = controller.get_realtime_frame(quality=60, scale=0.5)
            if frame:
                with latest_stream_lock:
                    latest_stream_frame = frame
            time.sleep(0.35)
        except Exception as e:
            controller = None
            time.sleep(1.5)

stream_thread = threading.Thread(target=_stream_capture_worker, daemon=True)
stream_thread.start()


@app.route("/api/device/stream")
def api_device_stream() -> Response:
    """Streams live device screen frames as MJPEG in real-time."""
    def frame_generator():
        while True:
            with latest_stream_lock:
                frame = latest_stream_frame
            if frame:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            time.sleep(0.35)

    return Response(
        frame_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# --- Multi-Account Cycle Endpoints ---
@app.route("/api/cycle/start", methods=["POST"])
def api_cycle_start() -> Response:
    """Starts the dynamic multi-account interaction cycle across all registered accounts."""
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    use_current = bool(data.get("use_current_screen", False))
    if not url and use_current:
        url = "current"

    tone = data.get("tone") or "positive"
    custom_comment = (data.get("custom_comment") or "").strip()
    do_like = bool(data.get("do_like", True))
    do_comment = bool(data.get("do_comment", True))
    repeat_cycles = bool(data.get("repeat_cycles", False))
    v_model = (data.get("vision_model") or "").strip() or config.VISION_MODEL
    t_model = (data.get("text_model") or "").strip() or config.TEXT_MODEL
    narrative = (data.get("narrative") or "a_favor").strip()
    package_name = (data.get("package_name") or "").strip() or config.PACKAGE_NAME
    try:
        max_cycles = int(data.get("max_cycles") or data.get("cycle_count") or 1)
    except (ValueError, TypeError):
        max_cycles = 1
    try:
        accounts_to_use = int(data.get("accounts_to_use") or data.get("max_accounts") or 0)
    except (ValueError, TypeError):
        accounts_to_use = 0

    manual_context = (data.get("manual_context") or "").strip()
    is_contest = bool(data.get("is_contest", False))
    contest_requirement = (data.get("contest_requirement") or "").strip()
    teams_or_topic = (data.get("teams_or_topic") or "").strip()
    media_type = (data.get("media_type") or "").strip().lower()
    if "/reel/" in url.lower() or "/reels/" in url.lower():
        media_type = "reel"
    elif not media_type:
        media_type = "post"

    comment_mode = (data.get("comment_mode") or "auto").strip().lower()
    raw_pasted = data.get("pasted_comments", [])
    if isinstance(raw_pasted, str):
        pasted_comments = [line.strip() for line in raw_pasted.splitlines() if line.strip()]
    elif isinstance(raw_pasted, list):
        pasted_comments = [str(c).strip() for c in raw_pasted if str(c).strip()]
    else:
        pasted_comments = []

    if not url:
        return jsonify({"success": False, "error": "Ingresa el enlace de la publicación o activa 'Usar pantalla actual'."}), 400

    if do_comment and comment_mode == "preset_list" and not pasted_comments:
        return jsonify({
            "success": False,
            "error": "Modo 'Lista Predefinida' seleccionado, pero la lista de comentarios está vacía. Pega tus comentarios en el banco o selecciona 'Modo Autónomo IA'."
        }), 400

    try:
        cooldown_seconds = max(0, int(data.get("cooldown_seconds") or 0))
    except (ValueError, TypeError):
        cooldown_seconds = 0
    clear_ram_between_cycles = bool(data.get("clear_ram_between_cycles", True))
    feed_browsing = bool(data.get("feed_browsing", False))
    auto_restart_on_failure = bool(data.get("auto_restart_on_failure", False))

    if comment_mode == "preset_list":
        started = cycle_manager.start_preset(
            url=url,
            pasted_comments=pasted_comments,
            accounts_to_use=accounts_to_use,
            do_like=do_like,
            do_comment=do_comment,
            repeat_cycles=repeat_cycles,
            max_cycles=max_cycles,
            package_name=package_name,
            media_type=media_type,
            cooldown_seconds=cooldown_seconds,
            clear_ram_between_cycles=clear_ram_between_cycles,
            feed_browsing=feed_browsing,
            auto_restart_on_failure=auto_restart_on_failure,
        )
    else:
        started = cycle_manager.start(
            url=url,
            tone=tone,
            custom_comment=custom_comment,
            do_like=do_like,
            do_comment=do_comment,
            repeat_cycles=repeat_cycles,
            vision_model=v_model,
            text_model=t_model,
            max_cycles=max_cycles,
            accounts_to_use=accounts_to_use,
            narrative=narrative,
            manual_context=manual_context,
            is_contest=is_contest,
            contest_requirement=contest_requirement,
            teams_or_topic=teams_or_topic,
            package_name=package_name,
            media_type=media_type,
            comment_mode=comment_mode,
            pasted_comments=pasted_comments,
            cooldown_seconds=cooldown_seconds,
            clear_ram_between_cycles=clear_ram_between_cycles,
            feed_browsing=feed_browsing,
            auto_restart_on_failure=auto_restart_on_failure,
        )

    if not started:
        return jsonify({"success": False, "error": "El ciclo de automatización ya está en ejecución."}), 409

    acc_info = f"{accounts_to_use} cuenta(s)" if accounts_to_use > 0 else "todas las cuentas"
    mode_name = "Lista Predefinida (Sin IA)" if comment_mode == "preset_list" else f"IA Ollama ({narrative})"
    return jsonify({"success": True, "message": f"Automatización iniciada [{mode_name}] ({max_cycles} ciclo(s), {acc_info}, tipo: '{media_type}')."})


@app.route("/api/cycle/start-preset", methods=["POST"])
def api_cycle_start_preset() -> Response:
    """Inicia la Automatización Manual con Lista Predefinida (100% sin IA, ejecuta manual_automation.py)."""
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    use_current = bool(data.get("use_current_screen", False))
    if not url and use_current:
        url = "current"
    elif not url:
        return jsonify({"success": False, "error": "Ingresa el enlace de la publicación o activa 'Usar pantalla actual'."}), 400

    raw_pasted = data.get("pasted_comments", [])
    if isinstance(raw_pasted, str):
        pasted_comments = [line.strip() for line in raw_pasted.splitlines() if line.strip()]
    elif isinstance(raw_pasted, list):
        pasted_comments = [str(c).strip() for c in raw_pasted if str(c).strip()]
    else:
        pasted_comments = []

    do_like = bool(data.get("do_like", True))
    do_comment = bool(data.get("do_comment", True))

    if do_comment and not pasted_comments:
        return jsonify({
            "success": False,
            "error": "La lista de comentarios está vacía. Pega tus comentarios predefinidos antes de iniciar."
        }), 400

    try:
        max_cycles = int(data.get("max_cycles") or data.get("cycle_count") or 1)
    except (ValueError, TypeError):
        max_cycles = 1
    try:
        accounts_to_use = int(data.get("accounts_to_use") or data.get("max_accounts") or 0)
    except (ValueError, TypeError):
        accounts_to_use = 0

    package_name = (data.get("package_name") or "").strip() or config.PACKAGE_NAME
    media_type = (data.get("media_type") or "").strip().lower()
    if "/reel/" in url.lower() or "/reels/" in url.lower():
        media_type = "reel"
    elif not media_type:
        media_type = "post"

    repeat_cycles = bool(data.get("repeat_cycles", False))
    clear_ram_between_cycles = bool(data.get("clear_ram_between_cycles", True))
    feed_browsing = bool(data.get("feed_browsing", False))
    auto_restart_on_failure = bool(data.get("auto_restart_on_failure", False))
    try:
        cooldown_seconds = max(0, int(data.get("cooldown_seconds") or 0))
    except (ValueError, TypeError):
        cooldown_seconds = 0

    started = cycle_manager.start_preset(
        url=url,
        pasted_comments=pasted_comments,
        accounts_to_use=accounts_to_use,
        do_like=do_like,
        do_comment=do_comment,
        repeat_cycles=repeat_cycles,
        max_cycles=max_cycles,
        package_name=package_name,
        media_type=media_type,
        cooldown_seconds=cooldown_seconds,
        clear_ram_between_cycles=clear_ram_between_cycles,
        feed_browsing=feed_browsing,
        auto_restart_on_failure=auto_restart_on_failure,
    )

    if not started:
        return jsonify({"success": False, "error": "Ya hay un ciclo en ejecución."}), 409

    return jsonify({"success": True, "message": f"Automatización con Lista Predefinida iniciada ({len(pasted_comments)} comentarios cargados)."})


@app.route("/api/cycle/start-ia", methods=["POST"])
def api_cycle_start_ia() -> Response:
    """Inicia la Automatización con IA (Ollama, visión y generación narrativa)."""
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    use_current = bool(data.get("use_current_screen", False))
    if not url and use_current:
        url = "current"
    elif not url:
        return jsonify({"success": False, "error": "Ingresa el enlace de la publicación o activa 'Usar pantalla actual'."}), 400

    tone = data.get("tone") or "positive"
    custom_comment = (data.get("custom_comment") or "").strip()
    do_like = bool(data.get("do_like", True))
    do_comment = bool(data.get("do_comment", True))
    repeat_cycles = bool(data.get("repeat_cycles", False))
    v_model = (data.get("vision_model") or "").strip() or config.VISION_MODEL
    t_model = (data.get("text_model") or "").strip() or config.TEXT_MODEL
    narrative = (data.get("narrative") or "a_favor").strip()
    package_name = (data.get("package_name") or "").strip() or config.PACKAGE_NAME

    try:
        max_cycles = int(data.get("max_cycles") or data.get("cycle_count") or 1)
    except (ValueError, TypeError):
        max_cycles = 1
    try:
        accounts_to_use = int(data.get("accounts_to_use") or data.get("max_accounts") or 0)
    except (ValueError, TypeError):
        accounts_to_use = 0

    manual_context = (data.get("manual_context") or "").strip()
    is_contest = bool(data.get("is_contest", False))
    contest_requirement = (data.get("contest_requirement") or "").strip()
    teams_or_topic = (data.get("teams_or_topic") or "").strip()
    media_type = (data.get("media_type") or "").strip().lower()
    if "/reel/" in url.lower() or "/reels/" in url.lower():
        media_type = "reel"
    elif not media_type:
        media_type = "post"

    clear_ram_between_cycles = bool(data.get("clear_ram_between_cycles", True))
    feed_browsing = bool(data.get("feed_browsing", False))
    auto_restart_on_failure = bool(data.get("auto_restart_on_failure", False))
    try:
        cooldown_seconds = max(0, int(data.get("cooldown_seconds") or 0))
    except (ValueError, TypeError):
        cooldown_seconds = 0

    started = cycle_manager.start(
        url=url,
        tone=tone,
        custom_comment=custom_comment,
        do_like=do_like,
        do_comment=do_comment,
        repeat_cycles=repeat_cycles,
        vision_model=v_model,
        text_model=t_model,
        max_cycles=max_cycles,
        accounts_to_use=accounts_to_use,
        narrative=narrative,
        manual_context=manual_context,
        is_contest=is_contest,
        contest_requirement=contest_requirement,
        teams_or_topic=teams_or_topic,
        package_name=package_name,
        media_type=media_type,
        comment_mode="auto",
        pasted_comments=[],
        cooldown_seconds=cooldown_seconds,
        clear_ram_between_cycles=clear_ram_between_cycles,
        feed_browsing=feed_browsing,
        auto_restart_on_failure=auto_restart_on_failure,
    )

    if not started:
        return jsonify({"success": False, "error": "Ya hay un ciclo en ejecución."}), 409

    return jsonify({"success": True, "message": f"Automatización con IA (Ollama) iniciada con modelo {t_model}."})


@app.route("/api/manual-automation/start", methods=["POST"])
def api_manual_automation_start() -> Response:
    """Ruta delegada directamente a api_cycle_start_preset."""
    return api_cycle_start_preset()


@app.route("/api/cycle/stop", methods=["POST"])
def api_cycle_stop() -> Response:
    """Requests stopping the multi-account cycle, resetting everything and freeing RAM."""
    res = full_system_stop_and_cleanup_ram()
    return jsonify(res)


@app.route("/api/cycle/status", methods=["GET"])
def api_cycle_status() -> Response:
    """Returns the current status of the multi-account cycle."""
    return jsonify({"success": True, "status": cycle_manager.get_status()})


@app.route("/api/device/clear-recents", methods=["POST", "GET"])
def api_device_clear_recents() -> Response:
    """Cierra las aplicaciones recientes y libera memoria RAM en el dispositivo."""
    try:
        ctrl = get_controller()
        ok = ctrl.clear_recent_apps()
        return jsonify({
            "success": ok,
            "message": "Aplicaciones recientes cerradas y memoria RAM liberada con éxito."
        })
    except Exception as e:
        logger.error(f"Error al liberar memoria/recientes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500







@app.route("/api/post/screen-image", methods=["GET"])
def api_post_screen_image() -> Response:
    """
    Serves the most recent captured screenshot image for frontend preview.
    """
    for fname in ["current_post_screen.png", config.DEFAULT_SCREEN_IMAGE, "screen.png"]:
        fpath = os.path.abspath(fname)
        if os.path.exists(fpath):
            resp = send_file(fpath, mimetype="image/png")
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            return resp
    return jsonify({"error": "No hay imagen disponible aún"}), 404


@app.route("/api/post/capture-and-read-screen", methods=["POST"])
@app.route("/api/post/extract-screen-context", methods=["POST"])
def api_post_capture_and_read_screen() -> Response:
    """
    Takes a screenshot of the phone screen via ADB (or receives an uploaded image),
    reads the post caption, dynamics, author, and summary directly from the image
    using local Vision AI (Qwen2.5-VL / Ollama), and returns the transcription + image preview.
    """
    screen_target = "current_post_screen.png"
    ui_text = ""
    is_upload = False

    # 1. Check if image file was uploaded directly via multipart form
    uploaded_file = request.files.get("image") or request.files.get("file")
    if uploaded_file and uploaded_file.filename:
        try:
            uploaded_file.save(screen_target)
            is_upload = True
            logger.info(f"User uploaded screenshot saved to '{screen_target}'.")
        except Exception as e:
            return jsonify({"success": False, "error": f"Error al guardar la imagen subida: {e}"}), 400
    else:
        # Check if base64 image was sent via JSON
        req_json = request.get_json(silent=True) or {}
        b64_img = req_json.get("image_base64", "")
        if b64_img:
            try:
                if "," in b64_img:
                    b64_img = b64_img.split(",", 1)[1]
                with open(screen_target, "wb") as f:
                    f.write(base64.b64decode(b64_img))
                is_upload = True
                logger.info("Decoded base64 image saved to current_post_screen.png.")
            except Exception as e:
                return jsonify({"success": False, "error": f"Error al procesar base64: {e}"}), 400
        else:
            # 2. Capture live from connected Android device via ADB
            try:
                ctrl = AndroidController()
                ctrl.capture_screen(screen_target)
                logger.info(f"ADB screen captured successfully to '{screen_target}'.")
            except Exception as e:
                logger.warning(f"Failed to capture screen via ADB: {e}")
                # Check if fallback screenshot exists
                if not os.path.exists(screen_target) and os.path.exists("screen.png"):
                    shutil.copy("screen.png", screen_target)
                elif not os.path.exists(screen_target):
                    return jsonify({
                        "success": False,
                        "error": (
                            f"No se pudo capturar la pantalla del dispositivo Android: {e}. "
                            "Verifica que el teléfono esté conectado por USB con Depuración USB activa "
                            "o utiliza el botón 'Subir imagen' para cargar una captura manualmente."
                        )
                    }), 400

            # 3. Non-fatal attempt to fetch auxiliary UI text
            try:
                ctrl = AndroidController()
                ui_text = ctrl.get_ui_text() or ""
            except Exception:
                ui_text = ""

    # 4. Multimodal Vision reading from the screenshot
    try:
        agent = AIAgent(vision_model=config.VISION_MODEL, text_model=config.TEXT_MODEL)
        read_result = agent.extract_post_from_screenshot(screen_target, ui_text=ui_text)

        if not isinstance(read_result, dict):
            read_result = {}

        extracted_text = (read_result.get("caption") or "").strip()
        author = (read_result.get("author") or "").strip()
        topic = read_result.get("topic") or "General"
        tone = read_result.get("tone") or "Informativo"
        is_contest = bool(read_result.get("is_contest", False))
        contest_req = (read_result.get("contest_requirement") or "").strip()
        teams_topic = (read_result.get("teams_or_topic") or "").strip()
        summary = (read_result.get("summary") or "").strip()
        ai_transcribed = bool(read_result.get("ai_transcribed", False))
        error_hint = read_result.get("error_hint") or ""

        return jsonify({
            "success": True,
            "image_url": f"/api/post/screen-image?t={int(time.time() * 1000)}",
            "extracted_text": extracted_text,
            "author": author,
            "topic": topic,
            "tone": tone,
            "is_contest": is_contest,
            "contest_requirement": contest_req,
            "teams_or_topic": teams_topic,
            "summary": summary,
            "ai_transcribed": ai_transcribed,
            "error_hint": error_hint,
            "is_upload": is_upload
        })
    except Exception as e:
        logger.error(f"Error reading screenshot: {e}")
        return jsonify({
            "success": False,
            "error": f"Error al procesar la captura de pantalla: {e}",
            "image_url": f"/api/post/screen-image?t={int(time.time() * 1000)}" if os.path.exists(screen_target) else ""
        }), 500


@app.route("/api/post/analyze-and-preview", methods=["POST"])
def api_post_analyze_and_preview() -> Response:
    """
    Analyzes the provided post context/caption, returns what the post is about
    (summary, key topic, tone, detected rules/dynamics), and provides rich
    comment examples for each narrative stance and for the configured accounts.
    """
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    narrative = (data.get("narrative") or "a_favor").strip()
    accounts_count = int(data.get("accounts_to_use") or 1)
    t_model = (data.get("text_model") or "").strip() or config.TEXT_MODEL
    is_contest = bool(data.get("is_contest", False))
    contest_req = (data.get("contest_requirement") or "").strip()
    teams_topic = (data.get("teams_or_topic") or "").strip()

    if not text:
        return jsonify({
            "success": False,
            "error": "Debes ingresar o pegar la descripción o contexto de la publicación para analizarla."
        }), 400

    contest_info = None
    if is_contest or contest_req:
        contest_info = {
            "is_contest": True,
            "contest_type": "pronostico" if any(w in (contest_req + text).lower() for w in ["pronostico", "pronóstico", "marcador", "clásico", "clasico"]) else "sorteo",
            "contest_requirement": contest_req or "Comentar pronóstico o participar en la dinámica",
            "teams_or_topic": teams_topic or "Dinámica de publicación",
            "sample_comments": ["2-1 Tigres", "1-0 Rayados", "Gana la U 2-0", "Empate 1-1"]
        }

    try:
        agent = AIAgent(text_model=t_model)

        # 1. Analyze what the post is about
        analysis = agent.analyze_post_context(text)

        # Merge contest info if user didn't specify contest but analysis detected it
        if not contest_info and analysis.get("is_contest"):
            contest_info = {
                "is_contest": True,
                "contest_type": "pronostico" if any(w in (analysis.get("contest_requirement", "") + text).lower() for w in ["pronostico", "pronóstico", "marcador", "clásico", "clasico"]) else "sorteo",
                "contest_requirement": analysis.get("contest_requirement") or "Participar en la dinámica",
                "teams_or_topic": analysis.get("teams_or_topic") or "Publicación interactiva",
                "sample_comments": ["2-1 Tigres", "1-0 Rayados", "Gana la U 2-0", "Empate 1-1"]
            }

        # 2. Generate 4 comment examples across all narrative stances
        narrative_samples = agent.generate_comment_samples_by_narrative(
            caption=text,
            contest_info=contest_info
        )

        # 3. Generate preview for the N configured accounts in the cycle
        account_samples = agent.generate_account_cycle_preview(
            caption=text,
            accounts_count=accounts_count,
            narrative=narrative,
            contest_info=contest_info
        )

        # 4. Generate preview for the 5 Human Archetypes with persistent memory
        human_samples = agent.preview_human_archetypes(
            caption=text,
            image_path="current_post_screen.png",
            contest_info=contest_info,
            narrative=narrative
        )

        return jsonify({
            "success": True,
            "analysis": analysis,
            "narrative_samples": narrative_samples,
            "account_samples": account_samples,
            "human_samples": human_samples,
            "contest_info": contest_info,
            "narrative": narrative,
            "accounts_count": accounts_count
        })
    except Exception as e:
        logger.error(f"Error in api_post_analyze_and_preview: {e}")
        return jsonify({"success": False, "error": str(e)}), 500







@app.route("/api/screenshots/clear", methods=["POST"])
def api_screenshots_clear() -> Response:
    """Deletes temporary cached screenshot images without touching reference icon templates."""
    try:
        ctrl = AndroidController()
        result = ctrl.clear_cached_screenshots()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error clearing screenshots: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/manual/capture", methods=["POST"])
def api_manual_capture() -> Response:
    """Takes a fresh screen capture and notifies clients."""
    try:
        controller = AndroidController()
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({"success": True, "message": "Screen captured."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/manual/scroll", methods=["POST"])
def api_manual_scroll() -> Response:
    """Triggers manual swipe up or down."""
    data = request.get_json() or {}
    direction = data.get("direction", "down")
    try:
        controller = AndroidController()
        if direction == "down":
            controller.swipe_down()
        else:
            # Swipe up (drag from top to bottom)
            import subprocess
            subprocess.run([controller.adb_bin, "shell", "input", "swipe", "500", "500", "500", "1500", "400"], check=False)

        time.sleep(1.0)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({"success": True, "message": f"Scrolled {direction}."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/manual/open-app", methods=["POST"])
def api_manual_open_app() -> Response:
    """Launches the selected application package."""
    data = request.get_json() or {}
    package_name = data.get("package_name", config.PACKAGE_NAME).strip()
    try:
        controller = AndroidController()
        controller.open_app(package_name)
        time.sleep(2.0)
        controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({"success": True, "message": f"Launched {package_name}."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/manual/open-url", methods=["POST"])
def api_manual_open_url() -> Response:
    """Dispatches a URL or deep link to the Android device."""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    package = data.get("package", "").strip() or None
    if not url:
        return jsonify({"success": False, "error": "URL cannot be empty."}), 400

    try:
        controller = AndroidController()
        success = controller.open_url(url, package=package)
        if success:
            time.sleep(2.5)
            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})
            return jsonify({"success": True, "message": f"Opened {url} on device."})
        return jsonify({"success": False, "error": "Failed to dispatch VIEW intent."}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/open-step1", methods=["POST"])
def api_report_open_step1() -> Response:
    """
    Paso 1 de la automatización de reporte de perfil:
    1. Abre el enlace del perfil (o usa la pantalla actual si ya está abierto).
    2. Reposo previo (sleep_before) para asegurar carga completa del perfil.
    3. Toca los tres puntos (···) en la barra superior derecha de opciones (~91.5% X, 6.0% Y).
    4. Reposo posterior (sleep_after) para dar tiempo al despliegue del menú.
    5. Verifica la presencia del menú en pantalla.
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    use_current = bool(data.get("use_current_screen", False))
    package = (data.get("package") or "com.facebook.lite").strip()
    sleep_before = float(data.get("sleep_before", 2.5))
    sleep_after = float(data.get("sleep_after", 2.0))

    if not url and not use_current:
        return jsonify({"success": False, "error": "Ingresa el enlace del perfil o activa 'Usar perfil ya abierto'."}), 400

    try:
        ctrl = AndroidController()
        if url:
            logger.info(f"[REPORTE PASO 1] Abriendo perfil en {package}: {url}...")
            ctrl.open_url(url, package=package)
            time.sleep(3.5)
        else:
            logger.info("[REPORTE PASO 1] Usando pantalla de perfil actual...")

        res = ctrl.tap_profile_three_dots_menu(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        is_ok = bool(res.get("success"))
        return jsonify({
            "success": is_ok,
            "message": res.get("message", "Paso 1 completado: Menú de opciones (···) pulsado y verificado."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 1 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/step2-denunciar", methods=["POST"])
def api_report_step2_denunciar() -> Response:
    """
    Paso 2 de la automatización de reporte de perfil:
    Toca la opción 'Denunciar perfil' (subrayada con línea roja en el menú desplegable).
    Soporta detección UI y coordenadas cinéticas proporcionales anti-restricción (X ≈ 42%, Y ≈ 55.6%).
    """
    data = request.get_json() or {}
    sleep_before = float(data.get("sleep_before", 1.0))
    sleep_after = float(data.get("sleep_after", 2.5))

    try:
        ctrl = AndroidController()
        res = ctrl.tap_denunciar_perfil(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        is_ok = bool(res.get("success"))
        return jsonify({
            "success": is_ok,
            "message": res.get("message", "'Denunciar perfil' pulsado exitosamente."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 2 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/run-steps", methods=["POST"])
def api_report_run_steps() -> Response:
    """
    Ejecuta en secuencia el Paso 1 (Tocar ··· con sleeps y verificación) y el
    Paso 2 (Tocar 'Denunciar perfil' con línea roja), transmitiendo actualización de pantalla en tiempo real.
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    use_current = bool(data.get("use_current_screen", False))
    package = (data.get("package") or "com.facebook.lite").strip()
    sleep_before_dots = float(data.get("sleep_before_dots", 2.5))
    sleep_after_dots = float(data.get("sleep_after_dots", 2.0))
    sleep_after_denunciar = float(data.get("sleep_after_denunciar", 2.5))

    try:
        ctrl = AndroidController()
        if url:
            logger.info(f"[REPORTE] Abriendo perfil en {package}: {url}...")
            ctrl.open_url(url, package=package)
            time.sleep(3.5)

        # Paso 1: Tocar tres puntos con reposos y verificación
        step1_res = ctrl.tap_profile_three_dots_menu(
            sleep_before=sleep_before_dots,
            sleep_after=sleep_after_dots
        )

        # Paso 2: Tocar 'Denunciar perfil'
        step2_res = ctrl.tap_denunciar_perfil(
            sleep_before=0.8,
            sleep_after=sleep_after_denunciar
        )

        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": True,
            "message": "Pasos 1 y 2 completados: Menú desplegado y 'Denunciar perfil' pulsado.",
            "step1": step1_res,
            "step2": step2_res
        })
    except Exception as e:
        logger.error(f"Error ejecutando pasos de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/step3-odio", methods=["POST"])
def api_report_step3_odio() -> Response:
    """
    Paso 3 del Reporte: Toca 'Contenido violento, perturbador o que incita al odio'.
    """
    data = request.get_json() or {}
    sleep_before = float(data.get("sleep_before", 0.5))
    sleep_after = float(data.get("sleep_after", 2.0))
    try:
        ctrl = AndroidController()
        res = ctrl.tap_report_step3_odio(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": True,
            "message": res.get("message", "Paso 3 completado."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 3 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/step4-promueve-odio", methods=["POST"])
def api_report_step4_promueve_odio() -> Response:
    """
    Paso 4 del Reporte: Toca 'Promueve el odio'.
    """
    data = request.get_json() or {}
    sleep_before = float(data.get("sleep_before", 0.5))
    sleep_after = float(data.get("sleep_after", 2.0))
    try:
        ctrl = AndroidController()
        res = ctrl.tap_report_step4_promueve_odio(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": True,
            "message": res.get("message", "Paso 4 completado."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 4 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/step5-lenguaje-odio", methods=["POST"])
def api_report_step5_lenguaje_odio() -> Response:
    """
    Paso 5 del Reporte: Toca 'Publica lenguaje que incita al odio'.
    """
    data = request.get_json() or {}
    sleep_before = float(data.get("sleep_before", 0.5))
    sleep_after = float(data.get("sleep_after", 2.5))
    try:
        ctrl = AndroidController()
        res = ctrl.tap_report_step5_lenguaje_odio(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": True,
            "message": res.get("message", "Paso 5 completado."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 5 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/step6-enviar", methods=["POST"])
def api_report_step6_enviar() -> Response:
    """
    Paso 6 del Reporte: Toca el botón azul 'Enviar'.
    """
    data = request.get_json() or {}
    sleep_before = float(data.get("sleep_before", 0.5))
    sleep_after = float(data.get("sleep_after", 2.5))
    try:
        ctrl = AndroidController()
        res = ctrl.tap_report_step6_enviar(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": True,
            "message": res.get("message", "Paso 6 completado."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 6 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/step7-siguiente", methods=["POST"])
def api_report_step7_siguiente() -> Response:
    """
    Paso 7 del Reporte: Toca el botón azul 'Siguiente'.
    """
    data = request.get_json() or {}
    sleep_before = float(data.get("sleep_before", 0.5))
    sleep_after = float(data.get("sleep_after", 2.0))
    try:
        ctrl = AndroidController()
        res = ctrl.tap_report_step7_siguiente(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": True,
            "message": res.get("message", "Paso 7 completado."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 7 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/step8-listo", methods=["POST"])
def api_report_step8_listo() -> Response:
    """
    Paso 8 del Reporte: Toca el botón azul 'Listo'.
    """
    data = request.get_json() or {}
    sleep_before = float(data.get("sleep_before", 0.5))
    sleep_after = float(data.get("sleep_after", 2.0))
    try:
        ctrl = AndroidController()
        res = ctrl.tap_report_step8_listo(sleep_before=sleep_before, sleep_after=sleep_after)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": True,
            "message": res.get("message", "Paso 8 completado."),
            "details": res
        })
    except Exception as e:
        logger.error(f"Error en Paso 8 de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/run-full-flow", methods=["POST"])
def api_report_run_full_flow() -> Response:
    """
    Ejecuta el flujo completo de reporte de perfil (Pasos 1 al 5) con los reposos (sleeps)
    personalizables seleccionados por el usuario.
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    use_current = bool(data.get("use_current_screen", True))
    package = (data.get("package") or "com.facebook.lite").strip()
    sleeps = data.get("sleeps") or {}

    cycle_manager.stop_requested = False
    manager.stop_requested = False

    try:
        ctrl = AndroidController()
        res = ctrl.run_full_profile_report(
            url=url,
            use_current_screen=use_current,
            package=package,
            sleeps=sleeps,
            stop_checker=lambda: cycle_manager.stop_requested or manager.stop_requested
        )
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify(res)
    except Exception as e:
        logger.error(f"Error en flujo completo de reporte: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/facebook/scan-accounts", methods=["POST"])
def api_report_facebook_scan_accounts() -> Response:
    """
    Escanea dinámicamente las cuentas de Facebook Lite registradas en el dispositivo
    deslizando hasta encontrar el marcador final 'Iniciar sesión en otra cuenta'.
    """
    try:
        ctrl = AndroidController()
        res = ctrl.scan_facebook_lite_accounts()
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify(res)
    except Exception as e:
        logger.error(f"Error al escanear cuentas de Facebook: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/facebook/switch-account", methods=["POST"])
def api_report_facebook_switch_account() -> Response:
    """
    Alterna inmediatamente a una cuenta específica de Facebook Lite seleccionada por el usuario.
    """
    data = request.get_json() or {}
    account_name = (data.get("account_name") or "").strip()
    if not account_name:
        return jsonify({"success": False, "error": "Debe especificar el nombre de la cuenta."}), 400

    try:
        ctrl = AndroidController()
        ok = ctrl.switch_to_facebook_account_by_name(account_name)
        if ok:
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})
            return jsonify({
                "success": True,
                "message": f"Sesión alternada exitosamente a '{account_name}'.",
                "account_name": account_name
            })
        else:
            return jsonify({"success": False, "error": f"No se pudo encontrar o alternar a la cuenta '{account_name}'."}), 400
    except Exception as e:
        logger.error(f"Error al alternar cuenta: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/facebook/start-cycle", methods=["POST"])
def api_report_facebook_start_cycle() -> Response:
    """
    Inicia el ciclo continuo multi-cuenta de reporte en Facebook Lite.
    """
    data = request.get_json() or {}
    target_url = (data.get("url") or "").strip()
    if not target_url:
        return jsonify({"success": False, "error": "Debe proporcionar el enlace del perfil a reportar."}), 400

    accounts_limit = int(data.get("accounts_limit") or 0)
    feed_browse_seconds = float(data.get("feed_browse_seconds") or 60.0)
    feed_variance = float(data.get("feed_variance") or 10.0)
    refresh_interval = int(data.get("refresh_interval") or 3)
    sleeps = data.get("sleeps") or {}

    res = fb_cycle_manager.start_cycle(
        target_url=target_url,
        accounts_limit=accounts_limit,
        feed_browse_seconds=feed_browse_seconds,
        feed_variance=feed_variance,
        sleeps=sleeps,
        refresh_interval=refresh_interval
    )
    return jsonify(res)


@app.route("/api/report/facebook/stop-cycle", methods=["POST"])
def api_report_facebook_stop_cycle() -> Response:
    """
    Detiene el ciclo multi-cuenta activo de Facebook Lite, reinicia todo y libera memoria RAM.
    """
    res = full_system_stop_and_cleanup_ram()
    return jsonify(res)


@app.route("/api/report/facebook/cycle-status", methods=["GET"])
def api_report_facebook_cycle_status() -> Response:
    """
    Retorna el estado en tiempo real del ciclo multi-cuenta.
    """
    return jsonify(fb_cycle_manager.get_status())


@app.route("/api/report/facebook/accounts", methods=["GET"])
def api_report_facebook_accounts() -> Response:
    """
    Retorna las cuentas registradas en memoria para el dispositivo conectado.
    """
    try:
        serial = ""
        model = ""
        try:
            ctrl = AndroidController()
            serial = ctrl.get_device_serial()
            model = ctrl.get_device_model()
        except Exception:
            pass

        if not serial:
            devices = accounts_memory._data.get("devices", {})
            for s, d in devices.items():
                if d.get("facebook_accounts"):
                    serial = s
                    break
            if not serial and devices:
                serial = list(devices.keys())[0]

        summary = accounts_memory.get_device_summary(serial)
        raw_accs = summary.get("facebook_accounts", [])
        normalized_accs = []
        for idx, acc in enumerate(raw_accs):
            if isinstance(acc, dict):
                account_name = acc.get("username") or acc.get("label") or acc.get("name") or f"Cuenta {idx + 1}"
                normalized_accs.append({
                    "name": account_name,
                    "label": acc.get("label") or account_name,
                    "username": account_name,
                    "index": acc.get("index", idx),
                    "last_used": acc.get("last_used"),
                    "used_on_posts": acc.get("used_on_posts", []),
                    "total_reports_made": acc.get("total_reports_made", 0)
                })
            else:
                account_name = str(acc)
                normalized_accs.append({
                    "name": account_name,
                    "label": account_name,
                    "username": account_name,
                    "index": idx
                })
        total = summary.get("total_facebook_accounts", len(normalized_accs))
        return jsonify({
            "success": True,
            "serial": serial,
            "model": model or summary.get("model", ""),
            "total_accounts": total,
            "count": total,
            "accounts": normalized_accs
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/facebook/requirement-status", methods=["GET"])
def api_facebook_requirement_status() -> Response:
    """Retorna el estado de cumplimiento del requerimiento de revisión de cuentas en Facebook Lite."""
    with fb_accounts_requirement["lock"]:
        return jsonify({
            "success": True,
            "reviewed": fb_accounts_requirement["reviewed"],
            "scanning": fb_accounts_requirement["scanning"],
            "total_accounts": fb_accounts_requirement["total_accounts"],
            "accounts": list(fb_accounts_requirement["accounts"]),
            "message": fb_accounts_requirement["message"],
            "error": fb_accounts_requirement["error"]
        })


@app.route("/api/facebook/scan-requirement", methods=["POST"])
def api_facebook_scan_requirement() -> Response:
    """Dispara manualmente la revisión obligatoria de cuentas en Facebook Lite."""
    res = execute_facebook_accounts_review(force=True)
    return jsonify(res)


@app.route("/api/report/facebook/test-transition", methods=["POST"])
def api_report_facebook_test_transition() -> Response:
    """
    Prueba de forma aislada la secuencia de transición:
    Regresar al Feed -> Navegar feed (15s o configurable) -> Abrir selector de cuentas.
    """
    data = request.get_json() or {}
    feed_seconds = float(data.get("feed_seconds", 15.0))
    try:
        ctrl = AndroidController()
        ctrl.return_to_facebook_feed()
        ctrl.browse_facebook_feed(duration_seconds=feed_seconds, variance=3.0)
        ctrl.open_facebook_account_switcher()
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({"success": True, "message": "Secuencia de transición completada con éxito."})
    except Exception as e:
        logger.error(f"Error en prueba de transición: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/report/facebook/test-return-feed", methods=["POST"])
def api_report_facebook_test_return_feed() -> Response:
    """
    Prueba de forma aislada la secuencia de retorno al Home Feed:
    Back + sleep variable + Back + sleep variable hasta detectar el Home Feed de Facebook.
    """
    try:
        ctrl = AndroidController()
        res = ctrl.return_to_facebook_feed(force_first_back=True)
        manager._broadcast_event("screen_updated", {"timestamp": time.time()})
        return jsonify({
            "success": bool(res),
            "message": "Retorno al Home Feed completado con éxito." if res else "No se pudo confirmar el Home Feed."
        })
    except Exception as e:
        logger.error(f"Error en prueba de retorno al feed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/api/post/auto-engage", methods=["POST"])
def api_post_auto_engage() -> Response:
    """
    Automates targeted interaction for a specific Instagram post:
    1. Opens post link in Instagram Lite
    2. Analyzes post visually with Qwen2.5-VL
    3. Dispatches Like
    4. Generates and submits contextual comment with Llama3
    """
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "URL cannot be empty."}), 400

    do_like = bool(data.get("do_like", True))
    do_comment = bool(data.get("do_comment", True))
    tone = data.get("tone", config.DEFAULT_COMMENT_TONE)
    custom_comment = data.get("custom_comment", "").strip()
    package = data.get("package_name", config.PACKAGE_NAME).strip() or config.PACKAGE_NAME
    v_model = data.get("vision_model", "").strip() or config.VISION_MODEL
    t_model = data.get("text_model", "").strip() or config.TEXT_MODEL

    def _execute_post_flow():
        logger.info(f"--- Starting Auto-Engage Flow for: {url} (Vision: {v_model}, Text: {t_model}) ---")
        manager._broadcast_event("post_engage_step", {"step": 1, "message": "Abriendo publicación en el teléfono..."})

        try:
            controller = AndroidController()
            ai_agent = AIAgent(
                base_url=config.OLLAMA_BASE_URL,
                vision_model=v_model,
                text_model=t_model,
            )

            # 1. Open post link in Instagram Lite
            controller.open_post_link(url, package=package)
            screen_file = controller.wait_for_instagram_content_load(min_wait=7.5, max_wait=20.0, check_interval=1.5, ai_agent=ai_agent)
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            # 2. Like action (Click to heart first)
            button_coords = controller.detect_action_buttons(screen_path=screen_file, ai_agent=ai_agent)
            detected_media = button_coords.get("media_type", "post")
            allow_expand = button_coords.get("allow_caption_expand", (detected_media != "reel"))

            if do_like:
                manager._broadcast_event("post_engage_step", {"step": 1, "message": "Buscando icono de corazón y dando Like..."})
                width, height = controller.get_screen_size()
                fallback_like = (int(width * 0.938), int(height * 0.404)) if detected_media == "reel" else (int(width * 0.071), int(height * 0.797))
                like_pt = button_coords.get("like", fallback_like)
                logger.info(f"Pulsando icono de corazón en {like_pt}...")
                controller.tap_coordinates(*like_pt)
                time.sleep(config.LIKE_DELAY_SECONDS)
                controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            # 3. Swipe Up (Gentle scroll to reveal full caption and '... más') solo si no es Reel
            if allow_expand:
                manager._broadcast_event("post_engage_step", {"step": 2, "message": "Realizando Swipe Up para alinear descripción y botón '... más'..."})
                logger.info("Realizando Swipe Up (scroll suave de 250px) para revelar descripción...")
                controller.gentle_scroll_down(250)
                time.sleep(1.0)

                # 4. Search and tap '... más' to expand the full post description
                manager._broadcast_event("post_engage_step", {"step": 3, "message": "Buscando botón '... más' y expandiendo descripción..."})
                controller.expand_caption_more(ai_agent=ai_agent)
                time.sleep(1.5)
            else:
                logger.info("Reel vertical rail detectado: Omitiendo swipe up para no cambiar de publicación.")

            screen_file = controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            ui_text = controller.get_ui_text()
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            # 5. Language Model & Vision AI read post and generate comment
            manager._broadcast_event("post_engage_step", {"step": 4, "message": f"Leyendo publicación con {ai_agent.vision_model} y creando comentario..."})
            post_analysis = ai_agent.analyze_single_post(screen_file, ui_text)
            manager._broadcast_event("post_analyzed", post_analysis)

            if custom_comment:
                comment_to_post = custom_comment
            else:
                caption_text = post_analysis.get("caption") or post_analysis.get("summary") or ui_text[:400]
                comment_to_post = ai_agent.generate_post_comment(
                    caption=caption_text,
                    author=post_analysis.get("author", ""),
                    tone=tone
                )

            manager._broadcast_event("comment_generated", {"comment": comment_to_post})

            # 6. Locate comment bubble and select it
            manager._broadcast_event("post_engage_step", {"step": 5, "message": "Localizando icono de comentario y abriendo panel..."})
            comment_pt = button_coords.get("comment")
            if not comment_pt:
                button_coords = controller.detect_action_buttons(screen_path=screen_file, ai_agent=ai_agent)
                comment_pt = button_coords.get("comment", (int(controller.get_screen_size()[0] * 0.938), int(controller.get_screen_size()[1] * 0.486)) if detected_media == "reel" else (131, 985))
            logger.info(f"Pulsando icono de comentario en {comment_pt}...")
            controller.tap_coordinates(*comment_pt)
            time.sleep(2.5)  # Esperar que se abra completamente el panel de comentarios
            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            # 7. Focus 'Añade un comentario...', wait for keyboard to rise, and type naturally
            manager._broadcast_event("post_engage_step", {"step": 6, "message": "Enfocando caja 'Añade un comentario...' y esperando apertura del teclado..."})
            controller.type_comment_naturally(comment_to_post)
            time.sleep(1.0)
            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            # 8. Tap 'Publicar' button to submit comment
            manager._broadcast_event("post_engage_step", {"step": 7, "message": "Pulsando botón 'Publicar' para enviar comentario..."})
            controller.tap_publish_comment(sleep_after=3.0)
            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            # 9. Dismiss keyboard, verify closure, and dismiss comment drawer
            manager._broadcast_event("post_engage_step", {"step": 8, "message": "Ocultando teclado, verificando cierre y regresando a la publicación con pausas naturales..."})
            controller.dismiss_keyboard_and_drawer(keyboard_wait=1.2, inter_pause=1.5, drawer_wait=2.0)
            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            # 10. Locate back arrow icon ('<- Publicaciones'), verify presence on screen, and tap
            manager._broadcast_event("post_engage_step", {"step": 9, "message": "Verificando icono de flecha hacia atrás ('<-') en pantalla y tocándolo..."})
            arrow_res = controller.locate_and_tap_icon("back_arrow", sleep_after=2.0)
            if not arrow_res.get("success"):
                arrow_res = controller.locate_and_tap_icon("arrow_back", sleep_after=2.0)
            controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

            manager._broadcast_event("post_engage_step", {
                "step": 10,
                "message": f"¡Flujo completado al 100%! Like aplicado, comentario publicado y pantalla regresada al feed principal."
            })
            logger.info(f"--- Flujo completado al 100%: Like, Comentario publicado y retorno al feed exitoso: \"{comment_to_post}\" ---")

        except Exception as e:
            err_msg = f"Error en Auto-Engage: {e}"
            logger.error(err_msg)
            manager._broadcast_event("post_engage_step", {"step": -1, "message": err_msg})

    thread = threading.Thread(target=_execute_post_flow, daemon=True)
    thread.start()

    return jsonify({"success": True, "message": "Auto-engage workflow started."})


@app.route("/api/post/generate-comment", methods=["POST"])
def api_post_generate_comment() -> Response:
    """Generates a contextual comment preview based on caption and author."""
    data = request.get_json() or {}
    caption = data.get("caption", "").strip()
    author = data.get("author", "").strip()
    tone = data.get("tone", "positive")
    t_model = data.get("text_model", "").strip() or config.TEXT_MODEL
    v_model = data.get("vision_model", "").strip() or config.VISION_MODEL

    try:
        ai_agent = AIAgent(
            base_url=config.OLLAMA_BASE_URL,
            vision_model=v_model,
            text_model=t_model,
        )
        comment = ai_agent.generate_post_comment(caption=caption, author=author, tone=tone)
        return jsonify({"success": True, "comment": comment})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/results", methods=["GET"])
def api_results() -> Response:
    """Returns stored analysis results from JSON file."""
    if os.path.exists(config.OUTPUT_FILE):
        try:
            with open(config.OUTPUT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return jsonify({"success": True, "results": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, "results": []})


# --- Facebook Automator Endpoints ---
def scan_facebook_screen_elements(ctrl: AndroidController, package: str = "com.facebook.katana") -> Dict[str, Any]:
    """Scans the current screen via UIAutomator dump and visual templates to detect Facebook action elements."""
    import subprocess
    import xml.etree.ElementTree as ET

    detected_elements: Dict[str, Any] = {
        "like_button": None,
        "comment_button": None,
        "input_box": None,
        "send_button": None,
        "package_detected": package
    }

    try:
        res = subprocess.run([ctrl.adb_bin, "shell", "uiautomator", "dump", "/sdcard/fb_window_dump.xml"], capture_output=True, check=False, timeout=6)
        cat_res = subprocess.run([ctrl.adb_bin, "shell", "cat", "/sdcard/fb_window_dump.xml"], capture_output=True, check=False, timeout=4)
        xml_text = cat_res.stdout.decode("utf-8", errors="ignore") if cat_res.stdout else ""

        if xml_text and "<hierarchy" in xml_text:
            root = ET.fromstring(xml_text)
            for node in root.iter():
                desc = (node.attrib.get("content-desc") or "").strip().lower()
                text = (node.attrib.get("text") or "").strip().lower()
                cls = (node.attrib.get("class") or "").strip().lower()
                bounds = node.attrib.get("bounds") or ""
                m = re.findall(r'\[(\d+),(\d+)\]', bounds)
                if len(m) != 2:
                    continue
                (x1, y1), (x2, y2) = [(int(x), int(y)) for x, y in m]
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                # Detect Like
                if not detected_elements["like_button"] and any(w in desc or w in text for w in ["me gusta", "like", "reaccionar"]):
                    detected_elements["like_button"] = {"bounds": bounds, "center": [cx, cy], "desc": desc or text}

                # Detect Comment button
                if not detected_elements["comment_button"] and any(w in desc or w in text for w in ["comentar", "comentario", "comment"]):
                    if "edittext" not in cls:
                        detected_elements["comment_button"] = {"bounds": bounds, "center": [cx, cy], "desc": desc or text}

                # Detect Comment Input box
                if not detected_elements["input_box"] and ("edittext" in cls or any(w in desc or w in text for w in ["escribe un comentario", "añade un comentario", "escribir un comentario", "write a comment"])):
                    detected_elements["input_box"] = {"bounds": bounds, "center": [cx, cy], "desc": desc or text, "class": cls}

                # Detect Send/Submit button
                if not detected_elements["send_button"] and any(w in desc or w in text for w in ["publicar", "enviar", "post", "send"]):
                    detected_elements["send_button"] = {"bounds": bounds, "center": [cx, cy], "desc": desc or text}
    except Exception as ex:
        logger.warning(f"Error scanning Facebook UI elements via XML: {ex}")

    # Fallback to OpenCV template matching if Like or Comment were not found by XML
    try:
        if not detected_elements["like_button"] or not detected_elements["comment_button"]:
            ctrl.capture_screen(config.DEFAULT_SCREEN_IMAGE)
            fb_vis = ctrl.detect_facebook_action_buttons(screen_path=config.DEFAULT_SCREEN_IMAGE)
            if not detected_elements["like_button"] and fb_vis.get("like"):
                lp = fb_vis["like"]
                detected_elements["like_button"] = {"bounds": f"[{lp[0]-30},{lp[1]-30}][{lp[0]+30},{lp[1]+30}]", "center": list(lp), "desc": "like (visual template)"}
            if not detected_elements["comment_button"] and fb_vis.get("comment"):
                cp = fb_vis["comment"]
                detected_elements["comment_button"] = {"bounds": f"[{cp[0]-30},{cp[1]-30}][{cp[0]+30},{cp[1]+30}]", "center": list(cp), "desc": "comment (visual template)"}
    except Exception as ex:
        logger.warning(f"Error in Facebook visual template detection fallback: {ex}")

    return detected_elements


@app.route("/api/facebook/open-post", methods=["POST"])
def api_facebook_open_post() -> Response:
    """
    Opens a Facebook post, reel, or video targeting Facebook standard or Facebook Lite.
    Optionally executes a gentle scroll down to reveal action icons (Like/Comment)
    and waits before scanning the screen.
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    package = (data.get("package") or "com.facebook.katana").strip()
    use_current = bool(data.get("use_current_screen", False))
    auto_scroll = bool(data.get("auto_scroll", True))
    scroll_pixels = int(data.get("scroll_pixels", 280))
    wait_before_scroll = float(data.get("wait_before_scroll", 3.5))
    wait_after_scroll = float(data.get("wait_after_scroll", 2.5))
    auto_scan = bool(data.get("auto_scan", True))

    if not url and not use_current:
        return jsonify({"success": False, "error": "Ingresa la URL de la publicación de Facebook o activa 'Usar pantalla actual'."}), 400

    try:
        ctrl = AndroidController()
        if not use_current:
            opened = ctrl.open_post_link(url, package=package)
            if not opened:
                return jsonify({"success": False, "error": f"No se pudo abrir el enlace con {package}."}), 500
            # Pausa para permitir que Facebook abra y cargue la publicación
            logger.info(f"Facebook: Esperando {wait_before_scroll}s para que cargue la publicación...")
            time.sleep(wait_before_scroll)

        # Pequeño deslizamiento hacia abajo para que aparezcan los iconos (Me gusta / Comentar)
        if auto_scroll:
            logger.info(f"Facebook: Realizando deslizamiento suave ({scroll_pixels}px) para revelar iconos...")
            ctrl.gentle_scroll_down(pixels=scroll_pixels, duration_ms=350)
            # Tarda un poco para que la pantalla se estabilice antes de escanear
            logger.info(f"Facebook: Esperando {wait_after_scroll}s para estabilizar la pantalla y permitir escaneo...")
            time.sleep(wait_after_scroll)

        detected_elements = None
        if auto_scan:
            logger.info("Facebook: Escaneando elementos de UI automáticamente tras el deslizamiento...")
            detected_elements = scan_facebook_screen_elements(ctrl, package=package)

        return jsonify({
            "success": True,
            "message": "Publicación abierta en Facebook, deslizamiento completado e iconos preparados.",
            "detected_elements": detected_elements,
            "scrolled": auto_scroll,
            "scroll_pixels": scroll_pixels,
            "wait_after_scroll": wait_after_scroll
        })
    except Exception as e:
        logger.error(f"Error opening Facebook post: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/facebook/follow-page", methods=["POST"])
def api_facebook_follow_page() -> Response:
    """
    Ejecuta el flujo para seguir una página de Facebook con Qwen 2.5-VL y ADB:
    Paso 1: Abrir el enlace en el dispositivo.
    Paso 2: Capturar pantalla del dispositivo.
    Paso 3: Revisar con Qwen 2.5-VL que el elemento 'Seguir' existe y localizar coordenadas.
    Paso 4: Tocar en esa pantalla en las coordenadas detectadas con ADB.
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "Introduce el enlace de la página o perfil de Facebook a seguir."}), 400

    package = (data.get("package") or "com.facebook.lite").strip()
    v_model = (data.get("vision_model") or config.VISION_MODEL).strip()

    try:
        ctrl = AndroidController()
        ai_agent = AIAgent(vision_model=v_model)

        def on_step(st: int, msg: str):
            manager._broadcast_event("post_engage_step", {
                "step": st,
                "message": f"Facebook Seguir: {msg}"
            })
            manager._broadcast_event("screen_updated", {"timestamp": time.time()})

        res = ctrl.follow_facebook_page_flow(
            url=url,
            ai_agent=ai_agent,
            package=package,
            load_wait=4.5,
            step_callback=on_step
        )

        manager._broadcast_event("screen_updated", {"timestamp": time.time()})

        return jsonify(res)
    except Exception as e:
        logger.error(f"Error en flujo de seguir página de Facebook: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/facebook/start-follow-cycle", methods=["POST"])
def api_facebook_start_follow_cycle() -> Response:
    """
    Inicia el ciclo continuo multi-cuenta de Seguir página en Facebook Lite:
    Abre página -> Seguir -> Siguiendo -> 3 slides perfil -> Atrás (<) ->
    Home verificado -> 6 slides feed (30s) -> Menú (☰) -> Cambio de cuenta (de 1 en 1).
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "Introduce el enlace de la página o perfil de Facebook a seguir."}), 400

    accounts_limit = int(data.get("accounts_limit") or data.get("accounts_count") or 5)
    delay_between = float(data.get("delay_between_accounts") or data.get("delay_seconds") or 18.0)
    package = (data.get("package") or "com.facebook.lite").strip()
    vision_model = (data.get("vision_model") or config.VISION_MODEL).strip()

    res = fb_follow_cycle_manager.start_cycle(
        target_url=url,
        accounts_limit=accounts_limit,
        delay_between_accounts=delay_between,
        package=package,
        vision_model=vision_model
    )
    return jsonify(res)


@app.route("/api/facebook/stop-follow-cycle", methods=["POST"])
def api_facebook_stop_follow_cycle() -> Response:
    """Detiene el ciclo multi-cuenta de Seguir en Facebook Lite."""
    res = fb_follow_cycle_manager.stop_cycle()
    return jsonify(res)


@app.route("/api/facebook/follow-cycle-status", methods=["GET"])
def api_facebook_follow_cycle_status() -> Response:
    """Devuelve el estado en vivo del ciclo multi-cuenta de Seguir."""
    return jsonify(fb_follow_cycle_manager.get_status())


# --- Ciclo de Interacción en Publicaciones y Reels de Facebook Lite ---
@app.route("/api/facebook/post-engage/start", methods=["POST"])
def api_facebook_start_post_engage_cycle() -> Response:
    """Inicia el ciclo multi-cuenta de interacción y comentarios en FB Lite (Reel o Post normal)."""
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    pasted_comments = data.get("pasted_comments") or []
    accounts_limit = int(data.get("accounts_limit") or 5)
    do_like = bool(data.get("do_like", True))
    do_comment = bool(data.get("do_comment", True))
    antiblock_pause = int(data.get("antiblock_pause") or 30)
    package = (data.get("package") or "com.facebook.lite").strip()

    # Validar requerimiento obligatorio de cuentas
    with fb_accounts_requirement["lock"]:
        if fb_accounts_requirement["scanning"]:
            return jsonify({
                "success": False,
                "error": "El servidor está realizando la revisión de cuentas en Facebook Lite. Espera unos segundos a que concluya."
            }), 400

    res = fb_post_engagement_manager.start_cycle(
        url=url,
        pasted_comments=pasted_comments,
        accounts_limit=accounts_limit,
        do_like=do_like,
        do_comment=do_comment,
        antiblock_pause=antiblock_pause,
        package=package
    )
    return jsonify(res)


@app.route("/api/facebook/post-engage/stop", methods=["POST"])
def api_facebook_stop_post_engage_cycle() -> Response:
    """Detiene el ciclo de interacción en Facebook."""
    res = fb_post_engagement_manager.stop_cycle()
    return jsonify(res)


@app.route("/api/facebook/post-engage/status", methods=["GET"])
def api_facebook_post_engage_status() -> Response:
    """Estado en vivo del ciclo de interacción en Facebook."""
    return jsonify(fb_post_engagement_manager.get_status())


@app.route("/api/facebook/scroll", methods=["POST"])
def api_facebook_scroll() -> Response:
    """Performs a gentle scroll down on Facebook to reveal action icons."""
    data = request.get_json() or {}
    pixels = int(data.get("pixels", 280))
    wait_after = float(data.get("wait_after", 2.5))
    try:
        ctrl = AndroidController()
        ctrl.gentle_scroll_down(pixels=pixels, duration_ms=350)
        time.sleep(wait_after)
        return jsonify({"success": True, "message": f"Deslizamiento de {pixels}px realizado."})
    except Exception as e:
        logger.error(f"Error in Facebook scroll: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/facebook/scan-ui", methods=["POST"])
def api_facebook_scan_ui() -> Response:
    """
    Scans the current Facebook screen via UIAutomator dump and OpenCV template matching to detect:
    Like/Reaction button, Comments button, Comment input field, and Submit button.
    """
    data = request.get_json() or {}
    package = (data.get("package") or "com.facebook.katana").strip()
    try:
        ctrl = AndroidController()
        detected_elements = scan_facebook_screen_elements(ctrl, package=package)
        return jsonify({
            "success": True,
            "detected_elements": detected_elements
        })
    except Exception as e:
        logger.error(f"Error scanning Facebook UI: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/facebook/run-engage", methods=["POST"])
def api_facebook_run_engage() -> Response:
    """
    Executes the complete Facebook engagement workflow:
    1. Opens post & gentle scrolls down to reveal action buttons.
    2. Locates Like (👍) and Comment (💬) icons.
    3. Captures screen & Vision AI analyzes the post ("tome captura y vea") to generate comment
       or selects from preset comments bank.
    4. Taps Like (👍, the first button).
    5. Taps Comment (💬, the second button) to open comment section & bottom input box.
    6. Types the generated comment naturally and taps Publicar/Enviar.
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    package = (data.get("package") or "com.facebook.lite").strip()
    use_current = bool(data.get("use_current_screen", False))
    auto_scroll = bool(data.get("auto_scroll", True))
    scroll_pixels = int(data.get("scroll_pixels", 280))
    wait_after_scroll = float(data.get("wait_after_scroll", 2.5))
    do_like = bool(data.get("do_like", True))
    do_comment = bool(data.get("do_comment", True))
    comment_mode = "preset_list"
    pasted_comments = data.get("pasted_comments") or []
    custom_comment = (data.get("custom_comment") or "").strip()
    manual_context = (data.get("manual_context") or "").strip()
    is_contest = bool(data.get("is_contest", False))
    contest_requirement = (data.get("contest_requirement") or "").strip()
    teams_or_topic = (data.get("teams_or_topic") or "").strip()
    v_model = (data.get("vision_model") or "").strip() or config.VISION_MODEL
    t_model = (data.get("text_model") or "").strip() or config.TEXT_MODEL
    narrative = (data.get("narrative") or "a_favor").strip()

    if not url and not use_current:
        return jsonify({"success": False, "error": "Ingresa la URL de la publicación de Facebook o activa 'Usar pantalla actual'."}), 400

    from core.workflows.facebook_engagement import (
        detect_facebook_content_type,
        wait_for_facebook_content_load,
        interact_facebook_standard_post,
        interact_facebook_reel
    )

    def _execute_facebook_flow():
        controller = AndroidController()

        try:
            if not use_current:
                manager._broadcast_event("post_engage_step", {
                    "step": 1,
                    "message": f"Facebook: Abriendo publicación en {package}..."
                })
                controller.open_url(url, package=package)

                # Esperar hasta 30s de carga y detectar Reel vs Post
                load_res = wait_for_facebook_content_load(
                    controller=controller,
                    url=url,
                    max_wait_seconds=30.0,
                    status_callback=lambda m: manager._broadcast_event("post_engage_step", {"step": 1, "message": f"Facebook: {m}"})
                )
                content_type = load_res.get("content_type", "post")
            else:
                controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
                content_type = detect_facebook_content_type(controller, screen_path=config.DEFAULT_SCREEN_IMAGE, url=url)

            manager._broadcast_event("post_engage_step", {
                "step": 2,
                "message": f"Facebook: Contenido detectado como {content_type.upper()}. Preparando interacción..."
            })

            # Seleccionar comentario del banco rotativo
            if custom_comment:
                comment_to_post = custom_comment
            elif pasted_comments:
                comment_to_post = pasted_comments.pop(0).strip()
                manager._broadcast_event("comment_bank_updated", {
                    "platform": "facebook",
                    "consumed_comment": comment_to_post,
                    "remaining_comments": pasted_comments
                })
            else:
                comment_to_post = "Excelente publicación, saludos cordiales."

            manager._broadcast_event("comment_generated", {"comment": comment_to_post})
            manager._broadcast_event("post_engage_step", {
                "step": 3,
                "message": f"Facebook: Comentario preparado: \"{comment_to_post}\""
            })

            def _step_sub(msg: str, st: int):
                manager._broadcast_event("post_engage_step", {"step": st, "message": f"Facebook: {msg}"})

            if content_type == "reel":
                res_act = interact_facebook_reel(
                    controller=controller,
                    do_like=do_like,
                    comment_text=comment_to_post if do_comment else "",
                    status_callback=_step_sub
                )
            else:
                res_act = interact_facebook_standard_post(
                    controller=controller,
                    do_like=do_like,
                    comment_text=comment_to_post if do_comment else "",
                    status_callback=_step_sub
                )

            # Registrar interacción en memoria
            dev_serial = controller.get_device_serial()
            accounts_memory.record_facebook_engagement(
                device_serial=dev_serial,
                account_name="active_account",
                target_url=url,
                comment_text=comment_to_post if do_comment else "",
                did_like=do_like
            )

            manager._broadcast_event("post_engage_step", {
                "step": 8,
                "message": f"Facebook: ¡Interacción ({content_type.upper()}) completada con éxito!"
            })
            logger.info(f"Facebook: Interacción simple ({content_type.upper()}) finalizada exitosamente.")

        except Exception as e:
            err_msg = f"Error en automatización de Facebook: {e}"
            logger.error(err_msg)
            manager._broadcast_event("post_engage_step", {"step": -1, "message": err_msg})

    thread = threading.Thread(target=_execute_facebook_flow, daemon=True)
    thread.start()

    return jsonify({"success": True, "message": "Proceso de automatización para Facebook iniciado."})


# --- Scrcpy & Multi-Device Farm Endpoints ---
@app.route("/api/scrcpy/devices", methods=["GET"])
def api_scrcpy_devices():
    """Devuelve la lista de dispositivos ADB conectados con telemetría y estado de scrcpy."""
    try:
        devs = scrcpy_manager.get_connected_devices()
        return jsonify({
            "success": True,
            "count": len(devs),
            "devices": devs
        })
    except Exception as e:
        logger.error(f"Error listando dispositivos para scrcpy: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scrcpy/launch", methods=["POST"])
def api_scrcpy_launch():
    """Lanza ventana(s) de scrcpy para un dispositivo o en mosaico para todos ('all')."""
    try:
        data = request.get_json(silent=True) or {}
        serial = data.get("serial", "all")
        options = {
            "max_size": int(data.get("max_size", 600)),
            "bit_rate": str(data.get("bit_rate", "2M")),
            "stay_awake": bool(data.get("stay_awake", True)),
            "turn_screen_off": bool(data.get("turn_screen_off", False)),
            "no_audio": bool(data.get("no_audio", True)),
        }
        res = scrcpy_manager.launch_scrcpy(serial, options)
        return jsonify(res)
    except Exception as e:
        logger.error(f"Error al lanzar scrcpy: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scrcpy/stop", methods=["POST"])
def api_scrcpy_stop():
    """Detiene ventana(s) de scrcpy para un dispositivo o para todos ('all')."""
    try:
        data = request.get_json(silent=True) or {}
        serial = data.get("serial", "all")
        res = scrcpy_manager.stop_scrcpy(serial)
        return jsonify(res)
    except Exception as e:
        logger.error(f"Error al detener scrcpy: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scrcpy/preview/<serial>", methods=["GET"])
def api_scrcpy_preview(serial: str):
    """Devuelve la captura de pantalla miniatura en tiempo real para el dispositivo."""
    try:
        img_bytes = scrcpy_manager.get_preview_image(serial)
        return Response(
            img_bytes,
            mimetype="image/jpeg",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    except Exception as e:
        logger.error(f"Error al obtener preview para {serial}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scrcpy/control/<serial>", methods=["POST"])
def api_scrcpy_control(serial: str):
    """Envía comandos de navegación / teclas (home, back, app_switch, power, wake, unlock)."""
    try:
        data = request.get_json(silent=True) or {}
        action = data.get("action", "home")
        res = scrcpy_manager.send_control(serial, action)
        return jsonify(res)
    except Exception as e:
        logger.error(f"Error en control de dispositivo {serial}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scrcpy/clear-recents/<serial>", methods=["POST"])
def api_scrcpy_clear_recents(serial: str):
    """Cierra apps recientes en el dispositivo o en todos ('all')."""
    try:
        res = scrcpy_manager.clear_device_recents(serial)
        return jsonify(res)
    except Exception as e:
        logger.error(f"Error al limpiar recientes para {serial}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/logs/stream")
def api_logs_stream() -> Response:
    """Server-Sent Events (SSE) stream for real-time logs and status updates."""
    def event_generator():
        # Send initial recent logs on connect
        with recent_logs_lock:
            for entry in recent_logs:
                yield f"data: {json.dumps(entry)}\n\n"

        # Stream new events
        while True:
            try:
                event = log_queue.get(timeout=25.0)
                yield f"data: {json.dumps(event)}\n\n"
            except queue.Empty:
                # Keep-alive heartbeat
                yield f": heartbeat\n\n"

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )






@app.route("/api/system/clear-cache", methods=["POST"])
def api_system_clear_cache() -> Response:
    """Reinicia la caché (llm_cache, localizadores, calibración) y el estado en memoria."""
    try:
        memory_store.clear_cache()
        accounts_memory.load()
        with fb_accounts_requirement["lock"]:
            fb_accounts_requirement["reviewed"] = False
            fb_accounts_requirement["scanning"] = False
            fb_accounts_requirement["total_accounts"] = 0
            fb_accounts_requirement["accounts"] = []
            fb_accounts_requirement["error"] = None
            fb_accounts_requirement["message"] = "Requerimiento obligatorio: presiona 'Revisar Cuentas FB' para revisar las cuentas antes de iniciar la automatización."
        return jsonify({"success": True, "message": "Caché y estado reiniciados correctamente."})
    except Exception as e:
        logger.error(f"api_system_clear_cache failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/system/restart", methods=["POST"])
def api_system_restart() -> Response:
    """Reinicia por completo el programa: detiene todo y relanza el proceso Python."""
    try:
        full_system_stop_and_cleanup_ram()
    except Exception as e:
        logger.warning(f"Aviso al detener antes de reiniciar: {e}")

    def _restart():
        time.sleep(0.5)
        project_root = os.getcwd()
        log_path = os.path.join(project_root, "data", "server.log")
        launcher = (
            "import os, sys, time, subprocess\n"
            f"os.chdir({project_root!r})\n"
            "time.sleep(2)\n"
            "subprocess.Popen([sys.executable, 'app.py'], start_new_session=True)\n"
        )
        try:
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(f"\n===== Reinicio solicitado {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                subprocess.Popen(
                    [sys.executable, "-c", launcher],
                    start_new_session=True,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
        except Exception as e:
            logger.error(f"No se pudo relanzar el servidor: {e}")
            return
        os._exit(0)

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"success": True, "message": "Reiniciando el programa..."})


@app.errorhandler(404)
def handle_404_error(e):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "error": f"Endpoint no encontrado ({request.path}). Asegúrate de reiniciar el servidor Python (app.py) para cargar las nuevas funciones."
        }), 404
    return e


@app.errorhandler(500)
def handle_500_error(e):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "error": f"Error interno en el servidor: {str(e)}"
        }), 500
    return e


if __name__ == "__main__":
    logger.info("Starting Web Control Dashboard on http://127.0.0.1:5000 ...")
    # La revisión de cuentas ahora es MANUAL (botón "Revisar Cuentas FB" en el panel).
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
