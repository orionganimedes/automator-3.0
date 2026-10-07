// static/js/instagram.js - Instagram Bot Automation Controller

function setMediaType(type) {
  const input = document.getElementById("mediaTypeInput");
  if (input) input.value = type;

  const btnPost = document.getElementById("btnMediaPost");
  const btnReel = document.getElementById("btnMediaReel");

  if (type === "post") {
    if (btnPost) {
      btnPost.className = "px-3 py-1.5 rounded bg-primary text-on-primary font-semibold text-xs flex items-center gap-1.5 shadow";
    }
    if (btnReel) {
      btnReel.className = "px-3 py-1.5 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant font-medium text-xs flex items-center gap-1.5 transition";
    }
  } else {
    if (btnReel) {
      btnReel.className = "px-3 py-1.5 rounded bg-primary text-on-primary font-semibold text-xs flex items-center gap-1.5 shadow";
    }
    if (btnPost) {
      btnPost.className = "px-3 py-1.5 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant font-medium text-xs flex items-center gap-1.5 transition";
    }
  }
}

// Comment Mode Switching & Bank Helpers
function selectCommentMode(mode) {
  const modeInput = document.getElementById("selectedCommentMode");
  const autoCard = document.getElementById("commentModeAutoCard");
  const presetCard = document.getElementById("commentModePresetCard");
  const dotAuto = document.getElementById("radioDotAuto");
  const dotPreset = document.getElementById("radioDotPreset");
  const currentBadge = document.getElementById("currentCommentModeBadge");

  if (modeInput) modeInput.value = mode;

  if (mode === "preset_list") {
    if (autoCard) {
      autoCard.className = "cursor-pointer rounded-xl border-2 border-[#262a35] bg-surface-container p-3.5 space-y-2 transition-all hover:border-[#353944] select-none";
    }
    if (presetCard) {
      presetCard.className = "cursor-pointer rounded-xl border-2 border-primary bg-primary/10 p-3.5 space-y-2 transition-all shadow-md select-none";
    }
    if (dotAuto) dotAuto.className = "w-1.5 h-1.5 rounded-full bg-transparent";
    if (dotPreset) dotPreset.className = "w-1.5 h-1.5 rounded-full bg-surface-container-lowest";
    if (currentBadge) {
      currentBadge.innerText = "Modo Banco Rotativo";
      currentBadge.className = "text-[10px] px-2 py-0.5 rounded bg-secondary/10 text-secondary border border-secondary/20";
    }
    showToast("Modo Banco Rotativo seleccionado (rotación cuenta por cuenta)", "info");
  } else {
    if (autoCard) {
      autoCard.className = "cursor-pointer rounded-xl border-2 border-primary bg-primary/10 p-3.5 space-y-2 transition-all shadow-md select-none";
    }
    if (presetCard) {
      presetCard.className = "cursor-pointer rounded-xl border-2 border-[#262a35] bg-surface-container p-3.5 space-y-2 transition-all hover:border-[#353944] select-none";
    }
    if (dotAuto) dotAuto.className = "w-1.5 h-1.5 rounded-full bg-surface-container-lowest";
    if (dotPreset) dotPreset.className = "w-1.5 h-1.5 rounded-full bg-transparent";
    if (currentBadge) {
      currentBadge.innerText = "Modo Autónomo IA";
      currentBadge.className = "text-[10px] px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20";
    }
    showToast("Modo Autónomo IA seleccionado (análisis con Ollama)", "info");
  }
}

function updatePastedCommentsCount() {
  const ta = document.getElementById("pastedCommentsTextarea");
  const badge = document.getElementById("pastedCommentsBadge");
  const sampleBtn = document.getElementById("btnLoadSamples");
  if (!ta || !badge) return;
  const count = ta.value.split("\n").map(s => s.trim()).filter(Boolean).length;
  badge.innerText = `${count} comentarios listos`;

  // Si ya hay comentarios escritos, ocultar el botón de ejemplos para evitar confusiones
  if (sampleBtn) {
    if (count > 0) {
      sampleBtn.classList.add("hidden");
    } else {
      sampleBtn.classList.remove("hidden");
    }
  }

  // Auto-activar Banco Rotativo si el usuario pegó comentarios y aún está en modo auto
  if (count > 0) {
    const modeInput = document.getElementById("selectedCommentMode");
    if (modeInput && modeInput.value !== "preset_list") {
      selectCommentMode("preset_list");
    }
  }
}

