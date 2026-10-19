# WORKER ADB — Estado

**Tarea activa:** F1-01  
**Estado:** EN PROGRESO  
**Última actualización:** 2026-10-19

---

## Instrucciones del Arquitecto

Al completar cada tarea, el Worker ADB debe:

1. Actualizar este archivo `status.md` con el estado real (EN PROGRESO / COMPLETADO / BLOQUEADO).
2. Guardar el diff de los cambios en:
   `.orchestrator/state/diffs/<ID_TAREA>_adb_<YYYY-MM-DD>.diff`
   Generarlo con:
   ```bash
   git diff HEAD -- <archivos_modificados> > .orchestrator/state/diffs/F1-01_adb_2026-10-19.diff
   ```
3. Notificar al Auditor cuando una tarea esté lista para revisión, indicando el ID y la ruta al diff.
4. No avanzar a la siguiente tarea sin `STATUS: APPROVED` del Auditor.

---

## Registro de tareas

| ID | Descripción | Estado | Fecha inicio | Fecha cierre |
|----|-------------|--------|--------------|--------------|
| F1-01 | Corregir `total_accounts_detected = 15` hardcodeado | EN PROGRESO | 2026-10-19 | — |
| F1-03 | Unificar tabla de sanitización de acentos | PENDIENTE | — | — |
| F2-01 | Consolidar `_dump_ui_hierarchy()` | PENDIENTE | — | — |
| F2-02 | Clase `ScreenLayout` | PENDIENTE | — | — |
| F2-03 | Formato de retorno estándar en métodos táctiles | PENDIENTE | — | — |
| RIESGO-VIS-01 | Bug: `find_icon_on_screen()` inexistente en `IconMatcher` | PENDIENTE | — | — |
| RIESGO-VIS-02 | Normalizar timeouts ADB (20/25/45/90s inconsistentes) | PENDIENTE | — | — |

---

## Detalle de cada tarea

### F1-01 — Corregir `total_accounts_detected = 15` hardcodeado
**Archivo:** `core/adb/controller.py` ~línea 1894  
**Método:** `inspect_and_switch_to_next_account()`  
**Problema:** El valor `15` ignora el conteo real de cuentas registradas.  
**Solución requerida:**
```python
# Reemplazar:
total_accounts_detected = 15

# Por:
total_accounts_detected = len(self.accounts_memory.get_accounts(self.device_serial))
# Fallback si el conteo retorna 0:
if total_accounts_detected == 0:
    total_accounts_detected = self.count_registered_accounts_with_swipe()
```
**Entrega:** diff en `.orchestrator/state/diffs/F1-01_adb_2026-10-19.diff`

---

### F1-03 — Unificar tabla de sanitización de acentos
**Archivos:** `core/adb/controller.py` líneas ~1447 y ~1622  
**Problema:** Dos implementaciones paralelas de la misma tabla de reemplazos de acentos.  
**Solución requerida:**
1. Crear `core/adb/text_utils.py` con función pública:
   ```python
   def sanitize_for_adb(text: str) -> str: ...
   ```
2. Reemplazar ambas implementaciones inline con `from core.adb.text_utils import sanitize_for_adb`.
3. Verificar que `type_comment_naturally()` y todos los `input text` usen esta función.

---

### F2-01 — Consolidar `_dump_ui_hierarchy()`
**Archivo:** `core/adb/controller.py`  
**Problema:** 15+ bloques inline que repiten el patrón `exec-out uiautomator dump | parse XML`.  
**Solución requerida:**
- Identificar todos los bloques con:
  ```bash
  grep -n "uiautomator\|exec-out.*dump\|xml.etree" core/adb/controller.py
  ```
- Reemplazar cada bloque por llamada al método `_dump_ui_hierarchy()` ya existente.
- No alterar la firma ni el comportamiento del método.

---

### F2-02 — Clase `ScreenLayout`
**Archivo nuevo:** `core/adb/screen_layout.py`  
**Problema:** Condicionales `if height == 1612: target_cy = 1468` dispersos en 8+ métodos.  
**Solución requerida:**
```python
class ScreenLayout:
    def __init__(self, width: int, height: int): ...
    def action_bar_y(self) -> int: ...
    def comment_input_y(self) -> int: ...
    # etc. por cada coordenada calibrada
```
- `AndroidController` instancia `ScreenLayout` una vez tras `get_screen_size()`.
- Eliminar todos los condicionales `height == N` inline.

---

### F2-03 — Formato de retorno estándar en métodos táctiles
**Archivo:** `core/adb/controller.py`  
**Problema:** Los métodos `tap_*`, `swipe_*`, `long_press_*` retornan tipos distintos o `None`.  
**Solución requerida:** Cada método táctil retorna:
```python
{
    "x": int,
    "y": int,
    "method": str,   # "ui_text" | "template" | "vision_ai" | "kinetic"
    "success": bool,
    "stabilize_ms": int
}
```

---

### RIESGO-VIS-01 — Bug: `find_icon_on_screen()` inexistente en `IconMatcher`
**Reportado por:** Worker Visión/IA  
**Archivo:** `core/adb/controller.py` líneas 2252 y 2616  
**Problema:** Se llama `matcher.find_icon_on_screen(...)` pero ese método no existe en `IconMatcher`. El `AttributeError` está silenciado por `try/except`, causando fallos silenciosos de detección.  
**Solución requerida:**
- Verificar si el método correcto es `locate_icon()` o `find_template()` en `icon_matcher.py`.
- Reemplazar las 2 llamadas con el método correcto.
- Eliminar el `try/except` que silencia el error una vez corregido.

---

### RIESGO-VIS-02 — Normalizar timeouts ADB
**Reportado por:** Worker Visión/IA  
**Problema:** Los timeouts de llamadas a Ollama son inconsistentes entre módulos:
- `agent.py`: 90s
- `icon_matcher.py`: 25s
- `facebook_engagement.py`: 20s
- `detect_reel_icons.py`: 45s

Un hang parcial deja el flujo en estado intermedio.  
**Solución requerida:**
- Definir en `config.py`:
  ```python
  OLLAMA_TIMEOUT_VISION: int = 60   # inferencias con imagen
  OLLAMA_TIMEOUT_TEXT: int = 30     # inferencias solo texto
  ```
- Reemplazar todos los timeouts inline por las constantes de config.

---

## Notas del Worker

<!-- El Worker ADB escribe aquí decisiones de implementación, hallazgos y bloqueos encontrados durante la ejecución -->
