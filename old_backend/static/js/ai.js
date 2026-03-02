// ─── AI Mode Management ───────────────────────────────────────────────────────

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

// ─── AI Action ────────────────────────────────────────────────────────────────

async function aiAction(overrideText = null, overrideMode = null) {
  const inputText = overrideText ?? ((dom.chatInput && dom.chatInput.value.trim()) || dom.editor.value);
  if (!inputText || !inputText.trim()) return;

  if (!overrideText && dom.chatInput && dom.chatInput.value.trim()) {
    appendUserMessage(dom.chatInput.value.trim());
    dom.chatInput.value = "";
  }

  const text = inputText;
  const mode = overrideMode || dom.aiModeSelector?.value || state.currentAIMode;
  const btn = dom.btnAi;
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  if (dom.chatInput) dom.chatInput.disabled = true;
  if (dom.chatSend) dom.chatSend.disabled = true;

  const modeLabel = state.aiModes[mode]?.display_name || mode;
  const placeholder = createAssistantPlaceholder(modeLabel);
  const bodyEl = placeholder.body;

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
    state.currentAIReader = reader;
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
            if (data.error) throw new Error(data.error);
            if (data.chunk) { fullText += data.chunk; if (bodyEl) bodyEl.textContent = fullText; }
            if (data.done) {
              fullText = data.text || fullText;
              if (bodyEl) bodyEl.textContent = fullText;
              setHeaderProgress("Complete", 100, { aiMode: true });
            }
          } catch (e) { continue; }
        }
      }
    }

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
    state.currentAIReader = null;
  }
}

async function refreshAiPanel() {
  if (!state.activeJobId) return;
  const res = await fetch(`/api/status/${state.activeJobId}`);
  const job = await res.json();
  renderAiResults(job.ai_results || []);
}

// ─── Auto-fix ─────────────────────────────────────────────────────────────────

function updateAutoFixButton() {
  const btn = document.getElementById("btn-auto-fix-toggle");
  if (!btn) return;
  btn.textContent = state.autoFixEnabled ? "Auto‑fix on" : "Auto‑fix off";
  btn.classList.toggle("active", state.autoFixEnabled);
}

function initAutoFix() {
  updateAutoFixButton();
  const btn = document.getElementById("btn-auto-fix-toggle");
  if (btn) {
    btn.addEventListener("click", () => {
      state.autoFixEnabled = !state.autoFixEnabled;
      localStorage.setItem("autoFixEnabled", state.autoFixEnabled);
      updateAutoFixButton();
    });
  }
}

async function triggerAutoFix(jobId, transcript) {
  if (!state.autoFixEnabled || !transcript) return;
  if (state.autoFixCompleted.has(jobId)) return;
  if (state.autoFixRunning) return;

  state.autoFixRunning = true;
  state.autoFixCompleted.add(jobId);
  toast("Auto-fixing transcript…", "info");

  const tempResult = { mode: "grammar", text: "", created_at: Math.floor(Date.now() / 1000), progress: 0, _temp: true };
  renderAiResults([tempResult]);

  let bodyEl = null;
  let progFill = null;
  if (dom.aiResultsPanel) {
    const tempElem = dom.aiResultsPanel.querySelector(".ai-result-block[data-temp]");
    bodyEl = tempElem?.querySelector(".ai-result-body");
    progFill = tempElem?.querySelector(".ai-result-progress-fill");
  }
  if (!bodyEl) {
    const placeholder = appendAssistantMessage("", "Grammar");
    bodyEl = placeholder.body;
  }

  setHeaderProgress("Auto-fixing", 10, { aiMode: true });

  try {
    const response = await fetch("/api/ai/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: transcript, mode: "grammar", job_id: jobId }),
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error || errorData.detail || "Request failed");
    }

    const reader = response.body.getReader();
    state.currentAIReader = reader;
    const decoder = new TextDecoder();
    let fullText = "";
    let fakeProgress = 10;

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
            if (data.error) { clearInterval(fakeInterval); throw new Error(data.error); }
            if (data.chunk) { fullText += data.chunk; if (bodyEl) bodyEl.textContent = fullText; }
            if (data.done) {
              clearInterval(fakeInterval);
              fullText = data.text || fullText;
              if (bodyEl) bodyEl.textContent = fullText;
              if (progFill) progFill.style.width = "100%";
              setHeaderProgress("Complete", 100, { aiMode: true });
            }
          } catch (e) { continue; }
        }
      }
    }

    clearInterval(fakeInterval);
    state.currentAIReader = null;

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
    state.autoFixCompleted.delete(jobId);
    renderAiResults([]);
  } finally {
    state.autoFixRunning = false;
  }
}

// ─── Voice Edit ───────────────────────────────────────────────────────────────

async function startVoiceEdit() {
  if (!dom.editor.value.trim()) {
    toast("No text in editor to edit", "error");
    return;
  }

  try {
    state.voiceEditStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    toast(`Microphone access denied: ${e.message}`, "error");
    return;
  }

  state.voiceEditChunks = [];
  const mimeType = getSupportedMimeType();

  try {
    state.voiceEditRecorder = new MediaRecorder(state.voiceEditStream, mimeType ? { mimeType } : {});
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
  if (state.voiceEditRecorder?.state !== "inactive") state.voiceEditRecorder.stop();
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

function initAI() {
  dom.btnAi.addEventListener("click", aiAction);

  dom.btnEditVoice.addEventListener("click", () => {
    state.voiceEditMode ? stopVoiceEdit() : startVoiceEdit();
  });

  dom.aiModeSelector.addEventListener("change", (e) => {
    state.currentAIMode = e.target.value;
    updateAiButtonLabel();
  });
}