function loadSampleInstagramComments() {
  const ta = document.getElementById("pastedCommentsTextarea");
  if (!ta) return;
  if (ta.value.trim().length > 0) {
    if (!confirm("Ya tienes comentarios escritos en el banco. ¿Deseas borrarlos y reemplazarlos por los 5 ejemplos de prueba?")) {
      return;
    }
  }
  ta.value = [
    "Excelente publicación, muy bien explicado el concepto.",
    "Totalmente de acuerdo con lo que mencionas aquí.",
    "Buen aporte para la comunidad, se agradece compartirlo.",
    "Interesante punto de vista sobre este tema.",
    "Muy buen contenido, a seguir con esa constancia."
  ].join("\n");
  updatePastedCommentsCount();
  selectCommentMode("preset_list");
  showToast("5 comentarios de ejemplo cargados en el banco rotativo", "info");
}

function clearInstagramComments() {
  const ta = document.getElementById("pastedCommentsTextarea");
  if (ta) ta.value = "";
  updatePastedCommentsCount();
}

// --- Selector Interactivo de Ejecuciones de Automatización ---
let currentDeviceTotalAccounts = 11;

function getDetectedAccountsTotal() {
  const input = document.getElementById("accountsToUseInput");
  if (input && input.max) {
    const maxVal = parseInt(input.max, 10);
    if (!isNaN(maxVal) && maxVal > 0) return maxVal;
  }
  return currentDeviceTotalAccounts || 11;
}

function updateExecutionSummary(count, total) {
  const summaryEl = document.getElementById("automationExecutionSummary");
  if (!summaryEl) return;
  const num = parseInt(count, 10) || 1;
  const max = total || getDetectedAccountsTotal();
  if (num === 1) {
    summaryEl.innerHTML = `Se ejecutará en <strong>1</strong> cuenta y al terminar se detendrá automáticamente.`;
  } else if (num >= max) {
    summaryEl.innerHTML = `Se ejecutará en las <strong>${max}</strong> cuentas del teléfono (ciclo completo) y al terminar se detendrá automáticamente.`;
  } else {
    summaryEl.innerHTML = `Se ejecutará en <strong>${num}</strong> cuentas diferentes de forma secuencial y al terminar se detendrá automáticamente.`;
  }
}

function updateExecutionChips(activeVal, total) {
  const max = total || getDetectedAccountsTotal();
  const chipAll = document.getElementById("chipTimesAll");
  if (chipAll) {
    chipAll.innerText = `Todas (${max})`;
  }

  const chips = [
    { el: document.getElementById("chipTimes1"), val: 1 },
    { el: document.getElementById("chipTimes3"), val: 3 },
    { el: document.getElementById("chipTimes5"), val: 5 },
    { el: chipAll, val: max, isAll: true }
  ];

  chips.forEach(chip => {
    if (!chip.el) return;
    const isSelected = (activeVal === chip.val) || (chip.isAll && activeVal >= max);
    if (isSelected) {
      chip.el.className = "px-2.5 py-1 rounded-md text-xs font-mono font-bold transition bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 cursor-pointer shadow-sm";
    } else {
      chip.el.className = "px-2.5 py-1 rounded-md text-xs font-mono font-medium transition bg-surface-container-high hover:bg-surface-container-highest text-on-surface border border-[#262a35] cursor-pointer";
    }
  });
}

function setAutomationTimes(val) {
  const total = getDetectedAccountsTotal();
  let count = (val === "all") ? total : parseInt(val, 10);
  if (isNaN(count) || count < 1) count = 1;
  if (count > total) count = total;

  const input = document.getElementById("accountsToUseInput");
  if (input) {
    input.value = count;
  }
  const cycleInput = document.getElementById("cycleCountInput");
  if (cycleInput) {
    cycleInput.value = 1;
  }

  updateExecutionChips(count, total);
  updateExecutionSummary(count, total);

  if (typeof syncAccountsInput === "function") {
    syncAccountsInput(count);
  }
}

