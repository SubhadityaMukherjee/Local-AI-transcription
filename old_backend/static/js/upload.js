// ─── File Upload ──────────────────────────────────────────────────────────────

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  toast(`Uploading ${file.name}…`);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (data.error) { toast(data.error, "error"); return; }
    startNewConversation();
    addJob({ id: data.job_id, filename: file.name, status: "queued", progress: 0 });
    watchJob(data.job_id);
    selectJob(data.job_id);
    toast("File uploaded — transcribing…", "success");
  } catch (e) {
    toast("Upload failed", "error");
  }
}

function initUpload() {
  if (dom.fileInput) {
    dom.fileInput.addEventListener("change", (e) => {
      if (e.target.files[0]) uploadFile(e.target.files[0]);
    });
  }
  if (dom.chatUploadBtn && dom.fileInput) {
    dom.chatUploadBtn.addEventListener("click", () => dom.fileInput.click());
  }

  // Drag-and-drop anywhere on the page
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = e.dataTransfer?.files?.[0];
    if (f) uploadFile(f);
  });

  if (dom.uploadZone) {
    dom.uploadZone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dom.uploadZone.classList.add("drag-over");
    });
    dom.uploadZone.addEventListener("dragleave", () => dom.uploadZone.classList.remove("drag-over"));
    dom.uploadZone.addEventListener("drop", (e) => {
      e.preventDefault();
      dom.uploadZone.classList.remove("drag-over");
      if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
    });
  }
}
