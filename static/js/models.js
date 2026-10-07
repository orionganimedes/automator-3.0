// static/js/models.js - Ollama Local Models & Vision Controller

let localModelsCache = [];

// Scan installed Ollama models
async function scanLocalModels(userInitiated = false) {
  const icon = document.getElementById("scanModelsIcon");
  const countLabel = document.getElementById("modelScanCount");
  const btn = document.getElementById("btnScanModels");

  if (icon) icon.classList.add("animate-spin");
  if (countLabel) countLabel.innerText = "Escaneando IAs locales...";
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/models");
    const data = await res.json();

    if (data.success && Array.isArray(data.models)) {
      localModelsCache = data.models;
      const activeV = data.active_vision_model;
      const activeT = data.active_text_model;

      renderLocalModelSelects(data.models, activeV, activeT);
      renderLocalModelCards(data.models, activeV, activeT);
      updateActiveModelLabels(activeV, activeT);

      if (countLabel) {
        countLabel.innerText = `${data.models.length} modelos en disco`;
      }
      const tsLabel = document.getElementById("modelScanTimestamp");
      if (tsLabel) {
        tsLabel.innerText = `Actualizado ${new Date().toLocaleTimeString()}`;
      }

      if (userInitiated) {
        showToast(`Detectados ${data.models.length} modelos en Ollama`, "success");
      }
    } else {
      if (countLabel) countLabel.innerText = "No se pudieron listar";
      renderEmptyModelsList();
    }
  } catch (err) {
    console.error("Error scanning local models:", err);
    if (countLabel) countLabel.innerText = "Error de conexión con Ollama";
    renderEmptyModelsList();
  } finally {
    if (icon) icon.classList.remove("animate-spin");
    if (btn) btn.disabled = false;
  }
}

// Populate select dropdowns
function renderLocalModelSelects(models, activeV, activeT) {
  const vSelect = document.getElementById("selectVisionModel");
  const tSelect = document.getElementById("selectTextModel");
  if (!vSelect || !tSelect) return;

  vSelect.innerHTML = "";
  tSelect.innerHTML = "";

  if (!models.length) {
    vSelect.innerHTML = '<option value="">No hay modelos en Ollama</option>';
    tSelect.innerHTML = '<option value="">No hay modelos en Ollama</option>';
    return;
  }

  models.forEach(m => {
    const optV = document.createElement("option");
    optV.value = m.name;
    optV.innerText = `${m.name} ${m.is_vision ? "⚡ (Visión)" : ""}`;
    if (m.name === activeV) optV.selected = true;
    vSelect.appendChild(optV);

    const optT = document.createElement("option");
    optT.value = m.name;
    optT.innerText = `${m.name} ${m.is_text ? "💬 (Texto)" : ""}`;
    if (m.name === activeT) optT.selected = true;
    tSelect.appendChild(optT);
  });
}

