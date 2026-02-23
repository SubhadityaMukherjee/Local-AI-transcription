// ─── Constants ────────────────────────────────────────────────────────────────

const STAGE_LABELS = {
  queued: "Queued",
  converting: "Converting",
  transcribing: "Transcribing",
};

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];

// ─── State ────────────────────────────────────────────────────────────────────

const state = {
  // Recording
  mediaRecorder: null,
  recordChunks: [],
  recordStream: null,
  recordStart: null,
  timerInterval: null,
  analyser: null,
  animFrame: null,
  appendMode: false,
  appendJobId: null,

  // Voice edit
  voiceEditMode: false,
  voiceEditChunks: [],
  voiceEditRecorder: null,
  voiceEditStream: null,

  // Jobs
  activeJobId: null,
  jobElements: new Map(),
  jobStartTime: {},
  lastUpdateTime: {},
  lastProgress: {},
  elapsedInterval: null,

  // Prefs
  autoFixEnabled: localStorage.getItem("autoFixEnabled") === "true",
};

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
  progressBarFill: document.getElementById("progress-bar-fill"),
  progressPct: document.getElementById("progress-pct"),
  progressElapsed: document.getElementById("progress-elapsed"),
  progressLastUpdate: document.getElementById("progress-last-update"),
  btnSummarize: document.getElementById("btn-summarize"),
  btnGrammar: document.getElementById("btn-grammar"),
  btnEditVoice: document.getElementById("btn-edit-voice"),
  btnCopy: document.getElementById("btn-copy"),
  btnExport: document.getElementById("btn-export"),
  charCount: document.getElementById("char-count"),
  aiResultsPanel: document.getElementById("ai-results"),
  headerProgress: document.getElementById("header-progress"),
  headerProgressFill: document.getElementById("header-progress-fill"),
  headerProgressLabel: document.getElementById("header-progress-label"),
  autoFixToggle: document.getElementById("auto-fix-toggle"),
  personalNamesModal: document.getElementById("personal-names-modal"),
  personalNamesInput: document.getElementById("personal-names-input"),
};

const ctx2d = dom.waveformCanvas.getContext("2d");

// ─── Toast ────────────────────────────────────────────────────────────────────

