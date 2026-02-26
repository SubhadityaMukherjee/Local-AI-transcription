// ─── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  // Load AI modes first so button/selector are ready
  await loadAIModes();

  // Load existing jobs
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

  // Wire up all subsystems
  initRecording();
  initUpload();
  initChatInput();
  initToolbar();
  initJobsDrawer();
  initAutoFix();
  initAI();
  initModals();
  initProcessingCancel();
}

init();
