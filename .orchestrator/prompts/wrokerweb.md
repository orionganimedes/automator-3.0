# ROL: WORKER 4 — SERVIDOR WEB, EVENTOS Y DASHBOARD

## Ámbito de Código

- Servidor Flask: `app.py`, `web/events.py`, módulos en `web/routes/`
- Interfaz Frontend: `templates/`, `static/css/cybernetic.css`, `static/js/`

## Directivas Técnicas Obligatorias

1. **Ejecución Asíncrona**:

   - Ninguna rutina de automatización o llamada pesada de ADB puede bloquear los hilos principales de Flask; toda tarea en segundo plano debe correr mediante `threading.Thread`.
   - Difundir el progreso, pasos ejecutados y estados de error a la interfaz a través del canal de Server-Sent Events (SSE) o WebSockets en `web/events.py`.

2. **Consistencia Visual Cybernetic**:

   - Mantener la línea visual oscura, minimalista y de alto contraste estipulada en `static/css/cybernetic.css`.
   - Optimizar el streaming de pantalla en `device_stream.js` para procesar los frames JPEG entregados por `get_realtime_frame()` sin saturar la memoria del navegador.

---

## Plan de Optimización Asignado a Worker Web

### FASE 5 — Estabilización Web y SSE

**OPT-WEB-01 (F5-01): Auditoría de rutas bloqueantes**
- Revisar todos los endpoints en `web/routes/accounts.py`, `comments.py`, `icons.py`, `memory.py`.
- Identificar cualquier llamada a `AndroidController` o Ollama que se ejecute directamente en el hilo Flask.
- Envolver en `threading.Thread(target=fn, daemon=True).start()` y devolver inmediatamente `{"status": "queued", "task_id": id}`.
- El progreso se emite por SSE; el cliente no espera la respuesta HTTP del endpoint pesado.

**OPT-WEB-02 (F5-02): Esquema de eventos SSE normalizado**
- Definir en `web/events.py` un helper `emit_event(type, worker, msg, data=None)`.
- Tipos estándar: `"log"`, `"step"`, `"error"`, `"done"`.
- Todos los Workers emiten eventos usando este helper en lugar de cadenas arbitrarias.
- El frontend en `static/js/core.js` distingue tipos y aplica estilos diferentes en el terminal.

**OPT-WEB-03 (F5-03): Buffer de frames en `device_stream.js`**
- Implementar una cola de máximo 2 frames: si llega un frame nuevo y la cola está llena, descartar el más antiguo.
- Medir con `performance.now()` el tiempo entre frames; si supera 500 ms, insertar un mensaje de advertencia en el terminal SSE.
- Liberar el `ObjectURL` del frame anterior con `URL.revokeObjectURL()` antes de asignar el nuevo.

**OPT-WEB-04: Indicador de estado de Worker en navbar**
- Agregar en `templates/components/navbar.html` un indicador visual por Worker (ADB, Visión, Workflows).
- Estado posible: `idle` (gris), `running` (verde pulsante), `error` (rojo).
- El estado se actualiza vía SSE cuando el Worker emite eventos `"step"` o `"error"`.

### Restricciones de Entrega

- Ningún endpoint Flask con tiempo de respuesta > 200 ms bajo carga de automatización.
- Cero saturación de memoria en el navegador durante streaming de 30+ minutos continuo.
- El esquema SSE normalizado es compatible con el frontend existente (no requiere cambios en `cybernetic.css`).