function toast(msg, type = "info") {
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.getElementById("toast-container").appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ─── Header Progress Bar ──────────────────────────────────────────────────────
// Single unified function replaces: updateHeaderProgress, hideHeaderProgress,
// showAiProgress, showVoiceEditProgress, hideVoiceEditProgress

function setHeaderProgress(
  label = "",
  pct = 0,
  { aiMode = false, hide = false } = {},
) {
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

// ─── Recording ────────────────────────────────────────────────────────────────

function getSupportedMimeType() {
  return MIME_CANDIDATES.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
}

function mimeToExt(mimeType) {
  if (mimeType.includes("mp4")) return "mp4";
  if (mimeType.includes("ogg")) return "ogg";
  return "webm";
}

dom.recordBtn.addEventListener("click", toggleRecord);
dom.appendBtn.addEventListener("click", toggleAppendRecord);

async function toggleAppendRecord() {
  if (!state.activeJobId) {
    toast("Select a recording first to append to", "error");
    return;
  }
  state.mediaRecorder?.state === "recording"
    ? stopRecording()
    : await startRecording(true);
}

async function toggleRecord() {
  state.mediaRecorder?.state === "recording"
    ? stopRecording()
    : await startRecording();
}

async function startRecording(isAppend = false) {
  state.appendMode = isAppend;
  state.appendJobId = isAppend ? state.activeJobId : null;

  try {
    state.recordStream = await navigator.mediaDevices.getUserMedia({
      audio: true,
    });
  } catch (e) {
    toast(`Microphone access denied: ${e.message}`, "error");
    return;
  }

  state.recordChunks = [];

  const audioCtx = new AudioContext();
  const source = audioCtx.createMediaStreamSource(state.recordStream);
  state.analyser = audioCtx.createAnalyser();
  state.analyser.fftSize = 256;
  source.connect(state.analyser);
  drawWaveform();

  const mimeType = getSupportedMimeType();
  try {
    state.mediaRecorder = new MediaRecorder(
      state.recordStream,
      mimeType ? { mimeType } : {},
    );
  } catch (e) {
    toast(`Recording not supported: ${e.message}`, "error");
    return;
  }

  state.mediaRecorder.ondataavailable = (e) => {
    if (e.data?.size > 0) state.recordChunks.push(e.data);
  };
  state.mediaRecorder.onstop = submitRecording;
  state.mediaRecorder.onerror = (e) =>
    toast(`Recording error: ${e.error?.message || e}`, "error");
  state.mediaRecorder.start(250);

  state.recordStart = Date.now();
  state.timerInterval = setInterval(updateTimer, 500);
  dom.recordBtn.classList.add("recording");
  dom.appendBtn.classList.toggle("recording", isAppend);
  dom.statusEl.textContent = isAppend
    ? "Appending… click to stop"
    : "Recording… click to stop";
  dom.statusEl.classList.add("active");
}

function stopRecording() {
  if (state.mediaRecorder?.state !== "inactive") state.mediaRecorder.stop();
  state.recordStream?.getTracks().forEach((t) => t.stop());
  clearInterval(state.timerInterval);
  cancelAnimationFrame(state.animFrame);
  dom.recordBtn.classList.remove("recording");
  dom.appendBtn.classList.remove("recording");
  dom.statusEl.textContent = "Uploading…";
  dom.statusEl.classList.remove("active");
  dom.timerEl.textContent = "";
  ctx2d.clearRect(0, 0, dom.waveformCanvas.width, dom.waveformCanvas.height);
}

async function submitRecording() {
  const mimeType = state.mediaRecorder?.mimeType || "audio/webm";
  const ext = mimeToExt(mimeType);
  const blob = new Blob(state.recordChunks, { type: mimeType });

  if (blob.size < 1000) {
    toast("Recording too short or empty — try again", "error");
    dom.statusEl.textContent = "Click to start recording";
    return;
  }

  const fd = new FormData();
  fd.append("audio", blob, `recording.${ext}`);
  if (state.appendMode && state.appendJobId)
    fd.append("append_to", state.appendJobId);

  try {
    const res = await fetch("/api/record", { method: "POST", body: fd });
    const data = await res.json();
    if (data.job_id) {
      if (state.appendMode && state.appendJobId) {
        toast("Appended audio, re-transcribing…", "success");
        watchJob(state.appendJobId);
        selectJob(state.appendJobId);
      } else {
        addJob({
          id: data.job_id,
          filename: `recording.${ext}`,
          status: "queued",
          progress: 0,
        });
        watchJob(data.job_id);
        selectJob(data.job_id);
        toast("Recording submitted");
      }
    } else {
      toast(`Server error: ${data.error}`, "error");
    }
  } catch (e) {
    toast(`Upload failed: ${e.message}`, "error");
  }

  state.appendMode = false;
  state.appendJobId = null;
  dom.statusEl.textContent = "Click to start recording";
}

function updateTimer() {
  const s = Math.floor((Date.now() - state.recordStart) / 1000);
  dom.timerEl.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function drawWaveform() {
  state.animFrame = requestAnimationFrame(drawWaveform);
  const data = new Uint8Array(state.analyser.frequencyBinCount);
  state.analyser.getByteTimeDomainData(data);
  const W = dom.waveformCanvas.offsetWidth;
  const H = dom.waveformCanvas.offsetHeight;
  dom.waveformCanvas.width = W;
  dom.waveformCanvas.height = H;
  ctx2d.clearRect(0, 0, W, H);
  ctx2d.strokeStyle = "#c4a882";
  ctx2d.lineWidth = 1.5;
  ctx2d.beginPath();
  const step = W / data.length;
  data.forEach((v, i) => {
    const y = (v / 128.0) * (H / 2);
    i === 0 ? ctx2d.moveTo(0, y) : ctx2d.lineTo(i * step, y);
  });
  ctx2d.stroke();
}

// ─── File Upload ──────────────────────────────────────────────────────────────

dom.fileInput.addEventListener("change", (e) => {
  if (e.target.files[0]) uploadFile(e.target.files[0]);
});
dom.uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dom.uploadZone.classList.add("drag-over");
});
dom.uploadZone.addEventListener("dragleave", () =>
  dom.uploadZone.classList.remove("drag-over"),
);
dom.uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dom.uploadZone.classList.remove("drag-over");
  if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
});

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  toast(`Uploading ${file.name}…`);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (data.error) {
      toast(data.error, "error");
      return;
    }
    addJob({
      id: data.job_id,
      filename: file.name,
      status: "queued",
      progress: 0,
    });
    watchJob(data.job_id);
    selectJob(data.job_id);
    toast("File uploaded — transcribing…", "success");
  } catch (e) {
    toast("Upload failed", "error");
  }
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

