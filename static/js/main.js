// static/js/main.js - Application Lifecycle & Tab Orchestrator

const TAB_CONFIG = [
  { id: "insta", btn: "tabInstaBtn", content: "tabInstaContent" },
  { id: "facebook", btn: "tabFacebookBtn", content: "tabFacebookContent" },
  { id: "hate/report", btn: "tabHateReportBtn", content: "tabHateReportContent" },
  { id: "comments", btn: "tabCommentsBtn", content: "tabCommentsContent" },
  { id: "models", btn: "tabModelsBtn", content: "tabModelsContent" },
  { id: "icons", btn: "tabIconsBtn", content: "tabIconsContent" },
  { id: "terminal", btn: "tabTerminalBtn", content: "tabTerminalContent" },
  { id: "scrcpy", btn: "tabScrcpyBtn", content: "tabScrcpyContent" }
];

function switchTab(tabId) {
  // Alias handling
  if (tabId === "auto_report" || tabId === "reportes_auto") {
    switchTab("hate/report");
    if (typeof switchHateSubMode === "function") switchHateSubMode("report");
    return;
  }

  const normalized = (tabId === "hate" || tabId === "hate_report" || tabId === "hate/report") ? "hate/report" : tabId;

  TAB_CONFIG.forEach(t => {
    const btn = document.getElementById(t.btn);
    const content = document.getElementById(t.content);

    if (btn) {
      btn.classList.remove("bg-surface-container", "text-on-surface", "font-semibold", "shadow-[0_1px_4px_rgba(0,0,0,0.2)]", "text-primary");
      btn.classList.add("text-on-surface-variant", "hover:text-on-surface", "hover:bg-surface-container-high");
    }
    if (content) {
      content.classList.add("hidden");
    }
  });

  const active = TAB_CONFIG.find(t => t.id === normalized) || TAB_CONFIG[0];
  const activeBtn = document.getElementById(active.btn);
  const activeContent = document.getElementById(active.content);

  if (activeBtn) {
    activeBtn.classList.remove("text-on-surface-variant", "hover:text-on-surface", "hover:bg-surface-container-high");
    activeBtn.classList.add("bg-surface-container", "text-on-surface", "font-semibold", "shadow-[0_1px_4px_rgba(0,0,0,0.2)]");
  }
  if (activeContent) {
    activeContent.classList.remove("hidden");
  }

  // Trigger on-switch lifecycle hooks
  if (normalized === "scrcpy") {
    if (typeof loadScrcpyDevices === "function") loadScrcpyDevices(true);
  } else if (normalized === "facebook") {
    if (typeof loadFacebookAccountsMemory === "function") loadFacebookAccountsMemory();
  } else if (normalized === "icons") {
    if (typeof loadIconTemplates === "function") loadIconTemplates();
  }
}

// Global App Initialization
document.addEventListener("DOMContentLoaded", () => {
  // 1. Initial status poll and background loop
  pollStatus();
  setInterval(pollStatus, 4000);

  // 2. Setup screen streaming
  setupAutoRefresh();

  // 3. Connect SSE event & log stream
  connectEventStream();

  // 4. Background asset initializations
  scanLocalModels(false);
  loadIconTemplates();
  loadScrcpyDevices(false);
  if (typeof loadFacebookAccountsMemory === "function") loadFacebookAccountsMemory();
  if (typeof checkFacebookAccountsRequirement === "function") {
    checkFacebookAccountsRequirement();
    setInterval(checkFacebookAccountsRequirement, 6000);
  }

  // 5. Restore comments memory from localStorage
  try {
    const savedComments = localStorage.getItem("granjita_pasted_comments");
    if (savedComments) {
      const ta = document.getElementById("pastedCommentsTextarea");
      if (ta) {
        ta.value = savedComments;
        if (typeof updatePastedCommentsCount === "function") updatePastedCommentsCount();
      }
    }
  } catch (e) {}

  // 6. Set initial view to Instagram Bot
  switchTab("insta");
});

// Reiniciar caché (llm_cache, localizadores, calibración) y estado en memoria
async function restartCache() {
  if (!confirm("¿Reiniciar la caché (llm_cache, localizadores, calibración) y el estado en memoria?")) return;
  try {
    const res = await fetch("/api/system/clear-cache", { method: "POST" });
    const data = await res.json();
    showToast(data.message || "Caché reiniciada", data.success ? "success" : "error");
    if (data.success && typeof pollStatus === "function") pollStatus();
  } catch (e) {
    showToast("Error al reiniciar caché: " + (e.message || e), "error");
  }
}

// Reiniciar por completo el programa (relanza el servidor)
async function restartProgram() {
  if (!confirm("¿Reiniciar por completo el programa? El servidor se detendrá y volverá a arrancar.")) return;
  showToast("Reiniciando el programa...", "info");
  try {
    await fetch("/api/system/restart", { method: "POST" });
  } catch (e) {
    // La conexión se cierra al reiniciar; es esperado.
  }
  setTimeout(() => { location.reload(); }, 3500);
}
