# ROL: WORKER 3 — FLUJOS DE TRABAJO Y ALMACÉN DE MEMORIA

## Ámbito de Código

- Workflows: `core/workflows/facebook_engagement.py`, `core/workflows/manual.py`
- Memoria y Persistencia: `core/memory/accounts.py`, `core/memory/store.py`, `scripts/import_memory.py`

## Directivas Técnicas Obligatorias

1. **Máquinas de Estado de Workflows**:

   - Diseñar flujos con confirmación previa y posterior a cada acción (verificar presencia de botones antes de tocar y validar cambio de estado después de tocar).
   - Incluir pausas aleatorizadas entre deslizamientos y lecturas para simular comportamiento humano natural frente a la detección de plataformas.

2. **Integridad de Memoria y Concurrencia**:

   - Mantener sincronización atómica dual: actualizar `data/accounts_memory.json` y la base de datos SQLite WAL (`data/agent_memory.db`).
   - Evitar interacciones duplicadas: antes de operar sobre un post, verificar que la cuenta actual no figure en la lista `used_on_posts`.
   - Registrar la telemetría de éxito o fallo de cada selector en la tabla `locators` para calibrar las estrategias futuras del agente.

---

## Plan de Optimización Asignado a Worker Workflows/Memoria

### FASE 1 — Bug Crítico

**OPT-WF-01 (F1-02): Corregir URL Ollama hardcodeada**
- Archivo: `core/workflows/facebook_engagement.py` ~línea 10068
- Reemplazar la URL literal `"http://127.0.0.1:11434/api/generate"` por `config.OLLAMA_BASE_URL`.
- Verificar que `config.py` exporte `OLLAMA_BASE_URL` con valor por defecto `"http://127.0.0.1:11434"`.

### FASE 4 — Integridad de Workflows y Memoria

**OPT-WF-02 (F4-01): Confirmación pre/post en flujo Facebook**
- En `facebook_engagement.py`, cada acción de tap sobre botón (Like, Comment, Follow) debe:
  1. Verificar presencia del botón antes de tocar (screenshot + detección o XML check).
  2. Verificar cambio de estado después de tocar (ej. botón Like cambia de outline a filled).
- Si la verificación post falla, registrar como `success=False` en telemetría y pasar a la siguiente cuenta sin reintentar.

**OPT-WF-03 (F4-02): Eliminar `sleep` estáticos**
- Buscar todos los `time.sleep(N)` con N constante en `facebook_engagement.py` y `manual.py`.
- Reemplazar por `time.sleep(random.uniform(min, max))` donde los rangos sean razonables:
  - Pausa entre acciones: `(1.5, 3.5)`
  - Pausa entre posts: `(4.0, 8.0)`
  - Pausa de lectura orgánica: `(8.0, 15.0)`

**OPT-WF-04 (F4-03): Unificar lógica write-through en `accounts.py`**
- Identificar las 3+ implementaciones de `register_device`/`register_account` con lógica SQLite+JSON duplicada.
- Extraer método privado `_write_account_atomic(device_serial, account_data)` que encapsule la escritura dual.
- Todos los métodos públicos delegan en `_write_account_atomic`.

**OPT-WF-05 (F4-04): Migrar `agents.md` legacy a solo SQLite**
- En `load_persistent_memory()`, eliminar el bloque de parseo de Markdown fallback.
- La fuente de verdad única es SQLite WAL en `data/agent_memory.db`.
- Ejecutar `scripts/import_memory.py` una vez para migrar datos históricos antes de eliminar el fallback.
- Documentar en `PLAN_MEMORIA_AGENTE.md` que la migración está completa.

**OPT-WF-06 (F4-05): Guard de interacción duplicada**
- Antes de ejecutar Like/Comment sobre cualquier post, consultar:
  ```python
  store.has_interacted(account_id=current_account, post_url=post_url)
  ```
- Si retorna `True`, saltar al siguiente post sin tocar la pantalla.
- Crear el método `has_interacted` en `core/memory/store.py` si no existe.

### Restricciones de Entrega

- Cero `time.sleep(N)` con N constante tras el refactor.
- Cero parseos de `agents.md` en el flujo de carga de memoria.
- `python3 -m py_compile core/workflows/facebook_engagement.py` sin errores.
