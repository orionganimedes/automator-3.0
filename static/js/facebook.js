// Facebook Comment Mode Switching & Bank Helpers (Preset List / Banco Rotativo only)
function selectFbCommentMode(mode) {
  const modeInput = document.getElementById("fbSelectedCommentMode");
  const currentBadge = document.getElementById("fbCurrentCommentModeBadge");

  if (modeInput) modeInput.value = "preset_list";
  if (currentBadge) {
    currentBadge.innerText = "📝 Banco Rotativo FB (Secuencial)";
    currentBadge.className = "text-[10px] px-2.5 py-0.5 rounded font-mono bg-blue-500/10 text-blue-400 border border-blue-500/20 font-bold";
  }
}

function updateFbPastedCommentsCount() {
  const ta = document.getElementById("fbPastedCommentsTextarea");
  const badge = document.getElementById("fbPastedCommentsBadge");
  if (!ta || !badge) return;
  const count = ta.value.split("\n").map(s => s.trim()).filter(Boolean).length;
  badge.innerText = `${count} comentarios`;
}

function loadSampleFacebookComments() {
  const ta = document.getElementById("fbPastedCommentsTextarea");
  if (!ta) return;
  ta.value = [
    "Muy buena información, gracias por compartirla.",
    "Totalmente de acuerdo con el mensaje del post.",
    "Excelente publicación para reflexionar sobre este tema.",
    "Gran trabajo, se nota la dedicación y el esfuerzo.",
    "Interesante contenido, saludos cordiales."
  ].join("\n");
  updateFbPastedCommentsCount();
  selectFbCommentMode("preset_list");
  showToast("Comentarios de muestra cargados en Facebook", "info");
}

function clearFacebookComments() {
  const ta = document.getElementById("fbPastedCommentsTextarea");
  if (ta) ta.value = "";
  updateFbPastedCommentsCount();
}

function importInstagramCommentsToFb() {
  const igTa = document.getElementById("pastedCommentsTextarea");
  const fbTa = document.getElementById("fbPastedCommentsTextarea");
  if (!igTa || !igTa.value.trim()) {
    showToast("El banco de Instagram está vacío. No hay comentarios que importar.", "warning");
    return;
  }
  if (fbTa) {
    fbTa.value = igTa.value.trim();
    updateFbPastedCommentsCount();
    selectFbCommentMode("preset_list");
    showToast("¡Comentarios importados de Instagram a Facebook con éxito!", "success");
  }
}

// =====================================================================
// REQUERIMIENTO OBLIGATORIO: REVISIÓN DE CUENTAS FB LITE AL INICIAR SERVIDOR
// =====================================================================
let fbRequirementInterval = null;

async function checkFacebookAccountsRequirement() {
  try {
    const res = await fetch("/api/facebook/requirement-status");
    const data = await res.json();
    if (data.success) {
      updateFacebookRequirementUI(data);
      if (data.reviewed) {
        if (typeof loadFacebookAccountsMemory === "function") loadFacebookAccountsMemory();
      }
    }
  } catch (e) {
    console.debug("Error al consultar estado del requerimiento de cuentas FB:", e);
  }
}

