const sessionListEl = document.getElementById("session-list");
const modelInfoEl = document.getElementById("model-info");
const newChatBtn = document.getElementById("new-chat");
const placeholderEl = document.getElementById("placeholder");
const terminalEl = document.getElementById("terminal");

let term = null;
let fitAddon = null;
let socket = null;
let activeSessionId = null;

async function loadModelInfo() {
  try {
    const res = await fetch("/api/hermes/model");
    const model = await res.json();
    modelInfoEl.textContent = `Model: ${model.default || "unknown"}`;
  } catch {
    modelInfoEl.textContent = "Model: unavailable";
  }
}

function formatTimestamp(epochSeconds) {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString();
}

async function loadSessions() {
  let sessions;
  try {
    const res = await fetch("/api/hermes/sessions");
    if (!res.ok) return;
    sessions = await res.json();
  } catch {
    return;
  }
  sessionListEl.replaceChildren();
  for (const s of sessions) {
    const item = document.createElement("div");
    item.className = "session-item";
    item.dataset.sessionId = s.id;

    // textContent, never innerHTML: these strings come straight out of the
    // agent's DB and may contain markup from anything the agent ingested.
    const titleEl = document.createElement("div");
    titleEl.className = "title";
    titleEl.textContent = s.title || s.display_name || s.id;

    const metaEl = document.createElement("div");
    metaEl.className = "meta";
    metaEl.textContent = `${s.source ?? ""} · ${s.message_count ?? 0} msgs · ${formatTimestamp(s.started_at)}`;

    item.append(titleEl, metaEl);
    item.addEventListener("click", () => openSession(s.id));
    sessionListEl.appendChild(item);
  }
}

function highlightActiveSession(sessionId) {
  for (const el of sessionListEl.querySelectorAll(".session-item")) {
    el.classList.toggle("active", el.dataset.sessionId === sessionId);
  }
}

function closeCurrentSession() {
  if (socket) {
    socket.close();
    socket = null;
  }
  if (term) {
    term.dispose();
    term = null;
  }
  fitAddon = null;
}

function openSession(sessionId) {
  openTerminal(sessionId);
}

function openTerminal(sessionId) {
  closeCurrentSession();
  activeSessionId = sessionId || null;
  highlightActiveSession(activeSessionId);

  placeholderEl.style.display = "none";
  terminalEl.style.display = "block";

  // Captured per session: a late event from a socket/terminal pair that has
  // already been replaced must never write into the *current* terminal.
  const thisTerm = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: "Menlo, Consolas, 'Courier New', monospace",
  });
  const thisFitAddon = new FitAddon.FitAddon();
  term = thisTerm;
  fitAddon = thisFitAddon;
  thisTerm.loadAddon(thisFitAddon);
  thisTerm.open(terminalEl);
  thisFitAddon.fit();

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  let wsUrl = `${proto}//${location.host}/ws/pty`;
  if (sessionId) {
    wsUrl += `?session_id=${encodeURIComponent(sessionId)}`;
  }
  const thisSocket = new WebSocket(wsUrl);
  socket = thisSocket;
  thisSocket.binaryType = "arraybuffer";

  thisSocket.addEventListener("open", () => {
    sendResize(thisTerm, thisSocket);
  });

  thisSocket.addEventListener("message", (event) => {
    if (thisTerm !== term) return;
    thisTerm.write(new Uint8Array(event.data));
  });

  thisSocket.addEventListener("close", () => {
    if (thisTerm === term) {
      thisTerm.write("\r\n\x1b[2m[session ended]\x1b[0m\r\n");
    }
    // The session the child just finished is now persisted; refresh the list.
    loadSessions();
  });

  thisTerm.onData((data) => {
    if (thisSocket.readyState === WebSocket.OPEN) {
      thisSocket.send(new TextEncoder().encode(data));
    }
  });

  thisTerm.onResize(() => sendResize(thisTerm, thisSocket));
}

function sendResize(targetTerm = term, targetSocket = socket) {
  if (!targetTerm || !targetSocket || targetSocket.readyState !== WebSocket.OPEN) return;
  targetSocket.send(
    JSON.stringify({ type: "resize", rows: targetTerm.rows, cols: targetTerm.cols })
  );
}

// One observer for the life of the tab, always fitting whatever addon is
// current — creating one per session leaked an observer per session switch.
new ResizeObserver(() => {
  if (!term || !fitAddon) return;
  try {
    fitAddon.fit();
  } catch {
    /* terminal disposed mid-resize */
  }
}).observe(terminalEl);

newChatBtn.addEventListener("click", () => {
  openTerminal(null);
  // Sidebar would otherwise stay stale until a full reload. (The brand-new
  // session isn't persisted yet, so it won't show up until it is.)
  loadSessions();
});

const changePasswordBtn = document.getElementById("change-password-btn");
const changePasswordForm = document.getElementById("change-password-form");
const cancelPasswordBtn = document.getElementById("cancel-password-btn");
const currentPasswordInput = document.getElementById("current-password");
const newPasswordInput = document.getElementById("new-password");
const changePasswordMsg = document.getElementById("change-password-msg");

changePasswordBtn.addEventListener("click", () => {
  changePasswordForm.hidden = !changePasswordForm.hidden;
  changePasswordMsg.textContent = "";
  changePasswordMsg.className = "";
});

cancelPasswordBtn.addEventListener("click", () => {
  changePasswordForm.hidden = true;
  changePasswordForm.reset();
  changePasswordMsg.textContent = "";
  changePasswordMsg.className = "";
});

changePasswordForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  changePasswordMsg.textContent = "";
  changePasswordMsg.className = "";
  try {
    const res = await fetch("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: currentPasswordInput.value,
        new_password: newPasswordInput.value,
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      changePasswordMsg.textContent = body.detail || "Failed to change password.";
      changePasswordMsg.className = "error";
      return;
    }
    changePasswordMsg.textContent = "Password changed. Reload and log in with the new one.";
    changePasswordMsg.className = "success";
    changePasswordForm.reset();
  } catch {
    changePasswordMsg.textContent = "Network error.";
    changePasswordMsg.className = "error";
  }
});

loadModelInfo();
loadSessions();
