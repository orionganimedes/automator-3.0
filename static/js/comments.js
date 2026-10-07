// static/js/comments.js - AI Comments Simulator & Generation Engine

let lastGeneratedCommentsList = [];

// Toggle Contest / Dynamics fields
function toggleSimContestFields(checked) {
  const fields = document.getElementById("simContestFields");
  if (fields) {
    if (checked) fields.classList.remove("hidden");
    else fields.classList.add("hidden");
  }
}

// Capture from phone using OCR
async function captureAndReadForSimulator() {
  showToast("Capturando pantalla y extrayendo texto del post...", "info");
  try {
    const res = await fetch("/api/post/capture-and-read-screen", { method: "POST" });
    const data = await res.json();
    if (data.success && data.extracted_text) {
      const simText = document.getElementById("simPostText");
      if (simText) simText.value = data.extracted_text;

      if (data.is_contest) {
        const simCb = document.getElementById("simIsContest");
        if (simCb) {
          simCb.checked = true;
          toggleSimContestFields(true);
        }
        const simReq = document.getElementById("simContestReq");
        if (simReq && data.contest_requirement) simReq.value = data.contest_requirement;
        const simTopic = document.getElementById("simContestTopic");
        if (simTopic && data.teams_or_topic) simTopic.value = data.teams_or_topic;
      }
      showToast("Texto capturado y leído del teléfono", "success");
    } else {
      showToast(data.error || "No se detectó texto en la pantalla actual", "warning");
    }
  } catch (e) {
    showToast("Error de conexión al capturar pantalla", "error");
  }
}

