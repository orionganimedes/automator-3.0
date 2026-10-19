# STATUS — WORKER 2: MOTOR DE VISIÓN COMPUTACIONAL E INFERENCIA IA

> Última actualización: 2026-10-19
> Workspace: `/home/orion/orca/workspaces/automator3/workervision`
> Rol: Worker Visión/IA (FASE 3 — Robustez de Visión e IA)

---

## 0. IMPLEMENTACIÓN COMPLETADA (2026-10-19): CLASIFICACIÓN VISUAL DE TIPO DE PUBLICACIÓN

**Flujo solicitado**: abrir link → captura por ADB → Qwen 2.5-VL toma la captura y distingue el
tipo de publicación → se siguen las opciones correspondientes → se guarda → Qwen analiza dónde
están los elementos → ADB hace el touch.

### Arquitectura implementada (cascada de 3 niveles, de barato a caro)

```
ctrl.open_url(target_url, "com.facebook.lite")        ← ADB: am start VIEW
        │
        ▼
wait_for_facebook_content_load()  (poll cada 1.5s, máx 30s)
        │
        ▼ cada iteración:
ctrl.capture_screen(screen.png)                       ← ADB: exec-out screencap
        │
        ▼
detect_facebook_content_type(controller, screen_path, url, ai_agent)
   ├─ NIVEL 1 — URL (cero costo):
   │    /reel|reels|share/r|watch|videos/  → REEL   (definitivo)
   │    /share/p|posts|story|permalink…/   → POST   (definitivo)
   ├─ NIVEL 2 — XML UIAutomator con scoring (cero costo LLM):
   │    reel_score: audio/remix/música (+4), "reel" (+3), riel derecho ≥2 botones (+4)
   │    post_score: "publicación de" (+5)
   │    margen |reel−post| ≥ 3 → vencedor XML (definitivo)
   └─ NIVEL 3 — ARBITRAJE VISUAL QWEN 2.5-VL (nuevo):
        solo si URL ambigua Y score XML inconcluso Y hay señal de contenido:
        ai_agent.classify_facebook_content_type(screen.png, w, h)
        → JSON estricto {loaded, content_type, confidence, reasoning}
        → veredicto DEFINITIVO, cacheado por URL (TTL 60s)
        → anti-flood: máx 1 consulta Qwen / 20s (el bucle polltea cada 1.5s)
        │
        ▼
GUARDADO (una vez por apertura de link, en wait_for_facebook_content_load):
   memory_store.record_content_classification(
       device_serial, url, content_type, classifier, confidence, reasoning)
   → tabla SQLite content_classifications (nueva)
        │
        ▼
RAMIFICACIÓN según tipo (ya existía):
   "reel" → interact_facebook_reel()
   "post" → interact_facebook_standard_post()
        │
        ▼ cada flujo:
   Qwen localiza elementos (like/comment/send/caja) → validación geométrica
   → controller.tap_coordinates(x, y)  ← ADB: input tap x y
```

### Cambios de código

| Archivo | Cambio |
|---|---|
| `core/ai/agent.py` | **Nuevo método** `AIAgent.classify_facebook_content_type(image_path, w, h)`: prompt estricto JSON con reglas visuales (riel derecho x≥80% + ticker de audio = reel; encabezado "Publicación de" + fila horizontal de acciones = post; `loaded:false` si pantalla en blanco). Usa `_clean_and_parse_json`, valida tipo/confianza, nunca lanza. |
| `core/memory/store.py` | **Nueva tabla** `content_classifications` (device_serial, url, content_type, classifier, confidence, reasoning, created_at) + índices + métodos `record_content_classification()` y `recent_content_classifications()`. Esquema auto-migrado por `CREATE TABLE IF NOT EXISTS` al iniciar. |
| `core/workflows/facebook_engagement.py` | `detect_facebook_content_type()`: nuevo parámetro `ai_agent` (resuelto perezosamente), cascada URL→XML→Qwen con margen de decisión 3, caché de veredicto por URL (TTL 60s) y throttle 1 Qwen/20s. `wait_for_facebook_content_load()`: acepta `ai_agent`, registra la clasificación final (con `classifier` y `confidence`) una sola vez por link, y expone `"classifier"` en el resultado. `FacebookPostEngagementManager._run_cycle()`: pasa `ai_agent` y muestra "Formato identificado: REEL (vía qwen2.5vl)" en el estado. |

### Pruebas funcionales (mocks, sin Ollama/dispositivo) — PASARON

