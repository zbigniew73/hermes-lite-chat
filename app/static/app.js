/* =============================================================================
 * hermes-lite-chat
 * =============================================================================
 * Strona   : automation-ai.eu - Automatyzacja AI
 * Autor    : Zbigniew Czechowski
 * WWW      : https://automation-ai.eu/
 * E-mail   : kontakt@automation-ai.eu
 * Licencja : MIT
 * ========================================================================== */

// --- theme ------------------------------------------------------------------
// The stored preference is already applied by the inline <head> script (to
// avoid a flash of the wrong theme); everything here only keeps the toggle
// button in sync and writes new choices.

const THEME_STORAGE_KEY = "hermes-lite-chat-theme";
const themeToggleEl = document.getElementById("theme-toggle");
const darkMediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    // Private mode / blocked storage: the toggle still works for this page,
    // it just won't be remembered across reloads.
    return null;
  }
}

function effectiveTheme() {
  return storedTheme() || (darkMediaQuery.matches ? "dark" : "light");
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* nothing to do — the in-page switch already happened */
  }
  syncThemeToggle();
}

function syncThemeToggle() {
  // Convention: the icon shows the theme you'd switch TO, not the current one.
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  themeToggleEl.textContent = next === "dark" ? "🌙" : "☀️";
  const label = next === "dark" ? "Switch to dark theme" : "Switch to light theme";
  themeToggleEl.title = label;
  themeToggleEl.setAttribute("aria-label", label);
}

themeToggleEl.addEventListener("click", () => {
  applyTheme(effectiveTheme() === "dark" ? "light" : "dark");
});

// Only matters while no explicit choice is stored: then the page follows the
// OS and the icon has to follow it too.
darkMediaQuery.addEventListener("change", () => {
  if (!storedTheme()) syncThemeToggle();
});

syncThemeToggle();

// --- app --------------------------------------------------------------------

const sessionListEl = document.getElementById("session-list");
const modelInfoEl = document.getElementById("model-info");
const newChatBtn = document.getElementById("new-chat");
const placeholderEl = document.getElementById("placeholder");
const terminalEl = document.getElementById("terminal");

let term = null;
let fitAddon = null;
let socket = null;
let activeSessionId = null;
// Once the user logs out this page must stop talking to the server: tearing
// the terminal down fires the socket's close handler, which would otherwise
// refill the sidebar — issuing a credentialed request right after we asked
// the browser to forget those credentials (and possibly popping its native
// login prompt).
let loggedOut = false;

async function loadModelInfo() {
  if (loggedOut) return;
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
  if (loggedOut) return;
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

    const textEl = document.createElement("div");
    textEl.className = "session-text";

    // textContent, never innerHTML: these strings come straight out of the
    // agent's DB and may contain markup from anything the agent ingested.
    const label = s.title || s.display_name || s.id;
    const titleEl = document.createElement("div");
    titleEl.className = "title";
    titleEl.textContent = label;

    const metaEl = document.createElement("div");
    metaEl.className = "meta";
    metaEl.textContent = `${s.source ?? ""} · ${s.message_count ?? 0} msgs · ${formatTimestamp(s.started_at)}`;

    textEl.append(titleEl, metaEl);

    const deleteEl = document.createElement("button");
    deleteEl.className = "session-delete";
    deleteEl.type = "button";
    deleteEl.textContent = "×";
    deleteEl.title = "Delete session";
    deleteEl.setAttribute("aria-label", `Delete session ${label}`);
    deleteEl.addEventListener("click", (event) => {
      // Without this the click bubbles to .session-item and opens the very
      // session we're about to delete.
      event.stopPropagation();
      deleteSession(s.id, label, deleteEl);
    });

    item.append(textEl, deleteEl);
    item.addEventListener("click", () => openSession(s.id));
    sessionListEl.appendChild(item);
  }
}

