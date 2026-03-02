// ─── Personal Names Modal ─────────────────────────────────────────────────────

function openPersonalNamesModal() {
  dom.personalNamesModal.classList.add("active");
  fetch("/api/personal-names")
    .then((r) => r.json())
    .then((data) => { dom.personalNamesInput.value = (data.names || []).join("\n"); })
    .catch(() => { dom.personalNamesInput.value = ""; });
}

function closePersonalNamesModal() {
  dom.personalNamesModal.classList.remove("active");
}

async function savePersonalNames() {
  const names = dom.personalNamesInput.value.split("\n").map((n) => n.trim()).filter(Boolean);
  try {
    const res = await fetch("/api/personal-names", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names }),
    });
    const data = await res.json();
    if (data.error) { toast(data.error, "error"); }
    else { toast("Personal names saved", "success"); closePersonalNamesModal(); }
  } catch (e) {
    toast(`Failed to save: ${e.message}`, "error");
  }
}

// ─── Mode Creation Modal ──────────────────────────────────────────────────────

function openModeModal() {
  dom.modeModal.classList.add("active");
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
  if (!mode) { toast("Mode name required", "error"); return; }
  const instruction = dom.modeInstructionInput.value.trim();
  if (!instruction) { toast("Instruction required", "error"); return; }

  const display = dom.modeDisplayInput.value.trim();
  const placeholder = dom.modePlaceholderInput.value.trim();
  const rawRules = dom.modeRulesInput.value.split("\n").map((l) => l.trim()).filter(Boolean);

  const config = { instruction };
  if (display) config.display_name = display;
  if (placeholder) config.input_placeholder = placeholder;
  if (rawRules.length) {
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
    if (data.error) { toast(data.error, "error"); }
    else {
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

// ─── Modal init ───────────────────────────────────────────────────────────────

function initModals() {
  document.getElementById("btn-personal-names").addEventListener("click", openPersonalNamesModal);
  document.getElementById("close-names-modal").addEventListener("click", closePersonalNamesModal);
  document.getElementById("cancel-names-modal").addEventListener("click", closePersonalNamesModal);
  document.getElementById("save-names-modal").addEventListener("click", savePersonalNames);
  dom.personalNamesModal.querySelector(".modal-backdrop").addEventListener("click", closePersonalNamesModal);

  document.getElementById("btn-add-mode").addEventListener("click", openModeModal);
  document.getElementById("close-mode-modal").addEventListener("click", closeModeModal);
  document.getElementById("cancel-mode-modal").addEventListener("click", closeModeModal);
  document.getElementById("save-mode-modal").addEventListener("click", saveModeModal);
  dom.modeModal.querySelector(".modal-backdrop").addEventListener("click", closeModeModal);
}