1. URL `share/r` → `reel` (sin consultar XML ni visión)
2. URL `share/p` → `post` (sin consultar XML ni visión)
3. XML ambiguo (reel_score=3, post_score=5, margen 2) + Qwen dice reel (conf 0.92) → `reel`
4. Repetición del mismo link → respuesta desde **caché**, 0 consultas Qwen adicionales
5. `wait_for_facebook_content_load` → fila guardada: `{url, reel, qwen2.5vl, 0.92, TEST-SERIAL-001}`
6. URL definitiva → fila guardada con `classifier="url"`

### Garantías operativas

- Qwen **nunca** se consulta sobre pantalla en blanco/spinner (guard `(reel_score+post_score) > 0` + campo `loaded` del veredicto).
- Si Ollama está caído o falla la inferencia, el flujo **no se rompe**: cae al fallback XML (empate → post) como antes.
- El guardado es best-effort con try/externo: nunca interrumpe la interacción.
- Costo adicional máximo: **1 inferencia de visión (~5-15s) por link con URL ambigua**, solo cuando XML no decide.

---

## 1. ANÁLISIS DEL FLUJO QWEN 2.5-VL: DE LA CAPTURA AL TOUCH

### 1.1 Pila de componentes

| Componente | Archivo | Rol |
|---|---|---|
| Config | `config.py` | `OLLAMA_BASE_URL = http://localhost:11434/api/generate`, `VISION_MODEL = qwen2.5vl:latest`, `TEXT_MODEL = qwen3:latest`, `REQUEST_TIMEOUT_SECONDS = 90`, `DEFAULT_SCREEN_IMAGE = screen.png` |
| Agente IA | `core/ai/agent.py` (2758 l) | 7 métodos de visión, `AIAgent` constructor con resolución de modelo instalado (`_resolve_model_name`), limpieza de JSON (`_clean_and_parse_json`) |
| Matching óptico | `core/vision/icon_matcher.py` (437 l) | Template matching multi-escala OpenCV con fallback a visión IA |
| Detección Reels | `core/vision/detect_reel_icons.py` (589 l) | Patrones de color + geometría del riel derecho + fallback Qwen |
| Catálogo | `icon_targets/` | Plantillas PNG (con o sin marcación roja) |
| Telemetría | `core/memory/store.py` | Tabla `locators` (agregada) en SQLite WAL |
| Ejecución táctil | `core/adb/controller.py` → `tap_coordinates(x, y)` | `adb shell input tap x y` vía `subprocess` |
| Workflows | `core/workflows/facebook_engagement.py` (1298 l) | Orquestación de capturas, validaciones y taps |

### 1.2 Cadma de inferencia (paso a paso)

