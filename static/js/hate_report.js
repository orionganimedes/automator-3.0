// static/js/hate_report.js - Hate Comments & Auto-Report Engine

function switchHateSubMode(mode) {
  const btnComments = document.getElementById("btnHateSubModeComments");
  const btnReport = document.getElementById("btnHateSubModeReport");
  const boxComments = document.getElementById("hateSubModeCommentsContainer");
  const boxReport = document.getElementById("hateSubModeReportContainer");

  if (mode === "comments") {
    if (btnComments) {
      btnComments.className = "px-4 py-2 rounded bg-primary text-on-primary font-semibold text-xs transition shadow flex items-center gap-1.5";
    }
    if (btnReport) {
      btnReport.className = "px-4 py-2 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant font-medium text-xs transition flex items-center gap-1.5";
    }
    if (boxComments) boxComments.classList.remove("hidden");
    if (boxReport) boxReport.classList.add("hidden");
  } else {
    if (btnReport) {
      btnReport.className = "px-4 py-2 rounded bg-error text-on-error font-semibold text-xs transition shadow flex items-center gap-1.5";
    }
    if (btnComments) {
      btnComments.className = "px-4 py-2 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant font-medium text-xs transition flex items-center gap-1.5";
    }
    if (boxComments) boxComments.classList.add("hidden");
    if (boxReport) boxReport.classList.remove("hidden");
  }
}

// Delays configuration
function applySleepPreset(preset) {
  const presets = {
    fast: { before: 0.5, after: 1.0 },
    normal: { before: 1.0, after: 2.0 },
    safe: { before: 2.0, after: 3.5 }
  };
  const val = presets[preset] || presets.normal;
  for (let i = 1; i <= 8; i++) {
    const sb = document.getElementById(`sleepStep${i}Before`);
    const sa = document.getElementById(`sleepStep${i}After`);
    if (sb) sb.value = val.before;
    if (sa) sa.value = val.after;
  }
  showToast(`Preset de tiempos aplicado: ${preset.toUpperCase()}`, "info");
}

function getReportSleeps() {
  const sleeps = {};
  for (let i = 1; i <= 8; i++) {
    const sb = document.getElementById(`sleepStep${i}Before`);
    const sa = document.getElementById(`sleepStep${i}After`);
    sleeps[`step${i}_before`] = parseFloat(sb?.value || "1.0");
    sleeps[`step${i}_after`] = parseFloat(sa?.value || "2.0");
  }
  return sleeps;
}

// Single step runners (Pasos 1 al 8)
async function runAutoReportStep(stepNum, endpoint) {
  const sleeps = getReportSleeps();
  showToast(`Ejecutando Paso ${stepNum}...`, "info");
  try {
    const res = await fetch(`/api/report/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sleep_before: sleeps[`step${stepNum}_before`] || 1.0,
        sleep_after: sleeps[`step${stepNum}_after`] || 2.0
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Paso ${stepNum} ejecutado correctamente`, "success");
      setTimeout(() => refreshScreen(false), 1000);
    } else {
      showToast(`Aviso en Paso ${stepNum}: ${data.error || data.message || "Fallo"}`, "warning");
    }
  } catch (e) {
    showToast(`Error al ejecutar Paso ${stepNum}: ${e.message}`, "error");
  }
}

function runAutoReportStep1() { runAutoReportStep(1, "open-step1"); }
function runAutoReportStep1OnlyTouch() { runAutoReportStep(1, "open-step1"); }
function runAutoReportStep2OnlyTouch() { runAutoReportStep(2, "step2-denunciar"); }
function runAutoReportStep3OnlyTouch() { runAutoReportStep(3, "step3-odio"); }
function runAutoReportStep4OnlyTouch() { runAutoReportStep(4, "step4-promueve-odio"); }
function runAutoReportStep5OnlyTouch() { runAutoReportStep(5, "step5-lenguaje-odio"); }
function runAutoReportStep6OnlyTouch() { runAutoReportStep(6, "step6-enviar"); }
function runAutoReportStep7OnlyTouch() { runAutoReportStep(7, "step7-siguiente"); }
function runAutoReportStep8OnlyTouch() { runAutoReportStep(8, "step8-listo"); }

