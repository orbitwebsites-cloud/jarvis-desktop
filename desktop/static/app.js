const state = {
  config: null,
  pendingToken: null,
  recognition: null,
  listening: false,
  busy: false,
  connected: null,
  consecutiveFailures: 0,
  lastOnlineAt: null,
  statusRefreshInFlight: false,
  commandHistory: JSON.parse(localStorage.getItem("jarvis.commandHistory") || "[]"),
  historyIndex: -1,
};

const $ = (selector) => document.querySelector(selector);
const messages = $("#messages");
const commandInput = $("#commandInput");
const commandForm = $("#commandForm");
const sendButton = $("#sendButton");
const voiceButton = $("#voiceButton");
const voiceStatus = $("#voiceStatus");
const voiceHint = $("#voiceHint");
const confirmDialog = $("#confirmDialog");
const settingsDialog = $("#settingsDialog");

function safeText(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function addMessage(role, content, isError = false) {
  const article = document.createElement("article");
  article.className = `message ${role === "user" ? "user-message" : "assistant-message"}${isError ? " error-message" : ""}`;
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = role === "user" ? "YOU" : (state.config?.assistant_name || "JARVIS");
  const body = document.createElement("p");
  body.textContent = safeText(content);
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  article.append(label, body, time);
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
}

function setBusy(busy, label = "THINKING") {
  state.busy = busy;
  sendButton.disabled = busy || state.connected === false;
  voiceButton.disabled = busy || state.connected === false;
  voiceButton.classList.toggle("thinking", busy);
  if (!state.listening) {
    voiceStatus.textContent = busy ? label : state.connected === false ? "SYSTEM OFFLINE" : "SYSTEM READY";
  }
}

async function api(path, options = {}) {
  const timeoutMs = options.timeoutMs ?? (options.method === "POST" ? 55000 : 7000);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
      signal: controller.signal,
    });
    const raw = await response.text();
    let data = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      throw new Error(`JARVIS returned an invalid response (${response.status}).`);
    }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("JARVIS took too long to respond. The request was safely stopped.");
    }
    if (error instanceof TypeError) {
      throw new Error("The JARVIS service is unreachable. Reconnecting…");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function updateConnection(connected, detail = "") {
  const previous = state.connected;
  const changed = state.connected !== connected;
  state.connected = connected;
  if (connected) {
    state.consecutiveFailures = 0;
    state.lastOnlineAt = Date.now();
  }

  const label = $("#systemConnectionLabel");
  const dot = $("#systemConnectionDot");
  if (label) label.textContent = connected ? "ONLINE" : "OFFLINE";
  if (dot) dot.classList.toggle("offline", !connected);
  document.body.classList.toggle("service-offline", !connected);
  sendButton.disabled = state.busy || !connected;
  voiceButton.disabled = state.busy || !connected;

  if (!state.busy && !state.listening) {
    voiceStatus.textContent = connected ? "SYSTEM READY" : "SYSTEM OFFLINE";
    voiceHint.textContent = connected
      ? "Tap the core or type a command. JARVIS is ready to control your computer."
      : "Connection lost. JARVIS is attempting to reconnect automatically.";
  }
  if (changed && !connected && detail) toast(detail);
  if (changed && connected && previous === false) toast("JARVIS connection restored.");
}

async function executeCommand(text, echo = true) {
  const command = normalizeCommand(text);
  if (!command || state.busy) return;
  if (state.connected === false) {
    await refreshStatus();
    if (state.connected === false) {
      toast("JARVIS is still reconnecting. Try again in a moment.");
      return;
    }
  }
  if (echo) addMessage("user", command);
  if (echo && state.commandHistory[0] !== command) {
    state.commandHistory.unshift(command);
    state.commandHistory = state.commandHistory.slice(0, 40);
    localStorage.setItem("jarvis.commandHistory", JSON.stringify(state.commandHistory));
  }
  state.historyIndex = -1;
  commandInput.value = "";
  localStorage.removeItem("jarvis.commandDraft");
  setBusy(true, "JARVIS THINKING");
  try {
    const researchCommand = /\bresearch\b|\b(?:save|put|write).*(?:doc|document)\b/i.test(command);
    const result = await api("/api/command", {
      method: "POST",
      body: JSON.stringify({ text: command }),
      timeoutMs: researchCommand ? 120000 : undefined,
    });
    handleResult(result);
  } catch (error) {
    addMessage("assistant", error.message, true);
    toast(error.message);
  } finally {
    setBusy(false);
    refreshStatus();
    refreshActivity();
  }
}

function handleResult(result) {
  addMessage("assistant", result.display, ["error", "blocked", "unknown"].includes(result.status));
  renderResultData(result.data || {});
  if (result.requires_confirmation) {
    state.pendingToken = result.confirmation_token;
    $("#confirmTitle").textContent = result.confirmation_title || "Confirm action?";
    $("#confirmDetail").textContent = result.confirmation_detail || result.display;
    confirmDialog.showModal();
  }
  if (result.data?.suggestions) renderSuggestions(result.data.suggestions);
  if (result.data?.timer_seconds) startTimer(result.data.timer_seconds, result.data.timer_label);
  if (result.data?.path) toast(`Saved: ${result.data.path}`);
  if (result.data?.reminder || result.data?.routine || result.action?.startsWith("routine")) {
    refreshPlanner();
  }
  if (state.config?.speak_responses && result.speech && result.status !== "confirmation_required") {
    speak(result.speech);
  }
}

function renderResultData(data) {
  let title = "";
  let lines = [];
  if (Array.isArray(data.files)) {
    title = "File matches";
    lines = data.files;
  } else if (Array.isArray(data.processes)) {
    title = "Processes by memory";
    lines = data.processes.map((item) => `${item.name} · PID ${item.pid} · ${item.memory_mb} MB`);
  } else if (Array.isArray(data.reminders)) {
    title = "Active reminders";
    lines = data.reminders.map((item) => `${item.text} · ${formatDateTime(item.due_at)}`);
  } else if (Array.isArray(data.routines)) {
    title = "Saved routines";
    lines = data.routines.map((item) => `${item.name} · ${item.commands.length} steps`);
  } else if (data.memory) {
    title = "Local memory";
    const memories = Object.entries(data.memory.memories || {}).map(([key, item]) => `${key}: ${item.value}`);
    const notes = (data.memory.notes || []).slice(-5).map((item) => `Note: ${item.text}`);
    lines = [...memories, ...notes];
  } else if (data.window) {
    title = "Foreground window";
    lines = [`${data.window.title} · PID ${data.window.pid}`];
  } else if (Array.isArray(data.windows)) {
    title = "Visible windows";
    lines = data.windows.map((item) => `${item.title} · PID ${item.pid}`);
  } else if (typeof data.clipboard === "string") {
    title = "Clipboard text";
    lines = [data.clipboard || "(empty)"];
  } else if (Array.isArray(data.sources)) {
    title = "Research sources";
    lines = data.sources.map((item, index) => `[${index + 1}] ${item.title} — ${item.url}`);
  }
  if (!lines.length) return;
  const card = document.createElement("div");
  card.className = "result-card";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const list = document.createElement("ul");
  lines.slice(0, 12).forEach((line) => {
    const item = document.createElement("li");
    item.textContent = safeText(line);
    item.title = safeText(line);
    list.append(item);
  });
  card.append(heading, list);
  messages.append(card);
  messages.scrollTop = messages.scrollHeight;
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function formatRelativeTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const absolute = Math.abs(seconds);
  if (absolute < 45) return seconds >= 0 ? "in under a minute" : "due now";
  const units = absolute < 3600
    ? [Math.round(absolute / 60), "min"]
    : absolute < 86400
      ? [Math.round(absolute / 3600), "hr"]
      : [Math.round(absolute / 86400), "day"];
  const suffix = units[0] === 1 ? units[1] : `${units[1]}s`;
  return seconds >= 0 ? `in ${units[0]} ${suffix}` : `${units[0]} ${suffix} overdue`;
}

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 1000));
  utterance.rate = 1.02;
  utterance.pitch = 0.9;
  speechSynthesis.speak(utterance);
}

function setupVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    voiceHint.textContent = "Voice recognition is unavailable in this browser. Text commands are fully operational.";
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.onstart = () => {
    state.listening = true;
    voiceButton.classList.add("listening");
    voiceStatus.textContent = "LISTENING";
    voiceHint.textContent = "Speak naturally. Release when the command appears.";
  };
  recognition.onresult = (event) => {
    let transcript = "";
    for (let index = event.resultIndex; index < event.results.length; index++) {
      transcript += event.results[index][0].transcript;
    }
    commandInput.value = transcript;
    if (event.results[event.results.length - 1].isFinal) executeCommand(transcript);
  };
  recognition.onerror = (event) => {
    const friendly = {
      "audio-capture": "No microphone was found.",
      "network": "Voice recognition lost its network connection.",
      "not-allowed": "Microphone access is blocked.",
      "service-not-allowed": "Voice recognition is unavailable.",
    }[event.error];
    if (event.error !== "no-speech" && event.error !== "aborted") {
      toast(friendly || `Voice input: ${event.error}`);
    }
  };
  recognition.onend = () => {
    state.listening = false;
    voiceButton.classList.remove("listening");
    voiceStatus.textContent = state.connected === false ? "SYSTEM OFFLINE" : "SYSTEM READY";
    voiceHint.textContent = state.connected === false
      ? "Connection lost. JARVIS is attempting to reconnect automatically."
      : "Tap the core or type a command. JARVIS is ready to control your computer.";
  };
  state.recognition = recognition;
  voiceButton.addEventListener("click", () => {
    if (state.listening) recognition.stop();
    else recognition.start();
  });
}

