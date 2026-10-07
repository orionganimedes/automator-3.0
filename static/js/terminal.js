// static/js/terminal.js - Terminal & Real-Time Event Log Controller

let terminalAutoScroll = true;
let activeLogFilter = "ALL";

function appendLog(log) {
  const container = document.getElementById("terminalContainer");
  if (!container) return;

  const level = (log.level || "INFO").toUpperCase();
  if (activeLogFilter !== "ALL" && activeLogFilter !== level) return;

  const line = document.createElement("div");

  const colors = {
    INFO: "text-on-surface",
    DEBUG: "text-on-surface-variant",
    WARNING: "text-tertiary",
    WARN: "text-tertiary",
    ERROR: "text-error",
    CRITICAL: "text-error font-bold",
    SUCCESS: "text-secondary"
  };

  const color = colors[level] || "text-on-surface";
  const ts = log.timestamp || new Date().toLocaleTimeString();

  line.className = `${color} break-all leading-relaxed font-mono text-xs flex items-start gap-2 py-0.5 hover:bg-surface-container-high/40 px-1 rounded transition`;
  line.innerHTML = `
    <span class="text-outline shrink-0">[${ts}]</span>
    <span class="px-1 py-0.2 rounded text-[10px] font-bold shrink-0 ${level === 'ERROR' ? 'bg-error/20 text-error' : level === 'WARN' || level === 'WARNING' ? 'bg-tertiary/20 text-tertiary' : 'bg-surface-container text-primary'}">${level}</span>
    <span class="flex-1">${escapeHtml(log.message || "")}</span>
  `;

  container.appendChild(line);

  // Keep max 800 lines in DOM for performance
  if (container.children.length > 800) {
    container.removeChild(container.firstChild);
  }

  if (terminalAutoScroll) {
    container.scrollTop = container.scrollHeight;
  }
}

function clearTerminal() {
  const container = document.getElementById("terminalContainer");
  if (container) {
    container.innerHTML = '<div class="text-on-surface-variant font-mono text-xs p-2">// Terminal de daemon y logs reiniciada.</div>';
    showToast("Terminal limpiada", "info");
  }
}

function toggleAutoScroll(enabled) {
  terminalAutoScroll = enabled;
  const btn = document.getElementById("btnToggleAutoScroll");
  if (btn) {
    if (enabled) {
      btn.className = "px-2.5 py-1 rounded bg-secondary/10 text-secondary border border-secondary/30 text-xs font-mono font-semibold flex items-center gap-1";
    } else {
      btn.className = "px-2.5 py-1 rounded bg-surface-container text-on-surface-variant border border-[#262a35] text-xs font-mono font-semibold flex items-center gap-1";
    }
  }
}

function filterLogs(level) {
  activeLogFilter = level;
  const buttons = document.querySelectorAll(".log-filter-btn");
  buttons.forEach(b => {
    if (b.dataset.level === level) {
      b.className = "log-filter-btn px-2.5 py-1 rounded bg-primary text-on-primary font-mono text-xs font-semibold shadow";
    } else {
      b.className = "log-filter-btn px-2.5 py-1 rounded bg-surface-container text-on-surface-variant hover:text-on-surface font-mono text-xs transition";
    }
  });
}

function exportLogs() {
  const container = document.getElementById("terminalContainer");
  if (!container) return;
  const text = container.innerText;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `automator_logs_${Date.now()}.txt`;
  a.click();
  URL.revokeObjectURL(url);
  showToast("Logs exportados", "success");
}