function adjustAutomationTimes(delta) {
  const input = document.getElementById("accountsToUseInput");
  const current = input ? (parseInt(input.value, 10) || 1) : 1;
  setAutomationTimes(current + delta);
}

function onAutomationTimesInputChange(val) {
  const total = getDetectedAccountsTotal();
  let count = parseInt(val, 10);
  if (isNaN(count)) return;
  if (count < 1) count = 1;
  if (count > total) count = total;

  const input = document.getElementById("accountsToUseInput");
  if (input && input.value != count) {
    input.value = count;
  }

  updateExecutionChips(count, total);
  updateExecutionSummary(count, total);

  if (typeof syncAccountsInput === "function") {
    syncAccountsInput(count);
  }
}

async function loadDeviceAccountsInitialMemory() {
  try {
    const res = await fetch("/api/accounts/memory");
    const json = await res.json();
    if (json.success && json.data) {
      const total = json.data.total_accounts || 11;
      currentDeviceTotalAccounts = total;
      
      const badge = document.getElementById("igAccountsFoundBadge");
      const limitLabel = document.getElementById("accountsLimitLabel");
      const input = document.getElementById("accountsToUseInput");
      const memBadge = document.getElementById("accountsMemoryBadge");
      
      if (badge) badge.innerText = `${total} perfiles listos`;
      if (limitLabel) limitLabel.innerText = `Máx: ${total} cuentas`;
      if (input) {
        input.max = total;
      }
      if (memBadge) {
        const model = json.data.model || "Dispositivo";
        memBadge.innerText = `${total} cuentas en memoria (${model})`;
      }
      
      const currentVal = input ? (parseInt(input.value, 10) || 1) : 1;
      updateExecutionChips(currentVal, total);
      updateExecutionSummary(currentVal, total);
    }
  } catch (e) {
    console.debug("Could not pre-fetch accounts memory:", e);
  }
}

