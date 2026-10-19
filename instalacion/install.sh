#!/usr/bin/env bash
# =============================================================================
#  automator-3.0 — Instalador universal (Arch Linux / Fedora)
#  Uso: bash install.sh [--skip-ollama] [--skip-adb] [--dev]
# =============================================================================
set -euo pipefail

# ── Colores ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}[OK]${RESET}    $*"; }
info() { echo -e "${CYAN}[INFO]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
fail() { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }
step() { echo -e "\n${BOLD}${CYAN}▶ $*${RESET}"; }

# ── Argumentos ────────────────────────────────────────────────────────────────
SKIP_OLLAMA=false
SKIP_ADB=false
DEV_MODE=false
for arg in "$@"; do
    case $arg in
        --skip-ollama) SKIP_OLLAMA=true ;;
        --skip-adb)    SKIP_ADB=true    ;;
        --dev)         DEV_MODE=true    ;;
    esac
done

# ── Detectar distro ───────────────────────────────────────────────────────────
detect_distro() {
    if command -v pacman &>/dev/null; then
        echo "arch"
    elif command -v dnf &>/dev/null; then
        echo "fedora"
    else
        fail "Distro no soportada. Este instalador requiere Arch Linux (pacman) o Fedora (dnf)."
    fi
}

DISTRO=$(detect_distro)
info "Distro detectada: ${BOLD}${DISTRO}${RESET}"

# ── Directorio raíz del proyecto ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
info "Directorio del proyecto: ${PROJECT_DIR}"

# ── 1. Dependencias del sistema ───────────────────────────────────────────────
step "Instalando dependencias del sistema"

install_pkg_arch() {
    sudo pacman -Sy --noconfirm --needed "$@"
}

install_pkg_fedora() {
    sudo dnf install -y "$@"
}

SYS_PACKAGES_ARCH=(
    python python-pip python-virtualenv
    android-tools          # adb
    scrcpy                 # espejo de pantalla
    ffmpeg
    opencv python-opencv
    base-devel
    curl wget git
)

SYS_PACKAGES_FEDORA=(
    python3 python3-pip python3-virtualenv
    android-tools          # adb
    scrcpy
    ffmpeg
    opencv opencv-python3
    gcc gcc-c++ make
    curl wget git
)

if [[ "$DISTRO" == "arch" ]]; then
    install_pkg_arch "${SYS_PACKAGES_ARCH[@]}"
else
    # Habilitar RPM Fusion para scrcpy y ffmpeg en Fedora
    if ! rpm -q rpmfusion-free-release &>/dev/null; then
        info "Habilitando RPM Fusion Free..."
        sudo dnf install -y \
            "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
            "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm"
    fi
    install_pkg_fedora "${SYS_PACKAGES_FEDORA[@]}"
fi
ok "Dependencias del sistema instaladas"

# ── 2. ADB — verificar y configurar udev ──────────────────────────────────────
if [[ "$SKIP_ADB" == false ]]; then
    step "Configurando ADB"

    if ! command -v adb &>/dev/null; then
        fail "adb no encontrado tras la instalación. Verifica android-tools."
    fi
    ok "adb $(adb version | head -1)"

    # Reglas udev para que el usuario pueda usar ADB sin sudo
    UDEV_RULE='SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0666", GROUP="plugdev"'
    UDEV_FILE="/etc/udev/rules.d/51-android.rules"
    if [[ ! -f "$UDEV_FILE" ]]; then
        echo "$UDEV_RULE" | sudo tee "$UDEV_FILE" > /dev/null
        sudo udevadm control --reload-rules && sudo udevadm trigger
        ok "Reglas udev configuradas"
    else
        ok "Reglas udev ya existentes"
    fi

    # Agregar usuario al grupo plugdev si existe
    if getent group plugdev &>/dev/null; then
        sudo usermod -aG plugdev "$USER"
        warn "Agregado a grupo 'plugdev'. Cierra sesión y vuelve a entrar para que tome efecto."
    fi
