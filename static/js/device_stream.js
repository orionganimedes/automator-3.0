// static/js/device_stream.js - Scrcpy Screen Stream & Device Controls

let screenRetryTimer = null;

// Initialize stream mode and event listeners
function setupAutoRefresh() {
  const toggle = document.getElementById("autoRefreshToggle");
  const img = document.getElementById("deviceScreen");
  if (!toggle || !img) return;

  toggle.addEventListener("change", (e) => {
    if (e.target.checked) {
      img.src = `/api/device/stream?t=${Date.now()}`;
      const badge = document.getElementById("liveStreamBadge");
      if (badge) badge.classList.remove("hidden");
    } else {
      img.src = `/api/screen?t=${Date.now()}`;
      const badge = document.getElementById("liveStreamBadge");
      if (badge) badge.classList.add("hidden");
    }
  });

  // Setup click-to-tap on device screen if desired
  img.addEventListener("click", async (e) => {
    const rect = img.getBoundingClientRect();
    const relX = (e.clientX - rect.left) / rect.width;
    const relY = (e.clientY - rect.top) / rect.height;
    
    // Animate tap ripple indicator
    const ripple = document.getElementById("screenTapRipple");
    if (ripple) {
      ripple.style.left = `${(relX * 100).toFixed(1)}%`;
      ripple.style.top = `${(relY * 100).toFixed(1)}%`;
      ripple.classList.remove("hidden");
      ripple.classList.add("animate-ping");
      setTimeout(() => {
        ripple.classList.remove("animate-ping");
        ripple.classList.add("hidden");
      }, 600);
    }

    try {
      // Send touch event if active device is connected
      const normX = Math.round(relX * 1080);
      const normY = Math.round(relY * 2400);
      await fetch("/api/manual/scroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "tap", x: normX, y: normY })
      }).catch(() => {});
    } catch (err) {}
  });
}

// Refresh Screen
function refreshScreen(showSpinner = true) {
  const img = document.getElementById("deviceScreen");
  const spinner = document.getElementById("screenSpinner");
  if (showSpinner && spinner) spinner.classList.remove("hidden");

  const toggle = document.getElementById("autoRefreshToggle");
  const baseSrc = (toggle && toggle.checked) ? "/api/device/stream" : "/api/screen";
  if (img) img.src = `${baseSrc}?t=${Date.now()}`;
}

// Manual Capture
async function takeManualCapture() {
  const spinner = document.getElementById("screenSpinner");
  if (spinner) spinner.classList.remove("hidden");
  try {
    const res = await fetch("/api/manual/capture", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Captura realizada con éxito", "success");
    } else {
      showToast(`Error al capturar: ${data.error || "Desconocido"}`, "error");
    }
  } catch (e) {
    showToast("Error de conexión al capturar pantalla", "error");
  } finally {
    refreshScreen(false);
  }
}

// Image load events
function onScreenLoadSuccess(img) {
  const spinner = document.getElementById("screenSpinner");
  const placeholder = document.getElementById("screenPlaceholder");
  if (spinner) spinner.classList.add("hidden");
  if (placeholder) placeholder.classList.add("hidden");
  img.classList.remove("hidden");
}

function onScreenLoadError(img) {
  const spinner = document.getElementById("screenSpinner");
  const placeholder = document.getElementById("screenPlaceholder");
  if (spinner) spinner.classList.add("hidden");
  if (placeholder) placeholder.classList.remove("hidden");

  clearTimeout(screenRetryTimer);
  screenRetryTimer = setTimeout(() => {
    const toggle = document.getElementById("autoRefreshToggle");
    const baseSrc = (toggle && toggle.checked) ? "/api/device/stream" : "/api/screen";
    img.src = `${baseSrc}?t=${Date.now()}`;
  }, 2000);
}

// Preset app helper
function setAppPackage(pkg) {
  const input = document.getElementById("packageNameInput");
  if (input) input.value = pkg;
  showToast(`Aplicación destino: ${pkg}`, "info");
}

// Accounts sync between modules
function syncAccountsInput(val) {
  const a = document.getElementById("accountsToUseInput");
  const b = document.getElementById("fbAccountsToUseInput");
  const c = document.getElementById("hrAccountsToUseInput");
  if (a && a.value != val) a.value = val;
  if (b && b.value != val) b.value = val;
  if (c && c.value != val) c.value = val;
}

// Clear Device Recent Apps
async function clearDeviceRecentApps() {
  const btn = document.getElementById("btnClearRecentsBtn");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>Liberando...</span>`;
  }
  try {
    const res = await fetch("/api/device/clear-recents", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Memoria RAM y apps recientes cerradas", "success");
    } else {
      showToast(`Error al liberar RAM: ${data.error || "Desconocido"}`, "error");
    }
  } catch (e) {
    showToast("Error de conexión al liberar RAM", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
    setTimeout(() => refreshScreen(false), 800);
  }
}

// Clear Cached Screenshots
async function clearScreenshots() {
  const btn = document.getElementById("btnClearScreenshots");
  const text = document.getElementById("clearText");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-50");
  }
  if (text) text.innerText = "...";
  try {
    const res = await fetch("/api/screenshots/clear", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Caché de capturas limpiada", "success");
    } else {
      showToast(`Error: ${data.error || "Desconocido"}`, "error");
    }
  } catch (e) {
    showToast("Error al limpiar caché", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-50");
    }
    if (text) text.innerText = "Limpiar";
  }
}

// Manual Scroll
async function manualScroll(direction) {
  try {
    await fetch("/api/manual/scroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction })
    });
    setTimeout(() => refreshScreen(false), 500);
  } catch (e) {
    console.error("Scroll error:", e);
  }
}

// Launch Target App
async function launchTargetApp() {
  const pkgInput = document.getElementById("packageNameInput");
  const pkg = pkgInput ? pkgInput.value : "com.instagram.lite";
  try {
    const res = await fetch("/api/manual/open-app", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ package_name: pkg })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Iniciando ${pkg}...`, "info");
      setTimeout(() => refreshScreen(false), 1200);
    } else {
      showToast(`Error al abrir app: ${data.error || "Fallo"}`, "error");
    }
  } catch (e) {
    showToast("Error de conexión al abrir app", "error");
  }
}

// Open URL on Device
async function openUrlOnDevice() {
  const input = document.getElementById("urlInput") || document.getElementById("fbUrlInput");
  const btn = document.getElementById("openUrlBtn");
  const url = input ? input.value.trim() : "";
  const packageName = document.getElementById("packageNameInput")?.value.trim() || "";

  if (!url) {
    showToast("Introduce una URL o enlace válido", "warning");
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-50");
  }

  try {
    const res = await fetch("/api/manual/open-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, package: packageName })
    });
    const data = await res.json();

    if (data.success) {
      showToast("Enlace enviado al dispositivo", "success");
      setTimeout(() => refreshScreen(false), 1200);
    } else {
      showToast(`Error al abrir URL: ${data.error || "Fallo"}`, "error");
    }
  } catch (e) {
    showToast("Error al abrir URL en el dispositivo", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-50");
    }
  }
}