function normalizeCommand(value) {
  return String(value || "")
    .trim()
    .replace(
      /^(?:(?:hey|okay|ok)\s+)?(?:jarvis|jervis|travis|jordan|george|joyce|doris)[,:]?\s+(?=(?:can|could|will|would|please|open|launch|start|play|search|find|show|tell|what|how|set|take|run|remember|remind)\b)/i,
      "Jarvis, "
    );
}

function renderSuggestions(items = []) {
  const container = $("#quickActions");
  container.replaceChildren();
  items.slice(0, 6).forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item;
    button.addEventListener("click", () => executeCommand(item));
    container.append(button);
  });
}

function updateClock() {
  const now = new Date();
  $("#clock").textContent = now.toLocaleTimeString([], { hour12: false });
  $("#date").textContent = now.toLocaleDateString([], { weekday: "short", month: "short", day: "2-digit" }).toUpperCase();
  const hour = now.getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  $("#voiceTitle").firstChild.textContent = `${greeting}, `;
}

function formatUptime(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${days}D ${hours}H` : `${hours}H`;
}

function setMeter(name, value, detail) {
  const safe = Math.max(0, Math.min(100, Number(value) || 0));
  $(`#${name}Meter`).style.setProperty("--value", safe);
  $(`#${name}Value`).textContent = safe;
  $(`#${name}Detail`).textContent = detail;
  $(`#${name}Line`).style.width = `${safe}%`;
}

async function refreshStatus() {
  if (state.statusRefreshInFlight) return;
  state.statusRefreshInFlight = true;
  try {
    const result = await api("/api/status", { timeoutMs: 6000 });
    updateConnection(true);
    const system = result.system;
    setMeter("memory", system.memory_percent, `${system.memory_used_gb} / ${system.memory_total_gb} GB`);
    setMeter("disk", system.disk_percent, `${system.disk_used_gb} / ${system.disk_total_gb} GB`);
    $("#batteryValue").textContent = system.battery_percent == null ? "DESKTOP" : `${system.battery_percent}%`;
    $("#uptimeValue").textContent = formatUptime(system.uptime_seconds);
    $("#hostValue").textContent = system.hostname;
    const intelligence = result.intelligence;
    const chip = $("#intelligenceChip");
    const degraded = intelligence.health === "degraded";
    chip.classList.toggle("offline", !intelligence.available);
    chip.classList.toggle("degraded", degraded);
    $("#intelligenceState").textContent = !intelligence.available
      ? "CORE OFFLINE"
      : degraded
        ? "CORE DEGRADED"
        : "CORE READY";
    chip.title = intelligence.available
      ? degraded
        ? `Last provider issue: ${intelligence.last_error || "temporary provider failure"}`
        : `${intelligence.active_provider || "JARVIS"} · ${intelligence.active_model || "automatic model"} · ${intelligence.context_turns || 0} saved turns${intelligence.latency_ms ? ` · ${intelligence.latency_ms}ms` : ""}`
      : `Diagnostic: ${intelligence.diagnostic_code || "JARVIS-E204"}`;
  } catch (error) {
    state.consecutiveFailures += 1;
    if (state.consecutiveFailures >= 2 || navigator.onLine === false) {
      updateConnection(false, error.message);
    }
    $("#intelligenceState").textContent = "JARVIS OFFLINE";
    $("#intelligenceChip").classList.add("offline");
  } finally {
    state.statusRefreshInFlight = false;
  }
}

