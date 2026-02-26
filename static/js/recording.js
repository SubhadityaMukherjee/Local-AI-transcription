// ─── Recording ────────────────────────────────────────────────────────────────

function getSupportedMimeType() {
  return MIME_CANDIDATES.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
}

function mimeToExt(mimeType) {
  if (mimeType.includes("mp4")) return "mp4";
  if (mimeType.includes("ogg")) return "ogg";
  return "webm";
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

function updateTimer() {
  const s = Math.floor((Date.now() - state.recordStart) / 1000);
  if (dom.timerEl)
    dom.timerEl.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

async function startRecording(isAppend = false) {
  showRecordOverlay();
  state.appendMode = isAppend;
  state.appendJobId = isAppend ? state.activeJobId : null;

  try {
    state.recordStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    hideRecordOverlay();
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
    state.mediaRecorder = new MediaRecorder(state.recordStream, mimeType ? { mimeType } : {});
  } catch (e) {
    toast(`Recording not supported: ${e.message}`, "error");
    return;
  }

  state.mediaRecorder.ondataavailable = (e) => {
    if (e.data?.size > 0) state.recordChunks.push(e.data);
  };
  state.mediaRecorder.onstop = submitRecording;
  state.mediaRecorder.onerror = (e) => toast(`Recording error: ${e.error?.message || e}`, "error");
  state.mediaRecorder.start(250);

  state.recordStart = Date.now();
  state.timerInterval = setInterval(updateTimer, 500);

  if (dom.recordBtn) dom.recordBtn.classList.add("recording");
  if (dom.chatRecordBtn) dom.chatRecordBtn.classList.add("recording");
  if (dom.overlayRecordBtn) dom.overlayRecordBtn.classList.add("recording");
  if (dom.appendBtn) dom.appendBtn.classList.toggle("recording", isAppend);
  if (dom.chatAppendBtn) dom.chatAppendBtn.classList.toggle("recording", isAppend);

  if (dom.chatRecordBtn) {
    dom.chatRecordBtn.style.animation = "pulse 1.2s infinite ease-in-out";
    dom.chatRecordBtn.style.boxShadow = "0 0 0 6px rgba(196, 168, 130, 0.12)";
  }
  if (dom.chatAppendBtn && isAppend) {
    dom.chatAppendBtn.style.animation = "pulse 1.2s infinite ease-in-out";
    dom.chatAppendBtn.style.boxShadow = "0 0 0 6px rgba(196, 168, 130, 0.12)";
  }
  if (dom.statusEl) {
    dom.statusEl.textContent = isAppend ? "Appending… click to stop" : "Recording… click to stop";
    dom.statusEl.classList.add("active");
  }
}

function stopRecording() {
  hideRecordOverlay();
  if (state.mediaRecorder?.state !== "inactive") state.mediaRecorder.stop();
  state.recordStream?.getTracks().forEach((t) => t.stop());
  clearInterval(state.timerInterval);
  cancelAnimationFrame(state.animFrame);

  if (dom.recordBtn) dom.recordBtn.classList.remove("recording");
  if (dom.chatRecordBtn) dom.chatRecordBtn.classList.remove("recording");
  if (dom.overlayRecordBtn) dom.overlayRecordBtn.classList.remove("recording");
  if (dom.chatRecordBtn) { dom.chatRecordBtn.style.animation = ""; dom.chatRecordBtn.style.boxShadow = ""; }
  if (dom.appendBtn) dom.appendBtn.classList.remove("recording");
  if (dom.chatAppendBtn) dom.chatAppendBtn.classList.remove("recording");
  if (dom.chatAppendBtn) { dom.chatAppendBtn.style.animation = ""; dom.chatAppendBtn.style.boxShadow = ""; }
  if (dom.statusEl) { dom.statusEl.textContent = "Uploading…"; dom.statusEl.classList.remove("active"); }
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
  if (state.appendMode && state.appendJobId) fd.append("append_to", state.appendJobId);

  try {
    const res = await fetch("/api/record", { method: "POST", body: fd });
    const data = await res.json();
    if (data.job_id) {
      if (state.appendMode && state.appendJobId) {
        toast("Appended audio, re-transcribing…", "success");
        watchJob(state.appendJobId);
        selectJob(state.appendJobId);
      } else {
        startNewConversation();
        addJob({ id: data.job_id, filename: `recording.${ext}`, status: "queued", progress: 0 });
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

async function toggleRecord() {
  state.mediaRecorder?.state === "recording" ? stopRecording() : await startRecording();
}

async function toggleAppendRecord() {
  if (!state.activeJobId) {
    toast("Select a recording first to append to", "error");
    return;
  }
  state.mediaRecorder?.state === "recording" ? stopRecording() : await startRecording(true);
}

function initRecording() {
  if (dom.recordBtn) dom.recordBtn.addEventListener("click", toggleRecord);
  if (dom.appendBtn) dom.appendBtn.addEventListener("click", toggleAppendRecord);
  if (dom.chatRecordBtn) dom.chatRecordBtn.addEventListener("click", toggleRecord);
  if (dom.chatAppendBtn) dom.chatAppendBtn.addEventListener("click", toggleAppendRecord);
  if (dom.overlayRecordBtn) dom.overlayRecordBtn.addEventListener("click", stopRecording);
}