function addJob(job) {
  const tpl = document.getElementById("tpl-job").content.cloneNode(true);
  const el = tpl.querySelector(".job-item");
  el.dataset.id = job.id;
  el.querySelector(".job-name").textContent = job.filename;

  const statusEl = el.querySelector(".job-status");
  statusEl.className = `job-status status-${job.status}`;
  statusEl.textContent = job.status;

  el.querySelector(".job-progress").style.width = (job.progress || 0) + "%";

  if (job.recording) {
    const dl = el.querySelector(".job-download");
    dl.href = `/api/jobs/${job.id}/download`;
    dl.style.display = "";
    dl.addEventListener("click", (e) => e.stopPropagation());
  }

  el.querySelector(".job-delete").addEventListener("click", (e) => {
    e.stopPropagation();
    deleteJob(job.id);
  });
  el.addEventListener("click", () => selectJob(job.id));

  dom.jobsList.prepend(el);
  state.jobElements.set(job.id, el);
}

function updateJobEl(job) {
  const el = state.jobElements.get(job.id);
  if (!el) return;
  const statusEl = el.querySelector(".job-status");
  statusEl.className = `job-status status-${job.status}`;
  statusEl.textContent = job.status;
  const bar = el.querySelector(".job-progress");
  bar.style.width = (job.progress || 0) + "%";
  if (job.status === "done") bar.style.background = "var(--green)";
  if (job.status === "error") bar.style.background = "var(--red)";
}

function selectJob(id) {
  dom.jobsList
    .querySelectorAll(".job-item")
    .forEach((e) => e.classList.remove("active"));
  state.jobElements.get(id)?.classList.add("active");
  state.activeJobId = id;

  fetch(`/api/status/${id}`)
    .then((r) => r.json())
    .then((job) => {
      if (job.status === "done" && job.transcript) {
        showTranscript(job.transcript, job.ai_results);
      } else if (job.status === "error") {
        toast(`Error: ${job.error}`, "error");
      } else {
        showProcessing(job.status);
      }
    });
}

async function deleteJob(id) {
  await fetch(`/api/jobs/${id}`, { method: "DELETE" });
  state.jobElements.get(id)?.remove();
  state.jobElements.delete(id);

  if (state.activeJobId === id) {
    state.activeJobId = null;
    dom.editor.style.display = "none";
    dom.emptyState.style.display = "flex";
    setEditorButtons(false);
    dom.charCount.textContent = "";
    renderAiResults([]);
  }
}

function watchJob(id) {
  if (!state.jobStartTime[id]) state.jobStartTime[id] = Date.now();
  if (!state.lastUpdateTime[id]) state.lastUpdateTime[id] = Date.now();
  if (state.activeJobId === id) startElapsedTimer(id);

  const poll = setInterval(async () => {
    const res = await fetch(`/api/status/${id}`);
    const job = await res.json();
    updateJobEl(job);

    if (state.activeJobId === id) {
      setHeaderProgress(STAGE_LABELS[job.status] ?? job.status, job.progress);
    }

    if (job.progress !== state.lastProgress[id]) {
      state.lastProgress[id] = job.progress;
      state.lastUpdateTime[id] = Date.now();
    }

    if (job.status === "done" || job.status === "error") {
      clearInterval(poll);
      stopElapsedTimer();
      delete state.jobStartTime[id];
      delete state.lastUpdateTime[id];
      delete state.lastProgress[id];
      setHeaderProgress("", 0, { hide: true });

      if (job.status === "done") {
        toast("Transcription complete!", "success");
        if (state.activeJobId === id || !state.activeJobId) {
          showTranscript(job.transcript, job.ai_results);
          selectJob(id);
        }
        if (state.autoFixEnabled && job.transcript)
          triggerAutoFix(id, job.transcript);
      } else {
        toast(`Job failed: ${job.error}`, "error");
        if (state.activeJobId === id)
          dom.processingOverlay.classList.remove("active");
      }
    } else if (state.activeJobId === id) {
      updateProcessingUI(job.status, job.progress);
    }
  }, 1500);
}

// ─── Editor ───────────────────────────────────────────────────────────────────

function showTranscript(text, aiResults) {
  dom.emptyState.style.display = "none";
  dom.processingOverlay.classList.remove("active");
  dom.editor.style.display = "block";
  dom.editor.value = text;
  updateCharCount();
  setEditorButtons(true);
  renderAiResults(aiResults || []);
}

