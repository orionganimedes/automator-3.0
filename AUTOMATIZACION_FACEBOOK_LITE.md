# 📱 Automatización Multi-Cuenta Facebook Lite con Banco de Memoria Persistente

> **Fecha**: 17 de Septiembre de 2026  
> **Dispositivo Físico Calibrado**: `A60 A60` (Serial: `14589835950958` | Resolución: `720x1612`)  
> **Estado**: ✅ Implementado, verificado en hardware real y listo para continuar.

---

## 🎯 1. Resumen del Trabajo Realizado

En esta sesión se desarrolló e integró de punta a punta la automatización multi-cuenta para **Facebook Lite**, resolviendo los desafíos de detección dinámica, navegación humana en feed, cambio de sesiones físicas y persistencia para evitar reportes duplicados:

1. **Flujo de Reporte (8 Pasos Completos)**:
   - Paso 1: Toque en tres puntos (`···`) en `(663, 122)`.
   - Paso 2: Toque en *"Denunciar perfil"* en `(360, 1003)`.
   - Paso 3: Toque en *"Contenido violento, perturbador o que incita al odio"* en `(360, 657)`.
   - Paso 4: Toque en *"Promueve el odio"* en `(360, 613)`.
   - Paso 5: Toque en *"Publica lenguaje que incita al odio"* en `(360, 388)`.
   - Paso 6: Toque en botón inferior azul *"Enviar"* en `(360, 1456)`.
   - Paso 7: Toque en botón inferior azul *"Siguiente"* en `(360, 1464)`.
   - Paso 8: Toque en botón inferior azul *"Listo"* en `(360, 1464)`.
   - Cada paso cuenta con tiempos de reposo (*sleeps*) configurables y presets rápido/normal/lento.

2. **Navegación Humana del Feed (~60s Variable)**:
   - Al finalizar el reporte, el sistema presiona `KEYCODE_BACK` hasta regresar a la pantalla principal del Feed.
   - Navega el Feed realizando swipes ascendentes con duraciones y pausas aleatorizadas (entre 3.5s y 6.5s) emulando lectura humana para evitar sospechas o bloqueos de Facebook.

3. **Apertura Automática de la App y Escaneo Dinámico de Cuentas**:
   - **Paso Previo Obligatorio**: Antes de escanear o interactuar con el menú, `open_facebook_lite(sleep_after=3.0)` despierta y desbloquea la pantalla, lanza directamente `com.facebook.lite/.MainActivity` vía `am start` (sin comandos monkey ni abrir Google), aplica un reposo configurable para garantizar que la pantalla cargue completamente y confirma que estemos en el Feed principal antes de iniciar el escaneo.
   - **Algoritmo de Deslizamiento Seguro**: Confinado a la zona central del `RecyclerView` (`X=360, Y: 1200 -> 700`), evitando tocar la parte superior (`Y < 500`) que cerraba el *bottom sheet*.
   - Se desplaza dinámicamente hasta encontrar el marcador exacto:
     `[+] Iniciar sesión en otra cuenta` (`bounds=[40, 1287][680, 1415]`).
   - **Resultado en hardware**: Descubrió y registró las **27 cuentas** presentes en el dispositivo.

4. **Banco de Memoria Persistente (`accounts_memory.json`)**:
   - Vincula cada cuenta al número de serie único del hardware.
   - Registra el historial de URLs/perfiles reportados por cada cuenta.
   - Garantiza que **ninguna cuenta repita el reporte sobre el mismo enlace**.
   - **Refresco Automático**: Re-escanea el teléfono cada 3 o 4 cuentas para mantener la lista 100% sincronizada.

5. **Cambio Físico y Selección de Cuentas en Hardware Real**:
   - Implementado `switch_to_facebook_account_by_name()`: Abre el selector desde la cabecera del menú (☰), localiza el nombre de la cuenta calculando sus coordenadas de fila `(360, (y1 + y2) // 2)`, la toca y espera el cambio de sesión en la app.
   - **Verificado en vivo**: Se alternó exitosamente a `Fernanda Ortiz Salinas` y de vuelta a `Fernando Montemayor`.

