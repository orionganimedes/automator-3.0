# Plan de implementación — Memoria del agente (automator3)

_Creado: 2026-10-01_

## Objetivo

Reemplazar los mecanismos actuales de memoria (JSON monolítico + Markdown parseado con regex)
por un sistema estructurado, consultable y con expiración, que además reduzca coste/latencia
de Ollama (`llm_cache`) y alimente la toma de decisiones del agente (rotación de arquetipos,
deduplicación de comentarios, estadísticas de localizadores).

**Enfoque elegido: Opción A (quick wins) + Opción B (SQLite).** Las opciones C (embeddings/RAG)
y D (híbrida) quedan como fase opcional al final.

---

## Inventario de la memoria actual (punto de partida)

| Sistema | Archivo(s) | Problemas a resolver |
|---|---|---|
| Estado de cuentas | `accounts_memory.py` → `accounts_memory.json` | Reescribe todo el JSON por mutación; `used_on_posts` sin límite; timestamps string; lógica `accounts`/`facebook_accounts` duplicada; singleton global difícil de testear |
| Persona + log del agente | `agents.md`, `agent.md` | Mezcla prompt y memoria; append ilimitado; parseo regex frágil (`load_persistent_memory`); escritura en 3 destinos |
| Resultados CLI | `analysis_results.json` | No es memoria (queda fuera) |
| Cache/estadísticas | — | No existe `llm_cache`, ni stats de localizadores, ni historial de runs |

---

## Fase 0 — Medir y congelar el baseline

Antes de tocar nada, registrar métricas para comparar después.

- [ ] Medir tamaño actual y tiempo de `save()`/`load()` de `accounts_memory.json`.
- [ ] Contar entradas: nº de dispositivos, cuentas, tamaño medio de `used_on_posts`, tamaño de `agents.md`.
- [ ] Guardar copia de seguridad de `accounts_memory.json` y `agents.md` en `debug/backup/` antes de migrar.

**Criterio de aceptación:** cifras "antes" documentadas en un `debug/benchmark_memoria.md`.

---

## Fase 1 — Quick wins sin cambiar arquitectura (Opción A)

Cambios seguros, reversibles, que resuelven la fragilidad y el crecimiento.

1. **Timestamps numéricos**
   - [ ] Reemplazar `datetime.now().strftime("%Y-%m-%d %H:%M:%S")` por `int(time.time())` (epoch) en `accounts_memory.py`.
   - [ ] Ordenar por `last_used` con número (no string) y eliminar el fallback `"1970-01-01"` (línea ~165).

2. **Podar `used_on_posts` y `interaction_history`**
   - [ ] Cap a `used_on_posts` (p.ej. últimos 200) por cuenta.
   - [ ] TTL opcional para URLs (p.ej. expiran tras 30 días) → evita crecimiento ilimitado.
   - [ ] `interaction_history`: bajar el cap de 50 → 20 con resumen (ver Fase 4).

3. **Separar persona de memoria en `agents.md`**
   - [ ] `agents.md` queda **solo** como prompt/persona (arquetipos, reglas).
   - [ ] El log de reflexión pasa a `data/agent_log.jsonl` (una línea JSON por interacción, esquema fijo).
   - [ ] `record_agent_memory()` (ai_agent.py:2336) escribe **un solo destino** JSONL; eliminar la triple escritura a `agents.md`/`agent.md`.
   - [ ] `load_persistent_memory()` (ai_agent.py:691) deja de usar regex: lee las últimas N líneas del JSONL.

4. **Migración y versión del esquema JSON**
   - [ ] Añadir `schema_version` y una función `migrate()` que actualice registros viejos al cargar.

**Criterio de aceptación:** el archivo de cuentas ya no crece sin límite; `agents.md` no recibe más appends de log; el parseo de memoria no depende de regex.

---

## Fase 2 — `memory_store.py` con SQLite (Opción B, núcleo)

Nuevo módulo que será la fuente de verdad. Ruta: `data/agent_memory.db`, modo **WAL**.

### Esquema propuesto

| Tabla | Columnas clave | Propósito |
|---|---|---|
| `devices` | serial (PK), model, first_seen, last_seen | Un registro por teléfono |
| `accounts` | id (PK), device_serial (FK), kind ('instagram'/'facebook'), index, username, last_used | Unifica `accounts` + `facebook_accounts` |
| `interactions` | id, account_id, post_url, action, comment_text, archetype, success, latency_ms, created_at | Reemplaza `used_on_posts` + `interaction_history` |
| `agent_reflections` | id, account_id, caption, archetype, visual_element, comment, thought, created_at | Reemplaza el log de `agents.md` |
| `runs` / `steps` | run_id, step, action, status, duration_ms, started_at | Métricas de cada ciclo |
| `locators` | strategy ('xml'/'template'/'vision'), query, hits, misses, last_used | Tasa de acierto por estrategia |
| `template_calib` | device_serial, template, scale, offset, success | Calibración recordada por dispositivo |
| `llm_cache` | model, prompt_hash, response, created_at, ttl | Cache de respuestas de Ollama |

