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
  // Track which jobs we've auto-triggered AI for to avoid duplicates
  aiTriggeredJobs: new Set(),

  // Prefs
  autoFixEnabled: localStorage.getItem("autoFixEnabled") === "true",

  // AI modes (populated from server)
  aiModes: {},
  currentAIMode: null,
  
  // Auto-fix tracking to prevent multiple runs
  autoFixRunning: false,
  autoFixCompleted: new Set(),  // Track jobs that have been auto-fixed
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
  // autoFixToggle removed, using button below
  btnAutoFix: document.getElementById("btn-auto-fix-toggle"),
  personalNamesModal: document.getElementById("personal-names-modal"),
  personalNamesInput: document.getElementById("personal-names-input"),
  modeModal: document.getElementById("mode-modal"),
  modeNameInput: document.getElementById("mode-name-input"),
  modeDisplayInput: document.getElementById("mode-display-input"),
  modeInstructionInput: document.getElementById("mode-instruction-input"),
  modeRulesInput: document.getElementById("mode-rules-input"),
  modePlaceholderInput: document.getElementById("mode-placeholder-input"),
  // Chat UI elements (new)
  chatMessages: document.getElementById("chat-messages"),
  chatInput: document.getElementById("chat-input"),
  chatSend: document.getElementById("chat-send"),
  chatRecordBtn: document.getElementById("chat-record-btn"),
  chatAppendBtn: document.getElementById("chat-append-btn"),
  chatUploadBtn: document.getElementById("chat-upload-btn"),
  // Jobs drawer
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

// ─── AI Mode Management ──────────────────────────────────────────────────

async function loadAIModes() {
  try {
    const res = await fetch("/api/ai/modes");
    if (!res.ok) throw new Error("Failed to load modes");
    const { modes, order } = await res.json();
    state.aiModes = modes;
    state.aiModeOrder = order || Object.keys(modes);

    const sel = dom.aiModeSelector;
    sel.innerHTML = "";
    state.aiModeOrder.forEach((mode) => {
      const info = modes[mode] || {};
      const opt = document.createElement("option");
      opt.value = mode;
      opt.textContent = info.display_name || mode;
      sel.appendChild(opt);
    });

    if (!state.currentAIMode || !modes[state.currentAIMode]) {
      state.currentAIMode = sel.options[0]?.value;
    }
    sel.value = state.currentAIMode;
    updateAiButtonLabel();
    sel.disabled = false;
    dom.btnAi.disabled = false;
  } catch (e) {
    console.error("unable to fetch ai modes", e);
  }
}

function updateAiButtonLabel() {
  const info = state.aiModes[state.currentAIMode] || {};
  const name = info.display_name || state.currentAIMode || "Process";
  dom.btnAi.textContent = name;
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

if (dom.recordBtn) dom.recordBtn.addEventListener("click", toggleRecord);
if (dom.appendBtn) dom.appendBtn.addEventListener("click", toggleAppendRecord);
if (dom.chatRecordBtn) dom.chatRecordBtn.addEventListener("click", toggleRecord);
if (dom.chatAppendBtn) dom.chatAppendBtn.addEventListener("click", toggleAppendRecord);

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
  if (dom.recordBtn) dom.recordBtn.classList.add("recording");
  if (dom.chatRecordBtn) dom.chatRecordBtn.classList.add("recording");
  if (dom.appendBtn) dom.appendBtn.classList.toggle("recording", isAppend);
  if (dom.chatAppendBtn) dom.chatAppendBtn.classList.toggle("recording", isAppend);
  // Apply inline styles as a fallback if CSS isn't applied/loaded.
  if (dom.chatRecordBtn) {
    dom.chatRecordBtn.style.animation = "pulse 1.2s infinite ease-in-out";
    dom.chatRecordBtn.style.boxShadow = "0 0 0 6px rgba(196, 168, 130, 0.12)";
  }
  if (dom.chatAppendBtn && isAppend) {
    dom.chatAppendBtn.style.animation = "pulse 1.2s infinite ease-in-out";
    dom.chatAppendBtn.style.boxShadow = "0 0 0 6px rgba(196, 168, 130, 0.12)";
  }
  if (dom.statusEl) {
    dom.statusEl.textContent = isAppend
      ? "Appending… click to stop"
      : "Recording… click to stop";
    dom.statusEl.classList.add("active");
  }
}

