# Plan de implementación — Refactor de carpetas, widget de cuentas y personajes personalizados

_Creado: 2026-10-01_

Complementa a `PLAN_MEMORIA_AGENTE.md` (memoria) con tres frentes que pidió el usuario:

1. Reorganización de carpetas (el proyecto está plano y desordenado).
2. Cambio de cuenta como **widget reutilizable**.
3. Generación de comentarios con **personaje personalizado** → 3 opciones basadas en arquetipos.

---

## Frente 1 — Reorganización de carpetas

### Estado actual
- Módulos Python sueltos en la raíz, mezclados con PNG/XML de debug, plantillas, iconos y planes.
- `app.py` (4.681 líneas), `adb_controller.py` (4.282), `ai_agent.py` (2.405): megamódulos con responsabilidades mezcladas.
- `adb_controller.py` mezcla control ADB + cuentas + Facebook + flujo de denuncias.

### Estructura objetivo

```text
automator3/
├── app.py · config.py · main.py · start.sh · requirements.txt
├── core/
│   ├── adb/       controller.py · accounts.py · facebook.py · report.py · utils.py
│   ├── ai/        agent.py · archetypes.py · comments.py
│   ├── vision/    icon_matcher.py · detect_reel_icons.py · client.py
│   ├── memory/    store.py · accounts.py · personas.py
│   └── workflows/ facebook_engagement.py · manual.py
├── web/
│   ├── __init__.py            # create_app()
│   └── routes/    dashboard.py · comments.py · accounts.py · facebook.py · instagram.py · report.py · device.py
├── templates/  components/ · tabs/ · widgets/
├── static/js/  widgets/ · (por pestaña)
├── data/ · debug/ · scripts/ · tests/ · icon_targets/
```

### Tareas (por fases, sin romper nada)

- [ ] **Fase 1.1** — Crear `core/`, `web/`, `data/`, `debug/`, `scripts/`, `tests/`; mover PNG/XML de debug a `debug/`; `git init` + `.gitignore`.
- [ ] **Fase 1.2** — Extraer `archetypes.py` (mover `HUMAN_ARCHETYPES`, `REGIOMONTANO_ARCHETYPES`, `NARRATIVE_GUIDELINES` de `ai_agent.py` a `core/ai/archetypes.py`).
- [ ] **Fase 1.3** — Partir `adb_controller.py` en mixins: `accounts.py` (cambio de cuenta), `facebook.py`, `report.py` (tap_report_step*). `controller.py` mantiene la clase `AndroidController` heredando los mixins → mismo `import` funciona.
- [ ] **Fase 1.4** — Partir `app.py` en Blueprints (`web/routes/*`) con `create_app()`; `app.py` queda como entrypoint delgado.
- [ ] **Fase 1.5** — Mover `icon_matcher.py`, `detect_reel_icons.py` a `core/vision/`; `facebook_post_engagement.py`, `manual_automation.py` a `core/workflows/`; `accounts_memory.py` + SQLite a `core/memory/`.
- [ ] **Fase 1.6** — Re-imports en todos los módulos + verificación (`python -c "import app"` sin errores).

**Criterio de aceptación:** `python app.py` arranca igual; todos los endpoints siguen respondiendo; raíz solo contiene archivos de nivel superior.

---

## Frente 2 — Widget de cambio de cuenta

### Estado actual
- Backend disperso en `adb_controller.py`: `inspect_and_switch_to_next_account` (:1562), `switch_to_account_by_index` (:1719), `open_facebook_account_switcher` (:3198), `switch_to_facebook_account_by_name` (:3696), `scan_facebook_lite_accounts` (:3536).
- Frontend duplicado en `tab_facebook.html` (:41–87) y `tab_instagram.html` (:23+), cada uno con JS propio (`facebook.js`, `instagram.js`).

### Diseño del widget

1. **Backend unificado**
   - [ ] Endpoint `GET /api/accounts/<platform>` → lista de cuentas desde `core/memory` (SQLite).
   - [ ] Endpoint `POST /api/accounts/<platform>/switch` → cambia a la cuenta indicada.
   - [ ] Endpoint `POST /api/accounts/<platform>/scan` → escanea cuentas del dispositivo.