El flujo completo para localizar un elemento y tocarlo:

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. CAPTURA                                                          │
│    controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)           │
│    → adb exec-out screencap > screen.png                            │
├─────────────────────────────────────────────────────────────────────┤
│ 2. CODIFICACIÓN                                                     │
│    open(screen.png, "rb") → base64.b64encode → img_b64              │
│    (imagen completa, sin recorte; se leen h,w con cv2.imread)       │
├─────────────────────────────────────────────────────────────────────┤
│ 3. PROMPT ESTRUCTURADO (contrato JSON estricto)                     │
│    - Declara dimensiones reales: "Screen dimensions: WxH pixels"    │
│    - Describe el objetivo con reglas de exclusión                   │
│      (p. ej. "el contador de reacciones NO es el botón Like")       │
│    - Exige "Return STRICT JSON only" con schema explícito           │
├─────────────────────────────────────────────────────────────────────┤
│ 4. LLAMADA A OLLAMA                                                 │
│    POST http://localhost:11434/api/generate                         │
│    payload: {model, prompt, images:[b64], stream:false,             │
│              format:"json"}                                         │
│    timeout: 90s (agent.py) / 25s (icon_matcher) / 20-45s (resto)    │
│    Model resolution previo: _resolve_model_name() consulta          │
│    /api/tags y si qwen2.5vl no existe, elige cualquier modelo       │
│    local con capability "vision"                                    │
├─────────────────────────────────────────────────────────────────────┤
│ 5. LIMPIEZA Y PARSEO DEL RESPONSE                                   │
│    _clean_and_parse_json(raw):                                      │
│      a) re.sub("", "", raw)   → quita razonamiento│
│      b) quita prefijos ```json / ``` y sufijo ```                  │
│      c) extrae de la primera "{" a la última "}" (texto previo)     │
│      d) json.loads(cleaned)                                        │
│    ⚠️ 2 violaciones activas de parseo inline (ver §4)               │
├─────────────────────────────────────────────────────────────────────┤
│ 6. CONVERSIÓN DE COORDENADAS                                        │
│    El modelo puede responder en 4 formatos distintos:               │
│      a) [x, y] en píxeles absolutos                                 │
│      b) [x, y] normalizado 0.0–1.0                                 │
│      c) [x, y] en escala 0–1000 (convención VL)                     │
│      d) box_2d: [ymin, xmin, ymax, xmax] normalizado               │
│         (con inferencia de ejes según qué valores superan W)        │
│    Heurística inline: si 0<=v<=1 → *dimensión; si >W y W<=1000 →   │
│    *dim/1000; si no → píxeles. Clamp final a [0, W-1]x[0, H-1].     │
│    ⚠️ 8 conversiones inline, 3 duplicadas (ver §4)                  │
├─────────────────────────────────────────────────────────────────────┤
│ 7. VALIDACIÓN GEOMÉTRICA (capa de seguridad pre-touch)              │
│    Cada caso de uso impone zonas permitidas:                        │
│    - Like/Comentar FB: min_valid_y=10% ≤ y ≤ 86%                   │
│    - Send FB: x ≥ 75% ancho; y según estado del teclado:            │
│      kb_open → 45%–68% | kb_closed → ≥75% o 45%–68%                │
│    - Seguir IG: y en 30%–75% (fuera → menor confianza)              │
│    - Reel: riel derecho x ≥ 80%, heart arriba de comment            │
│    Si la coordenada NO cumple la zona → se descarta y cae la        │
│    cadena de fallback (NUNCA se toca ciegamente)                    │
├─────────────────────────────────────────────────────────────────────┤
│ 8. REFINAMIENTO CRUZADO (opcional)                                  │
│    - XML uiautomator dump: ancla el centro exacto de la píldora     │
│      entre los iconos laterales (evita tocar el contador 👍❤ N)     │
│    - Refinamiento óptico: si Qwen dice "reel_vertical_rail", se     │
│      ejecuta detect_reel_icons_pattern para corregir centers        │
│    - Corrección de alineación: si |y_like − y_comment| > 25 px o    │
│      x_like < 10% → se reubica en (16.7%W, y_comment)              │
├─────────────────────────────────────────────────────────────────────┤
│ 9. EJECUCIÓN DEL TOUCH                                              │
│    controller.tap_coordinates(x, y)                                 │
│    → subprocess: [adb, "shell", "input", "tap", str(x), str(y)]     │
│    → post-tap: sleep (1.0–2.5s) + capture_screen de verificación    │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 Los 8 puntos de entrada de visión (Qwen 2.5-VL)

| # | Método | Objetivo | Formato JSON esperado | Validación pre-touch |
|---|---|---|---|---|
| 0 | `AIAgent.classify_facebook_content_type()` *(nuevo)* | Clasificar tipo de publicación: reel vs post | `{loaded, content_type, confidence, reasoning}` | Solo con señal de contenido; throttle 20s; caché TTL 60s; resultado guardado en SQLite |
| 1 | `AIAgent.analyze_screen_vision()` | Feed: extraer hasta 2 posts, autor, snippet, is_ad, should_scroll | `{posts:[{author,content_snippet,is_ad}], should_scroll, reasoning}` | Sin coordenadas (solo decisión de scroll) |
| 2 | `AIAgent.locate_follow_button_with_vision()` | Botón Seguir/Siguiendo de perfil IG | `{found, label, box_2d:[ymin,xmin,ymax,xmax], center_coordinates:[x,y], is_already_following}` | y en 30%–75%; inferencia de ejes de box_2d; clamp |
| 3 | `AIAgent.locate_post_reactions_with_vision()` | Like + Comentar de post FB estándar | `{buttons_visible, like_center:[x,y], comment_center:[x,y], confidence, reasoning}` | y en 10%–86%; same-row check |
| 4 | `AIAgent.locate_blue_send_button_with_vision()` | Flecha azul Send de FB | `{found, send_center:[x,y], confidence, reasoning}` | x ≥ 75%; banda Y según teclado |
| 5 | `AIAgent.analyze_instagram_post_layout()` | Layout IG: reel_vertical_rail vs post_horizontal_row + Like/Comment + already_liked | `{layout_type, like_button:{found,center,already_liked}, comment_button:{found,center}, confidence}` | triple heurística px/0-1/0-1000; clamp; refinamiento óptico si reel |
| 6 | `detect_reel_icons_qwen()` (módulo libre) | Like/Comment/WhatsApp en riel derecho de Reel | `{like_heart:{found,center,is_already_liked}, comment_bubble:{found,center}, whatsapp_bubble:{found,center}}` | escala 0-1000 → px; heart arriba de comment; whatsapp excluido |
| 7 | `IconMatcher._locate_with_vision_ai()` | Fallback genérico cuando el template matching falla (por nombre de ícono) | `{found, x, y, description}` | clamp a [0,W-1]x[0,H-1]; confidence fija 0.85 |

### 1.4 Cómo se produce el TOUCH en cada caso de uso

**A. Post estándar de Facebook** (`interact_facebook_standard_post`):
1. Bucle `CAPTURA → Qwen(locate_post_reactions_with_vision)`:
   - ¿`buttons_visible` y coords válidas? → salir del bucle.
   - ¿No? → swipe suave `(50%W, 72%H) → (50%W, 40%H)` + sleep 1.5s → repetir (máx. 15 slides).
2. Refinamiento XML del centro de la píldora + corrección de alineación Like/Comment.
3. `tap_coordinates(like)` → sleep 1.2s → captura.
4. Caja de comentario: cadena 3 niveles — XML anclaje entre iconos laterales → **Qwen** (prompt `center_coordinates`) → ancla calibrada `(41%W, 90.5%H)` (o `(294,1458)` en 720x1612).
5. `tap_coordinates(input)` → sleep 1.8s (teclado) → `type_comment_naturally()`.
6. **Botón Send** (`_tap_blue_send_button`), cadena 3 niveles:
   1. Qwen con contexto de teclado abierto/cerrado + validación x≥75% y banda Y.
   2. XML uiautomator: nodo clickable a la derecha con texto/desc "publicar|enviar|send|post" (prioridad 10) o solo clickable (prioridad 5).
   3. Calibrado seguro: `(91.6%W, 59.2%H)` kb_open / `(91.6%W, 95%H)` kb_closed.

**B. Reel de Facebook** (`interact_facebook_reel`):
1. Bucle CAPTURA → `detect_reel_icons_qwen` (rielo derecho x≥80%):
   - ¿heart Y comment found? → salir.
   - ¿Uno de los dos? → respaldo `detect_reel_action_buttons` (patrones de color + XML, sin Qwen).
   - ¿Ninguno? → swipe 400ms + sleep 1.5s → repetir.
2. Último recurso: calibrado `(93%W, 50.5%H)` / `(93%W, 58.5%H)`.
3. `tap(like)` → `tap(comment)` → sleep 2.5s (sube la hoja modal) →
4. Localizar caja de texto de la hoja (XML uiautomator, edittext en 35%–70% Y) → `tap(input)` → escribir →
5. `_tap_blue_send_button` (mismo flujo A.6) → sleep 3s → `keyevent 4` (Back) para cerrar modal.

**C. Instagram (post/reel)** (`detect_action_buttons_dynamically` en controller.py):
1. `analyze_instagram_post_layout` (Qwen) → layout + coords + already_liked.
2. Si `reel_vertical_rail`: refinamiento óptico `detect_reel_icons_pattern` (comment ≥0.70, heart ≥0.60).
3. Si no hay coords: patrones OpenCV (score ≥0.50) → template matching (`comment_bubble_whatsapp` 0.50 → `comment_bubble` 0.50 → `whatsapp_image` 0.40 → `like_heart` 0.50, cada uno con fallback a Qwen por nombre).
4. `tap_coordinates` por cada botón localizado.

**D. Botón Seguir de perfil** (`tap_profile_three_dots_menu` y seguidores, controller.py:3933):
1. Template `facebook_profile_dots.png` (0.68).
2. Qwen `locate_follow_button_with_vision` → box_2d/center → clamp → tap.
3. Detecta también estado "ya siguiendo" (campana/label) para no re-tocarlo.

**E. Fallback genérico de plantillas** (`IconMatcher.locate_icon`):
1. Template matching multi-escala (0.70–1.30, TM_CCOEFF_NORMED, threshold por llamada).
2. Si falla y `ai_agent` provisto → Qwen por descripción del ícono → coords px → clamp → tap.
3. Cada intento (template y vision) registra `memory_store.record_locator(strategy, query, hit)`.

### 1.5 Patrón transversal: "IA dice CUÁNDO, geometría dice DÓNDE"

El sistema nunca toca con la coordenada cruda de Qwen sin al menos UNA validación:
- **Zonas permitidas** (bandas de X/Y por elemento y por estado de teclado).
- **Anclaje XML** (uiautomator dump) para refinar el centro exacto.
- **Refinamiento óptico** (OpenCV) cuando Qwen detecta el tipo de layout.
- **Correcciones de alineación** (Like y Comentar deben compartir fila; X mínima de Like).
- **Cadena de fallback** ordenada por precisión: Qwen → XML → OpenCV → coordenada calibrada.
La calibración geométrica final (`int(width * ratio)`) actúa como último recurso "seguro", nunca como valor primario.

### 1.6 Ejecución del tap (nivel ADB)

```python
def tap_coordinates(self, x: int, y: int) -> None:
    cmd = [self.adb_bin, "shell", "input", "tap", str(x), str(y)]
    subprocess.run(cmd, capture_output=True, text=True, check=False)
