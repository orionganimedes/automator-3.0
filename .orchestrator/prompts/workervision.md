# ROL: WORKER 2 — MOTOR DE VISIÓN COMPUTACIONAL E INFERENCIA IA

## Ámbito de Código

- Motores de IA: `core/ai/agent.py`, `core/ai/archetypes.py`
- Visión Artificial: `core/vision/icon_matcher.py`, `core/vision/detect_reel_icons.py`
- Catálogo de Iconos: `icon_targets/`

## Directivas Técnicas Obligatorias

1. **Inferencia Visual (Qwen 2.5-VL / Ollama)**:

   - Todo prompt de visión debe exigir salida estricta en formato JSON y procesarse con `_clean_and_parse_json()` para eliminar etiquetas `<think>...</think>` de modelos de razonamiento.
   - Convertir coordenadas normalizadas (`[0.0, 1.0]` o cajas `[ymin, xmin, ymax, xmax]`) a píxeles absolutos validados dentro de los límites físicos del dispositivo.

2. **Template Matching Óptico (OpenCV)**:

   - Utilizar umbrales calibrados (`threshold >= 0.65`) con soporte para patrones recortados y verificación de disposición horizontal (`post_horizontal_row`) o vertical (`reel_vertical_rail`).

3. **Generación de Comentarios Humanos**:

   - Cumplir de forma innegociable con las reglas del sistema:
     - **Cero emojis**: Filtrado estricto mediante `strip_emojis()`.
     - **Máximo 3 líneas**: Control mediante `enforce_max_3_lines()`.
     - **Sin primera persona**: Prohibido usar "yo", "a mí", "me parece", "en mi opinión".
     - **Rotación de Arquetipos**: Rotar entre los 5 arquetipos humanos y los 6 arquetipos regiomontanos consultando la memoria previa para no repetir los últimos 3 utilizados.

---

## Plan de Optimización Asignado a Worker Visión/IA

### FASE 3 — Robustez de Visión e IA

**OPT-VIS-01 (F3-01): Centralizar `_clean_and_parse_json()`**
- Auditar `core/ai/agent.py` y `core/workflows/facebook_engagement.py` para localizar parseos JSON inline que no pasen por `_clean_and_parse_json()`.
- Mover la función a `core/ai/json_utils.py` e importarla desde todos los módulos que la necesiten.
- La función debe manejar: tags `<think>`, bloques ```json```, y respuestas con texto previo al JSON.

**OPT-VIS-02 (F3-02): Módulo de conversión de coordenadas**
- Crear `core/vision/coord_utils.py` con función `normalize_to_pixels(coords, width, height) -> tuple[int, int]`.
- Soportar formatos: `[x_norm, y_norm]`, `[ymin, xmin, ymax, xmax]`, `{"x": float, "y": float}`.
- Validar que el resultado esté dentro de `(0, width)` × `(0, height)`; lanzar `ValueError` si no.
- Reemplazar todas las conversiones inline en `agent.py` y `facebook_engagement.py`.

**OPT-VIS-03 (F3-03): Telemetría de localizadores**
- Después de cada intento de localización (template match o visión IA), registrar en tabla `locators` de SQLite:
  ```
  {timestamp, method, icon_name, success, confidence, device_serial}
  ```
- Crear la tabla en `core/memory/store.py` si no existe con `CREATE TABLE IF NOT EXISTS`.
- Usar esta telemetría para identificar qué íconos tienen baja tasa de detección.

**OPT-VIS-04 (F3-04): Auditoría de umbrales OpenCV**
- Ejecutar búsqueda de todos los llamados a `cv2.matchTemplate` o `minMaxLoc` en el proyecto.
- Elevar a `>= 0.65` cualquier umbral inferior; documentar en comentario si un umbral menor es intencional y por qué.

### Restricciones de Entrega

- Cero parseos JSON inline fuera de `_clean_and_parse_json()`.
- Cero conversiones de coordenadas normalizadas fuera de `coord_utils.py`.
- Telemetría activa sin penalizar latencia: escritura asíncrona o en hilo separado.