// Scan accounts directly via ADB in Instagram Lite
async function scanDeviceAccounts() {
  const btn = document.getElementById("btnScanAccounts");
  const label = document.getElementById("accountsLimitLabel");
  const igBadge = document.getElementById("igAccountsFoundBadge");
  const fbLabel = document.getElementById("fbAccountsLimitLabel");
  const hrLabel = document.getElementById("hrAccountsLimitLabel");
  const input = document.getElementById("accountsToUseInput");
  const fbInput = document.getElementById("fbAccountsToUseInput");
  const hrInput = document.getElementById("hrAccountsToUseInput");
  const memBadge = document.getElementById("accountsMemoryBadge");
  const origHtml = btn ? btn.innerHTML : "";

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-50");
    btn.innerHTML = `<span class="material-symbols-outlined text-[14px] animate-spin">sync</span><span>Abriendo IG Lite (esperando 38s)...</span>`;
  }
  if (label) label.innerText = "Abriendo app (38s)...";
  if (igBadge) igBadge.innerText = "Cargando IG Lite (38s)...";
  if (fbLabel) fbLabel.innerText = "Abriendo app...";
  showToast("Abriendo Instagram Lite. Esperando 38s (35-40s) para asegurar apertura completa antes de analizar...", "info");

  try {
    const res = await fetch("/api/accounts/scan");
    const data = await res.json();
    if (data.success) {
      const total = (typeof data.total_accounts !== "undefined") ? data.total_accounts : (data.count || 0);
      currentDeviceTotalAccounts = total;
      const text = `Total detectadas: ${total}`;
      if (label) label.innerText = `Máx: ${total} cuentas`;
      if (igBadge) igBadge.innerText = `${total} perfiles detectados`;
      if (fbLabel) fbLabel.innerText = text;
      if (hrLabel) hrLabel.innerText = text;

      if (input) {
        input.max = Math.max(1, total);
        if (parseInt(input.value, 10) > total) {
          input.value = total;
        }
      }
      if (fbInput) {
        fbInput.value = total;
        fbInput.max = Math.max(1, total);
      }
      if (hrInput) {
        hrInput.value = total;
        hrInput.max = Math.max(1, total);
      }

      if (memBadge) {
        const devInfo = data.device_model || data.device_serial || "Dispositivo";
        memBadge.innerText = `${total} cuentas en memoria (${devInfo})`;
      }

      const currentChosen = input ? (parseInt(input.value, 10) || 1) : 1;
      updateExecutionChips(currentChosen, total);
      updateExecutionSummary(currentChosen, total);

      if (typeof syncAccountsInput === "function") {
        syncAccountsInput(currentChosen);
      }

      const stepMsg = document.getElementById("cycleStepMsg");
      const stepNum = document.getElementById("cycleStepNum");
      const spinner = document.getElementById("cycleSpinner");
      const badge = document.getElementById("cycleAccountBadge");
      const bar = document.getElementById("cycleProgressBar");

      if (badge) badge.innerText = `${total} Cuentas`;
      if (stepNum) stepNum.innerText = "¡Listo! 🎉";
      if (stepMsg) stepMsg.innerText = `Análisis finalizado: ${total} cuentas registradas. Ahora pega tu link y pulsa 'Iniciar Automatización'.`;
      if (spinner) spinner.classList.add("hidden");
      if (bar) bar.style.width = "100%";

      showToast(`¡Se detectaron y registraron ${total} cuentas en Instagram Lite!`, "success");
      setTimeout(() => refreshScreen(false), 1200);
    } else {
      const err = "Error: " + (data.error || "Desconocido");
      if (label) label.innerText = err;
      if (igBadge) igBadge.innerText = err;
      showToast(`Aviso al escanear: ${data.error || "Fallo"}`, "error");
    }
  } catch (e) {
    const err = "Error de red: " + e.message;
    if (label) label.innerText = err;
    if (igBadge) igBadge.innerText = err;
    showToast(`Error al escanear cuentas: ${e.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-50");
      btn.innerHTML = origHtml;
    }
  }
}

// Memory Modal Controls
async function showMemoryModal() {
  const modal = document.getElementById("memoryModal");
  if (modal) modal.classList.remove("hidden");
  await refreshMemoryModal();
}

function closeMemoryModal() {
  const modal = document.getElementById("memoryModal");
  if (modal) modal.classList.add("hidden");
}

async function refreshMemoryModal() {
  const container = document.getElementById("memoryModalContent");
  if (!container) return;
  container.innerHTML = `<div class="p-6 text-center text-on-surface-variant font-mono text-xs"><span class="material-symbols-outlined text-[20px] animate-spin">sync</span> Leyendo base de datos de memoria y rotación...</div>`;

  try {
    const [accRes, recentRes, rawRes] = await Promise.all([
      fetch("/api/accounts/memory").catch(() => null),
      fetch("/api/memory/recent").catch(() => null),
      fetch("/api/memory/raw").catch(() => null)
    ]);

    let accData = accRes ? await accRes.json().catch(() => null) : null;
    let recentData = recentRes ? await recentRes.json().catch(() => null) : null;
    let rawData = rawRes ? await rawRes.json().catch(() => null) : null;

    let html = `<div class="space-y-4 font-mono text-xs">`;

    // 1. Memory Summary
    if (recentData && recentData.memory) {
      const recentArchs = recentData.memory.recent_archetypes || [];
      const recentElems = recentData.memory.recent_elements || [];
      html += `
        <div class="bg-surface-container p-3 rounded-lg border border-[#262a35] space-y-1.5">
          <div class="flex items-center justify-between text-[11px]">
            <span class="text-on-surface-variant">Últimos arquetipos usados:</span>
            <span class="text-primary font-bold">${recentArchs.slice(0, 3).join(" ➔ ") || "Ninguno aún registrado"}</span>
          </div>
          <div class="flex items-center justify-between text-[11px]">
            <span class="text-on-surface-variant">Elementos visuales citados:</span>
            <span class="text-secondary">${recentElems.slice(0, 4).map(e => `"${e}"`).join(", ") || "Ninguno"}</span>
          </div>
        </div>
      `;
    }

    // 2. Accounts List
    if (accData && accData.success && accData.memory) {
      const mem = accData.memory;
      const accList = mem.accounts || [];
      html += `
        <div class="bg-surface-container p-3 rounded-lg border border-[#262a35] space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-on-surface font-bold text-xs">Cuentas Registradas en Dispositivo:</span>
            <span class="text-primary font-bold">${accList.length}</span>
          </div>
          <div class="divide-y divide-[#262a35] border border-[#262a35] rounded-lg overflow-hidden bg-surface-container-lowest max-h-40 overflow-y-auto">
      `;
      if (accList.length === 0) {
        html += `<div class="p-3 text-center text-on-surface-variant text-[11px]">No hay cuentas indexadas aún. Usa "Saber cuántas cuentas hay" en el panel.</div>`;
      } else {
        accList.forEach((acc, idx) => {
          html += `
            <div class="p-2 flex items-center justify-between text-[11px]">
              <div class="flex items-center gap-2">
                <span class="text-primary font-bold">#${idx + 1}</span>
                <span class="text-on-surface font-semibold">${escapeHtml(acc.username || acc.name || `Cuenta ${idx + 1}`)}</span>
              </div>
              <div class="text-[10px] text-on-surface-variant">
                <span>Usos: ${acc.times_used || 0}</span>
                <span class="ml-2 text-secondary">OK</span>
              </div>
            </div>
          `;
        });
      }
      html += `</div></div>`;
    }

    // 3. Raw agents.md excerpt
    if (rawData && rawData.content) {
      html += `
        <div class="bg-surface-container-lowest p-3 rounded-lg border border-[#262a35] max-h-36 overflow-y-auto whitespace-pre-wrap text-[10px] text-on-surface-variant">
          ${escapeHtml(rawData.content)}
        </div>
      `;
    }

    html += `</div>`;
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="p-4 text-xs text-error font-mono">Error al consultar memoria: ${e.message}</div>`;
  }
}

