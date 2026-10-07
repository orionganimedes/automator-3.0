// static/js/core.js - Core State, SSE, and Helpers for Automator 2.0

let totalPostsCount = 0;
let autoRefreshInterval = null;
let savedResultsCache = [];
let lastStatusData = null;

// Notification Toast
function showToast(message, type = "info") {
  let container = document.getElementById("toastContainer");
  if (!container) {
    container = document.createElement("div");
    container.id = "toastContainer";
    container.className = "fixed bottom-5 right-5 z-[9999] flex flex-col gap-2 max-w-sm pointer-events-none";
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  const bgClasses = {
    info: "bg-[#171b26] border-[#374151] text-[#dfe2f1]",
    success: "bg-[#003824] border-[#00a572] text-[#4edea3]",
    warning: "bg-[#3e2400] border-[#ca8100] text-[#ffb95f]",
    error: "bg-[#690005] border-[#93000a] text-[#ffb4ab]"
  };

  const icons = {
    info: "info",
    success: "check_circle",
    warning: "warning",
    error: "error"
  };

  const selectedBg = bgClasses[type] || bgClasses.info;
  const icon = icons[type] || "info";

  toast.className = `p-3 rounded-lg border shadow-xl flex items-center gap-2.5 text-xs font-medium animate-fade-in ${selectedBg}`;
  toast.innerHTML = `
    <span class="material-symbols-outlined text-[18px] shrink-0">${icon}</span>
    <span class="flex-1 leading-snug">${escapeHtml(message)}</span>
    <button class="text-on-surface-variant hover:text-on-surface ml-1" onclick="this.parentElement.remove()">
      <span class="material-symbols-outlined text-[14px]">close</span>
    </button>
  `;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(8px)";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Escapes
function escapeHtml(str) {
  if (typeof str !== "string") return String(str || "");
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeHtmlQuotes(str) {
  if (typeof str !== "string") return "";
  return str.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copiado al portapapeles", "success");
  } catch (e) {
    showToast("Error al copiar texto", "error");
  }
}

// Poll Core Status
async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    lastStatusData = data;

    // Device Badge & Info
    const devDot = document.getElementById("deviceDot");
    const devText = document.getElementById("deviceText");
    const devSerial = document.getElementById("topDeviceSerial");
    const devStats = document.getElementById("topDeviceStats");
    const leftDevStatus = document.getElementById("leftDeviceStatusPill");
    const leftDevModel = document.getElementById("leftDeviceModel");

    if (data.device && data.device.connected) {
      if (devDot) devDot.className = "w-2 h-2 rounded-full bg-secondary shadow-[0_0_8px_rgba(78,222,163,0.5)]";
      if (devText) devText.innerText = "ADB CONNECTED";
      if (devSerial) devSerial.innerText = data.device.model || data.device.device_id || "Android Device";
      if (devStats) devStats.innerText = `${data.device.battery || "98%"} • ${data.device.latency || "12ms"}`;
      if (leftDevStatus) {
        leftDevStatus.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-secondary animate-pulse"></span><span>ACTIVO</span>`;
        leftDevStatus.className = "text-[10px] px-2 py-0.5 rounded bg-secondary/10 text-secondary border border-secondary/30 font-mono flex items-center gap-1";
      }
      if (leftDevModel) leftDevModel.innerText = data.device.model || "Android Phone";
    } else {
      if (devDot) devDot.className = "w-2 h-2 rounded-full bg-error shadow-[0_0_8px_rgba(244,63,94,0.5)]";
      if (devText) devText.innerText = "NO ADB DEVICE";
      if (devSerial) devSerial.innerText = "Desconectado";
      if (devStats) devStats.innerText = "--";
      if (leftDevStatus) {
        leftDevStatus.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-error"></span><span>OFFLINE</span>`;
        leftDevStatus.className = "text-[10px] px-2 py-0.5 rounded bg-error/10 text-error border border-error/30 font-mono flex items-center gap-1";
      }
    }

    // Ollama Badge
    const olDot = document.getElementById("ollamaDot");
    const olText = document.getElementById("ollamaText");
    const olModel = document.getElementById("topOllamaModel");

    if (data.ollama && data.ollama.online) {
      const hasV = data.ollama.has_vision;
      const hasT = data.ollama.has_text;
      if (hasV && hasT) {
        if (olDot) olDot.className = "w-2 h-2 rounded-full bg-secondary shadow-[0_0_8px_rgba(78,222,163,0.5)]";
        if (olText) olText.innerText = "OLLAMA ACTIVE";
      } else {
        if (olDot) olDot.className = "w-2 h-2 rounded-full bg-tertiary shadow-[0_0_8px_rgba(255,185,95,0.5)]";
        if (olText) olText.innerText = "OLLAMA (INCOMPLETO)";
      }
      if (olModel) olModel.innerText = (data.config && data.config.vision_model) || "Ready";
    } else {
      if (olDot) olDot.className = "w-2 h-2 rounded-full bg-error shadow-[0_0_8px_rgba(244,63,94,0.5)]";
      if (olText) olText.innerText = "OLLAMA OFFLINE";
      if (olModel) olModel.innerText = "No disponible";
    }

    // Config Model labels
    if (data.config && data.config.vision_model && data.config.text_model) {
      if (typeof updateActiveModelLabels === "function") {
        updateActiveModelLabels(data.config.vision_model, data.config.text_model);
      }
    }

    // Automation State
    if (data.automation && data.automation.state) {
      updateStateBadge(data.automation.state);
    }

  } catch (err) {
    console.warn("Status poll error:", err);
  }
}

