// static/js/icons.js - Icon Template Matcher & Computer Vision Taps

let cachedIconTemplates = [];

// Load icon templates from backend
async function loadIconTemplates() {
  try {
    const res = await fetch("/api/icons");
    const data = await res.json();
    if (data.success) {
      cachedIconTemplates = data.templates || [];
      renderIconSelectors(cachedIconTemplates);
      renderIconGallery(cachedIconTemplates);
    }
  } catch (e) {
    console.error("Error loading icon templates:", e);
  }
}

function renderIconSelectors(templates) {
  const quickSelect = document.getElementById("quickIconSelect");
  const targetSelect = document.getElementById("selectedIconTarget");
  const badge = document.getElementById("templatesCountBadge");

  if (badge) badge.textContent = `${templates.length} plantilla${templates.length === 1 ? "" : "s"}`;

  const generateOptions = (items) => {
    if (!items || items.length === 0) {
      return `<option value="">(No hay iconos guardados)</option>`;
    }
    return items.map(t => {
      const markerTag = t.has_red_marker ? " [Marcador Rojo]" : "";
      return `<option value="${t.stem}">🎯 ${t.filename}${markerTag}</option>`;
    }).join("");
  };

  if (quickSelect) quickSelect.innerHTML = generateOptions(templates);
  if (targetSelect) targetSelect.innerHTML = generateOptions(templates);
}

function renderIconGallery(templates) {
  const container = document.getElementById("iconGalleryGrid");
  if (!container) return;

  if (!templates || templates.length === 0) {
    container.innerHTML = `
      <div class="col-span-full py-8 text-center text-on-surface-variant border border-dashed border-[#262a35] rounded-xl font-mono text-xs">
        No hay plantillas en icon_targets/. Sube tu primera captura arriba.
      </div>
    `;
    return;
  }

  container.innerHTML = templates.map(t => `
    <div class="bg-surface-container rounded-xl p-3 border border-[#262a35] flex flex-col justify-between hover:border-primary transition group">
      <div>
        <div class="h-24 bg-surface-container-lowest rounded-lg mb-2 flex items-center justify-center p-2 border border-[#262a35] overflow-hidden">
          <img src="/api/icons/image/${encodeURIComponent(t.filename)}?t=${Date.now()}" alt="${escapeHtml(t.filename)}" class="max-h-full max-w-full object-contain filter drop-shadow">
        </div>
        <div class="flex items-center justify-between mb-1">
          <span class="font-bold text-xs text-on-surface font-mono truncate" title="${escapeHtml(t.filename)}">${escapeHtml(t.filename)}</span>
        </div>
        <div class="text-[10px] text-on-surface-variant font-mono mb-2">
          ${t.dimensions ? `${t.dimensions[0]}x${t.dimensions[1]}px` : ""}
          ${t.has_red_marker ? '<span class="text-error font-bold ml-1">• Punto Rojo</span>' : ""}
        </div>
      </div>
      <button onclick="runLocateAndTap('${escapeHtmlQuotes(t.stem)}')" class="w-full py-1.5 px-2 rounded bg-surface-container-high hover:bg-primary hover:text-on-primary text-[11px] font-semibold text-on-surface transition font-mono flex items-center justify-center gap-1">
        <span class="material-symbols-outlined text-[14px]">touch_app</span>
        <span>Localizar & Tap</span>
      </button>
    </div>
  `).join("");
}

// Locate and tap icon in current screen
async function runLocateAndTap(targetName = null) {
  const target = targetName || document.getElementById("selectedIconTarget")?.value;
  const statusEl = document.getElementById("locateTapStatus");

  if (!target) {
    showToast("Selecciona un icono o plantilla objetivo", "warning");
    return;
  }

  showToast(`Buscando icono "${target}" en pantalla...`, "info");
  if (statusEl) {
    statusEl.innerText = `Buscando "${target}" con OpenCV...`;
    statusEl.className = "text-xs font-mono text-tertiary animate-pulse";
  }

  try {
    const res = await fetch("/api/icons/locate-and-tap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_name: target,
        threshold: 0.70,
        sleep_after: 1.5
      })
    });
    const data = await res.json();

    if (data.success && data.found) {
      const match = data.match;
      showToast(`¡Icono "${target}" encontrado en (${match.x}, ${match.y})! Tap ejecutado.`, "success");
      if (statusEl) {
        statusEl.innerText = `Encontrado en (${match.x}, ${match.y}) - Confianza: ${(match.confidence * 100).toFixed(1)}%`;
        statusEl.className = "text-xs font-mono text-secondary";
      }
      setTimeout(() => refreshScreen(false), 1200);
    } else {
      showToast(`No se detectó el icono "${target}" en la pantalla activa`, "warning");
      if (statusEl) {
        statusEl.innerText = `No encontrado (Confianza máx: ${data.match ? (data.match.confidence * 100).toFixed(1) : 0}%)`;
        statusEl.className = "text-xs font-mono text-error";
      }
    }
  } catch (e) {
    showToast("Error de conexión al buscar icono", "error");
  }
}

// Handle template upload
async function handleIconUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("icon", file);

  showToast("Subiendo nueva plantilla de icono...", "info");
  try {
    const res = await fetch("/api/icons/upload", {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Plantilla "${data.filename}" guardada con éxito`, "success");
      await loadIconTemplates();
    } else {
      showToast(`Error al subir plantilla: ${data.error}`, "error");
    }
  } catch (e) {
    showToast("Error al subir plantilla", "error");
  } finally {
    event.target.value = "";
  }
}

function toggleDebugMatchModal() {
  const modal = document.getElementById("debugMatchModal");
  const img = document.getElementById("debugMatchImg");
  if (!modal) return;

  if (modal.classList.contains("hidden")) {
    if (img) img.src = `/api/icons/debug-image?t=${Date.now()}`;
    modal.classList.remove("hidden");
  } else {
    modal.classList.add("hidden");
  }
}