// Helper to toggle all automation buttons state
function setCycleUiRunningState(isRunning) {
  const startBtn = document.getElementById("btnStartCycle");
  const presetBtn = document.getElementById("btnStartPreset");
  const aiBtn = document.getElementById("btnStartAi");
  const stopBtn = document.getElementById("btnStopCycle");

  [startBtn, presetBtn, aiBtn].forEach(b => {
    if (b) {
      b.disabled = isRunning;
      if (isRunning) {
        b.classList.add("opacity-50", "cursor-not-allowed");
      } else {
        b.classList.remove("opacity-50", "cursor-not-allowed");
      }
    }
  });

  if (stopBtn) {
    stopBtn.disabled = !isRunning;
    if (isRunning) {
      stopBtn.classList.remove("opacity-40", "cursor-not-allowed");
    } else {
      stopBtn.classList.add("opacity-40", "cursor-not-allowed");
    }
  }
}

// 1. Iniciar con Lista Predefinida (100% Sin IA, usa manual_automation.py)
async function startPresetAutomation() {
  const urlInput = document.getElementById("urlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  const useCurrentScreen = document.getElementById("cycleUseCurrentScreen")?.checked ?? false;
  const packageName = document.getElementById("packageNameInput")?.value.trim() || "com.instagram.lite";

  if (!url && !useCurrentScreen) {
    showToast("Introduce el enlace de Instagram o activa 'Usar pantalla actual'", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  const pastedComments = (document.getElementById("pastedCommentsTextarea")?.value || "")
    .split("\n").map(s => s.trim()).filter(Boolean);

  if (pastedComments.length === 0) {
    showToast("Pega tus comentarios en el área 'Banco de Comentarios' antes de iniciar.", "error");
    const ta = document.getElementById("pastedCommentsTextarea");
    if (ta) ta.focus();
    return;
  }

  const doLike = document.getElementById("cycleDoLike")?.checked ?? true;
  const doComment = document.getElementById("cycleDoComment")?.checked ?? true;
  const repeatCycles = document.getElementById("cycleRepeat")?.checked ?? false;
  const cycleCount = parseInt(document.getElementById("cycleCountInput")?.value) || 1;
  const accountsToUse = parseInt(document.getElementById("accountsToUseInput")?.value) || 0;
  const mediaType = document.getElementById("mediaTypeInput")?.value || "post";
  const clearRam = document.getElementById("clearRamCheck")?.checked ?? true;
  const feedBrowsing = document.getElementById("feedBrowsingCheck")?.checked ?? false;
  const autoRestart = document.getElementById("autoRestartCycleCheck")?.checked ?? false;
  const cooldownSeconds = parseInt(document.getElementById("cooldownSecondsSelect")?.value || "0", 10);

  setCycleUiRunningState(true);

  const statusWidget = document.getElementById("cycleStatusWidget");
  if (statusWidget) statusWidget.classList.remove("hidden");

  const badge = document.getElementById("cycleAccountBadge");
  const stepNum = document.getElementById("cycleStepNum");
  const stepMsg = document.getElementById("cycleStepMsg");
  const spinner = document.getElementById("cycleSpinner");

  if (badge) badge.innerText = `Lista Predefinida (${pastedComments.length})`;
  if (stepNum) stepNum.innerText = "Paso 1";
  if (stepMsg) stepMsg.innerText = "Iniciando automatización con Lista Predefinida (Sin IA)...";
  if (spinner) spinner.classList.remove("hidden");
  showToast(`Iniciando con Lista Predefinida (${pastedComments.length} comentarios)`, "info");

  try {
    const res = await fetch("/api/cycle/start-preset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        use_current_screen: useCurrentScreen,
        package_name: packageName,
        pasted_comments: pastedComments,
        do_like: doLike,
        do_comment: doComment,
        repeat_cycles: repeatCycles,
        cycle_count: cycleCount,
        max_cycles: cycleCount,
        accounts_to_use: accountsToUse,
        media_type: mediaType,
        clear_ram_between_cycles: clearRam,
        feed_browsing: feedBrowsing,
        auto_restart_on_failure: autoRestart,
        cooldown_seconds: cooldownSeconds
      })
    });
    const data = await res.json();
    if (!data.success) {
      handleCycleError({ error: data.error || "No se pudo iniciar el ciclo con lista predefinida" });
    } else {
      showToast("Ciclo con Lista Predefinida iniciado con éxito", "success");
    }
  } catch (e) {
    handleCycleError({ error: `Fallo de conexión: ${e.message}` });
  }
}

