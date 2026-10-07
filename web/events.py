"""
web.events - Cola de eventos y logs para Server-Sent Events (SSE).
Permite a cualquier Blueprint o módulo despachar eventos en tiempo real sin acoplamiento circular.
"""

import queue
import threading
from typing import Any, Dict, List

log_queue: queue.Queue = queue.Queue(maxsize=1000)
recent_logs: List[Dict[str, Any]] = []
recent_logs_lock = threading.Lock()


def broadcast_event(event_type: str, data: Any) -> None:
    """Envía un evento tipado a través de la cola de SSE para todos los clientes web."""
    try:
        log_queue.put_nowait({"type": event_type, "data": data})
    except Exception:
        pass