async function runAutoReportSteps1And2() {
  showToast("Ejecutando Pasos 1 y 2 combinados...", "info");
  try {
    const sleeps = getReportSleeps();
    const res = await fetch("/api/report/run-steps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ steps: [1, 2], sleeps })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Pasos 1 y 2 completados", "success");
      setTimeout(() => refreshScreen(false), 1200);
    }
  } catch (e) {
    showToast("Error en Pasos 1 y 2", "error");
  }
}

// Run Full Auto-Report Flow (Pasos 1 al 8 continuos)
async function runAutoReportFullFlow() {
  const urlInput = document.getElementById("autoReportProfileUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  const useCurrent = url ? false : ((document.getElementById("autoReportUseCurrentScreen") || document.getElementById("hrUseCurrentScreen"))?.checked ?? false);
  const pkgEl = document.querySelector('input[name="autoReportAppChoice"]:checked');
  const pkg = pkgEl ? pkgEl.value : "com.facebook.lite";
  const sleeps = getReportSleeps();

  if (!url && !useCurrent) {
    showToast("Ingresa el enlace del perfil o activa 'Usar perfil en pantalla'", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  showToast("Iniciando flujo completo de reporte (Pasos 1 al 8)...", "info");

  try {
    const res = await fetch("/api/report/run-full-flow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        use_current_screen: useCurrent,
        package: pkg,
        sleeps
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast("¡Reporte completado y enviado con éxito!", "success");
      setTimeout(() => refreshScreen(false), 1500);
    } else {
      showToast(`Aviso: ${data.error || "Flujo interrumpido"}`, "warning");
    }
  } catch (e) {
    showToast(`Error al ejecutar flujo completo: ${e.message}`, "error");
  }
}

// Hate Comments Submode helpers & Platform Switching
function onHateReportPlatformChange(pkg) {
  if (typeof setAppPackage === "function") {
    setAppPackage(pkg);
  }
  const instaCard = document.getElementById("hrPlatformInstaCard");
  const fbCard = document.getElementById("hrPlatformFbCard");
  if (pkg === "com.instagram.lite") {
    if (instaCard) {
      instaCard.classList.add("border-primary", "bg-primary/10");
      instaCard.classList.remove("border-[#262a35]", "bg-surface-container");
    }
    if (fbCard) {
      fbCard.classList.remove("border-primary", "bg-primary/10");
      fbCard.classList.add("border-[#262a35]", "bg-surface-container");
    }
  } else {
    if (fbCard) {
      fbCard.classList.add("border-blue-500", "bg-blue-500/10");
      fbCard.classList.remove("border-[#262a35]", "bg-surface-container");
    }
    if (instaCard) {
      instaCard.classList.remove("border-blue-500", "border-primary", "bg-blue-500/10", "bg-primary/10");
      instaCard.classList.add("border-[#262a35]", "bg-surface-container");
    }
  }
}

async function scanHateReportAccounts() {
  if (typeof scanDeviceAccounts === "function") {
    await scanDeviceAccounts();
  }
  const a = document.getElementById("accountsToUseInput");
  const hrA = document.getElementById("hrAccountsToUseInput");
  const hrLimit = document.getElementById("hrAccountsLimitLabel");
  const accLimit = document.getElementById("accountsLimitLabel");
  if (a && hrA && a.value) hrA.value = a.value;
  if (hrLimit && accLimit && accLimit.innerText) hrLimit.innerText = accLimit.innerText;
}

function updateHateReportCommentCount() {
  const ta = document.getElementById("hrPastedCommentsTextarea") || document.getElementById("hrCommentsTextarea");
  const badge = document.getElementById("hrCommentCountBadge");
  if (!ta || !badge) return;
  const count = ta.value.split("\n").map(s => s.trim()).filter(Boolean).length;
  badge.innerText = `${count} comentarios`;
}

function loadHateReportSampleComments() {
  const ta = document.getElementById("hrPastedCommentsTextarea") || document.getElementById("hrCommentsTextarea");
  if (!ta) return;
  ta.value = [
    "No comparto esta información, carece de sustento.",
    "Es importante verificar las fuentes antes de publicar.",
    "Totalmente en desacuerdo con lo que se expone aquí.",
    "Difiero completamente de este enfoque.",
    "Esto no refleja la realidad del tema."
  ].join("\n");
  updateHateReportCommentCount();
  showToast("Comentarios de muestra cargados", "info");
}

function clearHateReportComments() {
  const ta = document.getElementById("hrPastedCommentsTextarea") || document.getElementById("hrCommentsTextarea");
  if (ta) ta.value = "";
  updateHateReportCommentCount();
}

async function hrOpenPostLink() {
  const input = document.getElementById("hrUrlInput") || document.getElementById("hrTargetPostUrlInput");
  const url = input ? input.value.trim() : "";
  if (!url) {
    showToast("Introduce el enlace de la publicación a abrir", "warning");
    return;
  }
  showToast("Abriendo publicación destino...", "info");
  try {
    await fetch("/api/manual/open-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    setTimeout(() => refreshScreen(false), 1200);
  } catch (e) {
    showToast("Error al abrir publicación", "error");
  }
}

async function startHateReportAutomation() {
  const urlInput = document.getElementById("hrUrlInput") || document.getElementById("hrTargetPostUrlInput");
  const url = urlInput ? urlInput.value.trim() : "";
  const useCurrentScreen = document.getElementById("hrUseCurrentScreen")?.checked ?? false;
  const pkg = document.querySelector('input[name="hrPlatformChoice"]:checked')?.value || "com.instagram.lite";
  const accountsToUse = parseInt(document.getElementById("hrAccountsToUseInput")?.value) || 1;
  const cycleCount = parseInt(document.getElementById("hrCycleCountInput")?.value) || 1;
  const doLike = document.getElementById("hrDoLike")?.checked ?? false;
  const doComment = document.getElementById("hrDoComment")?.checked ?? true;
  const cooldownSeconds = parseInt(document.getElementById("hrCooldownSelect")?.value) || 0;
  const clearRam = document.getElementById("hrClearRamCheck")?.checked ?? true;

  if (!url && !useCurrentScreen) {
    showToast("Por favor introduce el enlace de la publicación de Instagram o Facebook, o marca 'Usar publicación actual'.", "warning");
    if (urlInput) urlInput.focus();
    return;
  }

  const ta = document.getElementById("hrPastedCommentsTextarea") || document.getElementById("hrCommentsTextarea");
  const pastedComments = (ta ? ta.value : "").split("\n").map(l => l.trim()).filter(Boolean);

  if (doComment && pastedComments.length === 0) {
    showToast("Has activado la acción de comentar pero la lista está vacía.", "warning");
    if (ta) ta.focus();
    return;
  }

  const btnStart = document.getElementById("btnStartHateReport");
  const btnStop = document.getElementById("btnStopHateReport");
  if (btnStart) {
    btnStart.disabled = true;
    btnStart.classList.add("opacity-50", "cursor-not-allowed");
  }
  if (btnStop) {
    btnStop.disabled = false;
    btnStop.classList.remove("opacity-50", "cursor-not-allowed");
  }

  showToast("Iniciando ciclo Hate / Report...", "info");

  try {
    const res = await fetch("/api/manual-automation/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: url,
        accounts_to_use: accountsToUse,
        cycle_count: cycleCount,
        do_like: doLike,
        do_comment: doComment,
        cooldown_seconds: cooldownSeconds,
        clear_ram: clearRam,
        pasted_comments: pastedComments,
        app_package: pkg,
        use_current_screen: useCurrentScreen
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast("¡Automatización Hate / Report iniciada!", "success");
      if (typeof startPollingCycleStatus === "function") {
        startPollingCycleStatus();
      }
    } else {
      showToast(`Error al iniciar: ${data.error || "Desconocido"}`, "error");
      if (btnStart) {
        btnStart.disabled = false;
        btnStart.classList.remove("opacity-50", "cursor-not-allowed");
      }
    }
  } catch (e) {
    showToast(`Error de conexión: ${e.message}`, "error");
    if (btnStart) {
      btnStart.disabled = false;
      btnStart.classList.remove("opacity-50", "cursor-not-allowed");
    }
  }
}