// 2. Iniciar con IA (Ollama + Visión + Generación narrativa)
async function startAiAutomation() {
  const urlInput = document.getElementById("urlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  const useCurrentScreen = document.getElementById("cycleUseCurrentScreen")?.checked ?? false;
  const packageName = document.getElementById("packageNameInput")?.value.trim() || "com.instagram.lite";

  if (!url && !useCurrentScreen) {
    showToast("Introduce el enlace de Instagram o activa 'Usar pantalla actual'", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  const doLike = document.getElementById("cycleDoLike")?.checked ?? true;
  const doComment = document.getElementById("cycleDoComment")?.checked ?? true;
  const repeatCycles = document.getElementById("cycleRepeat")?.checked ?? false;
  const cycleCount = parseInt(document.getElementById("cycleCountInput")?.value) || 1;
  const accountsToUse = parseInt(document.getElementById("accountsToUseInput")?.value) || 0;
  const narrative = document.getElementById("cycleNarrativeSelect")?.value || "a_favor";
  const tone = document.getElementById("cycleToneSelect")?.value || "auto";
  const customComment = document.getElementById("cycleCustomComment")?.value.trim() || "";
  const visionModel = document.getElementById("selectVisionModel")?.value || "";
  const textModel = document.getElementById("selectTextModel")?.value || "";
  const mediaType = document.getElementById("mediaTypeInput")?.value || "post";

  const clearRam = document.getElementById("clearRamCheck")?.checked ?? true;
  const feedBrowsing = document.getElementById("feedBrowsingCheck")?.checked ?? false;
  const autoRestart = document.getElementById("autoRestartCycleCheck")?.checked ?? false;
  const cooldownSeconds = parseInt(document.getElementById("cooldownSecondsSelect")?.value || "0", 10);

  const manualContext = document.getElementById("manualContextPostText")?.value.trim() || "";
  const isContest = document.getElementById("manualContextIsContest")?.checked ?? false;
  const contestRequirement = document.getElementById("manualContextContestReq")?.value.trim() || "";
  const teamsOrTopic = document.getElementById("manualContextContestTopic")?.value.trim() || "";

  setCycleUiRunningState(true);

  const statusWidget = document.getElementById("cycleStatusWidget");
  if (statusWidget) statusWidget.classList.remove("hidden");

  const badge = document.getElementById("cycleAccountBadge");
  const stepNum = document.getElementById("cycleStepNum");
  const stepMsg = document.getElementById("cycleStepMsg");
  const spinner = document.getElementById("cycleSpinner");

  if (badge) badge.innerText = `Modo IA (${accountsToUse || "todas"} ctas)`;
  if (stepNum) stepNum.innerText = "Paso 1";
  if (stepMsg) stepMsg.innerText = "Iniciando con IA (Ollama)... analizando publicación...";
  if (spinner) spinner.classList.remove("hidden");
  showToast("Iniciando con IA (Ollama)...", "info");

  try {
    const res = await fetch("/api/cycle/start-ia", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        use_current_screen: useCurrentScreen,
        package_name: packageName,
        do_like: doLike,
        do_comment: doComment,
        repeat_cycles: repeatCycles,
        cycle_count: cycleCount,
        max_cycles: cycleCount,
        accounts_to_use: accountsToUse,
        narrative,
        tone,
        custom_comment: customComment,
        media_type: mediaType,
        vision_model: visionModel,
        text_model: textModel,
        feed_browsing: feedBrowsing,
        auto_restart_on_failure: autoRestart,
        clear_ram_between_cycles: clearRam,
        cooldown_seconds: cooldownSeconds,
        manual_context: manualContext,
        is_contest: isContest,
        contest_requirement: contestRequirement,
        teams_or_topic: teamsOrTopic
      })
    });
    const data = await res.json();
    if (!data.success) {
      handleCycleError({ error: data.error || "No se pudo iniciar el ciclo con IA" });
    } else {
      showToast("Ciclo con IA iniciado en segundo plano", "success");
    }
  } catch (e) {
    handleCycleError({ error: `Fallo de conexión: ${e.message}` });
  }
}