function showProcessing(label, pct = 0) {
  dom.emptyState.style.display = "none";
  dom.processingOverlay.classList.add("active");
  dom.editor.style.display = "none";
  renderAiResults([]);
  setEditorButtons(false);
  updateProcessingUI(label, pct);
}

function updateProcessingUI(label, pct) {
  dom.processingLabel.textContent = (STAGE_LABELS[label] ?? label) + "…";
  dom.progressBarFill.style.width = pct + "%";
  dom.progressPct.textContent = pct > 0 ? pct + "%" : "";
}

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
        dom.progressLastUpdate.style.color =
          stale > 30 ? "var(--red)" : "var(--muted)";
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

function setEditorButtons(on) {
  [
    dom.btnSummarize,
    dom.btnGrammar,
    dom.btnEditVoice,
    dom.btnCopy,
    dom.btnExport,
  ].forEach((btn) => (btn.disabled = !on));
}

dom.editor.addEventListener("input", updateCharCount);
function updateCharCount() {
  const n = dom.editor.value.length;
  dom.charCount.textContent = n > 0 ? `${n.toLocaleString()} chars` : "";
}

// ─── Toolbar ──────────────────────────────────────────────────────────────────

dom.btnCopy.addEventListener("click", () => {
  navigator.clipboard
    .writeText(dom.editor.value)
    .then(() => toast("Copied!", "success"));
});

dom.btnExport.addEventListener("click", () => {
  downloadMarkdown("transcript.md", `# Transcript\n\n${dom.editor.value}\n`);
  toast("Exported as Markdown", "success");
});

function downloadMarkdown(filename, content) {
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/markdown" }),
  );
  const a = Object.assign(document.createElement("a"), {
    href: url,
    download: filename,
  });
  a.click();
  URL.revokeObjectURL(url);
}

// ─── AI Results ───────────────────────────────────────────────────────────────

function escHtml(str) {
  return str.replace(
    /[&<>]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c],
  );
}

const AI_EMPTY_HTML = `
  <div class="ai-empty-state">
    <div class="ai-empty-glyph">✦</div>
    <div class="ai-empty-text">Summarize or auto-fix your transcript to see results here</div>
  </div>`;

function renderAiResults(aiResults) {
  dom.aiResultsPanel.innerHTML = "";

  if (!aiResults?.length) {
    dom.aiResultsPanel.innerHTML = AI_EMPTY_HTML;
    return;
  }

  const sorted = [...aiResults]
    .map((r, originalIdx) => ({ ...r, originalIdx }))
    .sort((a, b) => {
      if (a.mode === b.mode) return b.created_at - a.created_at;
      return a.mode === "grammar" ? -1 : 1;
    });

  sorted.forEach((r) => {
    const isGrammar = r.mode !== "summarize";
    const label = isGrammar ? "✦ Grammar Fix" : "⚡ Summary";
    const badgeCls = isGrammar ? "badge-grammar" : "badge-summarize";
    const timeStr = new Date(r.created_at * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });

    const block = document
      .getElementById("tpl-ai-result")
      .content.cloneNode(true)
      .querySelector(".ai-result-block");
    const badge = block.querySelector(".ai-result-badge");
    badge.textContent = label;
    badge.className = `ai-result-badge ${badgeCls}`;
    block.querySelector(".ai-result-time").textContent = timeStr;
    block.querySelector(".ai-result-body").textContent = r.text; // textContent = no XSS risk, no need for escHtml
    block.querySelector(".ai-result-progress-fill").style.width =
      (r.progress || 0) + "%";

    block.querySelector(".ai-result-header").addEventListener("click", (e) => {
      if (e.target.classList.contains("ai-result-delete")) return;
      block.querySelector(".ai-result-body").classList.toggle("collapsed");
      block.querySelector(".ai-result-actions").classList.toggle("collapsed");
    });

    block.querySelector(".ai-copy-btn").addEventListener("click", () => {
      navigator.clipboard
        .writeText(r.text)
        .then(() => toast("Copied!", "success"));
    });

    block.querySelector(".ai-export-btn").addEventListener("click", () => {
      const lbl = isGrammar ? "Grammar-Fix" : "Summary";
      downloadMarkdown(`${lbl.toLowerCase()}.md`, `# ${lbl}\n\n${r.text}\n`);
      toast(`Exported ${lbl}`, "success");
    });

    block
      .querySelector(".ai-result-delete")
      .addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!state.activeJobId) return;
        const res = await fetch(
          `/api/jobs/${state.activeJobId}/ai/${r.originalIdx}`,
          {
            method: "DELETE",
          },
        );
        if (res.ok) {
          const data = await res.json();
          renderAiResults(data.ai_results);
          toast("Deleted", "success");
        }
      });

    dom.aiResultsPanel.appendChild(block);
  });
}