function updateFacebookRequirementUI(status) {
  if (!status) return;
  const banner = document.getElementById("fbAccountsRequirementBanner");
  const icon = document.getElementById("fbRequirementIcon");
  const badge = document.getElementById("fbRequirementBadge");
  const msg = document.getElementById("fbRequirementMsg");
  const btnTrigger = document.getElementById("btnTriggerRequirementScan");
  const followBtn = document.getElementById("btnStartFbFollowCycle");
  const shareBtn = document.getElementById("btnStartFbShareCycle");

  if (!banner) return;

  if (status.scanning) {
    banner.className = "rounded-xl border p-3 flex flex-wrap sm:flex-nowrap items-center justify-between gap-3 shadow-md transition-all duration-300 bg-amber-500/10 border-amber-500/30 text-amber-300";
    if (icon) {
      icon.className = "material-symbols-outlined text-[22px] text-amber-400 animate-spin";
      icon.innerText = "sync";
    }
    if (badge) {
      badge.className = "text-[9px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono font-bold animate-pulse";
      badge.innerText = "REVISANDO CUENTAS";
    }
    if (msg) msg.innerText = status.message || "Revisando cuántas cuentas hay en Facebook Lite...";
    if (btnTrigger) {
      btnTrigger.disabled = true;
      btnTrigger.innerHTML = `<span class="material-symbols-outlined text-[14px] animate-spin">sync</span><span>Revisando...</span>`;
    }
    if (followBtn) {
      followBtn.disabled = true;
      followBtn.classList.add("opacity-50", "cursor-not-allowed");
      followBtn.title = "Revisión obligatoria de cuentas en progreso...";
    }
    if (shareBtn) {
      shareBtn.disabled = true;
      shareBtn.classList.add("opacity-50", "cursor-not-allowed");
    }
  } else if (status.reviewed) {
    banner.className = "rounded-xl border p-3 flex flex-wrap sm:flex-nowrap items-center justify-between gap-3 shadow-md transition-all duration-300 bg-emerald-500/10 border-emerald-500/30 text-emerald-300";
    if (icon) {
      icon.className = "material-symbols-outlined text-[22px] text-emerald-400";
      icon.innerText = "verified";
    }
    if (badge) {
      badge.className = "text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-mono font-bold";
      badge.innerText = `${status.total_accounts || 0} CUENTAS LISTAS`;
    }
    if (msg) msg.innerText = status.message || `Requisito completado: ${status.total_accounts} cuentas revisadas en Facebook Lite. Automatización lista para usarse.`;
    if (btnTrigger) {
      btnTrigger.disabled = false;
      btnTrigger.innerHTML = `<span class="material-symbols-outlined text-[14px]">refresh</span><span>Volver a Revisar</span>`;
    }
    if (followBtn) {
      followBtn.disabled = false;
      followBtn.classList.remove("opacity-50", "cursor-not-allowed");
      followBtn.title = "";
    }
    if (shareBtn) {
      shareBtn.disabled = false;
      shareBtn.classList.remove("opacity-50", "cursor-not-allowed");
    }
  } else {
    banner.className = "rounded-xl border p-3 flex flex-wrap sm:flex-nowrap items-center justify-between gap-3 shadow-md transition-all duration-300 bg-amber-500/10 border-amber-500/30 text-amber-300";
    if (icon) {
      icon.className = "material-symbols-outlined text-[22px] text-amber-400";
      icon.innerText = "warning";
    }
    if (badge) {
      badge.className = "text-[9px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono font-bold";
      badge.innerText = "PENDIENTE";
    }
    if (msg) msg.innerText = status.message || "Requerimiento obligatorio: Se deben revisar cuántas cuentas hay en Facebook Lite antes de usar la automatización.";
    if (btnTrigger) {
      btnTrigger.disabled = false;
      btnTrigger.innerHTML = `<span class="material-symbols-outlined text-[14px]">play_arrow</span><span>Revisar Cuentas FB</span>`;
    }
    if (followBtn) {
      followBtn.disabled = true;
      followBtn.classList.add("opacity-50", "cursor-not-allowed");
      followBtn.title = "Requerimiento: Primero se deben revisar las cuentas en Facebook Lite.";
    }
    if (shareBtn) {
      shareBtn.disabled = true;
      shareBtn.classList.add("opacity-50", "cursor-not-allowed");
    }
  }
}

async function triggerFacebookAccountsRequirementScan() {
  showToast("Iniciando revisión obligatoria de cuentas en Facebook Lite...", "info");
  try {
    const res = await fetch("/api/facebook/scan-requirement", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      checkFacebookAccountsRequirement();
    } else {
      showToast(data.message || "Revisión ya en curso", "warning");
    }
  } catch (e) {
    showToast("Error al solicitar revisión de cuentas en Facebook Lite", "error");
  }
}