// 3. Puente de compatibilidad
async function startMultiAccountCycle() {
  const commentMode = document.getElementById("selectedCommentMode")?.value || "auto";
  if (commentMode === "preset_list") {
    return startPresetAutomation();
  } else {
    return startAiAutomation();
  }
}

// Alias para compatibilidad
const startManualAutomation = startPresetAutomation;

// Stop Multi-Account Cycle
async function stopMultiAccountCycle() {
  const stopBtn = document.getElementById("btnStopCycle");
  if (stopBtn) {
    stopBtn.disabled = true;
    stopBtn.classList.add("opacity-50");
  }

  try {
    const res = await fetch("/api/cycle/stop", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Deteniendo ciclo de automatización...", "warning");
      const stepMsg = document.getElementById("cycleStepMsg");
      if (stepMsg) stepMsg.innerText = "Deteniendo por solicitud de usuario...";
    }
  } catch (e) {
    showToast("Error al solicitar detención", "error");
  }
}

// Handle Cycle Step from SSE
function handleCycleStep(data) {
  const statusWidget = document.getElementById("cycleStatusWidget");
  const badge = document.getElementById("cycleAccountBadge");
  const num = document.getElementById("cycleStepNum");
  const msg = document.getElementById("cycleStepMsg");
  const bar = document.getElementById("cycleProgressBar");
  const startBtn = document.getElementById("btnStartCycle");
  const stopBtn = document.getElementById("btnStopCycle");
  const spinner = document.getElementById("cycleSpinner");
  const cBox = document.getElementById("cycleCommentBox");
  const cContent = document.getElementById("cycleCommentContent");

  if (statusWidget) statusWidget.classList.remove("hidden");

  if (data.is_error || data.step === -1) {
    handleCycleError({ error: data.message });
    return;
  }

  if (data.total_accounts && data.account_idx) {
    if (data.account_label) {
      badge.innerText = `${data.account_label} (${data.account_idx}/${data.total_accounts})`;
    } else {
      badge.innerText = `Cuenta ${data.account_idx} de ${data.total_accounts}`;
    }
  } else if (data.account_label) {
    badge.innerText = data.account_label;
  }

  if (msg) msg.innerText = data.message || "";

  if (data.current_comment && cBox && cContent) {
    cBox.classList.remove("hidden");
    cContent.innerText = `"${data.current_comment}"`;
  }

  if (data.step === 100) {
    if (num) num.innerText = "¡Completado! 🎉";
    if (bar) bar.style.width = "100%";
    setCycleUiRunningState(false);
    const hrStart = document.getElementById("btnStartHateReport");
    const hrStop = document.getElementById("btnStopHateReport");
    if (hrStart) {
      hrStart.disabled = false;
      hrStart.classList.remove("opacity-50", "cursor-not-allowed");
    }
    if (hrStop) {
      hrStop.disabled = true;
      hrStop.classList.add("opacity-50", "cursor-not-allowed");
    }
    if (spinner) spinner.classList.add("hidden");
    showToast("Ciclo de automatización completado con éxito", "success");
  } else {
    if (spinner) spinner.classList.remove("hidden");
    const pct = Math.max(5, Math.min(95, (data.step || 1) * 6));
    if (bar) bar.style.width = `${pct}%`;
    if (num) num.innerText = `Paso ${data.step || 1}`;
  }
}

