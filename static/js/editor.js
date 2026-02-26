// ─── Chat helpers ─────────────────────────────────────────────────────────────

function escHtml(str) {
  return str.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
}

function appendUserMessage(text) {
  if (!dom.chatMessages) return;
  dom.chatMessages.querySelectorAll(".empty-state")?.forEach((e) => e.remove());
  const el = document.createElement("div");
  el.className = "message user";
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  el.appendChild(body);
  dom.chatMessages.appendChild(el);
  dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
}

/**
 * Append an assistant message bubble.
 * @param {string} text initial content
 * @param {string} [label] optional label
 * @returns {{el:HTMLElement, body:HTMLElement}}
 */
function appendAssistantMessage(text, label) {
  if (!dom.chatMessages) return {};
  dom.chatMessages.querySelectorAll(".empty-state")?.forEach((e) => e.remove());
  const el = document.createElement("div");
  el.className = "message assistant";

  if (label) {
    const lbl = document.createElement("div");
    lbl.className = "message-label";
    lbl.textContent = label;
    el.appendChild(lbl);
  }

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  el.appendChild(body);

  const actions = document.createElement("div");
  actions.className = "message-actions";
  const copyBtn = document.createElement("button");
  copyBtn.className = "btn btn-ghost msg-copy-btn";
  copyBtn.title = "Copy message";
  copyBtn.innerText = "⎘";
  copyBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const txt = body.textContent || "";
    if (!txt.trim()) { toast("Nothing to copy", "error"); return; }
    navigator.clipboard.writeText(txt)
      .then(() => toast("Copied message", "success"))
      .catch(() => toast("Copy failed", "error"));
  });
  actions.appendChild(copyBtn);
  el.appendChild(actions);

  dom.chatMessages.appendChild(el);
  dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
  return { el, body };
}

function createAssistantPlaceholder(label) {
  return appendAssistantMessage("", label);
}

// ─── AI Results ───────────────────────────────────────────────────────────────

function renderAiResults(aiResults) {
  if (dom.aiResultsPanel) dom.aiResultsPanel.innerHTML = "";
  if (!aiResults || !aiResults.length) return;

  aiResults.forEach((r) => {
    if (!r.text) return;
    const label = (state.aiModes[r.mode] || {}).display_name || r.mode;
    const existing = Array.from((dom.chatMessages || document.createElement("div")).querySelectorAll(".message.assistant .message-body"))
      .some((m) => m.textContent && m.textContent.trim() === r.text.trim() && m.previousSibling?.textContent === label);
    if (!existing) appendAssistantMessage(r.text, label);
  });
}

// ─── Transcript Display ───────────────────────────────────────────────────────

function showTranscript(text, aiResults) {
  dom.emptyState.style.display = "none";
  dom.processingOverlay.classList.remove("active");

  try {
    if (dom.chatMessages) {
      const existing = Array.from(dom.chatMessages.querySelectorAll(".message.assistant"))
        .some((m) => {
          const lbl = m.querySelector(".message-label")?.textContent?.trim();
          const body = m.querySelector(".message-body")?.textContent?.trim();
          return lbl === "Transcript" && body === (text || "").trim();
        });
      if (!existing) appendAssistantMessage(text, "Transcript");
    } else {
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

  renderAiResults(aiResults || []);
}

function startNewConversation(preserve = false) {
  if (preserve) return;
  if (!dom.chatMessages) return;
  dom.chatMessages.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.innerHTML = '<div class="empty-glyph">◌</div><div class="empty-text">Start a conversation — type below or upload audio to begin</div>';
  dom.chatMessages.appendChild(empty);
}

// ─── Chat input wiring ────────────────────────────────────────────────────────

function initChatInput() {
  if (dom.chatSend) {
    dom.chatSend.addEventListener("click", () => {
      const t = dom.chatInput?.value?.trim();
      if (t) {
        dom.editor.value = t;
        aiAction();
      }
    });
  }

  if (dom.chatInput) {
    dom.chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const t = dom.chatInput.value.trim();
        if (!t) return;
        dom.editor.value = t;
        aiAction();
      }
    });
  }
}