function stopRecording() {
  if (state.mediaRecorder?.state !== "inactive") state.mediaRecorder.stop();
  state.recordStream?.getTracks().forEach((t) => t.stop());
  clearInterval(state.timerInterval);
  cancelAnimationFrame(state.animFrame);
  if (dom.recordBtn) dom.recordBtn.classList.remove("recording");
  if (dom.chatRecordBtn) dom.chatRecordBtn.classList.remove("recording");
  if (dom.chatRecordBtn) {
    dom.chatRecordBtn.style.animation = "";
    dom.chatRecordBtn.style.boxShadow = "";
  }
  if (dom.appendBtn) dom.appendBtn.classList.remove("recording");
  if (dom.chatAppendBtn) dom.chatAppendBtn.classList.remove("recording");
  if (dom.chatAppendBtn) {
    dom.chatAppendBtn.style.animation = "";
    dom.chatAppendBtn.style.boxShadow = "";
  }
  if (dom.statusEl) {
    dom.statusEl.textContent = "Uploading…";
    dom.statusEl.classList.remove("active");
  }
  if (dom.timerEl) dom.timerEl.textContent = "";
  if (ctx2d && dom.waveformCanvas) ctx2d.clearRect(0, 0, dom.waveformCanvas.width, dom.waveformCanvas.height);
}