2. **Componente reutilizable**
   - [ ] `templates/widgets/account_switcher.html` (macro Jinja2: botón escanear, dropdown "cambiar a", badge de cuenta activa, lista).
   - [ ] `static/js/widgets/account_switcher.js` → `AccountSwitcher.init(containerId, {platform})` que consulta los endpoints.

3. **Integración**
   - [ ] Reemplazar el bloque de `tab_facebook.html` por `{% include 'widgets/account_switcher.html' with platform='facebook' %}`.
   - [ ] Igual en `tab_instagram.html` con `platform='instagram'`.
   - [ ] Borrar el JS de cuentas duplicado de `facebook.js`/`instagram.js`.

**Criterio de aceptación:** el mismo widget funciona en ambas pestañas y se puede incluir en cualquier pestaña futura con una sola línea; los cambios de cuenta siguen funcionando.

---

## Frente 3 — Personaje personalizado → 3 opciones por arquetipo

### Estado actual
- `generate_human_comment()` (ai_agent.py:1371) genera **1** comentario con arquetipo fijo.
- `preview_human_archetypes()` (:1616) genera para los 5, sin personalización.
- Arquetipos hardcodeados en `HUMAN_ARCHETYPES` (:599); posturas en `NARRATIVE_GUIDELINES` (:1347).

### Diseño de la funcionalidad

**Modelo mental:** la **persona define la voz** (edad, tono, regionalismo, vocabulario); el **arquetipo define el ángulo** (pregunta/afirmación/imagen mental, elementos visuales). Combinar ambos produce 3 opciones distintas con la misma identidad.

1. **Backend**
   - [ ] Nuevo método `generate_persona_comments(persona, caption, image_path="", narrative="a_favor", count=3)` en `core/ai/comments.py`.
   - [ ] Selecciona `count` arquetipos por rotación de memoria (sin repetir los últimos N; ver `PLAN_MEMORIA_AGENTE.md` Fase 4).
   - [ ] Prompt por opción: `"Eres {persona}. Comenta siguiendo el ángulo del arquetipo '{arquetipo}': {descripción}. Reglas: 3 líneas, 0 emojis, sin 'yo', neutro de género, mención orgánica del elemento visual."`
   - [ ] Devuelve `[{texto, arquetipo, persona}, ...]` (3 opciones).

2. **API**
   - [ ] `POST /api/comments/generate-persona` con body `{persona, caption, image_path?, narrative?}` → `{options: [...]}`.

3. **Frontend**
   - [ ] Añadir campo "¿Qué personaje quieres?" en `tab_comments.html` (junto a narrativa/arquetipo).
   - [ ] Botón "Generar 3 opciones" que llame al endpoint y pinte 3 tarjetas seleccionables (listas para enviar).

**Criterio de aceptación:** escribo un personaje (p.ej. "un abuelo nostálgico") y recibo 3 comentarios distintos, todos con la voz del personaje, cada uno con un ángulo de arquetipo distinto.

### Ejemplo esperado
- Persona: *"un abuelo que habla con nostalgia de sus recuerdos"*
- Opción 1 (Heredero): *"Hay gestos que no se aprenden de la noche a la mañana, vienen directo de la raíz."*
- Opción 2 (Caminante): *"El sendero siempre tiene sus lecciones si se aprende a caminar sin apresurar el paso."*
- Opción 3 (Testigo Silencioso): *"Hay momentos donde la imagen habla sola y sobran las palabras."*

---

## Orden de ejecución recomendado

1. **Frente 1 (Fase 1.1–1.2)** — estructura + extraer arquetipos (base para el Frente 3).
2. **Frente 3** — personaje personalizado (valor visible inmediato).
3. **Frente 2** — widget de cuentas (depende de la memoria SQLite del plan anterior y de la reorganización).
4. Completar **Frente 1 (1.3–1.6)** — partir `adb_controller.py` y `app.py`.

---

## Nota de alcance

Este plan mantiene **fuera de alcance** (igual que `PLAN_IMPLEMENTACION.md` y `PLAN_MEMORIA_AGENTE.md`):
el ciclo de denuncias multicuenta, la navegación "anti-detección" y la generación de comentarios
que imitan humanos para evadir moderación. La funcionalidad de "personaje personalizado" aquí
propuesta sirve para un uso legítimo (estudios de tono, narrativa creativa, generación de contenido)
y no depende de esos módulos.