async function refreshActivity() {
  try {
    const result = await api("/api/history");
    const relevant = result.history.filter((item) => item.event === "command_result").slice(0, 6);
    const container = $("#activityList");
    container.replaceChildren();
    if (!relevant.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No actions in this session yet.";
      container.append(empty);
      return;
    }
    relevant.forEach((item) => {
      const row = document.createElement("div");
      row.className = "activity-item";
      const dot = document.createElement("i");
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = item.action || "command";
      const subtitle = document.createElement("small");
      subtitle.textContent = item.status || "complete";
      const time = document.createElement("time");
      time.textContent = new Date(item.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      copy.append(title, subtitle);
      row.append(dot, copy, time);
      container.append(row);
    });
  } catch { /* dashboard can operate without the activity feed */ }
}

async function refreshPlanner() {
  const reminderContainer = $("#reminderList");
  reminderContainer.setAttribute("aria-busy", "true");
  const [reminderResult, routineResult] = await Promise.allSettled([
    api("/api/reminders"),
    api("/api/routines"),
  ]);
  if (reminderResult.status === "fulfilled") {
    const container = reminderContainer;
    container.replaceChildren();
    const reminders = reminderResult.value.reminders.slice(0, 4);
    if (!reminders.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No active reminders.";
      container.append(empty);
    }
    reminders.forEach((item) => {
      const row = document.createElement("div");
      row.className = "planner-item";
      const title = document.createElement("strong");
      title.textContent = item.text;
      const due = document.createElement("small");
      due.textContent = `${formatRelativeTime(item.due_at)} · ${formatDateTime(item.due_at)}`;
      const actions = document.createElement("div");
      actions.className = "planner-item-actions";
      const snooze = document.createElement("button");
      snooze.textContent = "SNOOZE 10M";
      snooze.addEventListener("click", async () => {
        await api("/api/reminders/snooze", {
          method: "POST",
          body: JSON.stringify({ id: item.id, minutes: 10 }),
        });
        refreshPlanner();
      });
      const dismiss = document.createElement("button");
      dismiss.textContent = "DISMISS";
      dismiss.addEventListener("click", async () => {
        await api("/api/reminders/dismiss", {
          method: "POST",
          body: JSON.stringify({ id: item.id }),
        });
        refreshPlanner();
      });
      actions.append(snooze, dismiss);
      row.append(title, due, actions);
      container.append(row);
    });
    container.setAttribute("aria-busy", "false");
  } else {
    reminderContainer.replaceChildren();
    const error = document.createElement("p");
    error.className = "empty-state";
    error.textContent = "Reminders could not be loaded. JARVIS will retry.";
    reminderContainer.append(error);
    reminderContainer.setAttribute("aria-busy", "false");
  }
  if (routineResult.status === "fulfilled") {
    const container = $("#routineList");
    container.replaceChildren();
    const routines = routineResult.value.routines.slice(0, 4);
    if (!routines.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No saved routines.";
      container.append(empty);
    }
    routines.forEach((item) => {
      const row = document.createElement("div");
      row.className = "planner-item";
      const title = document.createElement("strong");
      title.textContent = item.name;
      const count = document.createElement("small");
      count.textContent = `${item.commands.length} STEPS`;
      const run = document.createElement("button");
      run.textContent = "RUN";
      run.addEventListener("click", () => executeCommand(`Run routine ${item.name}`));
      row.append(title, count, run);
      container.append(row);
    });
  }
}

async function loadCapabilities() {
  const result = await api("/api/capabilities");
  const available = result.capabilities.filter((item) => item.available).length;
  $("#capabilityCount").textContent = available;
}

async function loadConfig() {
  const result = await api("/api/config");
  state.config = result.config;
  $("#assistantName").textContent = result.config.assistant_name;
  $("#userName").textContent = result.config.user_name;
  $("#settingUserName").value = result.config.user_name;
  $("#settingSpeak").checked = result.config.speak_responses;
  $("#settingIntelligence").checked = result.config.intelligence_enabled;
  $("#settingPower").checked = result.config.allow_power_actions;
  $("#settingShell").checked = result.config.allow_shell;
}

async function saveSettings() {
  const changes = {
    user_name: $("#settingUserName").value.trim() || "Boss",
    speak_responses: $("#settingSpeak").checked,
    intelligence_enabled: $("#settingIntelligence").checked,
    allow_power_actions: $("#settingPower").checked,
    allow_shell: $("#settingShell").checked,
  };
  const result = await api("/api/config", {
    method: "POST",
    body: JSON.stringify({ changes }),
  });
  state.config = result.config;
  $("#userName").textContent = result.config.user_name;
  settingsDialog.close();
  toast("Settings saved. Intelligence changes apply after restart.");
}

function startTimer(seconds, label) {
  toast(`Timer active: ${label}`);
  setTimeout(() => {
    addMessage("assistant", `Timer complete: ${label}.`);
    speak(`Your ${label} timer is complete.`);
    toast(`Timer complete: ${label}`);
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("JARVIS timer", { body: `${label} is complete.` });
    }
  }, seconds * 1000);
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
}

