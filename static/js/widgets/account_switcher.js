// static/js/widgets/account_switcher.js - Widget reutilizable de cambio de cuenta
window.AccountSwitcher = {
  async refresh(platform) {
    const badge = document.getElementById(`acct-switch-badge-${platform}`);
    const select = document.getElementById(`acct-switch-select-${platform}`);
    const status = document.getElementById(`acct-switch-status-${platform}`);
    try {
      const res = await fetch(`/api/accounts/${platform}`);
      const data = await res.json();
      if (!data.success) throw new Error(data.error || "Error al cargar cuentas");
      const accounts = data.accounts || [];
      const isFb = platform === "facebook";
      if (badge) badge.textContent = `${accounts.length} cuentas`;
      if (select) {
        select.innerHTML = '<option value="">-- Seleccionar cuenta --</option>';
        accounts.forEach((acc) => {
          const value = isFb ? (acc.username || acc.label || "") : String(acc.index ?? "");
          const label = isFb ? (acc.username || acc.label) : (acc.label || acc.username || `Cuenta ${acc.index}`);
          if (!value) return;
          const opt = document.createElement("option");
          opt.value = value;
          opt.textContent = label;
          select.appendChild(opt);
        });
      }
      if (status) status.textContent = `Dispositivo: ${data.serial || "desconocido"}`;
      return data;
    } catch (e) {
      if (status) status.textContent = `Error: ${e.message}`;
      return null;
    }
  },

  async switch(platform) {
    const select = document.getElementById(`acct-switch-select-${platform}`);
    const status = document.getElementById(`acct-switch-status-${platform}`);
    if (!select || !select.value) {
      if (status) status.textContent = "Selecciona una cuenta primero.";
      return;
    }
    if (status) status.textContent = "Cambiando cuenta...";
    try {
      const isFb = platform === "facebook";
      const body = isFb ? { name: select.value } : { index: parseInt(select.value, 10) };
      const res = await fetch(`/api/accounts/${platform}/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      if (data.success) {
        if (status) status.textContent = "Cambio completado correctamente.";
      } else {
        if (status) status.textContent = `Error: ${data.error || "No se pudo cambiar"}`;
      }
    } catch (e) {
      if (status) status.textContent = `Error: ${e.message}`;
    }
    AccountSwitcher.refresh(platform);
  }
};

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".account-switcher-widget").forEach((el) => {
    const p = el.getAttribute("data-platform");
    if (p) AccountSwitcher.refresh(p);
  });
});
