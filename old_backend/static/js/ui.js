// ─── DOM refs ─────────────────────────────────────────────────────────────────

const dom = {
  recordBtn: document.getElementById("record-btn"),
  appendBtn: document.getElementById("append-btn"),
  timerEl: document.getElementById("timer"),
  statusEl: document.getElementById("record-status"),
  waveformCanvas: document.getElementById("waveform"),
  fileInput: document.getElementById("file-input"),
  uploadZone: document.getElementById("upload-zone"),
  jobsList: document.getElementById("jobs-list"),
  editor: document.getElementById("transcript-editor"),
  emptyState: document.getElementById("empty-state"),
  processingOverlay: document.getElementById("processing-overlay"),
  processingLabel: document.getElementById("processing-label"),
  processingCancel: document.getElementById("processing-cancel"),
  progressBarFill: document.getElementById("progress-bar-fill"),
  progressPct: document.getElementById("progress-pct"),
  progressElapsed: document.getElementById("progress-elapsed"),
  progressLastUpdate: document.getElementById("progress-last-update"),
  aiModeSelector: document.getElementById("ai-mode-selector"),
  btnAi: document.getElementById("btn-ai"),
  btnAddMode: document.getElementById("btn-add-mode"),
  btnEditVoice: document.getElementById("btn-edit-voice"),
  btnCopy: document.getElementById("btn-copy"),
  btnExport: document.getElementById("btn-export"),
  charCount: document.getElementById("char-count"),
  aiResultsPanel: document.getElementById("ai-results"),
  headerProgress: document.getElementById("header-progress"),
  headerProgressFill: document.getElementById("header-progress-fill"),
  headerProgressLabel: document.getElementById("header-progress-label"),
  btnAutoFix: document.getElementById("btn-auto-fix-toggle"),
  personalNamesModal: document.getElementById("personal-names-modal"),
  personalNamesInput: document.getElementById("personal-names-input"),
  modeModal: document.getElementById("mode-modal"),
  modeNameInput: document.getElementById("mode-name-input"),
  modeDisplayInput: document.getElementById("mode-display-input"),
  modeInstructionInput: document.getElementById("mode-instruction-input"),
  modeRulesInput: document.getElementById("mode-rules-input"),
  modePlaceholderInput: document.getElementById("mode-placeholder-input"),
  chatMessages: document.getElementById("chat-messages"),
  chatInput: document.getElementById("chat-input"),
  chatSend: document.getElementById("chat-send"),
  chatRecordBtn: document.getElementById("chat-record-btn"),
  chatAppendBtn: document.getElementById("chat-append-btn"),
  chatUploadBtn: document.getElementById("chat-upload-btn"),
  overlayRecordBtn: document.getElementById("overlay-record-btn"),
  btnToggleJobsDrawer: document.getElementById("btn-toggle-jobs-drawer"),
  jobsDrawer: document.getElementById("jobs-drawer"),
  jobsDrawerBackdrop: document.getElementById("jobs-drawer-backdrop"),
  jobsDrawerClose: document.getElementById("jobs-drawer-close"),
};

const ctx2d = dom.waveformCanvas ? dom.waveformCanvas.getContext("2d") : null;

// ─── Toast ────────────────────────────────────────────────────────────────────