async function submitRecording() {
  const mimeType = state.mediaRecorder?.mimeType || "audio/webm";
  const ext = mimeToExt(mimeType);
  const blob = new Blob(state.recordChunks, { type: mimeType });

  if (blob.size < 1000) {
    toast("Recording too short or empty — try again", "error");
    if (dom.statusEl) dom.statusEl.textContent = "Click to start recording";
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
        // start a new chat for this recording
        startNewConversation();
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
  if (dom.statusEl) dom.statusEl.textContent = "Click to start recording";
}

function updateTimer() {
  const s = Math.floor((Date.now() - state.recordStart) / 1000);
  if (dom.timerEl) dom.timerEl.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function drawWaveform() {
  if (!ctx2d || !dom.waveformCanvas || !state.analyser) return;
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

if (dom.fileInput) {
  dom.fileInput.addEventListener("change", (e) => {
    if (e.target.files[0]) uploadFile(e.target.files[0]);
  });
}
if (dom.chatUploadBtn && dom.fileInput) {
  dom.chatUploadBtn.addEventListener('click', () => dom.fileInput.click());
}

// Allow dropping files anywhere on the page to upload (graceful fallback)
document.addEventListener('dragover', (e) => e.preventDefault());
document.addEventListener('drop', (e) => {
  e.preventDefault();
  const f = e.dataTransfer?.files?.[0];
  if (f) uploadFile(f);
});
if (dom.uploadZone) {
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
}

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
    // start a new conversation when uploading a file
    startNewConversation();
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
  const nameEl = el.querySelector(".job-name");
  nameEl.textContent = job.filename;
  nameEl.title = job.filename; // tooltip for long names

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
  if (dom.jobsList) {
    dom.jobsList
      .querySelectorAll(".job-item")
      .forEach((e) => e.classList.remove("active"));
  }
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
  
  // Clear auto-fix tracking for this job
  state.autoFixCompleted.delete(id);

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

  const eventSource = new EventSource(`/api/transcribe/stream/${id}`);

  eventSource.onmessage = (event) => {
    try {
      const progress = JSON.parse(event.data);
      const el = state.jobElements.get(id);

      // 1. Update Job List Item (Sidebar)
      if (el) {
        const bar = el.querySelector(".job-progress");
        const statusEl = el.querySelector(".job-status");
        
        // Only update DOM if values actually changed to prevent flicker
        if (progress.pct !== undefined && state.lastProgress[id] !== progress.pct) {
          bar.style.width = progress.pct + "%";
          state.lastProgress[id] = progress.pct;
        }

        const newStatus = progress.message || progress.stage;
        if (statusEl.textContent !== newStatus) {
          statusEl.className = `job-status status-${progress.stage}`;
          statusEl.textContent = newStatus;
        }
      }

      // 2. Update Active View (Header & Main Overlay)
      if (state.activeJobId === id) {
        setHeaderProgress(progress.message || progress.stage, progress.pct || 0);
        
        // If we have a live text segment, show it in the processing label
        if (progress.text_segment) {
          dom.processingLabel.textContent = `“${progress.text_segment}...”`;
          dom.processingLabel.classList.add("live-text"); // Use CSS for a subtle fade/pulse
        } else {
          updateProcessingUI(progress.stage, progress.pct || 0);
        }

        if (progress.details?.tokens) {
          dom.progressLastUpdate.textContent = `Tokens: ${progress.details.tokens}`;
        }
      }

      // 3. Handle Lifecycle States
      if (progress.stage === "done" || progress.stage === "error") {
        eventSource.close();
        stopElapsedTimer();
        setHeaderProgress("", 0, { hide: true });

        if (progress.stage === "done") {
          fetch(`/api/status/${id}`)
            .then(r => r.json())
            .then(async (job) => {
              toast("Transcription complete!", "success");
              if (state.activeJobId === id || !state.activeJobId) {
                showTranscript(job.transcript, job.ai_results);
                selectJob(id);
              }

              // If there are no AI results saved for this job yet, automatically
              // invoke the AI action so the transcript is processed and appears
              // in the chat without requiring manual copy/paste.
              try {
                if ((!job.ai_results || job.ai_results.length === 0) && job.transcript) {
                  // Avoid double-triggering for the same job
                  if (!state.aiTriggeredJobs.has(id)) {
                    state.aiTriggeredJobs.add(id);
                    // Ensure a sensible AI mode is selected before invoking AI
                    state.currentAIMode = dom.aiModeSelector?.value || state.currentAIMode || Object.keys(state.aiModes || {})[0];
                    updateAiButtonLabel();
                    // Run only the selected mode for this transcript
                    await aiAction(job.transcript, state.currentAIMode);
                  }
                }
              } catch (e) {
                console.warn("Auto AI action failed:", e);
              }

                    // Only run auto-fix if the user has enabled it AND the selected
                    // AI mode is 'grammar' (avoid running multiple modes simultaneously).
                    if (state.autoFixEnabled && job.transcript && state.currentAIMode === 'grammar') {
                      triggerAutoFix(id, job.transcript);
                    }
            });
        } else {
          toast(`Job failed: ${progress.message}`, "error");
          if (state.activeJobId === id) {
            dom.processingOverlay.classList.remove("active");
          }
        }

        // Cleanup state
        delete state.jobStartTime[id];
        delete state.lastUpdateTime[id];
        delete state.lastProgress[id];
        // Allow re-triggering later if job is rerun
        state.aiTriggeredJobs.delete(id);
      }
    } catch (e) {
      console.error("Error parsing progress payload:", e);
    }
  };

  eventSource.onerror = () => {
    eventSource.close();
    console.warn("SSE Stream failed, falling back to polling for job:", id);
    watchJobPoll(id);
  };

  // Safety timeout: If no progress after 5s, fallback to polling
  setTimeout(() => {
    if (state.lastProgress[id] === undefined) {
      console.log("Stream non-responsive, switching to poll.");
      eventSource.close();
      watchJobPoll(id);
    }
  }, 5000);
}

function watchJobPoll(id) {
  // Fallback polling function
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
        state.aiTriggeredJobs.delete(id);
      setHeaderProgress("", 0, { hide: true });

      if (job.status === "done") {
        toast("Transcription complete!", "success");
        if (state.activeJobId === id || !state.activeJobId) {
          showTranscript(job.transcript, job.ai_results);
          selectJob(id);
        }

        // Auto-trigger AI processing for this transcript if no AI results
        // currently exist for the job.
        try {
          if ((!job.ai_results || job.ai_results.length === 0) && job.transcript) {
            // Avoid duplicate triggers from polling fallback
            if (!state.aiTriggeredJobs.has(id)) {
              state.aiTriggeredJobs.add(id);
              // select current mode from selector if available
              state.currentAIMode = dom.aiModeSelector?.value || state.currentAIMode || Object.keys(state.aiModes || {})[0];
              updateAiButtonLabel();
              // Run only the selected mode for this transcript
              await aiAction(job.transcript, state.currentAIMode);
            }
          }
        } catch (e) {
          console.warn("Auto AI action failed (poll):", e);
        }

        // Only run auto-fix if the user has enabled it AND the selected
        // AI mode is 'grammar' (avoid running multiple modes simultaneously).
        if (state.autoFixEnabled && job.transcript && state.currentAIMode === 'grammar')
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
  // merge transcript into the chat view as an assistant-labeled message
  try {
    // avoid duplicate transcript messages
    if (dom.chatMessages) {
      const existing = Array.from(dom.chatMessages.querySelectorAll('.message.assistant'))
        .some(m => {
          const lbl = m.querySelector('.message-label')?.textContent?.trim();
          const body = m.querySelector('.message-body')?.textContent?.trim();
          return lbl === 'Transcript' && body === (text || '').trim();
        });
      if (!existing) appendAssistantMessage(text, "Transcript");
    } else {
      // fallback: show in hidden editor if chat area missing
      dom.editor.style.display = "block";
      dom.editor.value = text;
      updateCharCount();
      setEditorButtons(true);
    }
  } catch (e) {
    dom.editor.style.display = "block";
    dom.editor.value = text;
    updateCharCount();
    setEditorButtons(true);
  }
  // also render any AI results into chat
  renderAiResults(aiResults || []);
}

// Start a new conversation in the chat (clear messages). If `preserve` is true, do not clear.
function startNewConversation(preserve = false) {
  if (preserve) return;
  if (!dom.chatMessages) return;
  dom.chatMessages.innerHTML = '';
  // show empty hint
  const empty = document.createElement('div');
  empty.className = 'empty-state';
  empty.innerHTML = '<div class="empty-glyph">◌</div><div class="empty-text">Start a conversation — type below or upload audio to begin</div>';
  dom.chatMessages.appendChild(empty);
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
    dom.aiModeSelector,
    dom.btnAi,
    dom.btnEditVoice,
    dom.btnCopy,
    dom.btnExport,
  ].forEach((btn) => (btn.disabled = !on));
}
// Guarded version used where callers may not expect missing elements
function setEditorButtonsSafe(on) {
  [
    dom.aiModeSelector,
    dom.btnAi,
    dom.btnEditVoice,
    dom.btnCopy,
    dom.btnExport,
  ].forEach((btn) => {
    if (btn) btn.disabled = !on;
  });
}

dom.editor.addEventListener("input", updateCharCount);
function updateCharCount() {
  const n = dom.editor.value.length;
  dom.charCount.textContent = n > 0 ? `${n.toLocaleString()} chars` : "";
}

// ─── Toolbar ──────────────────────────────────────────────────────────────────

dom.btnCopy.addEventListener("click", () => {
  // Prefer copying the editor content; if empty, copy the chat messages.
  let text = dom.editor?.value?.trim() || "";
  if (!text && dom.chatMessages) {
    const parts = Array.from(dom.chatMessages.querySelectorAll('.message .message-body'))
      .map(el => el.textContent?.trim())
      .filter(Boolean);
    text = parts.join('\n\n');
  }
  if (!text) {
    toast('Nothing to copy', 'error');
    return;
  }
  navigator.clipboard.writeText(text).then(() => toast('Copied!', 'success')).catch((e) => {
    console.error('Copy failed', e);
    toast('Copy failed', 'error');
  });
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

// Chat helpers (append messages to the new chat view)
function appendUserMessage(text) {
  if (!dom.chatMessages) return;
  dom.chatMessages.querySelectorAll('.empty-state')?.forEach(e => e.remove());
  const el = document.createElement('div');
  el.className = 'message user';
  const body = document.createElement('div');
  body.className = 'message-body';
  body.textContent = text;
  el.appendChild(body);
  dom.chatMessages.appendChild(el);
  dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
}

/**
 * Append an assistant message bubble.
 * @param {string} text initial content
 * @param {string} [label] optional label (e.g. grammar, summarize)
 * @returns {{el:HTMLElement, body:HTMLElement}} elements for later updates
 */
function appendAssistantMessage(text, label) {
  if (!dom.chatMessages) return {};
  dom.chatMessages.querySelectorAll('.empty-state')?.forEach(e => e.remove());
  const el = document.createElement('div');
  el.className = 'message assistant';
  if (label) {
    const lbl = document.createElement('div');
    lbl.className = 'message-label';
    lbl.textContent = label;
    el.appendChild(lbl);
  }
  const body = document.createElement('div');
  body.className = 'message-body';
  body.textContent = text;
  el.appendChild(body);

  // Add per-message actions (copy)
  const actions = document.createElement('div');
  actions.className = 'message-actions';
  const copyBtn = document.createElement('button');
  copyBtn.className = 'btn btn-ghost msg-copy-btn';
  copyBtn.title = 'Copy message';
  copyBtn.innerText = '⎘';
  copyBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const txt = body.textContent || '';
    if (!txt.trim()) {
      toast('Nothing to copy', 'error');
      return;
    }
    navigator.clipboard.writeText(txt).then(() => toast('Copied message', 'success')).catch(() => toast('Copy failed', 'error'));
  });
  actions.appendChild(copyBtn);
  el.appendChild(actions);
  dom.chatMessages.appendChild(el);
  dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
  return { el, body };
}

// helper for streaming tokens
function createAssistantPlaceholder(label) {
  return appendAssistantMessage("", label);
}

// Merge AI results into chat instead of separate right-side panel
function renderAiResults(aiResults) {
  // clear right-panel to avoid clutter (panel may be hidden anyway)
  if (dom.aiResultsPanel) dom.aiResultsPanel.innerHTML = "";
  if (!aiResults || !aiResults.length) return;

  aiResults.forEach((r) => {
    if (!r.text) return;
    const label = (state.aiModes[r.mode] || {}).display_name || r.mode;
    // avoid duplicates by checking text content
    const existing = Array.from((dom.chatMessages || document.createElement('div')).querySelectorAll('.message.assistant .message-body'))
      .some(m => m.textContent && m.textContent.trim() === r.text.trim() &&
                  m.previousSibling?.textContent === label);
    if (!existing) appendAssistantMessage(r.text, label);
  });
}



async function aiAction(overrideText = null, overrideMode = null) {
  // Determine text: explicit override wins, otherwise prefer chat input then editor
  const inputText = overrideText ?? ((dom.chatInput && dom.chatInput.value.trim()) || dom.editor.value);
  if (!inputText || !inputText.trim()) return;

  // Only append a user message when this was triggered from the chat input (not from an override)
  if (!overrideText && dom.chatInput && dom.chatInput.value.trim()) {
    appendUserMessage(dom.chatInput.value.trim());
    dom.chatInput.value = "";
  }

  const text = inputText;
  // Mode selection: explicit override, then selector, then current state
  const mode = overrideMode || dom.aiModeSelector?.value || state.currentAIMode;
  const btn = dom.btnAi;
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  if (dom.chatInput) dom.chatInput.disabled = true;
  if (dom.chatSend) dom.chatSend.disabled = true;

  // insert a placeholder chat message so we can stream tokens into it
  const modeLabel = state.aiModes[mode]?.display_name || mode;
  const placeholder = createAssistantPlaceholder(modeLabel);
  const bodyEl = placeholder.body; // will be updated as tokens arrive
  // we no longer render temporary results in the side panel

  setHeaderProgress(`${modeLabel}…`, 10, { aiMode: true });

  try {
    const response = await fetch("/api/ai/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, mode, job_id: state.activeJobId }),
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error || errorData.detail || "Request failed");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split("\n");

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));

            if (data.error) {
              throw new Error(data.error);
            }

            if (data.chunk) {
              fullText += data.chunk;
              if (bodyEl) bodyEl.textContent = fullText;
            }

            if (data.done) {
              // no fake interval to clear
              fullText = data.text || fullText;
              if (bodyEl) bodyEl.textContent = fullText;
              setHeaderProgress("Complete", 100, { aiMode: true });
              // placeholder already contains fullText
            }
          } catch (e) {
            continue;
          }
        }
      }
    }

    // refresh side-panel results for persistence (chat duplicate check will prevent repeats)
    await refreshAiPanel();
    toast(`${modeLabel} saved`, "success");
    setTimeout(() => setHeaderProgress("", 0, { hide: true }), 1500);
  } catch (e) {
    setHeaderProgress("", 0, { hide: true });
    toast(e.message || `AI request failed: ${e}`, "error");
    renderAiResults([]);
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
    setEditorButtons(true);
    if (dom.chatInput) dom.chatInput.disabled = false;
    if (dom.chatSend) dom.chatSend.disabled = false;
  }
}