// Generate Comments with Ollama
async function previewComments() {
  const postText = document.getElementById("simPostText")?.value.trim();
  const narrative = document.getElementById("simNarrativeSelect")?.value || "a_favor";
  const archChoice = document.getElementById("simArchetypeSelect")?.value || "human_all";
  const btn = document.getElementById("btnRunPreview");
  const btnText = document.getElementById("simBtnText");
  const btnIcon = document.getElementById("simBtnIcon");
  const container = document.getElementById("simResultsContainer");

  if (!postText) {
    showToast("Por favor escribe o captura el texto de una publicación para generar comentarios.", "warning");
    document.getElementById("simPostText")?.focus();
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-50", "cursor-not-allowed");
  }
  if (btnText) btnText.innerText = "Generando con IA Local (Ollama)...";
  if (btnIcon) btnIcon.classList.add("animate-spin");

  if (container) {
    container.innerHTML = `
      <div class="h-40 border border-[#262a35] rounded-xl flex flex-col items-center justify-center text-on-surface-variant p-4 text-center bg-surface-container/50">
        <span class="material-symbols-outlined text-[32px] text-primary animate-spin mb-2">sync</span>
        <p class="text-xs font-semibold text-on-surface">Generando comentarios con Ollama...</p>
        <p class="text-[11px] text-on-surface-variant font-mono mt-1">Postura: <span class="text-primary font-bold">${narrative}</span> | Cero emojis · Máx 3 líneas</p>
      </div>
    `;
  }

  try {
    const currentTextModel = document.getElementById("selectTextModel")?.value || "";
    const isContest = document.getElementById("simIsContest")?.checked || false;
    const contestReq = document.getElementById("simContestReq")?.value.trim() || "";
    const contestTopic = document.getElementById("simContestTopic")?.value.trim() || "";

    const res = await fetch("/api/comments/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: postText,
        narrative: narrative,
        archetype_id: archChoice,
        text_model: currentTextModel,
        is_contest: isContest,
        contest_requirement: contestReq,
        teams_or_topic: contestTopic
      })
    });

    const data = await res.json();
    const items = data.results || data.previews || [];
    lastGeneratedCommentsList = [];

    if (data.success && items.length > 0) {
      if (container) container.innerHTML = "";

      const narrativeBadges = {
        "a_favor": { label: "👍 A Favor", bg: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" },
        "participacion": { label: "💬 Participación", bg: "bg-sky-500/10 text-sky-400 border-sky-500/30" },
        "hate": { label: "⚡ Crítico", bg: "bg-rose-500/10 text-rose-400 border-rose-500/30" },
        "neutro": { label: "⚖️ Neutro", bg: "bg-surface-container-high text-on-surface border-[#262a35]" }
      };

      const activeBadge = narrativeBadges[data.narrative] || narrativeBadges["a_favor"];

      items.forEach((item, index) => {
        const archId = item.id || item.archetype_id || (index + 1);
        const archName = item.name || item.archetype_name || "Arquetipo";
        const commentText = item.comment || "";
        const words = item.word_count || commentText.split(/\s+/).filter(Boolean).length;
        const reg = item.regionalism || item.visual_element || "";
        const isHuman = item.is_human || ["testigo silencioso", "buscador", "guerrero interior", "caminante", "heredero"].includes(archName.toLowerCase());

        lastGeneratedCommentsList.push(commentText);

        const card = document.createElement("div");
        card.className = "bg-surface-container rounded-xl border border-[#262a35] p-3.5 space-y-2.5 shadow transition hover:border-[#353944]";

        card.innerHTML = `
          <div class="flex items-center justify-between flex-wrap gap-2">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="px-2 py-0.5 rounded bg-primary/10 border border-primary/20 text-primary text-[11px] font-mono font-bold">
                ${isHuman ? '✨ ' : 'Arquetipo ' + archId + ': '}${escapeHtml(archName)}
              </span>
              ${reg ? `
                <span class="px-2 py-0.5 rounded bg-surface-container-high border border-[#262a35] text-tertiary text-[10px] font-mono">
                  Ref: "${escapeHtml(reg)}"
                </span>
              ` : ''}
              ${item.contest_info ? `
                <span class="px-2 py-0.5 rounded bg-tertiary/10 text-tertiary border border-tertiary/30 text-[10px] font-mono">
                  🏆 Concurso Activo
                </span>
              ` : ''}
            </div>
            <div class="flex items-center gap-2 text-xs font-mono">
              <span class="text-[10px] px-2 py-0.5 rounded border ${activeBadge.bg}">
                ${activeBadge.label}
              </span>
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-container-lowest text-on-surface-variant border border-[#262a35]">
                ${words} palabras
              </span>
            </div>
          </div>

          <div class="bg-surface-container-lowest p-3 rounded-lg border border-[#262a35]">
            <p class="text-xs text-on-surface font-mono italic leading-relaxed">
              "${escapeHtml(commentText)}"
            </p>
          </div>

          <div class="flex items-center justify-between pt-1">
            <span class="text-[10px] text-secondary font-mono flex items-center gap-1">
              <span class="material-symbols-outlined text-[13px]">check_circle</span>
              <span>0 Emojis · Máx 3 Líneas</span>
            </span>
            <div class="flex items-center gap-1.5">
              <button onclick="copyToClipboard('${escapeHtmlQuotes(commentText)}')" class="px-2.5 py-1 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface text-[11px] font-mono flex items-center gap-1 transition cursor-pointer" title="Copiar al portapapeles">
                <span class="material-symbols-outlined text-[13px]">content_copy</span>
                <span>Copiar</span>
              </button>
              <button onclick="addCommentToInstagramBank('${escapeHtmlQuotes(commentText)}')" class="px-2.5 py-1 rounded bg-primary text-on-primary hover:bg-primary-container text-[11px] font-mono font-bold flex items-center gap-1 transition cursor-pointer" title="Añadir al banco rotativo de Instagram">
                <span class="material-symbols-outlined text-[13px]">photo_camera</span>
                <span>Usar en IG</span>
              </button>
              <button onclick="addCommentToFacebookBank('${escapeHtmlQuotes(commentText)}')" class="px-2.5 py-1 rounded bg-blue-600 text-white hover:bg-blue-500 text-[11px] font-mono font-bold flex items-center gap-1 transition cursor-pointer" title="Añadir al banco rotativo de Facebook">
                <span class="material-symbols-outlined text-[13px]">public</span>
                <span>Usar en FB</span>
              </button>
            </div>
          </div>
        `;
        if (container) container.appendChild(card);
      });

      showToast(`¡Se generaron ${items.length} comentarios con éxito!`, "success");
    } else {
      if (container) {
        container.innerHTML = `
          <div class="p-4 bg-error/10 border border-error/30 text-error text-xs font-mono rounded-xl">
            Error al generar comentarios: ${escapeHtml(data.error || "No se recibieron resultados de Ollama.")}
          </div>
        `;
      }
      showToast(`Aviso: ${data.error || "Fallo en inferencia"}`, "warning");
    }
  } catch (err) {
    if (container) {
      container.innerHTML = `
        <div class="p-4 bg-error/10 border border-error/30 text-error text-xs font-mono rounded-xl">
          Error de conexión con el servidor Python: ${escapeHtml(err.message || err)}
        </div>
      `;
    }
    showToast(`Error de conexión: ${err.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-50", "cursor-not-allowed");
    }
    if (btnText) btnText.innerText = "Generar Comentarios con IA Local";
    if (btnIcon) btnIcon.classList.remove("animate-spin");
  }
}

// Transfer generated comment to Instagram Bank
function addCommentToInstagramBank(commentText) {
  if (!commentText) return;
  const ta = document.getElementById("pastedCommentsTextarea");
  if (ta) {
    const existing = ta.value.trim();
    ta.value = existing ? `${existing}\n${commentText}` : commentText;
    if (typeof updatePastedCommentsCount === "function") {
      updatePastedCommentsCount();
    }
  }
  // Also select preset_list mode
  if (typeof selectCommentMode === "function") {
    selectCommentMode("preset_list");
  }
  showToast("Comentario agregado al Banco Rotativo de Instagram", "success");
  if (typeof switchTab === "function") {
    switchTab("insta");
  }
}

// Transfer generated comment to Facebook Bank
function addCommentToFacebookBank(commentText) {
  if (!commentText) return;
  const ta = document.getElementById("fbPastedCommentsTextarea");
  if (ta) {
    const existing = ta.value.trim();
    ta.value = existing ? `${existing}\n${commentText}` : commentText;
    if (typeof updateFbPastedCommentsCount === "function") {
      updateFbPastedCommentsCount();
    }
  }
  if (typeof selectFbCommentMode === "function") {
    selectFbCommentMode("preset_list");
  }
  showToast("Comentario agregado al Banco Rotativo de Facebook", "success");
  if (typeof switchTab === "function") {
    switchTab("facebook");
  }
}

// Copy ALL generated comments to Instagram
function copyAllGeneratedCommentsToInstagram() {
  if (!lastGeneratedCommentsList.length) {
    showToast("Primero genera comentarios con el botón de IA.", "warning");
    return;
  }
  const ta = document.getElementById("pastedCommentsTextarea");
  if (ta) {
    const existing = ta.value.trim();
    const newText = lastGeneratedCommentsList.join("\n");
    ta.value = existing ? `${existing}\n${newText}` : newText;
    if (typeof updatePastedCommentsCount === "function") {
      updatePastedCommentsCount();
    }
  }
  if (typeof selectCommentMode === "function") {
    selectCommentMode("preset_list");
  }
  showToast(`¡${lastGeneratedCommentsList.length} comentarios enviados a Instagram!`, "success");
  if (typeof switchTab === "function") {
    switchTab("insta");
  }
}

// Generate 3 options for a custom persona (one per archetype)
async function previewPersonaComments() {
  const persona = document.getElementById("simPersonaInput")?.value.trim();
  const postText = document.getElementById("simPostText")?.value.trim();
  const narrative = document.getElementById("simNarrativeSelect")?.value || "a_favor";
  const btn = document.getElementById("btnRunPersona");
  const container = document.getElementById("simResultsContainer");

  if (!persona) {
    showToast("Escribe el personaje que quieres (ej: un abuelo nostálgico).", "warning");
    document.getElementById("simPersonaInput")?.focus();
    return;
  }
  if (!postText) {
    showToast("Primero escribe o captura el texto de la publicación.", "warning");
    document.getElementById("simPostText")?.focus();
    return;
  }

  if (btn) { btn.disabled = true; btn.classList.add("opacity-50", "cursor-not-allowed"); }
  if (container) {
    container.innerHTML = `
      <div class="h-40 border border-[#262a35] rounded-xl flex flex-col items-center justify-center text-on-surface-variant p-4 text-center bg-surface-container/50">
        <span class="material-symbols-outlined text-[32px] text-secondary animate-spin mb-2">sync</span>
        <p class="text-xs font-semibold text-on-surface">Generando 3 opciones para el personaje...</p>
        <p class="text-[11px] text-on-surface-variant font-mono mt-1">Personaje: <span class="text-secondary font-bold">${escapeHtml(persona)}</span></p>
      </div>
    `;
  }

  try {
    const currentTextModel = document.getElementById("selectTextModel")?.value || "";
    const res = await fetch("/api/comments/generate-persona", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona, text: postText, narrative, text_model: currentTextModel, count: 3 })
    });
    const data = await res.json();
    const options = data.options || [];
    lastGeneratedCommentsList = [];

    if (data.success && options.length) {
      if (container) container.innerHTML = "";
      options.forEach((opt, index) => {
        const commentText = opt.comment || "";
        const archName = opt.archetype_name || "Arquetipo";
        const words = commentText.split(/\s+/).filter(Boolean).length;
        lastGeneratedCommentsList.push(commentText);

        const card = document.createElement("div");
        card.className = "bg-surface-container rounded-xl border border-[#262a35] p-3.5 space-y-2.5 shadow transition hover:border-[#353944]";
        card.innerHTML = `
          <div class="flex items-center justify-between flex-wrap gap-2">
            <span class="px-2 py-0.5 rounded bg-secondary/10 border border-secondary/20 text-secondary text-[11px] font-mono font-bold">
              Opción ${index + 1} · ${escapeHtml(archName)}
            </span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-container-lowest text-on-surface-variant border border-[#262a35]">${words} palabras</span>
          </div>
          <div class="bg-surface-container-lowest p-3 rounded-lg border border-[#262a35]">
            <p class="text-xs text-on-surface font-mono italic leading-relaxed">"${escapeHtml(commentText)}"</p>
          </div>
          <div class="flex items-center justify-end gap-1.5 flex-wrap">
            <button onclick="copyToClipboard('${escapeHtmlQuotes(commentText)}')" class="px-2.5 py-1 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface text-[11px] font-mono flex items-center gap-1 transition cursor-pointer">
              <span class="material-symbols-outlined text-[13px]">content_copy</span><span>Copiar</span>
            </button>
            <button onclick="addCommentToInstagramBank('${escapeHtmlQuotes(commentText)}')" class="px-2.5 py-1 rounded bg-primary text-on-primary hover:bg-primary-container text-[11px] font-mono font-bold flex items-center gap-1 transition cursor-pointer">
              <span class="material-symbols-outlined text-[13px]">photo_camera</span><span>Usar en IG</span>
            </button>
            <button onclick="addCommentToFacebookBank('${escapeHtmlQuotes(commentText)}')" class="px-2.5 py-1 rounded bg-blue-600 text-white hover:bg-blue-500 text-[11px] font-mono font-bold flex items-center gap-1 transition cursor-pointer">
              <span class="material-symbols-outlined text-[13px]">public</span><span>Usar en FB</span>
            </button>
          </div>
        `;
        if (container) container.appendChild(card);
      });
      showToast(`¡Se generaron ${options.length} opciones para tu personaje!`, "success");
    } else {
      if (container) {
        container.innerHTML = `
          <div class="p-4 bg-error/10 border border-error/30 text-error text-xs font-mono rounded-xl">
            Error: ${escapeHtml(data.error || "No se recibieron opciones.")}
          </div>
        `;
      }
      showToast(`Aviso: ${data.error || "Fallo al generar"}`, "warning");
    }
  } catch (err) {
    if (container) {
      container.innerHTML = `<div class="p-4 bg-error/10 border border-error/30 text-error text-xs font-mono rounded-xl">Error de conexión: ${escapeHtml(err.message || err)}</div>`;
    }
    showToast(`Error de conexión: ${err.message}`, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove("opacity-50", "cursor-not-allowed"); }
  }
}

// Copy ALL generated comments to Facebook
function copyAllGeneratedCommentsToFacebook() {
  if (!lastGeneratedCommentsList.length) {
    showToast("Primero genera comentarios con el botón de IA.", "warning");
    return;
  }
  const ta = document.getElementById("fbPastedCommentsTextarea");
  if (ta) {
    const existing = ta.value.trim();
    const newText = lastGeneratedCommentsList.join("\n");
    ta.value = existing ? `${existing}\n${newText}` : newText;
    if (typeof updateFbPastedCommentsCount === "function") {
      updateFbPastedCommentsCount();
    }
  }
  if (typeof selectFbCommentMode === "function") {
    selectFbCommentMode("preset_list");
  }
  showToast(`¡${lastGeneratedCommentsList.length} comentarios enviados a Facebook!`, "success");
  if (typeof switchTab === "function") {
    switchTab("facebook");
  }
}