// Update State Badge
function updateStateBadge(state) {
  const badge = document.getElementById("stateBadge");
  const dot = document.getElementById("stateDot");
  const text = document.getElementById("stateText");
  const startBtn = document.getElementById("btnStartCycle") || document.getElementById("startBtn");
  const stopBtn = document.getElementById("btnStopCycle") || document.getElementById("stopBtn");
  const abortBtn = document.getElementById("topAbortBtn");

  if (text) text.innerText = state;

  if (state === "RUNNING") {
    if (dot) dot.className = "w-2 h-2 rounded-full bg-secondary animate-ping";
    if (badge) badge.className = "flex items-center gap-1.5 px-3 py-1 rounded bg-secondary/10 border border-secondary/30 text-xs font-semibold text-secondary";
    if (startBtn) {
      startBtn.disabled = true;
      startBtn.classList.add("opacity-50", "cursor-not-allowed");
    }
    if (stopBtn) stopBtn.disabled = false;
    if (abortBtn) abortBtn.classList.remove("hidden");
  } else if (state === "STOPPING") {
    if (dot) dot.className = "w-2 h-2 rounded-full bg-tertiary animate-pulse";
    if (badge) badge.className = "flex items-center gap-1.5 px-3 py-1 rounded bg-tertiary/10 border border-tertiary/30 text-xs font-semibold text-tertiary";
  } else {
    if (dot) dot.className = "w-2 h-2 rounded-full bg-on-surface-variant";
    if (badge) badge.className = "flex items-center gap-1.5 px-3 py-1 rounded bg-surface-container-low border border-[#262a35] text-xs font-semibold text-on-surface-variant";
    if (startBtn) {
      startBtn.disabled = false;
      startBtn.classList.remove("opacity-50", "cursor-not-allowed");
    }
    if (stopBtn) stopBtn.disabled = true;
    if (abortBtn) abortBtn.classList.add("hidden");
  }
}

