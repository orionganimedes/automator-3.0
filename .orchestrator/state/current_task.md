# TAREA ACTIVA

**ID:** F1-01  
**Fase:** FASE 1 — Corrección de Bugs Críticos  
**Worker asignado:** ADB  
**Estado:** PENDIENTE  
**Fecha asignación:** 2026-10-19

---

## Descripción

Corregir `total_accounts_detected = 15` hardcodeado en `core/adb/controller.py:1894`.  
El valor debe obtenerse dinámicamente del método existente `count_registered_accounts_with_swipe()` o del registro en memoria `accounts_memory`.

## Archivos a modificar

- `core/adb/controller.py` — línea ~1894 en `inspect_and_switch_to_next_account()`

## Contrato de entrega

- El valor `total_accounts_detected` se calcula en tiempo de ejecución, no en compilación.
- Si el conteo falla, se usa un fallback seguro de `len(self.accounts_memory.get_accounts())`.
- No se introduce ningún otro cambio en el mismo commit.
- Auditor ejecuta `python3 -m py_compile core/adb/controller.py` antes de aprobar.

## Tarea siguiente tras aprobación

F1-02 — Corregir URL Ollama hardcodeada en `facebook_engagement.py`
