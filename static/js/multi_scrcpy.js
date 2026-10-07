// static/js/multi_scrcpy.js - Multi-Device Scrcpy Fleet Controller

let scrcpyDevicesCache = [];
let isScrcpyLoading = false;
let autoThumbnailsTimer = null;

async function loadScrcpyDevices(forceReload = false) {
  if (isScrcpyLoading && !forceReload) return;
  isScrcpyLoading = true;
  const grid = document.getElementById("scrcpyDevicesGrid");
  const badge = document.getElementById("scrcpyCountBadge");
  const statTotal = document.getElementById("scrcpyStatTotal");
  const statActive = document.getElementById("scrcpyStatActive");
  const statBattery = document.getElementById("scrcpyStatBattery");

  try {
    const res = await fetch("/api/scrcpy/devices");
    const data = await res.json();
    if (!data.success) throw new Error(data.error || "Error al consultar dispositivos");

    scrcpyDevicesCache = data.devices || [];
    const count = scrcpyDevicesCache.length;
    const activeCount = scrcpyDevicesCache.filter(d => d.scrcpy_running).length;
    let avgBat = 0;
    if (count > 0) {
      const sumBat = scrcpyDevicesCache.reduce((acc, d) => acc + (d.battery || 0), 0);
      avgBat = Math.round(sumBat / count);
    }

    if (badge) badge.textContent = count;
    if (statTotal) statTotal.textContent = count;
    if (statActive) statActive.textContent = activeCount;
    if (statBattery) statBattery.textContent = count > 0 ? `${avgBat}%` : "--%";

    if (!grid) return;

    if (count === 0) {
      grid.innerHTML = `
        <div class="col-span-full py-16 text-center text-on-surface-variant bg-surface-container border border-[#262a35] rounded-2xl p-8">
          <span class="material-symbols-outlined text-[40px] text-on-surface-variant mb-2">phonelink_off</span>
          <h3 class="text-sm font-bold text-on-surface">No se detectaron dispositivos ADB conectados</h3>
          <p class="text-xs text-on-surface-variant mt-1 max-w-md mx-auto font-mono">Verifica los cables USB o la conexión ADB por red Wi-Fi (adb connect ip:port).</p>
          <button onclick="loadScrcpyDevices(true)" class="mt-4 px-4 py-2 bg-secondary text-on-secondary font-semibold text-xs rounded-lg shadow transition">
            Escanear ADB / Redescubrir
          </button>
        </div>
      `;
      return;
    }

    const now = Date.now();
    grid.innerHTML = scrcpyDevicesCache.map((dev, idx) => {
      const isRunning = dev.scrcpy_running;
      const bat = dev.battery ?? 100;
      let batColor = "text-secondary";
      if (bat <= 20) batColor = "text-error";
      else if (bat <= 50) batColor = "text-tertiary";

      return `
        <div class="bg-surface-container rounded-xl border ${isRunning ? 'border-secondary shadow-[0_0_15px_-3px_rgba(78,222,163,0.25)]' : 'border-[#262a35]'} p-4 flex flex-col justify-between transition group">
          <div>
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-[20px] text-primary">smartphone</span>
                <div>
                  <h4 class="font-bold text-xs text-on-surface tracking-tight">${escapeHtml(dev.model || "Android Device")}</h4>
                  <span class="text-[10px] text-on-surface-variant font-mono">${escapeHtml(dev.serial)}</span>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-[11px] font-mono font-bold ${batColor}">${bat}%</span>
                <span class="text-[9px] px-2 py-0.5 rounded font-mono font-bold ${isRunning ? 'bg-secondary/10 text-secondary border border-secondary/30' : 'bg-surface-container-high text-on-surface-variant'}">
                  ${isRunning ? 'SCRCPY VIVO' : 'STANDBY'}
                </span>
              </div>
            </div>

            <!-- Thumbnail screen view -->
            <div class="relative aspect-[9/16] max-h-56 bg-surface-container-lowest rounded-lg overflow-hidden border border-[#262a35] flex items-center justify-center mb-3">
              <img id="scrcpy-thumb-${dev.serial}" src="/api/scrcpy/preview/${dev.serial}?t=${now}" alt="Preview" class="w-full h-full object-contain filter drop-shadow" onerror="this.src='/api/screen?t=${now}'">
              <button onclick="refreshSingleThumbnail('${dev.serial}')" class="absolute top-2 right-2 p-1 rounded bg-surface-container-lowest/80 text-on-surface hover:text-primary transition" title="Refrescar vista">
                <span class="material-symbols-outlined text-[14px]">refresh</span>
              </button>
            </div>
          </div>

          <!-- Device Controls -->
          <div class="space-y-2 pt-2 border-t border-[#262a35]">
            <div class="grid grid-cols-4 gap-1">
              <button onclick="sendDeviceControl('${dev.serial}', 'back')" class="p-1 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-[10px] font-mono flex items-center justify-center" title="Atrás">
                <span class="material-symbols-outlined text-[14px]">arrow_back</span>
              </button>
              <button onclick="sendDeviceControl('${dev.serial}', 'home')" class="p-1 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-[10px] font-mono flex items-center justify-center" title="Inicio">
                <span class="material-symbols-outlined text-[14px]">home</span>
              </button>
              <button onclick="sendDeviceControl('${dev.serial}', 'app_switch')" class="p-1 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-[10px] font-mono flex items-center justify-center" title="Apps Recientes">
                <span class="material-symbols-outlined text-[14px]">view_carousel</span>
              </button>
              <button onclick="clearSingleRecents('${dev.serial}')" class="p-1 rounded bg-surface-container-high hover:bg-error hover:text-on-error text-error text-[10px] font-mono flex items-center justify-center" title="Liberar RAM">
                <span class="material-symbols-outlined text-[14px]">delete</span>
              </button>
            </div>

            <div class="flex items-center gap-2">
              ${isRunning ? `
                <button onclick="stopSingleScrcpy('${dev.serial}')" class="w-full py-1.5 rounded bg-error/20 hover:bg-error text-error hover:text-on-error border border-error/40 text-xs font-semibold font-mono transition flex items-center justify-center gap-1">
                  <span class="material-symbols-outlined text-[14px]">stop</span>
                  <span>Detener Scrcpy</span>
                </button>
              ` : `
                <button onclick="launchSingleScrcpy('${dev.serial}')" class="w-full py-1.5 rounded bg-secondary text-on-secondary hover:bg-secondary-container text-xs font-semibold font-mono transition flex items-center justify-center gap-1">
                  <span class="material-symbols-outlined text-[14px]">play_arrow</span>
                  <span>Iniciar Scrcpy</span>
                </button>
              `}
            </div>
          </div>
        </div>
      `;
    }).join("");

  } catch (e) {
    console.error("Error al cargar dispositivos scrcpy:", e);
  } finally {
    isScrcpyLoading = false;
  }
}