async function aiAction(mode) {
  const text = dom.editor.value;
  if (!text.trim()) return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);

  const btn = mode === "summarize" ? dom.btnSummarize : dom.btnGrammar;
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";

  try {
    const res = await fetch("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, mode, job_id: state.activeJobId }),
      signal: controller.signal,
    });
    const data = await res.json();
    if (data.error) {
      toast(data.hint || data.detail || data.error, "error");
    } else {
      await refreshAiPanel();
      toast(
        `${mode === "summarize" ? "Summary" : "Grammar fix"} saved`,
        "success",
      );
    }
  } catch (e) {
    if (e.name === "AbortError") {
      toast("AI request timed out (45s)", "error");
    } else {
      toast(`AI request failed: ${e.message}`, "error");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
    setEditorButtons(true);
  }
}

dom.btnSummarize.addEventListener("click", () => aiAction("summarize"));
dom.btnGrammar.addEventListener("click", () => aiAction("grammar"));

async function refreshAiPanel() {
  if (!state.activeJobId) return;
  const res = await fetch(`/api/status/${state.activeJobId}`);
  const job = await res.json();
  renderAiResults(job.ai_results || []);
}

// ─── Auto-fix ─────────────────────────────────────────────────────────────────

dom.autoFixToggle.checked = state.autoFixEnabled;
dom.autoFixToggle.addEventListener("change", (e) => {
  state.autoFixEnabled = e.target.checked;
  localStorage.setItem("autoFixEnabled", state.autoFixEnabled);
});

async function triggerAutoFix(jobId, transcript) {
  if (!state.autoFixEnabled || !transcript) return;

  toast("Auto-fixing transcript…", "info");

  // Create temporary AI result block immediately
  const tempResult = {
    mode: "grammar",
    text: "Processing…",
    created_at: Math.floor(Date.now() / 1000),
    progress: 5,
    _temp: true,
  };

  renderAiResults([tempResult]);
  setHeaderProgress("Auto-fixing", 10, { aiMode: true });

  // Simulated smooth progress
  let fakeProgress = 10;
  const fakeInterval = setInterval(() => {
    fakeProgress = Math.min(fakeProgress + 5, 85);
    tempResult.progress = fakeProgress;
    renderAiResults([tempResult]);
  }, 400);

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000); // 45s timeout

    const res = await fetch("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: transcript,
        mode: "grammar",
        job_id: jobId,
      }),
      signal: controller.signal,
    });

    clearTimeout(timeout);
    clearInterval(fakeInterval);

    const data = await res.json();

    if (data.error) throw new Error(data.error);

    setHeaderProgress("Complete", 100, { aiMode: true });

    await refreshAiPanel();
    toast("Auto-fix complete!", "success");

    setTimeout(() => setHeaderProgress("", 0, { hide: true }), 1500);
  } catch (e) {
    clearInterval(fakeInterval);
    setHeaderProgress("", 0, { hide: true });

    if (e.name === "AbortError") {
      toast("Auto-fix timed out after 45 seconds", "error");
    } else {
      toast(`Auto-fix failed: ${e.message}`, "error");
    }

    renderAiResults([]);
  }
}
// ─── Voice Edit ───────────────────────────────────────────────────────────────

dom.btnEditVoice.addEventListener("click", () => {
  state.voiceEditMode ? stopVoiceEdit() : startVoiceEdit();
});

async function startVoiceEdit() {
  if (!dom.editor.value.trim()) {
    toast("No text in editor to edit", "error");
    return;
  }

  try {
    state.voiceEditStream = await navigator.mediaDevices.getUserMedia({
      audio: true,
    });
  } catch (e) {
    toast(`Microphone access denied: ${e.message}`, "error");
    return;
  }

  state.voiceEditChunks = [];
  const mimeType = getSupportedMimeType();

  try {
    state.voiceEditRecorder = new MediaRecorder(
      state.voiceEditStream,
      mimeType ? { mimeType } : {},
    );
  } catch (e) {
    toast(`Recording not supported: ${e.message}`, "error");
    return;
  }

  state.voiceEditRecorder.ondataavailable = (e) => {
    if (e.data?.size > 0) state.voiceEditChunks.push(e.data);
  };
  state.voiceEditRecorder.onstop = submitVoiceEdit;
  state.voiceEditRecorder.onerror = (e) => {
    toast(`Recording error: ${e.error?.message || e}`, "error");
    setHeaderProgress("", 0, { hide: true });
  };
  state.voiceEditRecorder.start(250);

  state.voiceEditMode = true;
  dom.btnEditVoice.classList.add("recording");
  setHeaderProgress("Recording edit commands…", 10, { aiMode: true });
  toast("Recording edit commands… speak now");
}