### Tareas

- [ ] Crear `memory_store.py`: conexión por hilo (SQLite no comparte conexiones entre threads), `with` context manager, `PRAGMA journal_mode=WAL`, `busy_timeout`.
- [ ] Función `init_schema()` idempotente (migraciones numeradas como lista de SQL).
- [ ] `llm_cache`: clave `(model, sha256(prompt_bytes))`, TTL configurable (p.ej. 24 h) → reutiliza respuestas idénticas y reduce llamadas a Ollama.
- [ ] `locators`: método `record_locator_hit/miss(strategy, query)` para alimentar estadísticas.
- [ ] Script único `scripts/import_memory.py` que migre `accounts_memory.json` → SQLite (idempotente, con verificación de conteo).

**Criterio de aceptación:** el DB se crea con WAL; la migración importa el 100% de dispositivos/cuentas sin pérdidas (test de conteo); `llm_cache` devuelve la misma respuesta para un prompt idéntico sin llamar a Ollama.

---

## Fase 3 — Integración y API

- [ ] `accounts_memory.py` se convierte en capa fina sobre `memory_store.py` (mantener las firmas de módulo `record_interaction`, `get_device_summary`, etc. para no romper `app.py` y `facebook_post_engagement.py`).
- [ ] `record_agent_memory` / `load_persistent_memory` (ai_agent.py) leen/escriben en `agent_reflections`.
- [ ] Nuevos endpoints:
  - [ ] `GET /api/memory/stats` → resumen (cuentas, interacciones, hits/misses por localizador).
  - [ ] `GET /api/memory/locators` → tasa de acierto por estrategia (para reordenar estrategias XML→plantilla→visión).
  - [ ] `GET /api/memory/interactions?device=...&days=...` → historial consultable.

**Criterio de aceptación:** `app.py` sigue funcionando sin cambios en sus llamadas a `accounts_memory`; los nuevos endpoints devuelven JSON correcto.

---

## Fase 4 — Estrategias de memoria del agente (cómo *recordar* para decidir mejor)

Esto define **qué** memoriza el agente y **cómo** lo usa para mejorar el proceso.

### 4.1 Qué guardar por interacción (contrato)
- Contexto del post (caption resumido), arquetipo usado, comentario emitido, elemento visual referenciado, cuenta, **resultado** (éxito/fallo) y latencia.

### 4.2 Rotación de arquetipos guiada por datos (no por regex)
- [ ] El "siguiente arquetipo" se calcula con SQL: el menos usado recientemente, respetando "no repetir los últimos N".
- [ ] Registrar qué arquetipo se usó por cuenta y por post para distribuir uniformemente.

### 4.3 Deduplicación de comentarios
- [ ] Normalización de texto (minúsculas, sin signos) y hash para detectar duplicados exactos.
- [ ] *(Opcional, Fase C)* embeddings para duplicados semánticos (casi-iguales).

### 4.4 Presupuesto de memoria + compactación
- [ ] Conservar últimos N reflexiones por cuenta; cap global.
- [ ] *(Opcional)* compactar reflexiones viejas en un resumen vía LLM (resumen rotativo) en vez de borrarlas.

**Criterio de aceptación:** la rotación ya no depende de `agents.md`; un mismo post no recibe el mismo comentario dos veces; la memoria se mantiene acotada en tamaño.

---

## Fase 5 — Calidad y operación

- [ ] Tests con pytest: SQLite `:memory:`, fixtures con los dumps XML/PNG existentes.
- [ ] `git init` + `.gitignore` (excluir `data/*.db`, `debug/`, PNG/XML de depuración).
- [ ] Mover PNG/XML de debug a `debug/`; borrar `index.html.bak_monolithic`.
- [ ] Documentar el esquema y las decisiones en este plan (o en `README.md`).

---

## Orden de ejecución recomendado

1. **Fase 0** → 2. **Fase 1** (quick wins, sin riesgo) → 3. **Fase 2** (SQLite) → 4. **Fase 3** (integración) → 5. **Fase 4** (estrategias) → 6. **Fase 5** (calidad).

Las Fases 1 y 2 son independientes en su mayor parte; la Fase 1 se puede desplegar ya, y la Fase 2 se puede construir en paralelo.

---

## Nota de alcance

Este plan **no incluye** el ciclo de denuncias multicuenta (`FacebookReportCycleManager`), la
navegación "anti-detección" ni la generación de comentarios que imitan humanos. Esos módulos
siguen estando fuera de alcance según `PLAN_IMPLEMENTACION.md` y se recomienda retirarlos.
El esquema de memoria aquí propuesto sirve igual para el núcleo técnico legítimo (control ADB,
visión local, matching de iconos, dashboard) sin depender de ellos.
