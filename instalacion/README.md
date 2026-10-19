# Instalador — automator-3.0

Compatible con **Arch Linux** y **Fedora** (35+).

## Uso rápido

```bash
bash instalacion/install.sh
```

## Opciones

| Flag | Efecto |
|------|--------|
| `--skip-ollama` | No instala Ollama ni descarga modelos (útil si ya los tienes) |
| `--skip-adb` | No configura ADB ni reglas udev |
| `--dev` | Instala además pytest, flake8, black y mypy |

## Qué hace el instalador

1. Detecta la distro (pacman → Arch, dnf → Fedora)
2. Instala paquetes del sistema: `python3`, `android-tools` (adb), `scrcpy`, `ffmpeg`, `opencv`
3. En Fedora habilita RPM Fusion automáticamente para scrcpy y ffmpeg
4. Configura reglas udev para ADB sin sudo
5. Crea entorno virtual `.venv/` e instala `requirements.txt`
6. Instala Ollama y descarga los modelos `qwen2.5vl:latest` y `qwen3:latest`
7. Crea el directorio `data/`
8. Verifica que todo esté disponible

## Requisitos previos

- Conexión a internet
- `sudo` disponible
- Dispositivo Android con **Depuración USB** activada (Ajustes → Opciones de desarrollador)

## Después de instalar

```bash
# Iniciar el servidor web
bash start.sh

# O manualmente con el entorno activo
source .venv/bin/activate
python3 app.py
```

El dashboard estará disponible en `http://localhost:5000`.