dom.btnAi.addEventListener("click", aiAction);

async function refreshAiPanel() {
  if (!state.activeJobId) return;
  const res = await fetch(`/api/status/${state.activeJobId}`);
  const job = await res.json();
  // render results into chat, side panel may not exist
  renderAiResults(job.ai_results || []);
}

// ─── Auto-fix ─────────────────────────────────────────────────────────────────

// migrate toggle input to button
function updateAutoFixButton() {
  const btn = document.getElementById("btn-auto-fix-toggle");
  if (!btn) return;
  btn.textContent = state.autoFixEnabled ? "Auto‑fix on" : "Auto‑fix off";
  btn.classList.toggle("active", state.autoFixEnabled);
}

// initialize after DOM ready
updateAutoFixButton();

const btnAutoFix = document.getElementById("btn-auto-fix-toggle");
if (btnAutoFix) {
  btnAutoFix.addEventListener("click", () => {
    state.autoFixEnabled = !state.autoFixEnabled;
    localStorage.setItem("autoFixEnabled", state.autoFixEnabled);
    updateAutoFixButton();
  });
}

async function triggerAutoFix(jobId, transcript) {
  // Guard: prevent multiple auto-fix runs for the same job
  if (!state.autoFixEnabled || !transcript) return;
  if (state.autoFixCompleted.has(jobId)) return;
  if (state.autoFixRunning) return;

  // Mark as running
  state.autoFixRunning = true;
  state.autoFixCompleted.add(jobId);
  
  toast("Auto-fixing transcript…", "info");

  // Create temporary AI result block immediately
  const tempResult = {
    mode: "grammar",
    text: "",
    created_at: Math.floor(Date.now() / 1000),
    progress: 0,
    _temp: true,
  };

  renderAiResults([tempResult]);
  // Try to find the temporary element in the ai results panel; if that panel
  // is not present (chat-first UI), fall back to creating a chat placeholder
  // assistant message and stream into it instead.
  let tempElem = null;
  let bodyEl = null;
  let progFill = null;
  if (dom.aiResultsPanel) {
    tempElem = dom.aiResultsPanel.querySelector(".ai-result-block[data-temp]");
    bodyEl = tempElem?.querySelector(".ai-result-body");
    progFill = tempElem?.querySelector(".ai-result-progress-fill");
  }
  if (!bodyEl) {
    const placeholder = appendAssistantMessage("", "Grammar");
    bodyEl = placeholder.body;
    progFill = null; // no progress bar in chat placeholder
  }

  setHeaderProgress("Auto-fixing", 10, { aiMode: true });

  try {
    const response = await fetch("/api/ai/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: transcript,
        mode: "grammar",
        job_id: jobId,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error || errorData.detail || "Request failed");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = "";
    let fakeProgress = 10;

    // Simulate progress while streaming
    const fakeInterval = setInterval(() => {
      fakeProgress = Math.min(fakeProgress + 3, 90);
      if (progFill) progFill.style.width = fakeProgress + "%";
    }, 300);

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split("\n");

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));

            if (data.error) {
              clearInterval(fakeInterval);
              throw new Error(data.error);
            }

            if (data.chunk) {
              fullText += data.chunk;
              if (bodyEl) bodyEl.textContent = fullText;
            }

            if (data.done) {
              clearInterval(fakeInterval);
              fullText = data.text || fullText;
              if (bodyEl) bodyEl.textContent = fullText;
              if (progFill) progFill.style.width = "100%";
              setHeaderProgress("Complete", 100, { aiMode: true });
            }
          } catch (e) {
            // Skip invalid JSON
            continue;
          }
        }
      }
    }

    clearInterval(fakeInterval);

    // Refresh AI panel with actual saved results
    const res2 = await fetch(`/api/status/${jobId}`);
    const job = await res2.json();
    renderAiResults(job.ai_results || []);

    toast("Auto-fix complete!", "success");

    setTimeout(() => setHeaderProgress("", 0, { hide: true }), 1500);
  } catch (e) {
    setHeaderProgress("", 0, { hide: true });

    if (e.name === "AbortError") {
      toast("Auto-fix timed out after 5 minutes", "error");
    } else {
      toast(`Auto-fix failed: ${e.message}`, "error");
    }

    // Remove from completed set so user can retry
    state.autoFixCompleted.delete(jobId);
    renderAiResults([]);
  } finally {
    // Reset the running flag
    state.autoFixRunning = false;
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

// ─── Mode Creation Modal ─────────────────────────────────────────────────────

function openModeModal() {
  dom.modeModal.classList.add("active");
  // clear fields
  dom.modeNameInput.value = "";
  dom.modeDisplayInput.value = "";
  dom.modeInstructionInput.value = "";
  dom.modeRulesInput.value = "";
  dom.modePlaceholderInput.value = "";
}

function closeModeModal() {
  dom.modeModal.classList.remove("active");
}

async function saveModeModal() {
  const mode = dom.modeNameInput.value.trim();
  if (!mode) {
    toast("Mode name required", "error");
    return;
  }
  const instruction = dom.modeInstructionInput.value.trim();
  if (!instruction) {
    toast("Instruction required", "error");
    return;
  }
  const display = dom.modeDisplayInput.value.trim();
  const placeholder = dom.modePlaceholderInput.value.trim();
  const rawRules = dom.modeRulesInput.value
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  const config = { instruction };
  if (display) config.display_name = display;
  if (placeholder) config.input_placeholder = placeholder;
  if (rawRules.length) {
    // decide whether to use formatting_rules (bullet) or numbered rules
    const bulletish = rawRules.every((l) => l.startsWith("-") || l.startsWith("*"));
    if (bulletish) {
      config.formatting_rules = rawRules.map((l) => l.replace(/^[-*]\s*/, ""));
    } else {
      config.rules = rawRules;
    }
  }

  try {
    const res = await fetch("/api/ai/modes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, config }),
    });
    const data = await res.json();
    if (data.error) {
      toast(data.error, "error");
    } else {
      toast("Mode saved", "success");
      closeModeModal();
      await loadAIModes();
      state.currentAIMode = mode;
      dom.aiModeSelector.value = mode;
      updateAiButtonLabel();
    }
  } catch (e) {
    toast(`Failed to save mode: ${e.message}`, "error");
  }
}

