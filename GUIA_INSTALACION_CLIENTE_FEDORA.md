# 📘 Guía de Instalación y Despliegue para Cliente (Fedora Linux)
### Sistema de Automatización Android con IA Local (AUTOMATOR)

Bienvenido. En este documento encontrarás las instrucciones paso a paso para poner en marcha el sistema en una computadora con **Fedora Linux**. Solo debes copiar y pegar los comandos indicados en la terminal.

---

## 📌 Requisitos Previos
Antes de empezar, asegúrate de contar con:
1. Una computadora con **Fedora Linux** y conexión a internet.
2. Un teléfono Android con su **cable USB original o de datos** (no cables que solo sirvan para cargar).
3. La carpeta con los archivos de este proyecto (**AUTOMATOR**).

---

## 🚀 Paso 1: Instalar los Programas Base en Fedora

Abre la aplicación **Terminal** en Fedora y ejecuta el siguiente comando para instalar las herramientas necesarias del sistema:

```bash
sudo dnf install -y python3 python3-pip git android-tools android-udev-rules libglvnd-glx curl
```

> **¿Qué hace esto?**
> * `android-tools`: Instala `adb`, que es el programa que comunica la computadora con el celular.
> * `android-udev-rules`: Le da permisos a tu usuario para controlar el celular por USB sin errores de acceso.
> * `libglvnd-glx`: Soporte gráfico necesario para la visión por computadora (OpenCV).
> * `python3` y `python3-pip`: El motor para ejecutar la aplicación y sus dependencias.

---

## 🧠 Paso 2: Instalar y Encender la Inteligencia Artificial (Ollama)

El sistema utiliza modelos de Inteligencia Artificial locales (no envían datos a la nube ni requieren pagar suscripciones).

1. **Instala Ollama en la computadora**:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

2. **Inicia y habilita el servicio para que esté siempre activo**:
   ```bash
   sudo systemctl enable --now ollama
   ```

3. **Descarga los modelos de IA necesarios**:
   ```bash
   ollama pull qwen2.5vl:latest
   ollama pull qwen3:latest
   ```
   *(Espera a que termine de descargar el 100% de ambos modelos. Esto solo se hace la primera vez).*

---

## 📱 Paso 3: Configurar el Celular Android

Para que el programa pueda interactuar con el teléfono, debes habilitar el modo desarrollador:

1. **Activar Opciones de Desarrollador**:
   * Ve a **Ajustes** > **Acerca del teléfono** > **Información de software**.
   * Presiona **7 veces seguidas** sobre **"Número de compilación"** hasta que aparezca el mensaje: *"¡Ya eres desarrollador!"*.

2. **Configurar los permisos de depuración**:
   * Regresa a **Ajustes** > **Sistema** (o **Ajustes adicionales**) > **Opciones de desarrollador**.
   * Activa **Depuración por USB**.
   * ⚠️ **MUY IMPORTANTE (Si tu celular es Xiaomi, Redmi, Poco, Realme u Oppo):**
     * Debes activar obligatoriamente **Depuración USB (Ajustes de seguridad)**.
     * *(Si no activas esta casilla, el sistema podrá ver la pantalla pero el teléfono bloqueará los toques automáticos).*
   * Activa **Pantalla activa durante la carga** (o *Stay Awake*), para evitar que el teléfono se bloquee.

3. **Conectar el celular a la computadora**:
   * Conéctalo con el cable USB.
   * En la pantalla del celular aparecerá una ventana preguntando: *"¿Permitir depuración por USB?"*.
   * Marca la casilla: **☑️ "Permitir siempre desde esta computadora"** y presiona **Permitir / Aceptar**.

4. **Verificar que la computadora reconoce el celular**:
   En la terminal de Fedora ejecuta:
   ```bash
   adb devices
   ```
   Debe aparecer una línea con un código y la palabra `device`. Ejemplo:
   ```text
   List of devices attached
   14589835950958    device
   ```
   *(Si dice `unauthorized`, revisa la pantalla del celular y acepta el permiso).*

---

## 🐍 Paso 4: Preparar el Entorno del Proyecto

1. Abre la terminal y sitúate dentro de la carpeta del proyecto:
   ```bash
   cd ~/AUTOMATOR
   ```
   *(O la ruta exacta donde hayas colocado la carpeta).*

2. **Crea el entorno virtual de Python**:
   ```bash
   python3 -m venv .venv
   ```

3. **Activa el entorno e instala las librerías**:
   ```bash
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

---

## 🌐 Paso 5: Iniciar el Panel de Control Web

1. **Arranca el servidor**:
   ```bash
   source .venv/bin/activate
   python3 app.py
   ```
   Verás un mensaje indicando que el servidor está escuchando en el puerto 5000.

2. **Abre tu navegador de internet**:
   Ingresa a la siguiente dirección:
   ```text
   http://localhost:5000
   ```

3. **¡Listo!** Verás el panel de control:
   * Puedes ver el estado de la conexión con el teléfono.
   * En la pestaña **Facebook Lite**, podrás ver las cuentas escaneadas, alternar entre perfiles o iniciar el ciclo de reporte automatizado.

---

## ⚙️ Paso 6 (Opcional): Que el sistema inicie solo al encender la PC

Si deseas que el servidor funcione como un servicio del sistema sin tener que abrir la terminal:

1. Crea el archivo de servicio con tu editor de texto:
   ```bash
   sudo nano /etc/systemd/system/automator.service
   ```

2. Pega el siguiente contenido (reemplaza `tu_usuario` por tu nombre de usuario en Fedora):
   ```ini
   [Unit]
   Description=Servidor Web AUTOMATOR
   After=network.target ollama.service

   [Service]
   Type=simple
   User=tu_usuario
   WorkingDirectory=/home/tu_usuario/AUTOMATOR
   ExecStart=/home/tu_usuario/AUTOMATOR/.venv/bin/python3 app.py
   Restart=always
   RestartSec=5
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```
   *(Guarda los cambios con `Ctrl + O`, presiona `Enter` y sal con `Ctrl + X`)*.

3. Activa e inicia el servicio:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now automator
   ```

---

## 🛠️ Preguntas Frecuentes y Solución de Problemas

* **Problema: `adb devices` muestra `no permissions` o `????????????`**
  * **Solución**: Desconecta y vuelve a conectar el cable USB. Asegúrate de haber instalado `android-udev-rules` en el Paso 1.
* **Problema: El sistema abre la app pero no presiona los botones en el celular.**
  * **Solución**: En teléfonos Xiaomi/MIUI/HyperOS/Realme no está activada la opción *"Depuración USB (ajustes de seguridad)"*. Actívala en las Opciones de Desarrollador.
* **Problema: Quiero ver el panel desde otra computadora de la misma oficina/casa y no carga.**
  * **Solución**: El firewall de Fedora bloquea conexiones externas por defecto. Desbloquea el puerto 5000 ejecutando:
    ```bash
    sudo firewall-cmd --add-port=5000/tcp --permanent
    sudo firewall-cmd --reload
    ```
* **Problema: Error de modelos de Ollama al analizar.**
  * **Solución**: Verifica que el servicio esté corriendo con `sudo systemctl status ollama` y que hayas descargado los modelos con `ollama pull qwen2.5vl:latest`.
