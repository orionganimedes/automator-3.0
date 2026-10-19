# ROL: WORKER 1 — CONTROLADOR ADB Y HARDWARE ANDROID

## Ámbito de Código

- Archivo principal: `core/adb/controller.py`
- Herramientas auxiliares: Binarios `adb`, dumps `uiautomator`, control de resolución (`wm size`) y gestión de procesos del sistema.

## Directivas Técnicas Obligatorias

1. **Resiliencia de Procesos**:

   - Toda invocación de subprocess vía ADB debe implementar parámetros `timeout` y capturar excepciones para no colapsar el hilo del servicio.
   - En situaciones de pantalla protegida o `FLAG_SECURE`, activar estrategias de fallback basadas en volcado XML (`uiautomator dump`) o cinéticas relativas a las dimensiones (`wm size`).

2. **Sanitización de Texto**:

   - Todo texto inyectado mediante `input text` debe sanitizarse previamente con la función única `sanitize_for_adb(text)` eliminando acentos, caracteres especiales y emojis para evitar código de salida 137.

3. **Gestión de Memoria y Ciclo de Vida**:

   - Garantizar el cierre forzado (`am force-stop`) y la limpieza de memoria (`send-trim-memory`) al finalizar ciclos de navegación o rotación de cuentas.

4. **Formato de Retorno**:

   - Cada método táctil debe reportar un diccionario con coordenadas `(x, y)`, método de detección empleado (`ui_text`, `template`, `vision_ai`, `kinetic`), estado de éxito y tiempo de estabilización.

---

## Plan de Optimización Asignado a Worker ADB

### FASE 1 — Bugs Críticos

**OPT-ADB-01 (F1-01): Corregir `total_accounts_detected` hardcodeado**
- Archivo: `core/adb/controller.py` ~línea 1894, método `inspect_and_switch_to_next_account()`
- Reemplazar `total_accounts_detected = 15` por conteo dinámico:
  ```python
  total_accounts_detected = len(self.accounts_memory.get_accounts(self.device_serial))
  ```
- Fallback si el conteo retorna 0: usar `count_registered_accounts_with_swipe()`.

**OPT-ADB-02 (F1-03): Unificar tabla de sanitización de acentos**
- Existe duplicación entre línea ~1447 (`_sanitize_for_adb_text`) y línea ~1622.
- Crear función pública `sanitize_for_adb(text: str) -> str` en un módulo utilitario `core/adb/text_utils.py`.
- Reemplazar ambas implementaciones con una sola llamada.

### FASE 2 — Consolidación de Infraestructura

**OPT-ADB-03 (F2-01): Consolidar `_dump_ui_hierarchy()`**
- Identificar los 15+ bloques inline que repiten el patrón `exec-out uiautomator dump | parse XML`.
- Reemplazar todos por llamadas al método privado `_dump_ui_hierarchy()` que ya existe.
- No alterar la firma ni el comportamiento del método existente.

**OPT-ADB-04 (F2-02): Clase `ScreenLayout`**
- Crear `core/adb/screen_layout.py` con clase `ScreenLayout(width: int, height: int)`.
- Migrar todos los condicionales `if height == 1612: target_cy = 1468` a métodos de `ScreenLayout`.
- `AndroidController` instancia `ScreenLayout` una vez tras obtener `wm size`.

**OPT-ADB-05 (F2-03): Formato de retorno estándar**
- Auditar todos los métodos táctiles (`tap_*`, `swipe_*`, `long_press_*`).
- Garantizar que cada uno retorne `dict` con claves: `x`, `y`, `method`, `success`, `stabilize_ms`.

**OPT-ADB-06 (RIESGO-VIS-01): Corregir `find_icon_on_screen()` inexistente**
- Reportado por Worker Visión/IA tras completar FASE 3.
- Archivo: `core/adb/controller.py` líneas 2252 y 2616.
- El método `matcher.find_icon_on_screen(...)` no existe en `IconMatcher`; el `AttributeError` está silenciado por `try/except`, provocando fallos silenciosos de detección.
- Verificar cuál es el método correcto (`locate_icon()` o `find_template()`), reemplazar las 2 llamadas y eliminar el `try/except` que oculta el error.

**OPT-ADB-07 (RIESGO-VIS-02): Normalizar timeouts de Ollama**
- Reportado por Worker Visión/IA tras completar FASE 3.
- Timeouts actuales inconsistentes: `agent.py` 90s, `icon_matcher.py` 25s, `facebook_engagement.py` 20s, `detect_reel_icons.py` 45s.
- Agregar en `config.py`:
  ```python
  OLLAMA_TIMEOUT_VISION: int = 60   # inferencias con imagen
  OLLAMA_TIMEOUT_TEXT: int = 30     # inferencias solo texto
  ```
- Reemplazar todos los timeouts inline por estas constantes.

### Orden de ejecución

Ejecutar en este orden; no avanzar sin `STATUS: APPROVED` del Auditor en cada tarea:

1. OPT-ADB-01 (F1-01)
2. OPT-ADB-06 (RIESGO-VIS-01)
3. OPT-ADB-02 (F1-03)
4. OPT-ADB-07 (RIESGO-VIS-02)
5. OPT-ADB-03 (F2-01)
6. OPT-ADB-04 (F2-02)
7. OPT-ADB-05 (F2-03)

### Protocolo de entrega por tarea

Al completar cada tarea:

1. Actualizar `.orchestrator/state/workers/adb/status.md`:
   - Cambiar el estado de la fila a `COMPLETADO`.
   - Escribir en "Notas del Worker" las decisiones de implementación relevantes.
2. Guardar el diff en `.orchestrator/state/diffs/`:
   ```bash
   git diff HEAD -- <archivos_modificados> > .orchestrator/state/diffs/<ID>_adb_<YYYY-MM-DD>.diff
   ```
3. Notificar al Auditor con: ID de tarea, archivos modificados y ruta al diff.
4. Esperar `STATUS: APPROVED` antes de cerrar la tarea y avanzar a la siguiente.

### Restricciones de Entrega

- Cero imports circulares tras el refactor.
- `python3 -m py_compile core/adb/controller.py` debe pasar sin errores.
- Ningún `subprocess.run` sin `timeout`.
- Cero `AttributeError` silenciados por `try/except` en llamadas a `IconMatcher`.