// Render Model Cards in Cybernetic Console style
function renderLocalModelCards(models, activeV, activeT) {
  const container = document.getElementById("localModelsGrid");
  if (!container) return;

  if (!models.length) {
    renderEmptyModelsList();
    return;
  }

  container.innerHTML = models.map(m => {
    const isActV = m.name === activeV;
    const isActT = m.name === activeT;
    const sizeMb = m.size ? `${(m.size / (1024 * 1024 * 1024)).toFixed(1)} GB` : "Local";

    return `
      <div class="bg-surface-container rounded-xl p-4 border ${isActV || isActT ? 'border-primary shadow-[0_0_15px_-3px_rgba(99,102,241,0.25)]' : 'border-[#262a35]'} flex flex-col justify-between transition-all">
        <div>
          <div class="flex items-center justify-between mb-2">
            <span class="font-bold text-xs text-on-surface font-mono tracking-tight">${escapeHtml(m.name)}</span>
            <span class="font-mono text-[10px] px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant">${sizeMb}</span>
          </div>
          <div class="flex flex-wrap gap-1.5 mb-3">
            ${m.is_vision ? '<span class="text-[9px] px-2 py-0.5 rounded bg-secondary/10 text-secondary border border-secondary/30 font-mono font-semibold">MULTIMODAL OCR</span>' : ''}
            ${m.is_text ? '<span class="text-[9px] px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/30 font-mono font-semibold">TEXT GEN</span>' : ''}
            ${isActV ? '<span class="text-[9px] px-2 py-0.5 rounded bg-primary text-on-primary font-mono font-bold">ACTIVO VISIÓN</span>' : ''}
            ${isActT ? '<span class="text-[9px] px-2 py-0.5 rounded bg-secondary text-on-secondary font-mono font-bold">ACTIVO TEXTO</span>' : ''}
          </div>
          <p class="text-[11px] text-on-surface-variant font-mono mb-4">Parámetros: ${escapeHtml(m.parameter_size || "Quantized")} • Formato: GGUF</p>
        </div>
        <div class="flex items-center gap-2 pt-2 border-t border-[#262a35]">
          <button onclick="quickSelectModel('${escapeHtmlQuotes(m.name)}', 'vision')" class="flex-1 py-1 px-2 rounded bg-surface-container-high hover:bg-primary hover:text-on-primary text-[11px] font-semibold text-on-surface transition font-mono">
            Usar Visión
          </button>
          <button onclick="quickSelectModel('${escapeHtmlQuotes(m.name)}', 'text')" class="flex-1 py-1 px-2 rounded bg-surface-container-high hover:bg-secondary hover:text-on-secondary text-[11px] font-semibold text-on-surface transition font-mono">
            Usar Texto
          </button>
        </div>
      </div>
    `;
  }).join("");
}

function renderEmptyModelsList() {
  const container = document.getElementById("localModelsGrid");
  if (container) {
    container.innerHTML = `
      <div class="col-span-full p-8 text-center bg-surface-container rounded-xl border border-[#262a35]">
        <span class="material-symbols-outlined text-[32px] text-tertiary mb-2">warning</span>
        <h4 class="text-sm font-semibold text-on-surface mb-1">No se detectaron modelos en Ollama</h4>
        <p class="text-xs text-on-surface-variant font-mono">Ejecuta 'ollama pull llama3.2-vision' en tu terminal para descargar modelos multimodales locales.</p>
      </div>
    `;
  }
}

// Apply selected models via dropdowns
async function applyModelSelection() {
  const vSelect = document.getElementById("selectVisionModel");
  const tSelect = document.getElementById("selectTextModel");
  const btn = document.getElementById("btnApplyModels");

  const vision_model = vSelect ? vSelect.value : "";
  const text_model = tSelect ? tSelect.value : "";

  if (!vision_model && !text_model) {
    showToast("Selecciona al menos un modelo para activar", "warning");
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-50");
  }

  try {
    const res = await fetch("/api/models/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vision_model, text_model })
    });
    const data = await res.json();

    if (data.success) {
      updateActiveModelLabels(data.active_vision_model, data.active_text_model);
      renderLocalModelCards(localModelsCache, data.active_vision_model, data.active_text_model);
      showToast(`Modelos activos actualizados: Visión (${data.active_vision_model}) • Texto (${data.active_text_model})`, "success");
    } else {
      showToast(`Error al seleccionar modelos: ${data.error}`, "error");
    }
  } catch (err) {
    showToast("Error de conexión al guardar modelos", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-50");
    }
  }
}

// Quick Select
async function quickSelectModel(modelName, role) {
  const payload = {};
  if (role === "vision") payload.vision_model = modelName;
  if (role === "text") payload.text_model = modelName;
  if (role === "both") payload.model = modelName;

  try {
    const res = await fetch("/api/models/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      updateActiveModelLabels(data.active_vision_model, data.active_text_model);
      renderLocalModelCards(localModelsCache, data.active_vision_model, data.active_text_model);
      showToast(`Modelo activado para ${role.toUpperCase()}: ${modelName}`, "success");
    }
  } catch (err) {
    showToast("Error al activar modelo", "error");
  }
}

function updateActiveModelLabels(visionModel, textModel) {
  const lblV = document.getElementById("labelActiveVisionModel");
  const lblT = document.getElementById("labelActiveTextModel");
  const topOllama = document.getElementById("topOllamaModel");

  if (lblV) lblV.innerText = visionModel || "Ninguno";
  if (lblT) lblT.innerText = textModel || "Ninguno";
  if (topOllama) topOllama.innerText = visionModel || textModel || "Llama 3";
}