function toast(msg, type = "info") {
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.getElementById("toast-container").appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ─── Recording overlay helpers ────────────────────────────────────────────────

function showRecordOverlay() {
  const overlay = document.getElementById("record-overlay");
  if (overlay) overlay.style.display = "flex";
}

function hideRecordOverlay() {
  const overlay = document.getElementById("record-overlay");
  if (overlay) overlay.style.display = "none";
}

// ─── Header Progress Bar ──────────────────────────────────────────────────────

function setHeaderProgress(label = "", pct = 0, { aiMode = false, hide = false } = {}) {
  const { headerProgress, headerProgressFill, headerProgressLabel } = dom;

  if (hide) {
    headerProgressFill.style.width = "0%";
    headerProgressLabel.textContent = "";
    setTimeout(() => {
      headerProgress.classList.remove("active", "ai-progress");
    }, 1000);
    return;
  }

  headerProgress.classList.toggle("active", pct > 0);
  headerProgress.classList.toggle("ai-progress", aiMode);
  headerProgressFill.style.width = pct + "%";
  headerProgressLabel.textContent = label && pct > 0 ? `${label} ${pct}%` : "";
}

// ─── Collapsible sections ─────────────────────────────────────────────────────

function initToggle(labelId, bodyId) {
  const label = document.getElementById(labelId);
  const body = document.getElementById(bodyId);
  if (!label || !body) return;
  label.addEventListener("click", () => {
    const collapsed = body.classList.toggle("collapsed");
    label.classList.toggle("collapsed", collapsed);
  });
}

initToggle("toggle-record", "body-record");
initToggle("toggle-upload", "body-upload");
initToggle("toggle-jobs", "jobs-list");

// ─── Editor buttons ───────────────────────────────────────────────────────────

function setEditorButtons(on) {
  [dom.aiModeSelector, dom.btnAi, dom.btnEditVoice, dom.btnCopy, dom.btnExport]
    .forEach((btn) => (btn.disabled = !on));
}

function setEditorButtonsSafe(on) {
  [dom.aiModeSelector, dom.btnAi, dom.btnEditVoice, dom.btnCopy, dom.btnExport]
    .forEach((btn) => { if (btn) btn.disabled = !on; });
}

// ─── Char count ───────────────────────────────────────────────────────────────

dom.editor.addEventListener("input", updateCharCount);

function updateCharCount() {
  const n = dom.editor.value.length;
  dom.charCount.textContent = n > 0 ? `${n.toLocaleString()} chars` : "";
}

// ─── Elapsed / progress timers ────────────────────────────────────────────────

function startElapsedTimer(jobId) {
  clearInterval(state.elapsedInterval);
  state.elapsedInterval = setInterval(() => {
    const start = state.jobStartTime[jobId];
    if (!start) return;
    const elapsed = Math.floor((Date.now() - start) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    dom.progressElapsed.textContent = `${m}:${s}`;

    const lastUpd = state.lastUpdateTime[jobId];
    if (lastUpd) {
      const stale = Math.floor((Date.now() - lastUpd) / 1000);
      if (stale > 10) {
        dom.progressLastUpdate.textContent = `last update ${stale}s ago`;
        dom.progressLastUpdate.style.color = stale > 30 ? "var(--red)" : "var(--muted)";
      } else {
        dom.progressLastUpdate.textContent = "";
      }
    }
  }, 1000);
}

function stopElapsedTimer() {
  clearInterval(state.elapsedInterval);
  dom.progressElapsed.textContent = "";
  dom.progressLastUpdate.textContent = "";
  dom.progressPct.textContent = "";
  dom.progressBarFill.style.width = "0%";
}

function updateProcessingUI(label, pct) {
  dom.processingLabel.textContent = (STAGE_LABELS[label] ?? label) + "…";
  dom.progressBarFill.style.width = pct + "%";
  dom.progressPct.textContent = pct > 0 ? pct + "%" : "";
}

function showProcessing(label, pct = 0) {
  dom.emptyState.style.display = "none";
  dom.processingOverlay.classList.add("active");
  dom.editor.style.display = "none";
  renderAiResults([]);
  setEditorButtons(false);
  updateProcessingUI(label, pct);
}

// ─── Jobs drawer ──────────────────────────────────────────────────────────────

function initJobsDrawer() {
  if (!dom.btnToggleJobsDrawer || !dom.jobsDrawer) return;
  const closeDrawer = () => dom.jobsDrawer.classList.remove("open");
  dom.btnToggleJobsDrawer.addEventListener("click", () => dom.jobsDrawer.classList.toggle("open"));
  if (dom.jobsDrawerBackdrop) dom.jobsDrawerBackdrop.addEventListener("click", closeDrawer);
  if (dom.jobsDrawerClose) dom.jobsDrawerClose.addEventListener("click", closeDrawer);
}

// ─── Copy / Export toolbar ────────────────────────────────────────────────────

function downloadMarkdown(filename, content) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/markdown" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}

function initToolbar() {
  dom.btnCopy.addEventListener("click", () => {
    let text = dom.editor?.value?.trim() || "";
    if (!text && dom.chatMessages) {
      const parts = Array.from(dom.chatMessages.querySelectorAll(".message .message-body"))
        .map((el) => el.textContent?.trim())
        .filter(Boolean);
      text = parts.join("\n\n");
    }
    if (!text) { toast("Nothing to copy", "error"); return; }
    navigator.clipboard.writeText(text)
      .then(() => toast("Copied!", "success"))
      .catch((e) => { console.error("Copy failed", e); toast("Copy failed", "error"); });
  });

  dom.btnExport.addEventListener("click", () => {
    downloadMarkdown("transcript.md", `# Transcript\n\n${dom.editor.value}\n`);
    toast("Exported as Markdown", "success");
  });
}

// ─── Processing cancel ────────────────────────────────────────────────────────

function initProcessingCancel() {
  if (!dom.processingCancel) return;
  dom.processingCancel.addEventListener("click", async () => {
    const id = state.activeJobId;
    if (!id) return;
    try {
      await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
      toast("Cancellation requested", "info");
    } catch (e) {
      console.warn("Cancel request failed", e);
    }
    if (state.currentAIReader) {
      try { state.currentAIReader.cancel(); } catch (_) {}
      state.currentAIReader = null;
    }
  });
}