function handleCycleError(data) {
  const msg = document.getElementById("cycleStepMsg");
  const num = document.getElementById("cycleStepNum");
  const spinner = document.getElementById("cycleSpinner");
  const hrStart = document.getElementById("btnStartHateReport");
  const hrStop = document.getElementById("btnStopHateReport");

  setCycleUiRunningState(false);

  if (msg) msg.innerText = `Error: ${data.error || data.message || "Interrumpido"}`;
  if (num) num.innerText = "Error";
  if (spinner) spinner.classList.add("hidden");
  if (hrStart) {
    hrStart.disabled = false;
    hrStart.classList.remove("opacity-50", "cursor-not-allowed");
  }
  if (hrStop) {
    hrStop.disabled = true;
    hrStop.classList.add("opacity-50", "cursor-not-allowed");
  }
  showToast(`Error en ciclo: ${data.error || data.message}`, "error");
}



function copyToCycleComment(commentText) {
  const input = document.getElementById("cycleCustomComment");
  if (input) {
    input.value = commentText;
    showToast("Comentario fijado para el ciclo", "success");
  }
}

function handlePostAnalyzed(data) {
  const container = document.getElementById("rightAnalysisContainer");
  if (container && data.description) {
    container.innerHTML = `
      <div class="p-3 bg-surface-container rounded-lg border border-[#262a35] text-xs">
        <div class="font-bold text-primary mb-1">Visión del Post:</div>
        <p class="text-on-surface mb-2 font-mono">${escapeHtml(data.description)}</p>
        <div class="text-[11px] text-secondary font-mono">Sentimiento: ${escapeHtml(data.sentiment || "Positivo")}</div>
      </div>
    `;
  }
}

// Auto-detect format (Reel vs Post) directly when user types or pastes their link in the frontend input
function setupUrlInputListener() {
  const urlInp = document.getElementById("urlInput");
  const useCurrentCheck = document.getElementById("cycleUseCurrentScreen");
  if (!urlInp) return;
  const updateMediaTypeFromUrl = () => {
    const rawVal = urlInp.value.trim();
    if (rawVal) {
      if (useCurrentCheck && useCurrentCheck.checked) {
        useCurrentCheck.checked = false;
      }
      const val = rawVal.toLowerCase();
      if (val.includes("/reel/") || val.includes("/reels/")) {
        setMediaType("reel");
      } else if (val.includes("/p/") || val.includes("/tv/")) {
        setMediaType("post");
      }
    }
  };
  urlInp.addEventListener("input", updateMediaTypeFromUrl);
  urlInp.addEventListener("paste", () => setTimeout(updateMediaTypeFromUrl, 60));
}

function initInstagramTab() {
  setupUrlInputListener();
  loadDeviceAccountsInitialMemory();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initInstagramTab);
} else {
  initInstagramTab();
}

