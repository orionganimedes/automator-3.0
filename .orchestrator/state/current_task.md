# TAREA ACTIVA

**ID:** F1-01  
**Fase:** FASE 1 — Corrección de Bugs Críticos  
**Worker asignado:** ADB  
**Estado:** EN PROGRESO  
**Fecha asignación:** 2026-10-19

---

## Cola de trabajo despachada al Worker ADB

Las tareas deben ejecutarse en este orden. No iniciar la siguiente sin `STATUS: APPROVED` del Auditor.

| Orden | ID | Descripción | Bloqueante |
|-------|----|-------------|------------|
| 1 | F1-01 | Corregir `total_accounts_detected = 15` hardcodeado en `controller.py:1894` | — |
| 2 | RIESGO-VIS-01 | Corregir `find_icon_on_screen()` inexistente en `IconMatcher` (líneas 2252 y 2616) | — |
| 3 | F1-03 | Unificar sanitización de acentos en `core/adb/text_utils.py` | — |
| 4 | RIESGO-VIS-02 | Normalizar timeouts Ollama con constantes en `config.py` | F1-03 |
| 5 | F2-01 | Consolidar `_dump_ui_hierarchy()` (15+ bloques duplicados) | — |
| 6 | F2-02 | Clase `ScreenLayout` para coordenadas calibradas | F2-01 |
| 7 | F2-03 | Formato de retorno estándar `{x, y, method, success, stabilize_ms}` en métodos táctiles | F2-02 |

---

## Protocolo de entrega por tarea

Al completar cada tarea el Worker ADB debe:

1. **Actualizar** `.orchestrator/state/workers/adb/status.md`:
   - Cambiar el estado de la fila correspondiente a `COMPLETADO`.
   - Escribir en la sección "Notas del Worker" cualquier decisión de implementación relevante.

2. **Guardar el diff** en `.orchestrator/state/diffs/`:
   ```bash
   git diff HEAD -- <archivos_modificados> > .orchestrator/state/diffs/<ID>_adb_2026-10-19.diff
   ```

3. **Notificar al Auditor** indicando:
   - ID de la tarea
   - Archivos modificados
   - Ruta al diff guardado

4. **Esperar** `STATUS: APPROVED` antes de marcar la tarea como CERRADA y avanzar.

---

## Contexto adicional (reportado por Worker Visión/IA)

- `RIESGO-VIS-01` es un `AttributeError` silenciado en `controller.py:2252` y `:2616` — provoca fallos silenciosos de detección de íconos. Prioritario.
- `RIESGO-VIS-02`: timeouts inconsistentes (20/25/45/90s) entre módulos pueden dejar flujos en estado intermedio.
- Ambos riesgos fueron detectados durante la FASE 3 y están documentados en `.orchestrator/state/workers/vision/status.md` sección 3.

---

## Tarea siguiente tras completar toda la cola

Despachar FASE 4 al Worker Workflows/Memoria.
