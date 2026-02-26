// ─── Jobs ─────────────────────────────────────────────────────────────────────

function addJob(job) {
  const tpl = document.getElementById("tpl-job").content.cloneNode(true);
  const el = tpl.querySelector(".job-item");
  el.dataset.id = job.id;

  const nameEl = el.querySelector(".job-name");
  nameEl.textContent = job.filename;
  nameEl.title = job.filename;

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
    dom.jobsList.querySelectorAll(".job-item").forEach((e) => e.classList.remove("active"));
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

      // 1. Update Job List Item
      if (el) {
        const bar = el.querySelector(".job-progress");
        const statusEl = el.querySelector(".job-status");

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

      // 2. Update Active View
      if (state.activeJobId === id) {
        setHeaderProgress(progress.message || progress.stage, progress.pct || 0);

        if (progress.text_segment) {
          dom.processingLabel.textContent = `"${progress.text_segment}..."`;
          dom.processingLabel.classList.add("live-text");
        } else {
          updateProcessingUI(progress.stage, progress.pct || 0);
        }

        if (progress.details?.tokens) {
          dom.progressLastUpdate.textContent = `Tokens: ${progress.details.tokens}`;
        }
      }

      // 3. Handle Lifecycle States
      if (progress.stage === "done" || progress.stage === "error" || progress.stage === "cancelled") {
        eventSource.close();
        stopElapsedTimer();
        setHeaderProgress("", 0, { hide: true });

        if (progress.stage === "done") {
          fetch(`/api/status/${id}`)
            .then((r) => r.json())
            .then(async (job) => {
              toast("Transcription complete!", "success");
              if (state.activeJobId === id || !state.activeJobId) {
                showTranscript(job.transcript, job.ai_results);
                selectJob(id);
              }

              try {
                if ((!job.ai_results || job.ai_results.length === 0) && job.transcript) {
                  if (!state.aiTriggeredJobs.has(id)) {
                    state.aiTriggeredJobs.add(id);
                    state.currentAIMode = dom.aiModeSelector?.value || state.currentAIMode || Object.keys(state.aiModes || {})[0];
                    updateAiButtonLabel();
                    await aiAction(job.transcript, state.currentAIMode);
                  }
                }
              } catch (e) {
                console.warn("Auto AI action failed:", e);
                state.aiTriggeredJobs.delete(id);
              }

              if (state.autoFixEnabled && job.transcript && state.currentAIMode === "grammar") {
                triggerAutoFix(id, job.transcript);
              }
            });
        } else if (progress.stage === "cancelled") {
          toast("Job cancelled", "info");
          if (state.activeJobId === id) dom.processingOverlay.classList.remove("active");
        } else {
          toast(`Job failed: ${progress.message}`, "error");
          if (state.activeJobId === id) dom.processingOverlay.classList.remove("active");
        }

        delete state.jobStartTime[id];
        delete state.lastUpdateTime[id];
        delete state.lastProgress[id];
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

  setTimeout(() => {
    if (state.lastProgress[id] === undefined) {
      console.log("Stream non-responsive, switching to poll.");
      eventSource.close();
      watchJobPoll(id);
    }
  }, 5000);
}

function watchJobPoll(id) {
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

    if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
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

        try {
          if ((!job.ai_results || job.ai_results.length === 0) && job.transcript) {
            if (!state.aiTriggeredJobs.has(id)) {
              state.aiTriggeredJobs.add(id);
              state.currentAIMode = dom.aiModeSelector?.value || state.currentAIMode || Object.keys(state.aiModes || {})[0];
              updateAiButtonLabel();
              await aiAction(job.transcript, state.currentAIMode);
            }
          }
        } catch (e) {
          console.warn("Auto AI action failed (poll):", e);
        }

        if (state.autoFixEnabled && job.transcript && state.currentAIMode === "grammar") {
          triggerAutoFix(id, job.transcript);
        }
      } else {
        toast(`Job failed: ${job.error}`, "error");
        if (state.activeJobId === id) dom.processingOverlay.classList.remove("active");
      }
    } else if (state.activeJobId === id) {
      updateProcessingUI(job.status, job.progress);
    }
  }, 1500);
}