// Connect Event Stream (SSE)
function connectEventStream() {
  const source = new EventSource("/api/logs/stream");

  source.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);

      if (payload.type === "log") {
        if (typeof appendLog === "function") appendLog(payload);
      } else if (payload.type === "screen_updated") {
        if (typeof refreshScreen === "function") refreshScreen(false);
      } else if (payload.type === "status_change") {
        updateStateBadge(payload.data.state);
      } else if (payload.type === "new_analysis") {
        renderAnalysisCard(payload.data);
      } else if (payload.type === "cycle_step") {
        if (typeof handleCycleStep === "function") handleCycleStep(payload.data);
      } else if (payload.type === "cycle_error") {
        if (typeof handleCycleError === "function") handleCycleError(payload.data);
      } else if (payload.type === "post_engage_step") {
        if (typeof handleCycleStep === "function") handleCycleStep(payload.data);
        if (typeof handleFbFollowStep === "function") handleFbFollowStep(payload.data);
      } else if (payload.type === "fb_requirement_status") {
        if (typeof updateFacebookRequirementUI === "function") updateFacebookRequirementUI(payload.data);
        if (payload.data && payload.data.reviewed && typeof loadFacebookAccountsMemory === "function") {
          loadFacebookAccountsMemory();
        }
      } else if (payload.type === "post_analyzed") {
        if (typeof handlePostAnalyzed === "function") handlePostAnalyzed(payload.data);
      } else if (payload.type === "comment_generated") {
        const cBox = document.getElementById("cycleCommentBox");
        const cArch = document.getElementById("cycleCommentArchLabel");
        const cContent = document.getElementById("cycleCommentContent");
        if (cBox && cContent) {
          cBox.classList.remove("hidden");
          cContent.innerText = `"${payload.data.comment}"`;
          if (cArch && payload.data.archetype) {
            const archName = payload.data.archetype.name || (typeof payload.data.archetype === "string" ? payload.data.archetype : "Humano");
            const reg = payload.data.archetype.regionalism ? `[${payload.data.archetype.regionalism}] ` : "";
            const isPreset = payload.data.origin === "preset_list" || archName.toLowerCase().includes("predefinid") || archName.toLowerCase().includes("banco") || archName.toLowerCase().includes("lista");

            if (isPreset) {
              cArch.innerHTML = `<span class="material-symbols-outlined text-[14px] text-emerald-400">playlist_play</span> <span class="text-emerald-300 font-semibold">${archName}</span>`;
            } else {
              cArch.innerHTML = `<span class="material-symbols-outlined text-[14px] text-tertiary">auto_awesome</span> <span>${reg}${archName}</span>`;
            }

            const cBadge = document.getElementById("cycleCommentOriginBadge");
            if (cBadge) {
              if (isPreset) {
                cBadge.textContent = "LISTA PREDEFINIDA";
                cBadge.className = "badge-label px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 text-[9px] font-bold";
              } else {
                cBadge.textContent = "IA OLLAMA";
                cBadge.className = "badge-label px-1.5 py-0.5 rounded bg-secondary/20 text-secondary border border-secondary/40 text-[9px] font-bold";
              }
            }
          }
        }
      } else if (payload.type === "comment_bank_updated") {
        const platform = payload.data.platform;
        const remaining = payload.data.remaining_comments || [];
        const consumed = payload.data.consumed_comment || "";

        if (platform === "instagram") {
          const ta = document.getElementById("pastedCommentsTextarea");
          if (ta) {
            ta.value = remaining.join("\n");
            if (typeof updatePastedCommentsCount === "function") {
              updatePastedCommentsCount();
            }
          }
        } else if (platform === "facebook") {
          const ta = document.getElementById("fbPastedCommentsTextarea");
          if (ta) {
            ta.value = remaining.join("\n");
            if (typeof updateFbPastedCommentsCount === "function") {
              updateFbPastedCommentsCount();
            }
          }
        }
        if (consumed) {
          const shortText = consumed.length > 40 ? consumed.substring(0, 37) + "..." : consumed;
          showToast(`Comentario utilizado y removido (${platform === "instagram" ? "IG" : "FB"}): "${shortText}"`, "info");
        }
      } else if (payload.type === "models_updated") {
        if (typeof updateActiveModelLabels === "function") {
          updateActiveModelLabels(payload.data.vision_model, payload.data.text_model);
        }
      }
    } catch (e) {
      // heartbeat ping or comment
    }
  };

  source.onerror = () => {
    console.warn("SSE stream disconnected. Reconnecting in 3s...");
  };
}

// Render Analysis Card (Live feed analysis)
function renderAnalysisCard(data) {
  totalPostsCount++;
  const container = document.getElementById("feedCardsContainer");
  const rightContainer = document.getElementById("rightAnalysisContainer");
  const countEl = document.getElementById("totalPostsCount");
  if (countEl) countEl.innerText = totalPostsCount;

  const cardHtml = `
    <div class="bg-surface-container border border-[#262a35] rounded-xl p-4 shadow-lg mb-3">
      <div class="flex items-center justify-between border-b border-[#262a35] pb-2 mb-3">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-[18px] text-primary">visibility</span>
          <span class="font-bold text-xs text-on-surface">Post Analizado #${totalPostsCount}</span>
        </div>
        <span class="font-mono text-[10px] text-on-surface-variant">${new Date().toLocaleTimeString()}</span>
      </div>
      <p class="text-xs text-on-surface mb-2 font-mono">${escapeHtml(data.description || "Sin descripción")}</p>
      ${data.generated_comment ? `
        <div class="p-2.5 rounded bg-surface-container-low border border-primary/20 text-xs text-primary font-mono mb-2">
          <strong>Comentario:</strong> "${escapeHtml(data.generated_comment)}"
        </div>` : ""}
      <div class="flex items-center justify-between text-[11px] text-on-surface-variant pt-1">
        <span>Confianza: <strong class="text-secondary">${Math.round((data.confidence || 0.95) * 100)}%</strong></span>
        <span>Acción: <strong class="text-tertiary">${escapeHtml(data.action_taken || "Comentado & Like")}</strong></span>
      </div>
    </div>
  `;

  if (container) {
    container.insertAdjacentHTML("afterbegin", cardHtml);
  }
  if (rightContainer) {
    rightContainer.innerHTML = cardHtml;
  }
}

// Results Persistence
async function loadSavedResults() {
  try {
    const res = await fetch("/api/results");
    const data = await res.json();
    savedResultsCache = data;
    showToast(`Cargados ${data.length} resultados históricos`, "info");
  } catch (e) {
    console.error("Error loading results:", e);
  }
}

function downloadJsonResults() {
  if (!savedResultsCache || !savedResultsCache.length) {
    showToast("No hay resultados en memoria para exportar", "warning");
    return;
  }
  const blob = new Blob([JSON.stringify(savedResultsCache, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `automator_results_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