// Scan accounts in Facebook Lite
async function scanFacebookLiteAccounts() {
  const btn = document.getElementById("btnScanFbAccounts");
  const origHtml = btn ? btn.innerHTML : "";
  const label = document.getElementById("fbAccountsLimitLabel");
  const badge = document.getElementById("fbAccountsBadge");

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-outlined text-[14px] animate-spin">sync</span><span>Abriendo FB Lite (esperando 38s)...</span>`;
  }
  if (label) label.innerText = "Abriendo FB Lite (38s)...";
  if (badge) badge.innerText = "Cargando FB Lite (38s)...";

  showToast("Iniciando escaneo: Abriendo Facebook Lite, esperando carga completa (38s) antes de analizar...", "info");

  try {
    const res = await fetch("/api/report/facebook/scan-accounts", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      const count = (typeof data.total_accounts !== "undefined") ? data.total_accounts : (data.count || (data.accounts ? data.accounts.length : 0));
      showToast(`¡Se encontraron ${count} cuentas en Facebook Lite! Pantalla en Home y RAM liberada.`, "success");
      await loadFacebookAccountsMemory();
    } else {
      showToast(`Aviso al escanear: ${data.error || "No se detectaron perfiles"}`, "warning");
      await loadFacebookAccountsMemory();
    }
  } catch (e) {
    showToast("Error de conexión al escanear cuentas de Facebook", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

// Load Accounts List
async function loadFacebookAccountsMemory() {
  const container = document.getElementById("fbAccountsListContainer");
  const selectEl = document.getElementById("fbSwitchAccountSelect");
  const label = document.getElementById("fbAccountsLimitLabel");
  const badge = document.getElementById("fbAccountsBadge");
  const inputToUse = document.getElementById("fbAccountsToUseInput");

  try {
    const res = await fetch("/api/report/facebook/accounts");
    const data = await res.json();
    if (data.success && data.accounts) {
      const accs = data.accounts;
      const total = (typeof data.total_accounts !== "undefined") ? data.total_accounts : accs.length;

      // Actualizar contadores y etiquetas visibles de 'Total detectadas'
      if (label) label.innerText = `Total detectadas: ${total}`;
      if (badge) badge.innerText = `${total} Cuentas`;
      if (inputToUse && total > 0) {
        inputToUse.max = Math.max(1, total);
        if (parseInt(inputToUse.value) > total || parseInt(inputToUse.value) <= 1) {
          inputToUse.value = total;
        }
      }

      const followInput = document.getElementById("fbFollowAccountsToUseInput");
      const shareInput = document.getElementById("fbShareAccountsToUseInput");
      if (followInput && total > 0) {
        followInput.max = total;
        if (parseInt(followInput.value) > total) followInput.value = total;
      }
      if (shareInput && total > 0) {
        shareInput.max = total;
        if (parseInt(shareInput.value) > total) shareInput.value = total;
      }

      // Dropdown de cambio rápido
      if (selectEl) {
        const curVal = selectEl.value;
        selectEl.innerHTML = `<option value="">-- Seleccionar cuenta (${accs.length}) --</option>` +
          accs.map((a, i) => {
            const accName = typeof a === "object" ? (a.name || a.username || a.label || `Cuenta ${i + 1}`) : String(a);
            return `<option value="${escapeHtml(accName)}">${i + 1}. ${escapeHtml(accName)}</option>`;
          }).join("");
        if (curVal) selectEl.value = curVal;
      }

      // Contenedor visual de lista de perfiles
      if (container) {
        if (accs.length === 0) {
          container.innerHTML = `<div class="p-3 text-center text-on-surface-variant text-xs font-mono">No hay cuentas detectadas en memoria. Haz clic en "Escanear Cuentas FB".</div>`;
        } else {
          container.innerHTML = accs.map((a, i) => {
            const accName = typeof a === "object" ? (a.name || a.username || a.label || `Cuenta ${i + 1}`) : String(a);
            return `
            <div class="p-2.5 rounded bg-surface-container border border-[#262a35] flex items-center justify-between text-xs">
              <div class="flex items-center gap-2">
                <span class="w-2 h-2 rounded-full bg-secondary"></span>
                <span class="text-on-surface font-semibold font-mono">${escapeHtml(accName)}</span>
              </div>
              <button onclick="switchFacebookAccountDirectly('${escapeHtmlQuotes(accName)}')" class="px-2.5 py-1 rounded bg-primary text-on-primary hover:bg-primary-container text-[11px] font-bold transition flex items-center gap-1">
                <span class="material-symbols-outlined text-[13px]">swap_horiz</span>
                <span>Cambiar</span>
              </button>
            </div>
          `;
          }).join("");
        }
      }
    }
  } catch (e) {
    console.error("Error al cargar cuentas de Facebook:", e);
  }
}

// Switch Account Directly
async function switchFacebookAccountDirectly(accountName) {
  if (!accountName) return;
  showToast(`Cambiando a cuenta: ${accountName}...`, "info");
  try {
    const res = await fetch("/api/report/facebook/switch-account", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_name: accountName })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`¡Cambio a "${accountName}" completado!`, "success");
      setTimeout(() => refreshScreen(false), 1500);
    } else {
      showToast(`Error al cambiar cuenta: ${data.error || "Fallo"}`, "error");
    }
  } catch (e) {
    showToast("Error de conexión al cambiar cuenta", "error");
  }
}

function switchFacebookAccountFromSelect() {
  const selectEl = document.getElementById("fbSwitchAccountSelect");
  if (selectEl && selectEl.value) {
    switchFacebookAccountDirectly(selectEl.value);
  }
}

function toggleFbAccountsLimitInput(mode) {
  const input = document.getElementById("fbAccountsCustomLimit");
  if (input) {
    if (mode === "custom") input.classList.remove("hidden");
    else input.classList.add("hidden");
  }
}

// Open Post / URL
async function testOpenFacebookPost() {
  const urlInput = document.getElementById("fbUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  if (!url) {
    showToast("Introduce el enlace de la publicación de Facebook", "warning");
    return;
  }
  showToast("Abriendo publicación en Facebook Lite...", "info");
  try {
    const res = await fetch("/api/facebook/open-post", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Publicación abierta con éxito", "success");
      setTimeout(() => refreshScreen(false), 1200);
    } else {
      showToast(`Error: ${data.error || "No se pudo abrir"}`, "error");
    }
  } catch (e) {
    showToast("Error al abrir publicación", "error");
  }
}

// Gentle Scroll
async function fbManualGentleScroll() {
  try {
    await fetch("/api/facebook/scroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction: "down", amount: 280 })
    });
    setTimeout(() => refreshScreen(false), 600);
  } catch (e) {
    console.error("Scroll error:", e);
  }
}

