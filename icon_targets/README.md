# Carpeta de Iconos Objetivo (icon_targets)

Esta carpeta almacena las imágenes de referencia para que el Automator localice y toque los iconos en la pantalla de tu teléfono usando Visión por Computadora (OpenCV) y Qwen2.5-VL.

## Cómo agregar nuevos iconos:

Tienes dos opciones muy sencillas:

### Opción 1: Recortar directamente el icono
1. Toma una captura de pantalla donde se vea el icono.
2. Recorta únicamente el icono (con un pequeño borde) y guárdalo como `.png` o `.jpg` en esta carpeta (ejemplo: `mi_icono.png`).

### Opción 2: Marcar el icono con color ROJO (Recomendada)
1. Toma una captura de pantalla completa o parcial.
2. Abre la imagen en Paint, WhatsApp o tu editor favorito.
3. **Dibuja un círculo o recuadro de color ROJO alrededor del icono** que deseas presionar.
4. Guárdalo en esta carpeta (ejemplo: `boton_guardar_marcado.png`).
5. El sistema detectará automáticamente el trazo rojo, aislará el icono dentro del recuadro y lo buscará en la pantalla del teléfono.