function toast(message) {
  const item = document.createElement("div");
  item.className = "toast";
  item.textContent = message;
  $("#toastRegion").append(item);
  setTimeout(() => item.remove(), 4500);
}

commandForm.addEventListener("submit", (event) => {
  event.preventDefault();
  executeCommand(commandInput.value);
});
$("#clearChat").addEventListener("click", async () => {
  try {
    await api("/api/context/clear", { method: "POST", body: "{}" });
    messages.replaceChildren();
    addMessage("assistant", "Fresh conversation started. Local memories and settings were kept.");
    toast("Conversation context cleared.");
  } catch (error) {
    toast(error.message);
  }
});
commandInput.value = localStorage.getItem("jarvis.commandDraft") || "";
commandInput.addEventListener("input", () => {
  localStorage.setItem("jarvis.commandDraft", commandInput.value);
});
commandInput.addEventListener("keydown", (event) => {
  if (!["ArrowUp", "ArrowDown"].includes(event.key) || !state.commandHistory.length) return;
  event.preventDefault();
  if (event.key === "ArrowUp") {
    state.historyIndex = Math.min(state.commandHistory.length - 1, state.historyIndex + 1);
  } else {
    state.historyIndex = Math.max(-1, state.historyIndex - 1);
  }
  commandInput.value = state.historyIndex < 0 ? "" : state.commandHistory[state.historyIndex];
  commandInput.setSelectionRange(commandInput.value.length, commandInput.value.length);
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    commandInput.focus();
  }
});
$("#refreshActivity").addEventListener("click", refreshActivity);
$("#refreshPlanner").addEventListener("click", refreshPlanner);
$("#activityButton").addEventListener("click", () => $(".activity-panel").scrollIntoView({ behavior: "smooth" }));
document.querySelector('[data-panel="memory"]').addEventListener("click", () => executeCommand("What do you remember"));
$("#settingsButton").addEventListener("click", () => settingsDialog.showModal());
$("#saveSettings").addEventListener("click", (event) => {
  event.preventDefault();
  saveSettings().catch((error) => toast(error.message));
});

document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", () => executeCommand(button.dataset.command));
});

$("#confirmAction").addEventListener("click", async (event) => {
  event.preventDefault();
  const token = state.pendingToken;
  confirmDialog.close();
  if (!token) return;
  setBusy(true, "EXECUTING");
  try {
    state.pendingToken = null;
    const result = await api("/api/confirm", { method: "POST", body: JSON.stringify({ token }) });
    handleResult(result);
  } catch (error) {
    addMessage("assistant", error.message, true);
  } finally {
    setBusy(false);
    refreshActivity();
  }
});

$("#cancelAction").addEventListener("click", async (event) => {
  event.preventDefault();
  const token = state.pendingToken;
  confirmDialog.close();
  state.pendingToken = null;
  if (!token) return;
  const result = await api("/api/cancel", { method: "POST", body: JSON.stringify({ token }) });
  addMessage("assistant", result.display);
});

async function boot() {
  updateClock();
  setInterval(updateClock, 1000);
  setupVoice();
  renderSuggestions([
    "Open calculator",
    "System status",
    "Take a screenshot",
    "Volume down",
    "Set a timer for 5 minutes",
  ]);
  await Promise.allSettled([
    loadConfig(),
    refreshStatus(),
    refreshActivity(),
    refreshPlanner(),
    loadCapabilities(),
  ]);
  setInterval(refreshStatus, 8000);
  setInterval(refreshPlanner, 10000);
  window.addEventListener("online", refreshStatus);
  window.addEventListener("offline", () => updateConnection(false, "This computer is offline."));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshStatus();
  });
  window.addEventListener("unhandledrejection", (event) => {
    if (event.reason?.message) toast(event.reason.message);
  });
  commandInput.focus();
}

boot();
