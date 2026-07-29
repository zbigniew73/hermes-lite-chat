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
  const res = await fetch("/api/hermes/sessions");
  const sessions = await res.json();
  sessionListEl.innerHTML = "";
  for (const s of sessions) {
    const item = document.createElement("div");
    item.className = "session-item";
    item.dataset.sessionId = s.id;
    const title = s.title || s.display_name || s.id;
    item.innerHTML = `
      <div class="title">${title}</div>
      <div class="meta">${s.source} · ${s.message_count ?? 0} msgs · ${formatTimestamp(s.started_at)}</div>
    `;
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

  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: "Menlo, Consolas, 'Courier New', monospace",
  });
  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(terminalEl);
  fitAddon.fit();

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  let wsUrl = `${proto}//${location.host}/ws/pty`;
  if (sessionId) {
    wsUrl += `?session_id=${encodeURIComponent(sessionId)}`;
  }
  socket = new WebSocket(wsUrl);
  socket.binaryType = "arraybuffer";

  socket.addEventListener("open", () => {
    sendResize();
  });

  socket.addEventListener("message", (event) => {
    term.write(new Uint8Array(event.data));
  });

  socket.addEventListener("close", () => {
    term.write("\r\n\x1b[2m[session ended]\x1b[0m\r\n");
  });

  term.onData((data) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(new TextEncoder().encode(data));
    }
  });

  term.onResize(() => sendResize());

  new ResizeObserver(() => {
    if (fitAddon) fitAddon.fit();
  }).observe(terminalEl);
}

function sendResize() {
  if (!term || !socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
}

newChatBtn.addEventListener("click", () => openTerminal(null));

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