fi

# ── 3. Entorno virtual Python ─────────────────────────────────────────────────
step "Creando entorno virtual Python"

VENV_DIR="${PROJECT_DIR}/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
    ok "Virtualenv creado en .venv/"
else
    ok "Virtualenv ya existe"
fi

source "${VENV_DIR}/bin/activate"

# ── 4. Dependencias Python ────────────────────────────────────────────────────
step "Instalando dependencias Python"

pip install --upgrade pip --quiet

if [[ "$DEV_MODE" == true ]]; then
    info "Modo --dev: instalando también herramientas de desarrollo"
    pip install --quiet \
        pytest pytest-cov \
        flake8 black \
        mypy
fi

pip install --quiet -r "${PROJECT_DIR}/requirements.txt"
ok "Paquetes Python instalados"

# ── 5. Ollama ─────────────────────────────────────────────────────────────────
if [[ "$SKIP_OLLAMA" == false ]]; then
    step "Instalando Ollama"

    if command -v ollama &>/dev/null; then
        ok "Ollama ya instalado: $(ollama --version 2>/dev/null || echo 'versión desconocida')"
    else
        info "Descargando e instalando Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
        ok "Ollama instalado"
    fi

    # Habilitar servicio systemd si está disponible
    if command -v systemctl &>/dev/null; then
        if systemctl list-unit-files ollama.service &>/dev/null 2>&1; then
            sudo systemctl enable --now ollama.service
            ok "Servicio ollama habilitado y activo"
        fi
    fi

    # Descargar modelos requeridos
    step "Descargando modelos de IA (puede tardar varios minutos)"

    info "Iniciando ollama serve en segundo plano para la descarga..."
    ollama serve &>/dev/null &
    OLLAMA_PID=$!
    sleep 3

    pull_model() {
        local model=$1
        info "Descargando ${model}..."
        if ollama pull "$model"; then
            ok "${model} descargado"
        else
            warn "No se pudo descargar ${model}. Descárgalo manualmente con: ollama pull ${model}"
        fi
    }

    pull_model "qwen2.5vl:latest"
    pull_model "qwen3:latest"

    kill $OLLAMA_PID 2>/dev/null || true
fi

# ── 6. Directorios de datos ────────────────────────────────────────────────────
step "Creando directorios de datos"

mkdir -p "${PROJECT_DIR}/data"
ok "data/ listo"

# ── 7. Permisos de scripts ────────────────────────────────────────────────────
chmod +x "${PROJECT_DIR}/start.sh" 2>/dev/null || true
chmod +x "${PROJECT_DIR}/main.py"  2>/dev/null || true

# ── 8. Verificación final ──────────────────────────────────────────────────────
step "Verificación del sistema"

check() {
    local label=$1; shift
    if "$@" &>/dev/null; then
        ok "$label"
    else
        warn "$label — NO encontrado o falla"
    fi
}

check "Python 3"       python3 --version
check "pip"            pip --version
check "Flask"          python3 -c "import flask"
check "OpenCV"         python3 -c "import cv2"
check "NumPy"          python3 -c "import numpy"
check "Requests"       python3 -c "import requests"
check "adb"            adb version
check "scrcpy"         scrcpy --version
[[ "$SKIP_OLLAMA" == false ]] && check "Ollama" ollama --version

# ── 9. Resumen ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗"
echo -e "║     automator-3.0 instalado correctamente    ║"
echo -e "╚══════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Iniciar el servidor:  ${CYAN}bash ${PROJECT_DIR}/start.sh${RESET}"
echo -e "  Activar entorno:      ${CYAN}source ${PROJECT_DIR}/.venv/bin/activate${RESET}"
[[ "$SKIP_OLLAMA" == false ]] && \
echo -e "  Iniciar Ollama:       ${CYAN}ollama serve${RESET}"
echo ""
warn "Si conectas el dispositivo Android por primera vez, reinicia la sesión para que los grupos udev tomen efecto."
echo ""