async function deleteSession(sessionId, label, buttonEl) {
  if (
    !confirm(
      `Delete session "${label}"?\n\n` +
        "This runs `hermes sessions delete` and cannot be undone."
    )
  ) {
    return;
  }

  buttonEl.disabled = true;
  let res;
  let body = {};
  try {
    res = await fetch(`/api/hermes/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    body = await res.json().catch(() => ({}));
  } catch {
    buttonEl.disabled = false;
    alert("Network error while deleting the session.");
    return;
  }

  if (!res.ok) {
    buttonEl.disabled = false;
    alert(body.detail || `Could not delete the session (HTTP ${res.status}).`);
    return;
  }

  // The open terminal is now attached to a session that no longer exists;
  // drop back to the same state as a fresh page with nothing selected.
  if (activeSessionId === sessionId) {
    closeCurrentSession();
    showPlaceholder();
  }
  loadSessions();
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

/** Back to the "nothing selected" state the page starts in. */
function showPlaceholder() {
  activeSessionId = null;
  highlightActiveSession(null);
  terminalEl.style.display = "none";
  terminalEl.replaceChildren();
  placeholderEl.style.display = "flex";
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
    fontSize: 22,
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

// --- settings dropdown ------------------------------------------------------

const settingsEl = document.getElementById("settings");
const settingsBtn = document.getElementById("settings-btn");
const settingsPanel = document.getElementById("settings-panel");

function setSettingsOpen(open) {
  settingsPanel.hidden = !open;
  settingsBtn.setAttribute("aria-expanded", String(open));
}

settingsBtn.addEventListener("click", () => {
  setSettingsOpen(settingsPanel.hidden);
});

// The wrapper contains the button too, so the click that just opened the panel
// is ignored here instead of closing it again on its way up to the document.
document.addEventListener("click", (event) => {
  if (settingsPanel.hidden || settingsEl.contains(event.target)) return;
  setSettingsOpen(false);
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

// --- logout -----------------------------------------------------------------

const logoutBtn = document.getElementById("logout-btn");
const loggedOutEl = document.getElementById("logged-out");
const loggedOutReloadBtn = document.getElementById("logged-out-reload");

/**
 * Best-effort logout for HTTP Basic Auth.
 *
 * There is no server-side session here and no browser API for "forget these
 * credentials" — the browser holds them in its own HTTP auth cache, keyed by
 * (origin, realm), and replays them on every request by itself. The only
 * lever we have is the long-standing trick of sending a request with
 * *deliberately wrong* credentials: fetch() lets us set the Authorization
 * header explicitly, and per the Fetch spec an explicit Authorization header
 * wins over the cached entry, so the server answers 401 and most browsers
 * treat that 401 as "the credentials I had for this realm are no good" and
 * evict them. The next navigation then re-prompts.
 *
 * Known limits, none of which we can fix from here:
 *   - It is a heuristic, not a standard. Chrome and Firefox evict on the 401;
 *     Safari has historically ignored Authorization headers set from JS, so
 *     the eviction may simply not happen there.
 *   - Even where it works, some browsers keep the credentials alive for tabs
 *     already open, or until every window for the site is closed. The overlay
 *     tells the user to close the browser if reloading logs them back in.
 *   - The 401 may make the browser show its native login prompt right away
 *     instead of on the next reload. That is harmless (cancelling it leaves
 *     the overlay in place), and it is the same prompt they would get anyway.
 *
 * So the visible half matters as much as the header trick: tear down the
 * terminal and the WebSocket, and cover the page. This page was loaded while
 * authenticated and its JS keeps running regardless of what the credential
 * cache does, so without the overlay there would be nothing to see.
 */
async function logout() {
  // Set before tearing anything down: closing the socket fires its close
  // handler, and this is what stops that handler re-fetching the session list.
  loggedOut = true;
  logoutBtn.disabled = true;
  closeCurrentSession();
  showPlaceholder();
  sessionListEl.replaceChildren();

  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      cache: "no-store",
      headers: {
        // Any value the server cannot verify does the job; the random suffix
        // just keeps it from ever matching a real password by accident.
        Authorization: "Basic " + btoa(`logout:${Date.now()}-${Math.random()}`),
      },
    });
  } catch {
    /* The request failing is fine: the 401 is the point, not the response. */
  }

  loggedOutEl.hidden = false;
  loggedOutReloadBtn.focus();
}

logoutBtn.addEventListener("click", logout);
loggedOutReloadBtn.addEventListener("click", () => location.reload());

document.getElementById("current-date").textContent = new Date().toLocaleDateString(
  undefined,
  { weekday: "long", day: "numeric", month: "numeric", year: "numeric" }
);

loadModelInfo();
loadSessions();