// Scrcpy Options builder
function getScrcpyOptions() {
  return {
    stay_awake: document.getElementById("scrcpyOptStayAwake")?.checked ?? true,
    turn_screen_off: document.getElementById("scrcpyOptScreenOff")?.checked ?? false,
    no_audio: document.getElementById("scrcpyOptNoAudio")?.checked ?? true,
    max_size: parseInt(document.getElementById("scrcpyOptMaxSize")?.value || "1080"),
    bit_rate: parseInt(document.getElementById("scrcpyOptBitrate")?.value || "8")
  };
}

// Launch / Stop
async function launchSingleScrcpy(serial) {
  showToast(`Iniciando Scrcpy para ${serial}...`, "info");
  try {
    const res = await fetch("/api/scrcpy/launch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ serial, options: getScrcpyOptions() })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Scrcpy activo en ${serial}`, "success");
      setTimeout(() => loadScrcpyDevices(true), 1000);
    } else {
      showToast(`Error: ${data.error}`, "error");
    }
  } catch (e) {
    showToast("Error al lanzar scrcpy", "error");
  }
}

async function stopSingleScrcpy(serial) {
  try {
    const res = await fetch("/api/scrcpy/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ serial })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Scrcpy cerrado en ${serial}`, "info");
      setTimeout(() => loadScrcpyDevices(true), 800);
    }
  } catch (e) {
    showToast("Error al detener scrcpy", "error");
  }
}

async function launchAllScrcpy() {
  showToast("Iniciando Scrcpy en todos los dispositivos conectados...", "info");
  try {
    await fetch("/api/scrcpy/launch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ serial: "all", options: getScrcpyOptions() })
    });
    setTimeout(() => loadScrcpyDevices(true), 1500);
  } catch (e) {
    showToast("Error al iniciar flota scrcpy", "error");
  }
}

async function stopAllScrcpy() {
  showToast("Deteniendo todas las ventanas de Scrcpy...", "info");
  try {
    await fetch("/api/scrcpy/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ serial: "all" })
    });
    setTimeout(() => loadScrcpyDevices(true), 1000);
  } catch (e) {
    showToast("Error al detener", "error");
  }
}

// Single / Batch controls
async function sendDeviceControl(serial, action) {
  try {
    await fetch(`/api/scrcpy/control/${serial}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action })
    });
    setTimeout(() => refreshSingleThumbnail(serial), 400);
  } catch (e) {
    console.error("Error en control:", e);
  }
}

async function clearSingleRecents(serial) {
  showToast(`Cerrando apps recientes en ${serial}...`, "info");
  try {
    await fetch(`/api/scrcpy/clear-recents/${serial}`, { method: "POST" });
    setTimeout(() => refreshSingleThumbnail(serial), 600);
  } catch (e) {
    showToast("Error al limpiar recientes", "error");
  }
}

function refreshSingleThumbnail(serial) {
  const img = document.getElementById(`scrcpy-thumb-${serial}`);
  if (img) img.src = `/api/scrcpy/preview/${serial}?t=${Date.now()}`;
}

function refreshAllThumbnails() {
  scrcpyDevicesCache.forEach(d => refreshSingleThumbnail(d.serial));
}

function toggleAutoThumbnails(checked) {
  clearInterval(autoThumbnailsTimer);
  if (checked) {
    autoThumbnailsTimer = setInterval(refreshAllThumbnails, 3000);
  }
}
