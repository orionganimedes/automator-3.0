# PLAN MAESTRO — automator3 Optimización Interna
**Fecha inicio:** 2026-10-19  
**Arquitecto:** orionganimedes  
**Alcance:** Saneamiento, refactor y estabilización del código existente. Sin nuevas features externas.

---

## FASE 1 — Corrección de Bugs Críticos
**Worker responsable:** ADB  
**Estado:** PENDIENTE

| ID | Archivo | Problema | Prioridad |
|----|---------|----------|-----------|
| F1-01 | `core/adb/controller.py:1894` | `total_accounts_detected = 15` hardcodeado ignora conteo real | CRÍTICA |
| F1-02 | `core/workflows/facebook_engagement.py` | URL Ollama hardcodeada en lugar de `config.OLLAMA_BASE_URL` | ALTA |
| F1-03 | `core/adb/controller.py:1611-1622` | Tabla de acentos duplicada respecto a `_sanitize_for_adb_text()` línea 1447 | ALTA |

**Criterio de salida:** Auditor emite `STATUS: APPROVED` en los 3 archivos modificados.

---

## FASE 2 — Consolidación de Infraestructura ADB
**Worker responsable:** ADB  
**Depende de:** FASE 1 completada  
**Estado:** PENDIENTE

| ID | Tarea | Descripción |
|----|-------|-------------|
| F2-01 | Consolidar `_dump_ui_hierarchy()` | 15+ bloques inline idénticos → usar el método privado existente en todos los puntos |
| F2-02 | Clase `ScreenLayout` | Encapsular coordenadas calibradas por resolución de dispositivo; eliminar condicionales `height == 1612` dispersos |
| F2-03 | Formato de retorno estándar | Cada método táctil retorna `{x, y, method, success, stabilize_ms}` según contrato de Worker ADB |

**Criterio de salida:** `controller.py` sin bloques XML duplicados y sin coordenadas absolutas hardcodeadas.

---

## FASE 3 — Robustez de Visión e IA
**Worker responsable:** Visión/IA  
**Depende de:** FASE 1 completada (puede correr en paralelo con FASE 2)  
**Estado:** PENDIENTE

| ID | Tarea | Descripción |
|----|-------|-------------|
| F3-01 | Unificar `_clean_and_parse_json()` | Garantizar que todos los prompts de visión pasen por un único parser con limpieza de `<think>` tags |
| F3-02 | Validación de coordenadas normalizadas | Convertir `[0.0–1.0]` a píxeles absolutos con validación de límites físicos del dispositivo en un solo lugar |
| F3-03 | Registro de telemetría de localizadores | Cada match de template o inferencia visual registra resultado en tabla `locators` de SQLite |
| F3-04 | Auditoría de umbral OpenCV | Verificar que todos los llamados a `matchTemplate` usen `threshold >= 0.65`; elevar los que estén por debajo |

**Criterio de salida:** Cero parseos JSON inline fuera de `_clean_and_parse_json()`; telemetría activa en `locators`.

---

## FASE 4 — Integridad de Workflows y Memoria
**Worker responsable:** Workflows/Memoria  
**Depende de:** FASE 2 completada  
**Estado:** PENDIENTE

| ID | Tarea | Descripción |
|----|-------|-------------|
| F4-01 | Verificación previa/posterior en cada acción FB | Confirmar presencia de botón antes de tocar; validar cambio de estado después |
| F4-02 | Eliminar `sleep` estáticos | Reemplazar todo `time.sleep(N)` fijo por `time.sleep(random.uniform(min, max))` |
| F4-03 | Deduplicación de `register_device/account` | Unificar las 3 implementaciones de write-through SQLite+JSON en `accounts.py` |
| F4-04 | Completar migración `agents.md` → SQLite | Eliminar el fallback de parseo Markdown en `load_persistent_memory()`; solo SQLite WAL |
| F4-05 | Guard de interacción duplicada | Antes de operar sobre un post verificar que la cuenta actual no figure en `used_on_posts` |

**Criterio de salida:** Cero `sleep` estáticos; memoria unificada en SQLite; workflows con confirmación pre/post.

---

## FASE 5 — Estabilización Web y SSE
**Worker responsable:** Web  
**Depende de:** FASE 3 y FASE 4 completadas  
**Estado:** PENDIENTE

| ID | Tarea | Descripción |
|----|-------|-------------|
| F5-01 | Garantizar no-blocking en todas las rutas Flask | Auditar que cero rutas en `web/routes/` ejecuten ADB directamente sin `threading.Thread` |
| F5-02 | Normalizar emisión de errores por SSE | Todo fallo de Worker debe emitir evento `{type: "error", worker: "...", msg: "..."}` por `web/events.py` |
| F5-03 | Optimizar buffer de frames en `device_stream.js` | Implementar cola de máximo 2 frames para evitar saturación de memoria en el navegador |

**Criterio de salida:** Ningún hilo Flask bloqueado durante ejecución de workflow; errores visibles en dashboard.

---

## FASE 6 — Cobertura de Tests
**Worker responsable:** Auditor supervisa; todos los Workers participan  
**Depende de:** FASES 1–5 completadas  
**Estado:** PENDIENTE

| ID | Tarea | Descripción |
|----|-------|-------------|
| F6-01 | `MockAndroidController` | Clase mock con capturas y XMLs pre-grabados para testear flujos sin dispositivo físico |
| F6-02 | Tests de flujo Instagram | Cubrir: detección de layout, generación de comentario, rotación de cuenta |
| F6-03 | Tests de flujo Facebook | Cubrir: detección Post vs Reel, localización de botones, flujo "Seguir página" |
| F6-04 | Tests de memoria concurrente | Verificar write-through atómico bajo concurrencia con `threading` |

**Criterio de salida:** `pytest` pasa sin dispositivo conectado; cobertura > 60% en `core/`.

---

## Registro de Fases

| Fase | Estado | Worker | Fecha inicio | Fecha cierre | Auditoría |
|------|--------|--------|--------------|--------------|-----------|
| FASE 1 | PENDIENTE | ADB | — | — | — |
| FASE 2 | PENDIENTE | ADB | — | — | — |
| FASE 3 | PENDIENTE | Visión/IA | — | — | — |
| FASE 4 | PENDIENTE | Workflows | — | — | — |
| FASE 5 | PENDIENTE | Web | — | — | — |
| FASE 6 | PENDIENTE | Todos | — | — | — |
