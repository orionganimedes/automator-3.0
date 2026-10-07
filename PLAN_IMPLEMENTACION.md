# Plan de implementación — automator3

_Creado: 2026-09-30_

## Alcance
Optimización de la infraestructura general: ADB, capturas, stream, matching de iconos,
cliente de visión (qwen2.5vl), memoria SQLite y estructura de Flask.

**Fuera de alcance:** el ciclo de denuncias multicuenta (`FacebookReportCycleManager`, pestaña
"Hate/Auto-Denuncias", `tap_report_step*`), la navegación "anti-detección" y la generación de
comentarios que se hacen pasar por humanos. Recomendación: quitar esos módulos del proyecto escolar.

---

## Fase 0 — Medir
- [ ] Decorador `@timed` en capture / dump / match / inferencia
- [ ] `cProfile` de un flujo típico → cifras de antes y después

## Fase 1 — Cambios rápidos
- [ ] `get_controller(serial)`: una instancia cacheada (hoy hay 44 `AndroidController()` en `app.py`)
- [ ] Helper `_adb(*args, timeout=8)` con `-s serial` y timeout por defecto
- [ ] Borrar el `get_realtime_frame` duplicado (`adb_controller.py:346` y `:1916`)
- [ ] `app.run(host="127.0.0.1")` (hoy es `0.0.0.0` sin autenticación)

## Fase 2 — Rendimiento
- [ ] Stream solo con clientes conectados (o usar H.264 de scrcpy)
- [ ] `capture_array()` en memoria (screencap sin PNG, sin ir a disco)
- [ ] `icon_matcher`: plantillas cacheadas en gris, pantalla a 0,5x, región de interés, escala recordada por dispositivo
- [ ] Dump único de uiautomator con caché de ~0,5 s que se invalida tras cada acción
- [ ] Ollama: `requests.Session`, `is_ollama_online` cacheado 30 s

## Fase 3 — Módulo `ui_locator.py` (ideas de la skill ui-automator)
- [ ] Clase `Node` y `UILocator.dump/find/screen_state/tap_node`
- [ ] Ciclo DUMP → FIND → TAP → VERIFY; texto/desc/rid antes que coordenadas
- [ ] `wait_until(cond, timeout)` en vez de `sleep` fijos (hay 163)

## Fase 4 — Memoria SQLite `memory_store.py` (`data/agent_memory.db`, modo WAL)
Tablas: `screens`, `locators` (hits/misses por estrategia), `template_calib`,
`runs`, `steps` (métricas), `llm_cache`.
- [ ] Orden de estrategias: XML → plantilla → visión, guiado por la tasa de aciertos
- [ ] Coordenadas guardadas = solo pista de dónde buscar; siempre se verifica con un dump nuevo
- [ ] `@record_step` en el controlador; endpoints `/api/memory/stats` y `/api/memory/locators`

## Fase 5 — `VisionClient` (qwen2.5vl)
- [ ] Formato nativo `bbox_2d` [x1,y1,x2,y2] en píxeles (hoy usa `box_2d` de Gemini + heurísticas)
- [ ] Redimensionar a múltiplos de 28 px y convertir las coordenadas de vuelta a la pantalla
- [ ] `options: {temperature: 0, num_ctx: 8192}`, `keep_alive: "30m"`
- [ ] Recortar la región de interés (cabecera, barra inferior) antes de enviar
- [ ] Salidas estructuradas (JSON Schema en `format`), sin ejemplos con números concretos
- [ ] Unificar las ~10 funciones `locate_*/verify_*_with_vision` en `VisionClient.locate()`
- [ ] Evaluación con capturas existentes: error en píxeles, latencia, % de JSON válido

## Fase 6 — Estructura y calidad
- [ ] Dividir `app.py` (4.684 líneas) en Blueprints y servicios
- [ ] Excepciones concretas + `logger.exception` (hoy hay ~260 `except Exception`)
- [ ] Tests con pytest: dumps XML y PNG como fixtures, SQLite `:memory:`
- [ ] Mover PNG/XML de depuración a `debug/`, `git init` + `.gitignore`, borrar `index.html.bak_monolithic`