```
- Coordenadas en **píxeles absolutos del framebuffer del dispositivo** (720x1612 de referencia; la resolución real se lee con `get_screen_size()` y se pasa a todos los prompts de visión).
- Variante de doble toque para likes: `input tap x y && sleep 0.1 && input tap x y` (controller.py:601).
- Los swipes de re-análisis usan `input swipe x1 y1 x2 y2 duración`.

---

## 2. ESTADO DEL PLAN FASE 3 (OPT-VIS-01 a 04)

| OPT | Descripción | Estado | Hallazgos |
|---|---|---|---|
| OPT-VIS-01 | Centralizar `_clean_and_parse_json()` en `core/ai/json_utils.py` | ⏳ PENDING | 2 violaciones: `detect_reel_icons.py:363` (sin limpieza de `think`), `facebook_engagement.py:568` (json.loads crudo). Llamadores externos al método privado: `icon_matcher.py:361`, `controller.py:2274/2640`. |
| OPT-VIS-02 | Módulo `core/vision/coord_utils.py` con `normalize_to_pixels()` | ⏳ PENDING | 8 conversiones inline en 4 archivos; el bloque box_2d del botón Seguir está **duplicado 3 veces** (agent.py:899, 1380, 1540). 4 formatos de coordenada en juego. |
| OPT-VIS-03 | Telemetría por evento en tabla `locators` | ⚠️ PARCIAL | Tabla existe pero es agregada (hits/misses), no por evento. Faltan `confidence`, `device_serial`, `timestamp` por intento. `record_locator()` ya se llama en `icon_matcher.locate_icon` (299/309) pero con commit síncrono (penaliza latencia). `clear_cache()` borra `locators` — la telemetría no debe ser volátil. |
| OPT-VIS-04 | Auditoría de umbrales OpenCV ≥ 0.65 | ⚠️ VIOLACIONES | `icon_matcher.py` default 0.55 (189, 257); `detect_reel_icons.py:49` 0.60; controller.py: 0.40/0.50/0.55/0.58 en 10 llamadas. Los 0.75/0.76 de facebook son intencionales (documentados). |

---

## 3. RIESGOS IDENTIFICADOS EN EL FLUJO

1. **Latencia de inferencia en el camino crítico**: cada `capture → Qwen` tarda hasta 90s; los bucles de swipe pueden invocar Qwen hasta 15 veces. No hay reuso de caché de LLM para localización (la `llm_cache` del store existe pero no se usa en la rama de visión).
2. **Inconsistencia de timeouts**: 90s (agent), 25s (icon_matcher), 20s (facebook), 45s (detect_reel). Un hang parcial deja el flujo en estados intermedios.
3. **Coordinadas no validadas con `ValueError`**: solo se hace clamp silencioso; una respuesta corrupta (p. ej. `x: "abc"`) lanza excepción genérica capturada y se cae al fallback sin diagnóstico.
4. **Tres copias del bloque box_2d** = riesgo de drift: un fix aplicado a una copia no se aplica a las otras.
5. **Parseo inline sin `think` strip** en detect_reel_icons: un modelo de razonamiento devolvería JSON inválido y la rama de Reels fallaría al parseo.
6. **Telemetría agregada no identifica íconos con baja detección por dispositivo** (falta device_serial por evento).

## 4. SIGUIENTE ACCIÓN

Arranque de implementación en orden de riesgo:
1. **OPT-VIS-01** (mecánico, sin cambio de comportamiento).
2. **OPT-VIS-02** (incluye de-duplicación del bloque box_2d).
3. **OPT-VIS-03** (nueva tabla `locator_events` + escritura asíncrona en hilo daemon).
4. **OPT-VIS-04** (elevar umbrales + documentar excepciones intencionales).

**Status: EN ESPERA de aprobación para iniciar OPT-VIS-01.**