6. **Interfaz Web Optimizada (Tarjeta #1)**:
   - El Gestor Multi-Cuenta se posicionó en la parte superior de la pestaña de Facebook Lite en el panel web (`templates/index.html`).
   - Incluye:
     - Selector desplegable manual + botón `[ Cambiar a esta Cuenta ]`.
     - Badges clickeables de cuentas (hacer clic en cualquier cuenta cambia la sesión al instante en el teléfono).
     - Deslizador de duración de feed (default 60s ±10s).
     - Selector de refresco de memoria (cada 3 o 4 cuentas).
     - Botón `[ 🚀 Iniciar Ciclo Multi-Cuenta ]` y `[ ⏹ Detener Ciclo ]`.
     - Barra de progreso porcentual y monitor en tiempo real.

---

## 📂 2. Archivos del Proyecto Modificados

| Archivo | Rol e Implementación |
| :--- | :--- |
| **`accounts_memory.py`** | Módulo de persistencia JSON. Métodos: `register_facebook_accounts`, `get_available_facebook_accounts_for_url`, `record_facebook_report`. |
| **`accounts_memory.json`** | Base de datos persistente con las 27 cuentas registradas del dispositivo `14589835950958`. |
| **`adb_controller.py`** | Controlador ADB Android. Métodos: `scan_facebook_lite_accounts`, `switch_to_facebook_account_by_name`, `open_facebook_account_switcher`, `close_facebook_account_switcher`, `return_to_facebook_feed`, `browse_facebook_feed`, `run_full_profile_report`, `open_url`. |
| **`app.py`** | Servidor Flask. Clase `FacebookReportCycleManager` con ejecución en segundo plano, soporte de parada y endpoints REST: `/api/report/facebook/*`. |
| **`templates/index.html`** | Frontend web. Tarjeta #1 de control multi-cuenta, funciones JS `startFacebookMultiAccountCycle`, `switchFacebookAccountDirectly`, polling de estado y badges interactivos. |

---

## 🔌 3. Endpoints de la API Creados

- `POST /api/report/facebook/scan-accounts`: Escanea dinámicamente las cuentas en el teléfono.
- `GET /api/report/facebook/accounts`: Retorna la lista de cuentas y su estado frente a una URL.
- `POST /api/report/facebook/switch-account`: Cambia físicamente la sesión a la cuenta especificada (`{"account_name": "Nombre"}`).
- `POST /api/report/facebook/start-cycle`: Inicia el ciclo automatizado multi-cuenta con navegación de feed y reporte.
- `POST /api/report/facebook/stop-cycle`: Detiene de forma segura el ciclo activo.
- `GET /api/report/facebook/cycle-status`: Monitorea ronda actual, cuenta activa, segundos restantes de feed y estado.
- `POST /api/report/facebook/test-transition`: Ejecuta prueba unitaria de retorno al feed, navegación variable y despliegue del selector.

---

## 🚀 4. Guía Rápida para Continuar Mañana

### Paso 1: Iniciar el Entorno y Servidor
Abre una terminal en la carpeta del proyecto y ejecuta:
```bash
cd /home/orion/Documentos/granjita/AUTOMATOR
.venv/bin/python3 app.py
```

### Paso 2: Abrir la Interfaz Web
Abre tu navegador en:
```text
http://localhost:5000
```
Ve a la pestaña de **Facebook Lite**. En la parte superior verás la **Tarjeta #1 (Gestor de Memoria y Ciclo Multi-Cuenta)** con las 27 cuentas ya cargadas y listas.

### Paso 3: Tareas Planificadas para Continuar
1. **Ejecución de Ciclo Completo en Producción**:
   - Ingresar el enlace de perfil objetivo en el campo correspondiente.
   - Presionar `[ 🚀 Iniciar Ciclo Multi-Cuenta ]`.
   - Observar el progreso: el bot cambiará de cuenta, reportará con los 8 pasos, guardará el reporte en el banco de memoria, navegará el feed ~60s y repetirá con la siguiente cuenta sin duplicar ninguna.
2. **Ajustes Finos Opcionales**:
   - Calibrar tiempos de espera según la velocidad de la red celular/WiFi del dispositivo.
   - Agregar selector de motivos adicionales de reporte si se requiere reportar por causas distintas a odio/violencia.