document.getElementById("btn-add-mode").addEventListener("click", openModeModal);
document.getElementById("close-mode-modal").addEventListener("click", closeModeModal);
document.getElementById("cancel-mode-modal").addEventListener("click", closeModeModal);
document.getElementById("save-mode-modal").addEventListener("click", saveModeModal);
dom.modeModal.querySelector(".modal-backdrop").addEventListener("click", closeModeModal);

// ─── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  // load AI modes first so button/selector are ready
  await loadAIModes();

  // selector change handler
  dom.aiModeSelector.addEventListener("change", (e) => {
    state.currentAIMode = e.target.value;
    updateAiButtonLabel();
  });

  // jobs list now optional (left panel removed)
  if (dom.jobsList) {
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
}

init();

// Chat input wiring: send on button or Enter (Shift+Enter for newline)
if (dom.chatSend) {
  dom.chatSend.addEventListener('click', () => {
    const t = dom.chatInput?.value?.trim();
    if (t) {
      // use editor value path so aiAction works unchanged
      dom.editor.value = t;
      aiAction();
    }
  });
}

if (dom.chatInput) {
  dom.chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const t = dom.chatInput.value.trim();
      if (!t) return;
      dom.editor.value = t;
      aiAction();
    }
  });
}

// Jobs drawer toggle wiring
if (dom.btnToggleJobsDrawer && dom.jobsDrawer) {
  const openDrawer = () => dom.jobsDrawer.classList.add('open');
  const closeDrawer = () => dom.jobsDrawer.classList.remove('open');
  dom.btnToggleJobsDrawer.addEventListener('click', () => {
    dom.jobsDrawer.classList.toggle('open');
  });
  if (dom.jobsDrawerBackdrop) dom.jobsDrawerBackdrop.addEventListener('click', closeDrawer);
  if (dom.jobsDrawerClose) dom.jobsDrawerClose.addEventListener('click', closeDrawer);
}
