"""
accounts_memory.py - Gestor de Memoria Persistente de Cuentas por Dispositivo

Permite registrar y rastrear las cuentas vinculadas a cada teléfono físico
conectado por ADB (identificado por su número de serie de hardware).
Evita repetir cuentas en la misma publicación, registra historial de comentarios
y permite alternar limpiamente entre múltiples teléfonos.
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.memory.store import memory_store

logger = logging.getLogger("AccountsMemory")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMORY_FILE_PATH = os.path.join(_PROJECT_ROOT, "data", "accounts_memory.json")
_memory_lock = threading.Lock()


def _sqlite_mirror_interaction(
    serial: str,
    kind: str,
    index: int,
    username: str,
    label: str,
    post_url: str,
    action: str,
    comment: str = "",
    archetype: str = "",
) -> None:
    """Espeja una interacción en SQLite (write-through). Nunca lanza."""
    try:
        uname = username or label or f"cuenta_{index + 1}"
        aid = memory_store.register_account(serial, kind, index, uname, label or uname)
        memory_store.record_interaction(aid, post_url, action, comment, archetype)
    except Exception as e:
        logger.warning(f"SQLite write-through falló: {e}")


class DeviceAccountsMemory:
    """Gestiona la persistencia de cuentas agrupadas por número de serie del dispositivo."""

    def __init__(self, file_path: str = MEMORY_FILE_PATH):
        self.file_path = file_path
        self._data: Dict[str, Any] = {"devices": {}, "version": 1}
        self.load()

    def load(self) -> None:
        """Carga el archivo JSON de memoria. Si no existe, inicializa estructura limpia."""
        with _memory_lock:
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        self._data = json.load(f)
                    if "devices" not in self._data:
                        self._data["devices"] = {}
                except Exception as e:
                    logger.error(f"Error al cargar {self.file_path}: {e}. Reiniciando memoria en blanco.")
                    self._data = {"devices": {}, "version": 1}
            else:
                self._data = {"devices": {}, "version": 1}
                self._save_unlocked()

    def save(self) -> None:
        """Guarda la memoria en el archivo JSON de forma segura con lock."""
        with _memory_lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        try:
            temp_file = f"{self.file_path}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, self.file_path)
        except Exception as e:
            logger.error(f"Error al guardar memoria en {self.file_path}: {e}")

    def get_or_register_device(
        self,
        device_serial: str,
        device_model: str = "Android Device",
        total_accounts: int = 1,
        known_usernames: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Obtiene el registro del dispositivo o lo crea si es nuevo.
        Actualiza el total de cuentas y preserva el historial existente.
        """
        if not device_serial or device_serial.strip() in ["", "unknown", "none"]:
            device_serial = "default_device"

        with _memory_lock:
            devices = self._data.setdefault("devices", {})
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if device_serial not in devices:
                devices[device_serial] = {
                    "serial": device_serial,
                    "model": device_model or "Android Phone",
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                    "total_accounts": max(1, total_accounts),
                    "last_used_account_idx": -1,
                    "accounts": []
                }
            else:
                dev = devices[device_serial]
                dev["last_seen"] = now_iso
                if device_model and device_model != "Android Device":
                    dev["model"] = device_model
                if total_accounts > 0:
                    dev["total_accounts"] = total_accounts

            dev = devices[device_serial]
            existing_accounts = {acc.get("index"): acc for acc in dev.get("accounts", [])}
            target_count = dev.get("total_accounts", 1)

            new_accounts_list = []
            for idx in range(target_count):
                if idx in existing_accounts:
                    acc = existing_accounts[idx]
                    if known_usernames and idx < len(known_usernames) and known_usernames[idx]:
                        acc["username"] = known_usernames[idx]
                    new_accounts_list.append(acc)
                else:
                    uname = known_usernames[idx] if known_usernames and idx < len(known_usernames) else f"cuenta_{idx+1}"
                    new_accounts_list.append({
                        "index": idx,
                        "label": f"Cuenta {idx + 1}",
                        "username": uname,
                        "last_used": None,
                        "used_on_posts": [],
                        "total_comments_made": 0
                    })
            dev["accounts"] = new_accounts_list
            self._save_unlocked()
            # Write-through a SQLite (dispositivo + cuentas Instagram)
            try:
                memory_store.register_device(device_serial, dev.get("model", "Android Device"))
                for acc in dev.get("accounts", []):
                    memory_store.register_account(
                        device_serial, "instagram", acc.get("index", 0),
                        acc.get("username") or "", acc.get("label") or "", acc.get("last_used"),
                    )
            except Exception as e:
                logger.warning(f"SQLite write-through falló: {e}")
            return dev

    def get_available_accounts_for_post(
        self,
        device_serial: str,
        post_url: str,
        requested_count: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Retorna la lista de cuentas del dispositivo que AÚN NO han comentado en `post_url`.
        Si todas ya comentaron o la URL está vacía, devuelve las cuentas ordenadas por menor uso reciente.
        """
        if not device_serial or device_serial.strip() in ["", "unknown"]:
            device_serial = "default_device"

        with _memory_lock:
            dev = self._data.get("devices", {}).get(device_serial)
            if not dev:
                dev = self.get_or_register_device(device_serial, total_accounts=requested_count)

            accounts = dev.get("accounts", [])
            cleaned_url = post_url.strip() if post_url else ""

            if not cleaned_url or cleaned_url.lower() in ["current", "none", "actual"]:
                # Si no hay URL específica, ordenar por last_used más antiguo
                return accounts[:requested_count] if accounts else []

            # Filtrar cuentas que NO tienen la URL en used_on_posts
            unused_accounts = [
                acc for acc in accounts
                if cleaned_url not in acc.get("used_on_posts", [])
            ]

            if unused_accounts:
                logger.info(
                    f"[{device_serial}] Se encontraron {len(unused_accounts)} cuentas que aún no han interactuado con {cleaned_url}."
                )
                return unused_accounts[:requested_count]

            logger.warning(
                f"[{device_serial}] Todas las cuentas ({len(accounts)}) ya han interactuado con {cleaned_url}. Retornando ordenadas por antigüedad."
            )
            # Ordenar por fecha de último uso
            sorted_accs = sorted(
                accounts,
                key=lambda a: a.get("last_used") or "1970-01-01"
            )
            return sorted_accs[:requested_count]

    def record_interaction(
        self,
        device_serial: str,
        account_index: int,
        post_url: str,
        comment_text: str = "",
        archetype_name: str = "",
        action_type: str = "like_and_comment"
    ) -> None:
        """
        Registra en la memoria persistente que la cuenta `account_index` del dispositivo
        interactuó con `post_url`.
        """
        if not device_serial:
            device_serial = "default_device"

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with _memory_lock:
            dev = self._data.setdefault("devices", {}).setdefault(device_serial, {
                "serial": device_serial,
                "model": "Android Phone",
                "accounts": []
            })
            dev["last_seen"] = now_iso
            dev["last_used_account_idx"] = account_index

            accounts = dev.setdefault("accounts", [])
            target_acc = None
            for acc in accounts:
                if acc.get("index") == account_index:
                    target_acc = acc
                    break

            if not target_acc:
                target_acc = {
                    "index": account_index,
                    "label": f"Cuenta {account_index + 1}",
                    "username": f"cuenta_{account_index + 1}",
                    "last_used": now_iso,
                    "used_on_posts": [],
                    "total_comments_made": 0
                }
                accounts.append(target_acc)

            target_acc["last_used"] = now_iso
            target_acc["total_comments_made"] = target_acc.get("total_comments_made", 0) + 1

            cleaned_url = post_url.strip() if post_url else ""
            if cleaned_url and cleaned_url not in target_acc.setdefault("used_on_posts", []):
                target_acc["used_on_posts"].append(cleaned_url)

            # Historial de interacciones recientes (últimas 50)
            history = target_acc.setdefault("interaction_history", [])
            history.append({
                "timestamp": now_iso,
                "post_url": cleaned_url,
                "comment": comment_text,
                "archetype": archetype_name,
                "action": action_type
            })
            if len(history) > 50:
                target_acc["interaction_history"] = history[-50:]

            self._save_unlocked()
            _sqlite_mirror_interaction(
                device_serial, "instagram", account_index,
                target_acc.get("username", ""), target_acc.get("label", ""),
                cleaned_url, action_type, comment_text, archetype_name,
            )
            logger.info(
                f"[{device_serial}] Cuenta index={account_index} ('{target_acc.get('label')}') registrada con éxito en memoria para post {cleaned_url}."
            )

    def get_device_summary(self, device_serial: str) -> Dict[str, Any]:
        """Retorna un resumen formateado del dispositivo y sus cuentas para el frontend."""
        if not device_serial:
            device_serial = "default_device"

        with _memory_lock:
            dev = self._data.get("devices", {}).get(device_serial)
            if not dev:
                return {
                    "serial": device_serial,
                    "model": "Desconocido",
                    "total_accounts": 0,
                    "accounts": [],
                    "facebook_accounts": []
                }
            return {
                "serial": dev.get("serial", device_serial),
                "model": dev.get("model", "Android Phone"),
                "total_accounts": dev.get("total_accounts", len(dev.get("accounts", []))),
                "last_used_account_idx": dev.get("last_used_account_idx", -1),
                "accounts": dev.get("accounts", []),
                "total_facebook_accounts": dev.get("total_facebook_accounts", len(dev.get("facebook_accounts", []))),
                "last_used_account_name": dev.get("last_used_account_name", ""),
                "facebook_accounts": dev.get("facebook_accounts", [])
            }

    def register_facebook_accounts(
        self,
        device_serial: str,
        device_model: str,
        account_names: List[str]
    ) -> Dict[str, Any]:
        """
        Registra o actualiza la lista de cuentas de Facebook Lite para un dispositivo.
        Preserva el historial de posts reportados de cada cuenta.
        """
        if not device_serial or device_serial.strip() in ["", "unknown", "none"]:
            device_serial = "default_device"

        with _memory_lock:
            devices = self._data.setdefault("devices", {})
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if device_serial not in devices:
                devices[device_serial] = {
                    "serial": device_serial,
                    "model": device_model or "Android Phone",
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                    "total_accounts": len(account_names),
                    "last_used_account_idx": -1,
                    "facebook_accounts": []
                }
            dev = devices[device_serial]
            dev["last_seen"] = now_iso
            if device_model and device_model != "Android Device":
                dev["model"] = device_model
            dev["total_facebook_accounts"] = len(account_names)

            existing_fb = {acc.get("username"): acc for acc in dev.get("facebook_accounts", [])}
            new_fb_list = []
            for idx, name in enumerate(account_names):
                if name in existing_fb:
                    acc = existing_fb[name]
                    acc["index"] = idx
                    acc["label"] = name
                    new_fb_list.append(acc)
                else:
                    new_fb_list.append({
                        "index": idx,
                        "label": name,
                        "username": name,
                        "last_used": None,
                        "used_on_posts": [],
                        "total_reports_made": 0
                    })
            dev["facebook_accounts"] = new_fb_list
            self._save_unlocked()
            # Write-through a SQLite (cuentas Facebook)
            try:
                memory_store.register_device(device_serial, dev.get("model", "Android Device"))
                for acc in new_fb_list:
                    memory_store.register_account(
                        device_serial, "facebook", acc.get("index", 0),
                        acc.get("username") or acc.get("label") or "", acc.get("label") or "",
                        acc.get("last_used"),
                    )
            except Exception as e:
                logger.warning(f"SQLite write-through falló: {e}")
            logger.info(f"[{device_serial}] Registradas {len(new_fb_list)} cuentas de Facebook Lite en memoria.")
            return dev

    def get_available_facebook_accounts_for_url(
        self,
        device_serial: str,
        target_url: str,
        limit: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Retorna las cuentas de Facebook Lite que NO han reportado el URL objetivo.
        """
        if not device_serial:
            device_serial = "default_device"

        with _memory_lock:
            dev = self._data.get("devices", {}).get(device_serial)
            if not dev:
                return []
            fb_accounts = dev.get("facebook_accounts", [])
            cleaned_url = target_url.strip() if target_url else ""

            if not cleaned_url:
                return fb_accounts if limit <= 0 else fb_accounts[:limit]

            unused = [
                acc for acc in fb_accounts
                if cleaned_url not in acc.get("used_on_posts", [])
            ]
            if limit > 0:
                return unused[:limit]
            return unused

    def record_facebook_report(
        self,
        device_serial: str,
        account_name: str,
        target_url: str
    ) -> None:
        """
        Registra que una cuenta de Facebook Lite reportó el URL objetivo.
        """
        if not device_serial:
            device_serial = "default_device"

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cleaned_url = target_url.strip() if target_url else ""

        with _memory_lock:
            dev = self._data.setdefault("devices", {}).setdefault(device_serial, {
                "serial": device_serial,
                "model": "Android Phone",
                "facebook_accounts": []
            })
            dev["last_seen"] = now_iso
            for acc in dev.setdefault("facebook_accounts", []):
                if acc.get("username") == account_name or acc.get("label") == account_name:
                    acc["last_used"] = now_iso
                    acc["total_reports_made"] = acc.get("total_reports_made", 0) + 1
                    if cleaned_url and cleaned_url not in acc.setdefault("used_on_posts", []):
                        acc["used_on_posts"].append(cleaned_url)
                    dev["last_used_account_name"] = account_name
                    break
            self._save_unlocked()
            _sqlite_mirror_interaction(device_serial, "facebook", 0, account_name, account_name, cleaned_url, "facebook_report")
            logger.info(f"[{device_serial}] Reporte de Facebook registrado para cuenta '{account_name}' en {cleaned_url}.")

    def record_facebook_follow(
        self,
        device_serial: str,
        account_name: str,
        target_url: str
    ) -> None:
        """
        Registra que una cuenta de Facebook Lite siguió al URL objetivo.
        """
        if not device_serial:
            device_serial = "default_device"

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cleaned_url = target_url.strip() if target_url else ""

        with _memory_lock:
            dev = self._data.setdefault("devices", {}).setdefault(device_serial, {
                "serial": device_serial,
                "model": "Android Phone",
                "facebook_accounts": []
            })
            dev["last_seen"] = now_iso
            for acc in dev.setdefault("facebook_accounts", []):
                if acc.get("username") == account_name or acc.get("label") == account_name:
                    acc["last_used"] = now_iso
                    acc["total_follows_made"] = acc.get("total_follows_made", 0) + 1
                    if cleaned_url and cleaned_url not in acc.setdefault("used_on_posts", []):
                        acc["used_on_posts"].append(cleaned_url)
                    dev["last_used_account_name"] = account_name
                    break
            self._save_unlocked()
            _sqlite_mirror_interaction(device_serial, "facebook", 0, account_name, account_name, cleaned_url, "facebook_follow")
            logger.info(f"[{device_serial}] Acción 'Seguir' de Facebook registrada para cuenta '{account_name}' en {cleaned_url}.")

    def record_facebook_engagement(
        self,
        device_serial: str,
        account_name: str,
        target_url: str,
        comment_text: str = "",
        did_like: bool = True
    ) -> None:
        """
        Registra que una cuenta de Facebook Lite interactuó (Like y/o Comentario) en una publicación o Reel.
        Agrega la URL a used_on_posts para evitar repeticiones.
        """
        if not device_serial:
            device_serial = "default_device"

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cleaned_url = target_url.strip() if target_url else ""

        with _memory_lock:
            dev = self._data.setdefault("devices", {}).setdefault(device_serial, {
                "serial": device_serial,
                "model": "Android Phone",
                "facebook_accounts": []
            })
            dev["last_seen"] = now_iso
            for acc in dev.setdefault("facebook_accounts", []):
                if acc.get("username") == account_name or acc.get("label") == account_name:
                    acc["last_used"] = now_iso
                    acc["total_engagements_made"] = acc.get("total_engagements_made", 0) + 1
                    if did_like:
                        acc["total_likes_made"] = acc.get("total_likes_made", 0) + 1
                    if comment_text:
                        acc["total_comments_made"] = acc.get("total_comments_made", 0) + 1
                        history = acc.setdefault("comments_history", [])
                        history.append({
                            "url": cleaned_url,
                            "comment": comment_text,
                            "timestamp": now_iso
                        })
                    if cleaned_url and cleaned_url not in acc.setdefault("used_on_posts", []):
                        acc["used_on_posts"].append(cleaned_url)
                    dev["last_used_account_name"] = account_name
                    break
            self._save_unlocked()
            _sqlite_mirror_interaction(
                device_serial, "facebook", 0, account_name, account_name,
                cleaned_url, "facebook_engagement", comment_text,
            )
            logger.info(f"[{device_serial}] Interacción FB (Like={did_like}, Comentario='{comment_text[:30]}...') registrada para '{account_name}' en {cleaned_url}.")


# Instancia global accesible desde app.py y módulos
accounts_memory = DeviceAccountsMemory()


def record_facebook_engagement(
    device_serial: str,
    account_name: str,
    target_url: str,
    comment_text: str = "",
    did_like: bool = True
) -> None:
    """Proxy a nivel de módulo para record_facebook_engagement."""
    accounts_memory.record_facebook_engagement(
        device_serial=device_serial,
        account_name=account_name,
        target_url=target_url,
        comment_text=comment_text,
        did_like=did_like
    )


def get_device_summary(device_serial: str) -> Dict[str, Any]:
    """Proxy a nivel de módulo para get_device_summary."""
    return accounts_memory.get_device_summary(device_serial)


def get_or_register_device(
    device_serial: str,
    device_model: str = "Android Device",
    total_accounts: int = 1,
    known_usernames: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Proxy a nivel de módulo para get_or_register_device."""
    return accounts_memory.get_or_register_device(
        device_serial=device_serial,
        device_model=device_model,
        total_accounts=total_accounts,
        known_usernames=known_usernames
    )


def record_interaction(
    device_serial: str,
    account_index: int,
    post_url: str,
    comment_text: str = "",
    archetype_name: str = "Lista Predefinida #1",
    action_type: str = "like_and_comment"
) -> None:
    """Proxy a nivel de módulo para record_interaction."""
    accounts_memory.record_interaction(
        device_serial=device_serial,
        account_index=account_index,
        post_url=post_url,
        comment_text=comment_text,
        archetype_name=archetype_name,
        action_type=action_type
    )