function stopVoiceEdit() {
  if (state.voiceEditRecorder?.state !== "inactive")
    state.voiceEditRecorder.stop();
  state.voiceEditStream?.getTracks().forEach((t) => t.stop());
  state.voiceEditMode = false;
  dom.btnEditVoice.classList.remove("recording");
  setHeaderProgress("", 0, { hide: true });
}

async function submitVoiceEdit() {
  const mimeType = state.voiceEditRecorder?.mimeType || "audio/webm";
  const ext = mimeToExt(mimeType);
  const blob = new Blob(state.voiceEditChunks, { type: mimeType });

  if (blob.size < 1000) {
    setHeaderProgress("", 0, { hide: true });
    toast("Recording too short — try again", "error");
    state.voiceEditMode = false;
    dom.btnEditVoice.classList.remove("recording");
    return;
  }

  setHeaderProgress("Transcribing", 40, { aiMode: true });

  const fd = new FormData();
  fd.append("audio", blob, `voice_edit.${ext}`);
  fd.append("text", dom.editor.value);
  fd.append("mode", "edit");

  try {
    const res = await fetch("/api/ai", { method: "POST", body: fd });
    setHeaderProgress("Applying edit", 70, { aiMode: true });
    const data = await res.json();

    if (data.error) {
      setHeaderProgress("", 0, { hide: true });
      toast(data.hint || data.detail || data.error, "error");
    } else if (data.text) {
      setHeaderProgress("Done", 100, { aiMode: true });
      dom.editor.value = data.text;
      updateCharCount();
      setTimeout(() => {
        setHeaderProgress("", 0, { hide: true });
        toast("Text edited successfully", "success");
      }, 500);
      await refreshAiPanel();
    } else {
      setHeaderProgress("", 0, { hide: true });
      toast("No result returned", "error");
    }
  } catch (e) {
    setHeaderProgress("", 0, { hide: true });
    toast(`Edit failed: ${e.message}`, "error");
  }

  state.voiceEditMode = false;
  dom.btnEditVoice.classList.remove("recording");
}

// ─── Personal Names Modal ─────────────────────────────────────────────────────

function openPersonalNamesModal() {
  dom.personalNamesModal.classList.add("active");
  fetch("/api/personal-names")
    .then((r) => r.json())
    .then((data) => {
      dom.personalNamesInput.value = (data.names || []).join("\n");
    })
    .catch(() => {
      dom.personalNamesInput.value = "";
    });
}

function closePersonalNamesModal() {
  dom.personalNamesModal.classList.remove("active");
}

async function savePersonalNames() {
  const names = dom.personalNamesInput.value
    .split("\n")
    .map((n) => n.trim())
    .filter(Boolean);

  try {
    const res = await fetch("/api/personal-names", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names }),
    });
    const data = await res.json();
    if (data.error) {
      toast(data.error, "error");
    } else {
      toast("Personal names saved", "success");
      closePersonalNamesModal();
    }
  } catch (e) {
    toast(`Failed to save: ${e.message}`, "error");
  }
}

document
  .getElementById("btn-personal-names")
  .addEventListener("click", openPersonalNamesModal);
document
  .getElementById("close-names-modal")
  .addEventListener("click", closePersonalNamesModal);
document
  .getElementById("cancel-names-modal")
  .addEventListener("click", closePersonalNamesModal);
document
  .getElementById("save-names-modal")
  .addEventListener("click", savePersonalNames);
dom.personalNamesModal
  .querySelector(".modal-backdrop")
  .addEventListener("click", closePersonalNamesModal);

// ─── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  try {
    const res = await fetch("/api/jobs");
    const list = await res.json();
    [...list].reverse().forEach((job) => {
      addJob(job);
      if (job.status !== "done" && job.status !== "error") watchJob(job.id);
    });
    const done = list.find((j) => j.status === "done");
    if (done) selectJob(done.id);
  } catch {
    /* server not ready */
  }
}

init();