// Scan UI Elements
async function scanFacebookUI() {
  const btn = document.getElementById("btnScanFbUI");
  const container = document.getElementById("fbDetectedElementsContainer");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-outlined text-[14px] animate-spin">search</span><span>Escaneando UI...</span>`;
  }

  try {
    const res = await fetch("/api/facebook/scan-ui", { method: "POST" });
    const data = await res.json();
    if (data.success && data.elements) {
      showToast(`Detectados ${data.elements.length} elementos en pantalla`, "success");
      renderFacebookDetectedElements(data.elements);
    } else {
      showToast(`Aviso: ${data.error || "No se detectaron elementos específicos"}`, "warning");
    }
  } catch (e) {
    showToast("Error al escanear UI de Facebook", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

function renderFacebookDetectedElements(els) {
  const container = document.getElementById("fbDetectedElementsContainer");
  if (!container) return;
  if (!els || !els.length) {
    container.innerHTML = `<div class="p-2 text-xs text-on-surface-variant font-mono">Sin elementos interactivos detectados.</div>`;
    return;
  }
  container.innerHTML = els.map((el, i) => `
    <div class="p-2 rounded bg-surface-container-high border border-[#262a35] text-[11px] font-mono flex items-center justify-between">
      <span class="text-primary font-bold">${escapeHtml(el.type || "Elemento")}</span>
      <span class="text-on-surface">${escapeHtml(el.text || el.desc || "Coordenadas")}</span>
      <span class="text-secondary">[${el.x || 0}, ${el.y || 0}]</span>
    </div>
  `).join("");
}

// =====================================================================
// CICLO MULTI-CUENTA DE INTERACCIÓN & COMENTARIOS FB (REEL / POST)
// =====================================================================
let fbPostEngageStatusInterval = null;

async function startFbPostEngagementCycle() {
  const urlInput = document.getElementById("fbUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  if (!url) {
    showToast("Introduce el enlace de la publicación o Reel de Facebook", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  const doLike = document.getElementById("fbDoLike")?.checked ?? true;
  const doComment = document.getElementById("fbDoComment")?.checked ?? true;
  const accountsCount = parseInt(document.getElementById("fbPostEngageAccountsCount")?.value || "5", 10);
  const delaySecs = parseInt(document.getElementById("fbPostEngageDelaySeconds")?.value || "30", 10);
  const pastedComments = (document.getElementById("fbPastedCommentsTextarea")?.value || "")
    .split("\n").map(s => s.trim()).filter(Boolean);

  if (doComment && pastedComments.length === 0) {
    showToast("El banco de comentarios está vacío. Agrega comentarios antes de iniciar.", "warning");
    return;
  }

  const btnStart = document.getElementById("btnStartFbPostEngage");
  const btnStop = document.getElementById("btnStopFbPostEngage");
  if (btnStart) btnStart.disabled = true;
  if (btnStop) btnStop.classList.remove("hidden");

  const badge = document.getElementById("fbPostEngageStatusBadge");
  if (badge) {
    badge.innerText = "Iniciando ciclo...";
    badge.className = "px-2.5 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/40 text-[10px] font-semibold animate-pulse";
  }
  const pbarCont = document.getElementById("fbPostEngageProgressBarContainer");
  if (pbarCont) pbarCont.classList.remove("hidden");

  showToast("Iniciando ciclo de interacción en Facebook Lite...", "info");

  try {
    const res = await fetch("/api/facebook/post-engage/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        pasted_comments: pastedComments,
        accounts_limit: accountsCount,
        do_like: doLike,
        do_comment: doComment,
        antiblock_pause: delaySecs,
        package: "com.facebook.lite"
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Ciclo de interacción iniciado correctamente", "success");
      startPollingFbPostEngageStatus();
    } else {
      showToast(`Error: ${data.error || "No se pudo iniciar"}`, "error");
      resetFbPostEngageButtons();
    }
  } catch (e) {
    showToast(`Error de conexión: ${e.message}`, "error");
    resetFbPostEngageButtons();
  }
}

async function stopFbPostEngagementCycle() {
  const btnStop = document.getElementById("btnStopFbPostEngage");
  if (btnStop) btnStop.disabled = true;
  showToast("Deteniendo ciclo de interacción...", "warning");
  try {
    const res = await fetch("/api/facebook/post-engage/stop", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Detención solicitada.", "info");
    }
  } catch (e) {
    showToast("Error al solicitar detención", "error");
  }
}

function startPollingFbPostEngageStatus() {
  if (fbPostEngageStatusInterval) clearInterval(fbPostEngageStatusInterval);
  fbPostEngageStatusInterval = setInterval(pollFbPostEngageStatus, 2000);
  pollFbPostEngageStatus();
}

async function pollFbPostEngageStatus() {
  try {
    const res = await fetch("/api/facebook/post-engage/status");
    const data = await res.json();
    updateFbPostEngageUI(data);
    if (!data.is_running) {
      clearInterval(fbPostEngageStatusInterval);
      fbPostEngageStatusInterval = null;
      resetFbPostEngageButtons();
    }
  } catch (e) {
    console.error("Error polling FB post engage status:", e);
  }
}

function updateFbPostEngageUI(data) {
  const badge = document.getElementById("fbPostEngageStatusBadge");
  const text = document.getElementById("fbPostEngageProgressText");
  const pbar = document.getElementById("fbPostEngageProgressBar");

  if (badge) {
    if (data.is_running) {
      badge.innerText = data.current_account ? `Activo: ${data.current_account}` : "Procesando...";
      badge.className = "px-2.5 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/40 text-[10px] font-semibold animate-pulse";
    } else {
      const isComplete = data.status_message && (data.status_message.includes("completado") || data.status_message.includes("éxito"));
      badge.innerText = isComplete ? "¡Completado!" : "Listo para iniciar";
      badge.className = "px-2.5 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/30 text-[10px] font-semibold";
    }
  }

  if (text) {
    const cur = data.current_round || 0;
    const tot = data.total_rounds || 0;
    const rem = data.remaining_comments !== undefined ? ` • ${data.remaining_comments} com. restantes` : "";
    text.innerText = `${cur} / ${tot} cuentas${rem}`;
  }

  if (pbar && data.total_rounds > 0) {
    const pct = Math.min(100, Math.round(((data.current_round || 0) / data.total_rounds) * 100));
    pbar.style.width = `${pct}%`;
  }
}

function resetFbPostEngageButtons() {
  const btnStart = document.getElementById("btnStartFbPostEngage");
  const btnStop = document.getElementById("btnStopFbPostEngage");
  if (btnStart) btnStart.disabled = false;
  if (btnStop) {
    btnStop.disabled = false;
    btnStop.classList.add("hidden");
  }
}

// Single Engagement Action
async function runFacebookEngagement() {
  const urlInput = document.getElementById("fbUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  const useCurrent = document.getElementById("fbUseCurrentScreen")?.checked || false;
  const doLike = document.getElementById("fbDoLike")?.checked ?? true;
  const doComment = document.getElementById("fbDoComment")?.checked ?? true;
  const commentMode = "preset_list";
  const pastedComments = (document.getElementById("fbPastedCommentsTextarea")?.value || "")
    .split("\n").map(s => s.trim()).filter(Boolean);

  if (!url && !useCurrent) {
    showToast("Introduce el enlace de Facebook o marca 'Usar pantalla actual'", "warning");
    return;
  }

  if (doComment && pastedComments.length === 0) {
    showToast("El banco de comentarios de Facebook está vacío. Pega tus comentarios.", "warning");
    return;
  }

  showToast("Iniciando interacción simple en Facebook...", "info");
  try {
    const res = await fetch("/api/facebook/run-engage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        use_current_screen: useCurrent,
        do_like: doLike,
        do_comment: doComment,
        comment_mode: commentMode,
        pasted_comments: pastedComments
      })
    });
    const data = await res.json();
    if (data.success) {
      if (Array.isArray(data.remaining_comments)) {
        const ta = document.getElementById("fbPastedCommentsTextarea");
        if (ta) {
          ta.value = data.remaining_comments.join("\n");
          updateFbPastedCommentsCount();
        }
      }
      showToast("¡Interacción completada!", "success");
      setTimeout(() => refreshScreen(false), 1200);
    } else {
      showToast(`Error: ${data.error}`, "error");
    }
  } catch (e) {
    showToast("Error al ejecutar interacción", "error");
  }
}

// Feed transitions test
async function testFacebookFeedTransition() {
  showToast("Navegando al Feed de Noticias...", "info");
  try {
    const res = await fetch("/api/report/facebook/test-transition", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Transición completada", "success");
      setTimeout(() => refreshScreen(false), 1000);
    }
  } catch (e) {
    showToast("Error en transición", "error");
  }
}

async function testFacebookReturnFeed() {
  showToast("Volviendo al feed de noticias...", "info");
  try {
    const res = await fetch("/api/report/facebook/test-return-feed", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Retorno al feed exitoso", "success");
      setTimeout(() => refreshScreen(false), 1000);
    }
  } catch (e) {
    showToast("Error al retornar", "error");
  }
}

// Master Multi-Account Facebook Cycle
async function startFacebookMultiAccountCycle() {
  const urlInput = document.getElementById("autoReportProfileUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  if (!url) {
    showToast("Ingresa el enlace del perfil de Facebook a procesar", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  const limitMode = document.getElementById("fbAccountsLimitMode")?.value || "all";
  const customLimit = parseInt(document.getElementById("fbAccountsCustomLimit")?.value || "5", 10);
  const accountsLimit = limitMode === "custom" ? customLimit : 0;
  const feedBrowseSeconds = parseFloat(document.getElementById("fbFeedBrowseSlider")?.value || "60");
  const refreshInterval = parseInt(document.getElementById("fbRefreshIntervalSelect")?.value || "3", 10);
  const sleeps = (typeof getReportSleeps === "function") ? getReportSleeps() : {};
  const commentMode = document.getElementById("fbSelectedCommentMode")?.value || "auto";
  const pastedComments = (document.getElementById("fbPastedCommentsTextarea")?.value || "")
    .split("\n").map(s => s.trim()).filter(Boolean);

  const btnStart = document.getElementById("btnStartFbMultiAccountCycle");
  const btnStop = document.getElementById("btnStopFbMultiAccountCycle");
  if (btnStart) btnStart.disabled = true;
  if (btnStop) {
    btnStop.disabled = false;
    btnStop.classList.remove("opacity-50", "cursor-not-allowed");
  }

  const pbarContainer = document.getElementById("fbCycleProgressBarContainer");
  if (pbarContainer) pbarContainer.classList.remove("hidden");

  showToast("Iniciando ciclo multi-cuenta de Facebook...", "info");

  try {
    const res = await fetch("/api/report/facebook/start-cycle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        accounts_limit: accountsLimit,
        feed_browse_seconds: feedBrowseSeconds,
        feed_variance: 10.0,
        refresh_interval: refreshInterval,
        sleeps,
        comment_mode: commentMode,
        pasted_comments: pastedComments
      })
    });
    const data = await res.json();
    if (data.success) {
      startPollingFacebookCycleStatus();
    } else {
      showToast(`Aviso: ${data.error || "No se pudo iniciar el ciclo"}`, "error");
      resetFbCycleButtons();
    }
  } catch (e) {
    showToast(`Error de red: ${e.message}`, "error");
    resetFbCycleButtons();
  }
}

async function stopFacebookMultiAccountCycle() {
  const btnStop = document.getElementById("btnStopFbMultiAccountCycle");
  if (btnStop) btnStop.disabled = true;

  try {
    const res = await fetch("/api/report/facebook/stop-cycle", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Detención de ciclo Facebook solicitada", "warning");
    }
  } catch (e) {
    showToast("Error al detener ciclo", "error");
  }
}

function startPollingFacebookCycleStatus() {
  clearInterval(fbCyclePollInterval);
  fbCyclePollInterval = setInterval(pollFacebookCycleStatus, 2500);
}

async function pollFacebookCycleStatus() {
  try {
    const res = await fetch("/api/report/facebook/cycle-status");
    const data = await res.json();
    if (!data.success) return;

    const status = data.status || {};
    const pbar = document.getElementById("fbCycleProgressBar");
    const label = document.getElementById("fbCycleProgressLabel");

    if (label) {
      label.innerText = `Cuenta ${status.current_account_idx || 1}/${status.total_accounts || 1} - ${status.step_description || "En progreso..."}`;
    }

    if (pbar && status.total_accounts) {
      const pct = Math.round(((status.current_account_idx || 1) / status.total_accounts) * 100);
      pbar.style.width = `${pct}%`;
    }

    if (!status.is_running) {
      clearInterval(fbCyclePollInterval);
      resetFbCycleButtons();
      showToast("Ciclo de Facebook completado o finalizado", "success");
    }
  } catch (e) {
    console.error("Error polling FB cycle status:", e);
  }
}

function resetFbCycleButtons() {
  const btnStart = document.getElementById("btnStartFbMultiAccountCycle");
  const btnStop = document.getElementById("btnStopFbMultiAccountCycle");
  if (btnStart) btnStart.disabled = false;
  if (btnStop) {
    btnStop.disabled = true;
    btnStop.classList.add("opacity-50", "cursor-not-allowed");
  }
}

// ==========================================
// ADVANCED ACTIONS: SEGUIR & COMPARTIR (MULTI-ACCOUNT)
// ==========================================

let isFbActionsCollapsed = false;

function toggleFbActionsCollapse() {
  const content = document.getElementById("fbMultiActionContent");
  const icon = document.getElementById("fbMultiActionCollapseIcon");
  if (!content) return;

  isFbActionsCollapsed = !isFbActionsCollapsed;
  if (isFbActionsCollapsed) {
    content.classList.add("hidden");
    if (icon) icon.style.transform = "rotate(-90deg)";
  } else {
    content.classList.remove("hidden");
    if (icon) icon.style.transform = "rotate(0deg)";
  }
}

function switchFbActionsSubTab(tabName) {
  const btnFollow = document.getElementById("btnSubTabFbFollow");
  const btnShare = document.getElementById("btnSubTabFbShare");
  const contentFollow = document.getElementById("fbSubTabFollowContent");
  const contentShare = document.getElementById("fbSubTabShareContent");

  if (!btnFollow || !btnShare || !contentFollow || !contentShare) return;

  if (tabName === "follow") {
    btnFollow.className = "px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold flex items-center gap-1.5 transition bg-blue-600 text-white shadow-sm";
    btnShare.className = "px-3.5 py-1.5 rounded-lg text-xs font-mono font-medium flex items-center gap-1.5 transition text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high";
    contentFollow.classList.remove("hidden");
    contentShare.classList.add("hidden");
  } else if (tabName === "share") {
    btnShare.className = "px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold flex items-center gap-1.5 transition bg-emerald-600 text-white shadow-sm";
    btnFollow.className = "px-3.5 py-1.5 rounded-lg text-xs font-mono font-medium flex items-center gap-1.5 transition text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high";
    contentShare.classList.remove("hidden");
    contentFollow.classList.add("hidden");
  }
}

function toggleFbShareCommentField(mode) {
  const container = document.getElementById("fbShareCustomCommentContainer");
  if (!container) return;
  if (mode === "share_with_text") {
    container.classList.remove("hidden");
  } else {
    container.classList.add("hidden");
  }
}

async function testOpenFbTargetUrl(inputId) {
  const input = document.getElementById(inputId);
  const url = input ? input.value.trim() : "";
  if (!url) {
    showToast("Introduce el enlace de Facebook primero", "warning");
    return;
  }
  showToast("Abriendo enlace en Facebook Lite en el teléfono...", "info");
  try {
    const res = await fetch("/api/facebook/open-post", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Enlace abierto con éxito en el teléfono", "success");
      setTimeout(() => refreshScreen(false), 1200);
    } else {
      showToast(`Aviso: ${data.error || "No se pudo abrir el enlace"}`, "error");
    }
  } catch (e) {
    showToast("Error de conexión al abrir enlace", "error");
  }
}

let fbFollowPollTimer = null;

function setFbFollowAccountsCount(val) {
  const inp = document.getElementById("fbFollowAccountsToUseInput");
  if (!inp) return;
  if (val === 'all') {
    inp.value = 100;
  } else {
    inp.value = parseInt(val) || 1;
  }
}

function handleFbFollowStep(data) {
  if (!data) return;
  const statusBadge = document.getElementById("fbFollowStatusBadge");
  const progressText = document.getElementById("fbFollowProgressText");
  const mainPill = document.getElementById("fbMultiActionStatusPill");

  const msg = data.message || "";
  const curRound = data.current_round || 0;
  const totalRounds = data.total_rounds || 0;
  const accName = data.account_name || "";

  if (totalRounds > 0 && curRound > 0 && progressText) {
    progressText.innerText = `${curRound} / ${totalRounds} cuentas${accName ? ` (${accName})` : ''}`;
  }

  if (statusBadge && msg) {
    if (data.step === 100) {
      statusBadge.innerText = "¡Ciclo Completado! 🎉";
      statusBadge.className = "px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-[10px] font-semibold";
      resetFbFollowButtons();
    } else if (data.step === -1) {
      statusBadge.innerText = "Ciclo Detenido";
      statusBadge.className = "px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px] font-semibold";
      resetFbFollowButtons();
    } else {
      statusBadge.innerText = msg.length > 55 ? msg.substring(0, 52) + "..." : msg;
      statusBadge.className = "px-2.5 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/40 text-[10px] font-semibold animate-pulse";
    }
  }

  if (mainPill && msg) {
    mainPill.innerText = msg.length > 30 ? msg.substring(0, 27) + "..." : msg;
    mainPill.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30";
  }
}

function resetFbFollowButtons() {
  const btnStart = document.getElementById("btnStartFbFollowCycle");
  const btnStop = document.getElementById("btnStopFbFollowCycle");
  const mainPill = document.getElementById("fbMultiActionStatusPill");

  if (btnStart) {
    btnStart.disabled = false;
    btnStart.classList.remove("opacity-50");
    btnStart.innerHTML = `<span class="material-symbols-outlined text-[16px]">person_add</span><span>Iniciar Ciclo Seguir</span>`;
  }
  if (btnStop) {
    btnStop.disabled = false;
    btnStop.classList.remove("opacity-50");
    btnStop.classList.add("hidden");
  }
  if (mainPill) {
    mainPill.innerText = "Listo";
    mainPill.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant";
  }
}

async function startFbFollowCycle() {
  const urlInput = document.getElementById("fbFollowPageUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  if (!url) {
    showToast("Por favor introduce el enlace de la página o perfil de FB a seguir", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  const accountsCount = parseInt(document.getElementById("fbFollowAccountsToUseInput")?.value) || 5;
  const delaySec = parseInt(document.getElementById("fbFollowDelaySeconds")?.value) || 18;

  const btnStart = document.getElementById("btnStartFbFollowCycle");
  const btnStop = document.getElementById("btnStopFbFollowCycle");
  const statusBadge = document.getElementById("fbFollowStatusBadge");
  const progressText = document.getElementById("fbFollowProgressText");
  const mainPill = document.getElementById("fbMultiActionStatusPill");

  if (btnStart) {
    btnStart.disabled = true;
    btnStart.classList.add("opacity-50");
    btnStart.innerHTML = `<span class="material-symbols-outlined text-[15px] animate-spin">sync</span><span>Iniciando ciclo...</span>`;
  }
  if (btnStop) btnStop.classList.remove("hidden");
  if (statusBadge) {
    statusBadge.innerText = `Iniciando ciclo (${accountsCount} cuentas)...`;
    statusBadge.className = "px-2.5 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/40 text-[10px] font-semibold animate-pulse";
  }
  if (progressText) progressText.innerText = `0 / ${accountsCount} cuentas`;
  if (mainPill) {
    mainPill.innerText = "Iniciando ciclo...";
    mainPill.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30";
  }

  showToast(`Iniciando ciclo multi-cuenta de Seguir (${accountsCount} cuentas)...`, "info");

  try {
    const res = await fetch("/api/facebook/start-follow-cycle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        accounts_limit: accountsCount,
        delay_between_accounts: delaySec,
        package: "com.facebook.lite"
      })
    });

    const data = await res.json();
    if (!data.success) {
      showToast(`Aviso: ${data.error || "No se pudo iniciar el ciclo"}`, "warning");
      if (statusBadge) statusBadge.innerText = data.error || "Error al iniciar";
      resetFbFollowButtons();
      return;
    }

    showToast("Ciclo multi-cuenta iniciado correctamente.", "success");

    // Iniciar sondeo de respaldo en segundo plano
    if (fbFollowPollTimer) clearInterval(fbFollowPollTimer);
    fbFollowPollTimer = setInterval(async () => {
      try {
        const sRes = await fetch("/api/facebook/follow-cycle-status");
        const sData = await sRes.json();
        if (sData) {
          if (progressText && sData.total_rounds) {
            progressText.innerText = `${sData.current_round} / ${sData.total_rounds} cuentas${sData.current_account ? ` (${sData.current_account})` : ''}`;
          }
          if (!sData.is_running) {
            clearInterval(fbFollowPollTimer);
            fbFollowPollTimer = null;
            resetFbFollowButtons();
            if (statusBadge) {
              statusBadge.innerText = sData.status_message || "Ciclo finalizado";
              statusBadge.className = "px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-[10px] font-semibold";
            }
            setTimeout(() => refreshScreen(false), 1200);
          }
        }
      } catch (ePoll) {
        console.debug("Aviso en sondeo de estado:", ePoll);
      }
    }, 2000);

  } catch (e) {
    showToast(`Error al iniciar ciclo de seguir: ${e.message}`, "error");
    if (statusBadge) statusBadge.innerText = "Error de conexión";
    resetFbFollowButtons();
  }
}

async function stopFbFollowCycle() {
  const btnStop = document.getElementById("btnStopFbFollowCycle");
  const statusBadge = document.getElementById("fbFollowStatusBadge");
  if (btnStop) {
    btnStop.disabled = true;
    btnStop.classList.add("opacity-50");
  }

  try {
    const res = await fetch("/api/facebook/stop-follow-cycle", { method: "POST" });
    const data = await res.json();
    showToast("Detención de ciclo solicitada", "warning");
    if (statusBadge) statusBadge.innerText = "Deteniendo...";
  } catch (e) {
    showToast("Error al solicitar detención", "error");
  } finally {
    if (fbFollowPollTimer) {
      clearInterval(fbFollowPollTimer);
      fbFollowPollTimer = null;
    }
    setTimeout(resetFbFollowButtons, 2000);
  }
}

function startFbShareCycle() {
  const urlInput = document.getElementById("fbSharePostUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  if (!url) {
    showToast("Por favor introduce el enlace de la publicación a compartir", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  const shareMode = document.getElementById("fbShareModeSelect")?.value || "share_now";
  const accountsCount = parseInt(document.getElementById("fbShareAccountsToUseInput")?.value) || 5;
  const delaySec = parseInt(document.getElementById("fbShareDelaySeconds")?.value) || 20;

  const btnStart = document.getElementById("btnStartFbShareCycle");
  const btnStop = document.getElementById("btnStopFbShareCycle");
  const statusBadge = document.getElementById("fbShareStatusBadge");
  const progressText = document.getElementById("fbShareProgressText");
  const mainPill = document.getElementById("fbMultiActionStatusPill");

  if (btnStart) {
    btnStart.disabled = true;
    btnStart.classList.add("opacity-50");
    btnStart.innerHTML = `<span class="material-symbols-outlined text-[15px] animate-spin">sync</span><span>Iniciando Compartir...</span>`;
  }
  if (btnStop) btnStop.classList.remove("hidden");
  if (statusBadge) {
    statusBadge.innerText = "Ciclo Activo (Compartir)";
    statusBadge.className = "px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-[10px] font-semibold animate-pulse";
  }
  if (progressText) progressText.innerText = `0 / ${accountsCount} cuentas`;
  if (mainPill) {
    mainPill.innerText = "Compartir activo";
    mainPill.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
  }

  showToast(`Ciclo COMPARTIR configurado para ${accountsCount} cuentas (${shareMode}). Frontend preparado para conectar al backend.`, "info");
}

function stopFbShareCycle() {
  const btnStart = document.getElementById("btnStartFbShareCycle");
  const btnStop = document.getElementById("btnStopFbShareCycle");
  const statusBadge = document.getElementById("fbShareStatusBadge");
  const mainPill = document.getElementById("fbMultiActionStatusPill");

  if (btnStart) {
    btnStart.disabled = false;
    btnStart.classList.remove("opacity-50");
    btnStart.innerHTML = `<span class="material-symbols-outlined text-[16px]">share</span><span>Iniciar Ciclo Compartir</span>`;
  }
  if (btnStop) btnStop.classList.add("hidden");
  if (statusBadge) {
    statusBadge.innerText = "Ciclo Detenido";
    statusBadge.className = "px-2.5 py-0.5 rounded-full bg-surface-container-high text-on-surface-variant text-[10px] font-semibold";
  }
  if (mainPill) {
    mainPill.innerText = "Listo";
    mainPill.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant";
  }
  showToast("Ciclo de compartir detenido", "warning");
}
