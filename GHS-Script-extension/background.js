// SPDX-License-Identifier: GPL-3.0-or-later
// background.js - service worker.
// Owns ONE resilient WebSocket to the local bridge (ws://127.0.0.1:PORT).
// Keeping the socket here (not in the content script) avoids https→ws mixed
// content issues and centralises reconnect / timeout logic.
//
// Contract with content.js: every sendMessage ALWAYS gets a response object,
// even when the bridge is offline. The agentic loop must never hang waiting.

const PORT = 17613;
let bridgeHost = "127.0.0.1";
try {
  chrome.storage.local.get("zsBridgeHost", (r) => {
    if (r && r.zsBridgeHost) bridgeHost = r.zsBridgeHost.trim();
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.zsBridgeHost) {
      bridgeHost = (changes.zsBridgeHost.newValue || "127.0.0.1").trim();
      if (ws) { try { ws.close(); } catch { } }
    }
  });
} catch { }

const DEFAULT_CLOUD_WS = "wss://ghs-cloud-backend.onrender.com";
const DEFAULT_LOCAL_WS = "ws://127.0.0.1:17613";
let bridgeWsCustom = DEFAULT_LOCAL_WS;

try {
  chrome.storage.local.get(["zsWsUrl", "zsMode"], (r) => {
    if (r && r.zsMode === "cloud") {
      bridgeWsCustom = DEFAULT_CLOUD_WS;
    } else {
      bridgeWsCustom = DEFAULT_LOCAL_WS;
      if (!r || r.zsMode !== "local") {
        chrome.storage.local.set({ zsMode: "local" });
      }
    }
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local") {
      if (changes.zsMode) {
        bridgeWsCustom = changes.zsMode.newValue === "cloud" ? DEFAULT_CLOUD_WS : DEFAULT_LOCAL_WS;
        if (ws) { try { ws.close(); } catch { } }
      } else if (changes.zsWsUrl && bridgeWsCustom !== DEFAULT_LOCAL_WS) {
        bridgeWsCustom = (changes.zsWsUrl.newValue || "").trim() || DEFAULT_CLOUD_WS;
        if (ws) { try { ws.close(); } catch { } }
      }
    }
  });
} catch { }

function getWsUrl() {
  if (bridgeWsCustom && bridgeWsCustom.length > 5) {
    return bridgeWsCustom;
  }
  return DEFAULT_LOCAL_WS;
}

// Chat sites where a GHS provider content script runs. Status pushes go
// to every tab matching these. Add the new provider's URL pattern here (and in
// manifest.json content_scripts + host_permissions) when integrating another AI.
const PROVIDER_URLS = ["https://chat.deepseek.com/*", "https://chatgpt.com/*", "https://chat.openai.com/*", "https://gemini.google.com/*", "https://aistudio.google.com/*", "https://claude.ai/*", "https://www.kimi.ai/*", "https://kimi.ai/*", "https://chat.z.ai/*", "https://chat.qwen.ai/*", "https://arena.ai/*", "https://www.meta.ai/*", "https://meta.ai/*"];

const RECONNECT_MIN = 1000;
const RECONNECT_MAX = 5000;
const HEARTBEAT_MS = 10000;
// If no message (incl. pong) arrives within this window while we believe we're
// connected and NO tools are in flight, the socket is half-open: force a reconnect.
const STALE_SOCKET_MS = 75000;
const REQUEST_TIMEOUT_DEFAULT = 130000; // a bit above the 120s tool timeout

let ws = null;
let connected = false;
let reconnectDelay = RECONNECT_MIN;
let reconnectTimer = null;
let heartbeatTimer = null;
let lastMessageAt = 0; // timestamp of the last frame received from the bridge
let nextId = 1;
const pending = new Map(); // id -> {resolve, timer}
let toolsCache = [];
let mcpAlive = false;
let serversCache = [];
// true/false = a PLACE is loaded and usable in Roblox Studio; null = unknown.
// The MCP process stays alive when Studio is closed or its MCP option is off,
// so this is probed separately (bridge "studio_status").
let studioConnected = null;
// true/false = a Roblox Studio app is connected to the MCP server at all; null =
// unknown. studioApp=true with studioConnected=false means "Studio open but no
// place"; studioApp=false means "Studio closed OR its MCP option disabled".
let studioApp = null;
// true/false = a Roblox Studio WINDOW/PROCESS exists on this machine (checked
// bridge-side via tasklist); null = unknown/old bridge. Distinguishes the two
// studioApp=false sub-cases the UI must word differently: Studio genuinely not
// launched ("open Roblox Studio") vs Studio OPEN but its MCP plugin never
// registered with the bridge - the documented fix for the latter is opening
// Assistant Settings > MCP Servers inside Studio (validated live 3x), which
// "open Roblox Studio" wording completely fails to convey.
let studioProc = null;
let securityCache = null;

const systemLogs = [];
const MAX_SYSTEM_LOGS = 500;

function addSystemLog(level, tag, text, detail = null) {
  const item = {
    id: "log_" + Date.now() + "_" + Math.random().toString(36).substring(2, 6),
    timestamp: new Date().toLocaleTimeString(),
    level: String(level || "INFO").toUpperCase(),
    tag: String(tag || "SYS").toUpperCase(),
    text: String(text || ""),
    detail: detail ? (typeof detail === "object" ? JSON.stringify(detail) : String(detail)) : null,
  };
  systemLogs.push(item);
  if (systemLogs.length > MAX_SYSTEM_LOGS) systemLogs.shift();
  chrome.runtime.sendMessage({ type: "new_system_log", log: item }).catch(() => { });
  return item;
}

function log(...a) {
  console.log("[zs-bg]", ...a);
}

// ── WebSocket lifecycle ─────────────────────────────────────────────────
function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  clearTimeout(reconnectTimer);
  try {
    ws = new WebSocket(getWsUrl());
  } catch (e) {
    log("WebSocket ctor failed", e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    connected = true;
    reconnectDelay = RECONNECT_MIN;
    lastMessageAt = Date.now();
    log("connected to bridge");
    addSystemLog("INFO", "BRIDGE", `Conectado al puente local WebSocket (${getWsUrl()})`);
    startHeartbeat();
    broadcastStatus();
  };

  ws.onmessage = (ev) => {
    lastMessageAt = Date.now();
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    handleBridgeMessage(msg);
  };

  ws.onclose = () => {
    connected = false;
    mcpAlive = false;
    studioConnected = null;
    studioApp = null;
    studioProc = null;
    serversCache = [];
    stopHeartbeat();
    failAllPending("bridge connection closed");
    addSystemLog("WARN", "BRIDGE", "Conexión con el puente local perdida. Reintentando...");
    broadcastStatus();
    scheduleReconnect();
  };

  ws.onerror = () => {
    addSystemLog("ERROR", "BRIDGE", "Error en el socket del puente");
    // onclose will follow; nothing to do here but avoid an unhandled error.
    try { ws.close(); } catch { }
  };
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 1.7, RECONNECT_MAX);
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (connected) {
      // Half-open socket: the WS still reports OPEN but nothing comes through.
      // If there are pending tool calls in flight, the bridge is actively busy executing them;
      // do NOT force-close the socket while awaiting tool execution results.
      if (pending.size === 0 && lastMessageAt && Date.now() - lastMessageAt > STALE_SOCKET_MS) {
        log("socket stale, forcing reconnect");
        try { ws.close(); } catch { }
        return;
      }
      // Keeps the MV3 service worker alive AND detects a half-open socket.
      send({ type: "ping" }).catch(() => { });
      refreshStudioStatus();
    }
  }, HEARTBEAT_MS);
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

// Resolve once the socket is OPEN, or false after `timeout` ms.
function waitForConnection(timeout = 8000) {
  return new Promise((resolve) => {
    if (connected && ws && ws.readyState === WebSocket.OPEN) return resolve(true);
    connect(); // nudge a (re)connection - important after a worker wake-up
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (connected && ws && ws.readyState === WebSocket.OPEN) {
        clearInterval(iv);
        resolve(true);
      } else if (Date.now() - t0 > timeout) {
        clearInterval(iv);
        resolve(false);
      }
    }, 100);
  });
}

// ── request/response over the socket ────────────────────────────────────
async function send(obj, timeout = REQUEST_TIMEOUT_DEFAULT) {
  // The MV3 service worker can be suspended; the first message after a wake-up
  // arrives before the socket has re-opened. Wait for it instead of failing -
  // otherwise Kimi wrongly hears "bridge offline".
  if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
    await waitForConnection(8000);
  }
  return new Promise((resolve) => {
    if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
      resolve({ ok: false, kind: "disconnected", error: "bridge not connected" });
      return;
    }
    const id = nextId++;
    const payload = { ...obj, id };
    const timer = setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        resolve({ ok: false, kind: "timeout", error: "bridge did not respond in time" });
      }
    }, timeout);
    pending.set(id, { resolve, timer });
    try {
      ws.send(JSON.stringify(payload));
    } catch (e) {
      clearTimeout(timer);
      pending.delete(id);
      resolve({ ok: false, kind: "disconnected", error: String(e) });
    }
  });
}

// Ask the bridge whether a Roblox Studio instance is actually connected to the
// MCP server. Broadcasts only on change so the UI updates promptly but quietly.
let studioProbing = false;
async function refreshStudioStatus() {
  if (studioProbing || !connected) return;
  studioProbing = true;
  try {
    const r = await send({ type: "studio_status" }, 12000);
    const v = r && r.ok && typeof r.studio === "boolean" ? r.studio : null;
    if (v !== studioConnected) {
      studioConnected = v;
      broadcastStatus();
    }
  } finally {
    studioProbing = false;
  }
}

function handleBridgeMessage(msg) {
  if ("studio" in msg && (typeof msg.studio === "boolean" || msg.studio === null)) {
    studioConnected = msg.studio;
  }
  if ("studio_app" in msg && (typeof msg.studio_app === "boolean" || msg.studio_app === null)) {
    studioApp = msg.studio_app;
  }
  if ("studio_proc" in msg && (typeof msg.studio_proc === "boolean" || msg.studio_proc === null)) {
    studioProc = msg.studio_proc;
  }
  if (msg.type === "studio_status") {
    resolvePending(msg.id, { ok: true, studio: studioConnected });
    broadcastStatus();
    return;
  }
  if (msg.type === "connected") {
    mcpAlive = !!msg.mcp_alive;
    if (Array.isArray(msg.tools)) toolsCache = msg.tools;
    if (Array.isArray(msg.servers)) serversCache = msg.servers;
    if (msg.security) securityCache = msg.security;
    broadcastStatus();
    return;
  }
  if (msg.type === "security_config") {
    if (msg.config) securityCache = msg.config;
    resolvePending(msg.id, { ok: !!msg.ok, config: msg.config });
    broadcastStatus();
    return;
  }
  if (msg.type === "pong") {
    resolvePending(msg.id, { ok: true });
    return;
  }
  if (msg.type === "tools") {
    if (Array.isArray(msg.tools)) toolsCache = msg.tools;
    if (Array.isArray(msg.servers)) serversCache = msg.servers;
    mcpAlive = !!msg.mcp_alive;
    resolvePending(msg.id, { ok: true, tools: toolsCache });
    broadcastStatus();
    return;
  }
  if (msg.type === "tool_result") {
    resolvePending(msg.id, msg.ok
      ? { ok: true, text: msg.text, images: msg.images || [] }
      : { ok: false, kind: msg.kind, error: msg.error });
    return;
  }
  if (msg.type === "mcp_status") {
    mcpAlive = !!msg.alive;
    if (Array.isArray(msg.tools)) toolsCache = msg.tools;
    if (Array.isArray(msg.servers)) serversCache = msg.servers;
    resolvePending(msg.id, { ok: !!msg.ok, alive: msg.alive, error: msg.error });
    broadcastStatus();
    return;
  }
  if (msg.type === "server_changed") {
    // The bridge acks, then restarts itself to reload config.json. The socket
    // will drop right after this - the content script shows a spinner until the
    // reconnect lands and a fresh status arrives.
    resolvePending(msg.id, { ok: !!msg.ok, error: msg.error, restarting: !!msg.restarting });
    return;
  }
  if (msg.type === "error") {
    resolvePending(msg.id, { ok: false, error: msg.error });
    return;
  }
}

function resolvePending(id, value) {
  const p = pending.get(id);
  if (!p) return;
  clearTimeout(p.timer);
  pending.delete(id);
  p.resolve(value);
}

function failAllPending(reason) {
  for (const [, p] of pending) {
    clearTimeout(p.timer);
    p.resolve({ ok: false, kind: "disconnected", error: reason });
  }
  pending.clear();
}

// ── status push to any open DeepSeek tab + popup ─────────────────────────
// Tab Roles Registry: tabId -> role ("all_in_one" | "architect" | "worker" | "ui_designer" | "standalone")
const tabRoles = new Map();
// Tab Details Registry: tabId -> { tabId, role, aiName, title, url, lastSeen }
const tabDetails = new Map();
const swarmEvents = []; // Max 50 events for real-time dashboard feed

let swarmMode = "all_in_one"; // Default: "all_in_one" (Mono-Pestaña) or "distributed" (Multi-Pestaña)
try {
  chrome.storage.local.get("zs_swarm_mode", (r) => {
    if (r && r.zs_swarm_mode) swarmMode = r.zs_swarm_mode;
  });
} catch (e) { }

// State Ledger (Persistent mission memory & Zero-Loss Session Management)
const projectState = {
  sessionId: null,
  projectName: "",
  mission: "",
  projectDir: "Downloads",
  status: "idle", // 'idle' | 'active' | 'quota_exhausted' | 'completed'
  currentTask: "",
  completedTasks: [],
  visualAudit: null,
  masterBlueprint: "",
  niche: "",
  aesthetic: "",
  pendingGemLaunch: false,
  originalIdea: "",
  devServerUrl: "",
  startedAt: null,
  concludedAt: null,
  lastUpdated: Date.now(),
};

let cachedDevServers = [];

try {
  chrome.storage.local.get("ghsProjectState", (r) => {
    if (r && r.ghsProjectState) Object.assign(projectState, r.ghsProjectState);
  });
} catch (e) { }

async function syncStateLedgerToDisk() {
  projectState.lastUpdated = Date.now();
  try {
    chrome.storage.local.set({ ghsProjectState: projectState });
  } catch (e) { }
  try {
    await send({
      type: "call_tool",
      name: "hermes_file_write",
      arguments: {
        path: "agent_workspace/ghs_project_state.json",
        content: JSON.stringify(projectState, null, 2),
      },
    }, 15000);
  } catch (e) { }

  // Sincronizar copia interna del estado (.ghs_session.json) en la carpeta del proyecto en Windows
  if (projectState.projectDir && projectState.projectDir !== "Downloads") {
    try {
      const sep = projectState.projectDir.includes("/") ? "/" : "\\";
      const sessPath = `${projectState.projectDir}${sep}.ghs_session.json`;
      await send({
        type: "call_tool",
        name: "hermes_file_write",
        arguments: {
          path: sessPath,
          content: JSON.stringify(projectState, null, 2),
        },
      }, 15000);
    } catch (e) { }
  }
}

function inferAiProvider(url = "", title = "") {
  const u = (url || "").toLowerCase();
  if (u.includes("deepseek.com")) return "DeepSeek";
  if (u.includes("chatgpt.com") || u.includes("openai.com")) return "ChatGPT";
  if (u.includes("gemini.google.com")) return "Gemini";
  if (u.includes("claude.ai")) return "Claude";
  if (u.includes("kimi.ai")) return "Kimi";
  if (u.includes("chat.z.ai")) return "Z.ai";
  if (u.includes("chat.qwen.ai")) return "Qwen";
  if (u.includes("arena.ai")) return "LMSYS Arena";
  if (u.includes("meta.ai")) return "Meta AI";
  if (title) {
    if (/deepseek/i.test(title)) return "DeepSeek";
    if (/chatgpt/i.test(title)) return "ChatGPT";
    if (/gemini/i.test(title)) return "Gemini";
    if (/claude/i.test(title)) return "Claude";
  }
  return "AI Provider";
}

function addSwarmEvent(ev) {
  const event = {
    id: "ev_" + Date.now() + "_" + Math.random().toString(36).substring(2, 6),
    timestamp: new Date().toLocaleTimeString(),
    ...ev,
  };
  swarmEvents.unshift(event);
  if (swarmEvents.length > 50) swarmEvents.pop();
  broadcastSwarmUpdate(event);
  return event;
}

function broadcastSwarmUpdate(latestEvent = null) {
  const payload = {
    type: "swarm_state_update",
    mode: swarmMode,
    state: projectState,
    tabs: Array.from(tabDetails.values()),
    events: swarmEvents,
    latestEvent,
  };
  chrome.runtime.sendMessage(payload).catch(() => { });
  chrome.tabs.query({}, (tabs) => {
    for (const t of tabs) {
      chrome.tabs.sendMessage(t.id, payload).catch(() => { });
    }
  });
}

async function triggerTabFailover(failedTabId, reason = "Límite de tokens o interrupción de sesión") {
  const failedInfo = tabDetails.get(failedTabId);
  const failedAi = failedInfo ? failedInfo.aiName : "Agente";

  let nextTab = null;
  for (const [tId, det] of tabDetails.entries()) {
    if (tId !== failedTabId) {
      nextTab = det;
      break;
    }
  }

  if (!nextTab) {
    addSwarmEvent({
      type: "failover_failed",
      message: `El agente en ${failedAi} se detuvo (${reason}), pero no hay otra pestaña disponible para el relevo. Abre otra pestaña de IA (ej. Gemini o ChatGPT) para continuar.`,
    });
    return;
  }

  addSwarmEvent({
    type: "failover",
    fromTab: failedTabId,
    fromAi: failedAi,
    toTab: nextTab.tabId,
    toAi: nextTab.aiName,
    reason,
    projectDir: projectState.projectDir,
    currentTask: projectState.currentTask,
  });

  await focusTab(nextTab.tabId);
  chrome.tabs.sendMessage(nextTab.tabId, {
    type: "swarm_handover_task",
    projectDir: projectState.projectDir,
    task: projectState.currentTask || "Continuar la construcción del proyecto y realizar la inspección visual.",
    subtaskId: "handover_" + Date.now().toString(36),
  }).catch(() => { });
}

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabRoles.has(tabId) || tabDetails.has(tabId)) {
    tabRoles.delete(tabId);
    tabDetails.delete(tabId);
    broadcastStatus();
    broadcastSwarmUpdate();
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tab && tab.url) {
    const isAiSite = PROVIDER_URLS.some((pattern) => {
      const base = pattern.replace(/\/\*.*$/, "");
      return tab.url.startsWith(base);
    });
    if (isAiSite && (changeInfo.status === "complete" || changeInfo.title)) {
      const aiName = inferAiProvider(tab.url, tab.title);
      const role = tabRoles.get(tabId) || "standalone";
      tabDetails.set(tabId, {
        tabId,
        role,
        aiName,
        title: tab.title || aiName,
        url: tab.url,
        lastSeen: Date.now(),
      });
      broadcastStatus();
      broadcastSwarmUpdate();
    }
  }
});

function statusObj(targetTabId = null) {
  const currentRole = (targetTabId && tabRoles.get(targetTabId)) || "standalone";
  return {
    type: "zs-status",
    connected,
    mcpAlive,
    studio: studioConnected,
    studioApp,
    studioProc,
    tools: toolsCache.length,
    isLocal: getWsUrl() === DEFAULT_LOCAL_WS,
    wsUrl: getWsUrl(),
    servers: serversCache,
    security: securityCache,
    role: currentRole,
    swarm: {
      mode: swarmMode,
      allInOne: Array.from(tabRoles.values()).filter((r) => r === "all_in_one").length,
      architects: Array.from(tabRoles.values()).filter((r) => r === "architect").length,
      workers: Array.from(tabRoles.values()).filter((r) => r === "worker").length,
      designers: Array.from(tabRoles.values()).filter((r) => r === "ui_designer").length,
      tabs: Array.from(tabDetails.values()),
      state: projectState,
    },
  };
}

function broadcastStatus() {
  chrome.runtime.sendMessage(statusObj()).catch(() => { });
  chrome.tabs.query({ url: PROVIDER_URLS }, (tabs) => {
    for (const t of tabs) chrome.tabs.sendMessage(t.id, statusObj(t.id)).catch(() => { });
  });
}

async function focusTab(tabId) {
  if (!tabId) return false;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab) return false;
    if (tab.windowId) {
      await chrome.windows.update(tab.windowId, { focused: true, drawAttention: true }).catch(() => { });
    }
    await chrome.tabs.update(tabId, { active: true });
    // If discarded by Chrome Memory Saver, give it time to reload
    if (tab.discarded) {
      addSystemLog("INFO", "SWARM", `Despertando pestaña en suspensión (Tab ${tabId})...`);
      await new Promise((resolve) => {
        const timeout = setTimeout(() => resolve(false), 4000);
        const lsn = (tId, chg) => {
          if (tId === tabId && chg.status === "complete") {
            clearTimeout(timeout);
            chrome.tabs.onUpdated.removeListener(lsn);
            resolve(true);
          }
        };
        chrome.tabs.onUpdated.addListener(lsn);
      });
    }
    // Small delay to ensure the browser switches visually and activates the tab's DOM loop
    await new Promise((r) => setTimeout(r, 250));
    return true;
  } catch (e) {
    return false;
  }
}

async function sendToTabSafe(tabId, message, maxRetries = 2) {
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const res = await chrome.tabs.sendMessage(tabId, message);
      return { ok: true, res };
    } catch (err) {
      const errStr = String(err);
      const isConnectionErr = /Could not establish connection|Receiving end does not exist|tab was discarded/i.test(errStr);
      if (isConnectionErr && attempt < maxRetries) {
        await focusTab(tabId);
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }
      return { ok: false, error: errStr };
    }
  }
  return { ok: false, error: "Timed out communicating with tab" };
}

async function findOrSelectWorkerTab(archTabId) {
  // 1. Search registered worker tabs (excluding the architect tab)
  for (const [tId, r] of tabRoles.entries()) {
    if (tId !== archTabId && r === "worker") {
      try {
        const tab = await chrome.tabs.get(tId);
        if (tab) {
          const aiName = inferAiProvider(tab.url, tab.title);
          return {
            tabId: tId,
            info: tabDetails.get(tId) || { tabId: tId, aiName, role: "worker", title: tab.title || aiName, url: tab.url },
          };
        }
      } catch (e) {
        tabRoles.delete(tId);
        tabDetails.delete(tId);
      }
    }
  }

  // 2. Fallback: Search among open tabs matching any AI provider
  try {
    const aiTabs = await chrome.tabs.query({ url: PROVIDER_URLS });
    const candidates = aiTabs.filter((t) => t.id !== archTabId);
    if (candidates.length > 0) {
      // Prioritize active tab, then any open AI tab
      const chosen = candidates.find((t) => t.active) || candidates[0];
      const aiName = inferAiProvider(chosen.url, chosen.title);
      tabRoles.set(chosen.id, "worker");
      const info = {
        tabId: chosen.id,
        role: "worker",
        aiName,
        title: chosen.title || aiName,
        url: chosen.url,
        lastSeen: Date.now(),
      };
      tabDetails.set(chosen.id, info);
      broadcastStatus();
      broadcastSwarmUpdate();
      addSystemLog("INFO", "SWARM", `⚡ Auto-detectada pestaña para rol [⚙️ Obrero]: ${aiName} (Tab ${chosen.id})`);
      return { tabId: chosen.id, info };
    }
  } catch (e) { }

  return null;
}

async function startSwarmMissionInternal(promptText, mode = swarmMode, preferredArchTabId = null) {
  if (!projectState.sessionId || projectState.status === "completed" || projectState.status === "idle") {
    projectState.sessionId = "sess_" + Date.now();
    projectState.status = "active";
    projectState.startedAt = Date.now();
    projectState.concludedAt = null;
  }
  projectState.mission = promptText || "";
  projectState.completedTasks = [];
  projectState.currentTask = "01_init";
  projectState.visualAudit = null;
  if (!projectState.projectName && promptText) {
    const cleanName = promptText.slice(0, 32).toLowerCase().replace(/[^a-z0-9_-]/g, "-").replace(/-+/g, "-");
    projectState.projectName = cleanName || "proyecto-ghs";
  }
  syncStateLedgerToDisk();

  if (mode === "all_in_one") {
    let targetTabId = null;
    let targetInfo = null;
    for (const [tId, r] of tabRoles.entries()) {
      if (r === "all_in_one") {
        targetTabId = tId;
        targetInfo = tabDetails.get(tId);
        break;
      }
    }
    if (!targetTabId) {
      const firstTab = Array.from(tabDetails.values())[0];
      if (firstTab) {
        targetTabId = firstTab.tabId;
        targetInfo = firstTab;
      }
    }
    if (!targetTabId) {
      return {
        ok: false,
        error: "No se encontró ninguna pestaña de IA abierta. Abre una pestaña (Gemini, DeepSeek o ChatGPT) para iniciar el Enjambre.",
      };
    }
    addSwarmEvent({
      type: "mission_start",
      mode: "all_in_one",
      toTab: targetTabId,
      aiName: targetInfo ? targetInfo.aiName : "Enjambre All-in-One",
      prompt: promptText,
    });
    await focusTab(targetTabId);
    const sendRes = await sendToTabSafe(targetTabId, {
      type: "swarm_user_prompt",
      prompt: promptText,
      mode: "all_in_one",
    }, 3);
    if (sendRes.ok) return { ok: true, targetTabId };
    return { ok: false, error: sendRes.error };
  }

  // Distributed mode: search for architect
  let archTabId = preferredArchTabId || null;
  let archInfo = archTabId ? tabDetails.get(archTabId) : null;
  if (!archTabId) {
    for (const [tId, r] of tabRoles.entries()) {
      if (r === "architect") {
        archTabId = tId;
        archInfo = tabDetails.get(tId);
        break;
      }
    }
  }
  // Fallback 1: Any tab with role "gem" or "gem_builder"
  if (!archTabId) {
    for (const [tId, r] of tabRoles.entries()) {
      if (r === "gem" || r === "gem_builder") {
        archTabId = tId;
        archInfo = tabDetails.get(tId);
        break;
      }
    }
  }
  // Fallback 2: Any tab that is not a worker
  if (!archTabId) {
    for (const [tId, r] of tabRoles.entries()) {
      if (r !== "worker") {
        archTabId = tId;
        archInfo = tabDetails.get(tId);
        break;
      }
    }
  }
  // Fallback 3: Search among open AI tabs
  if (!archTabId) {
    try {
      const aiTabs = await chrome.tabs.query({ url: PROVIDER_URLS });
      const nonWorker = aiTabs.find((t) => tabRoles.get(t.id) !== "worker");
      if (nonWorker) {
        archTabId = nonWorker.id;
        archInfo = tabDetails.get(nonWorker.id);
      } else if (aiTabs.length > 0) {
        archTabId = aiTabs[0].id;
        archInfo = tabDetails.get(aiTabs[0].id);
      }
    } catch (e) { }
  }
  if (!archTabId) {
    return {
      ok: false,
      error: "No se encontró ninguna pestaña de IA para el rol [🏛️ Arquitecto]. Abre una pestaña de IA (ej. LMSYS Arena, DeepSeek o Claude) y asigna el rol Arquitecto en la barra de GHS.",
    };
  }

  // Ensure role is recorded and propagated as architect
  tabRoles.set(archTabId, "architect");
  if (!archInfo && tabDetails.has(archTabId)) archInfo = tabDetails.get(archTabId);
  if (archInfo) archInfo.role = "architect";
  chrome.tabs.sendMessage(archTabId, { type: "zs_assign_role", role: "architect" }).catch(() => { });
  broadcastStatus();
  broadcastSwarmUpdate();

  addSwarmEvent({
    type: "mission_start",
    mode: "distributed",
    toTab: archTabId,
    aiName: archInfo ? archInfo.aiName : "Arquitecto",
    prompt: promptText,
  });
  await focusTab(archTabId);
  const sendRes = await sendToTabSafe(archTabId, {
    type: "swarm_user_prompt",
    prompt: promptText,
    mode: "distributed",
  }, 3);
  if (sendRes.ok) return { ok: true, archTabId };
  return { ok: false, error: sendRes.error };
}

async function startGemGenerationInternal(idea, customPrompt = "") {
  if (!idea || !idea.trim()) {
    return { ok: false, error: "La idea del proyecto está vacía." };
  }

  let gemTabId = null;
  let gemInfo = null;
  for (const [tId, r] of tabRoles.entries()) {
    if (r === "gem" || r === "gem_builder") {
      gemTabId = tId;
      gemInfo = tabDetails.get(tId);
      break;
    }
  }
  // If no tab explicitly set to GEM, pick any open AI tab (active tab or first available)
  if (!gemTabId) {
    const allTabs = Array.from(tabDetails.values());
    if (allTabs.length > 0) {
      const activeAiTab = allTabs.find((t) => t.active) || allTabs[0];
      gemTabId = activeAiTab.tabId;
      gemInfo = activeAiTab;
      tabRoles.set(gemTabId, "gem");
      activeAiTab.role = "gem";
      broadcastStatus();
      broadcastSwarmUpdate();
    }
  }

  if (!gemTabId) {
    return {
      ok: false,
      error: "No se encontró ninguna pestaña de IA abierta. Abre una pestaña de IA (DeepSeek, Gemini, ChatGPT, etc.) para que el Agente GEM razone y cree el Blueprint Maestro.",
    };
  }

  addSwarmEvent({
    type: "gem_start",
    toTab: gemTabId,
    aiName: gemInfo ? gemInfo.aiName : "Agente GEM Builder",
    idea: idea,
  });
  addSystemLog("INFO", "GEM", `Iniciando Agente GEM Builder en ${gemInfo ? gemInfo.aiName : "Pestaña"} para diseñar Blueprint de: "${idea.slice(0, 60)}..."`);

  await focusTab(gemTabId);
  const sendRes = await sendToTabSafe(gemTabId, {
    type: "swarm_gem_prompt",
    idea: idea,
    customPrompt: customPrompt,
  }, 3);
  if (sendRes.ok) return { ok: true, targetTabId: gemTabId };
  return { ok: false, error: sendRes.error };
}

// Deduplicate and throttle background polling of hermes_dev_server
let inFlightDevServerPoll = null;
let lastDevServerPollResult = null;
let lastDevServerPollTime = 0;

// ── messages from content.js / popup.js ─────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      case "status": {
        if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
          await waitForConnection(2500);
        }
        const tabId = _sender && _sender.tab && _sender.tab.id;
        if (tabId && _sender.tab.url) {
          const aiName = inferAiProvider(_sender.tab.url, _sender.tab.title);
          const currentRole = msg.role || tabRoles.get(tabId) || "standalone";
          tabRoles.set(tabId, currentRole);
          tabDetails.set(tabId, {
            tabId,
            role: currentRole,
            aiName,
            title: _sender.tab.title || aiName,
            url: _sender.tab.url,
            lastSeen: Date.now(),
          });
        }
        sendResponse(statusObj(tabId));
        break;
      }
      case "set_tab_role": {
        const tabId = _sender && _sender.tab && _sender.tab.id;
        if (tabId) {
          const role = msg.role || "standalone";
          tabRoles.set(tabId, role);
          const aiName = inferAiProvider(_sender.tab.url, _sender.tab.title);
          tabDetails.set(tabId, {
            tabId,
            role,
            aiName,
            title: _sender.tab.title || aiName,
            url: _sender.tab.url,
            lastSeen: Date.now(),
          });
          broadcastStatus();
          broadcastSwarmUpdate();
        }
        sendResponse({ ok: true, role: msg.role });
        break;
      }
      case "get_swarm_mode": {
        sendResponse({ ok: true, mode: swarmMode });
        break;
      }
      case "set_swarm_mode": {
        swarmMode = msg.mode || "all_in_one";
        try { chrome.storage.local.set({ zs_swarm_mode: swarmMode }); } catch (e) { }
        broadcastStatus();
        broadcastSwarmUpdate();
        sendResponse({ ok: true, mode: swarmMode });
        break;
      }
      case "get_project_state": {
        sendResponse({ ok: true, state: projectState });
        break;
      }
      case "get_swarm_state": {
        sendResponse({
          ok: true,
          mode: swarmMode,
          state: projectState,
          tabs: Array.from(tabDetails.values()),
          events: swarmEvents,
        });
        break;
      }
      case "swarm_clear_events": {
        swarmEvents.length = 0;
        broadcastSwarmUpdate();
        sendResponse({ ok: true });
        break;
      }
      case "swarm_start_mission": {
        const mode = msg.mode || swarmMode;
        const useGem = msg.useGem !== false;

        projectState.sessionId = "sess_" + Date.now();
        projectState.status = "active";
        projectState.startedAt = Date.now();
        projectState.concludedAt = null;
        projectState.mission = msg.prompt || "";
        projectState.completedTasks = [];
        projectState.currentTask = "01_init";
        if (msg.prompt) {
          const cleanName = msg.prompt.slice(0, 32).toLowerCase().replace(/[^a-z0-9_-]/g, "-").replace(/-+/g, "-");
          projectState.projectName = cleanName || "proyecto-ghs";
        }

        if (useGem) {
          projectState.pendingGemLaunch = true;
          projectState.originalIdea = msg.prompt || "";
          syncStateLedgerToDisk();
          const gemRes = await startGemGenerationInternal(msg.prompt, msg.customGemPrompt || "");
          if (!gemRes.ok) {
            projectState.pendingGemLaunch = false;
            sendResponse(gemRes);
          } else {
            sendResponse({ ok: true, step: "gem_started", targetTabId: gemRes.targetTabId });
          }
          break;
        }

        // Direct mission without GEM
        projectState.pendingGemLaunch = false;
        const res = await startSwarmMissionInternal(msg.prompt, mode);
        sendResponse(res);
        break;
      }
      case "swarm_delegate_task": {
        const archTabId = _sender && _sender.tab && _sender.tab.id;
        const archInfo = archTabId ? tabDetails.get(archTabId) : null;

        const workerTarget = await findOrSelectWorkerTab(archTabId);
        if (!workerTarget) {
          const errMsg = "No se encontró ninguna pestaña con rol [⚙️ Obrero] ni otra pestaña de IA abierta. Abre otra pestaña de IA (ej. ChatGPT, Claude o Gemini), selecciona el rol Obrero en la barra de GHS, o usa el rol [🐝 Mono-Pestaña] para ejecutar todo en este mismo chat.";
          addSwarmEvent({
            type: "error",
            fromRole: "architect",
            fromAi: archInfo ? archInfo.aiName : "Arquitecto",
            message: errMsg,
          });
          sendResponse({ ok: false, error: errMsg });
          break;
        }

        const workerTabId = workerTarget.tabId;
        const workerInfo = workerTarget.info;
        const subtaskId = msg.subtaskId || Date.now();
        projectState.projectDir = msg.projectDir || projectState.projectDir;
        projectState.currentTask = msg.task || "";
        syncStateLedgerToDisk();

        addSwarmEvent({
          type: "delegate",
          fromTab: archTabId,
          fromAi: archInfo ? archInfo.aiName : "Arquitecto",
          toTab: workerTabId,
          toAi: workerInfo ? workerInfo.aiName : "Obrero",
          subtaskId: subtaskId,
          projectDir: msg.projectDir || "",
          task: msg.task,
        });
        addSystemLog("INFO", "SWARM", `Delegando Subtarea #${subtaskId} al Obrero (${workerInfo ? workerInfo.aiName : "Obrero"}). Cambiando a su pestaña...`);

        await focusTab(workerTabId);
        const sendRes = await sendToTabSafe(workerTabId, {
          type: "swarm_incoming_task",
          task: msg.task,
          subtaskId: subtaskId,
          projectDir: msg.projectDir || "",
          fromTab: archTabId,
        }, 3);

        if (sendRes.ok) {
          sendResponse({ ok: true, workerTabId });
        } else {
          addSwarmEvent({
            type: "error",
            fromAi: workerInfo ? workerInfo.aiName : "Obrero",
            message: `Fallo al enviar tarea al Obrero: ${sendRes.error}`,
          });
          sendResponse({
            ok: false,
            error: `Fallo al conectar con la pestaña del Obrero (${workerInfo ? workerInfo.aiName : "Obrero"}): ${sendRes.error}. Asegúrate de que la pestaña del Obrero esté abierta y activa, o recárgala (F5).`,
          });
        }
        break;
      }
      case "swarm_event": {
        const ev = msg.event || {};
        if (ev.type === "gem_inquiry_ready") {
          addSystemLog("INFO", "GEM", `💡 GEM Builder formuló las preguntas y opciones de diseño. Esperando respuestas del usuario en el chat...`);
          addSwarmEvent({
            type: "gem_inquiry_ready",
            fromAi: ev.aiName || "GEM Builder",
            preview: ev.text || "",
          });
        }
        sendResponse({ ok: true });
        break;
      }
      case "swarm_tool_event": {
        const tabId = _sender && _sender.tab && _sender.tab.id;
        const tInfo = tabId ? tabDetails.get(tabId) : null;
        if (msg.tool === "hermes_ui_inspect") {
          projectState.visualAudit = {
            timestamp: Date.now(),
            url: (msg.params && msg.params.url) || "http://localhost:5173",
          };
          syncStateLedgerToDisk();
        }
        addSwarmEvent({
          type: "tool_call",
          tabId: tabId,
          aiName: tInfo ? tInfo.aiName : "Obrero",
          subtaskId: msg.subtaskId,
          tool: msg.tool,
          params: msg.params,
        });
        sendResponse({ ok: true });
        break;
      }
      case "swarm_report_result": {
        const workerTabId = _sender && _sender.tab && _sender.tab.id;
        const workerInfo = workerTabId ? tabDetails.get(workerTabId) : null;
        let archTabId = msg.toTab;
        let archInfo = archTabId ? tabDetails.get(archTabId) : null;

        projectState.completedTasks.push({
          subtaskId: msg.subtaskId,
          report: msg.report,
          success: msg.success !== false,
          timestamp: Date.now(),
        });
        syncStateLedgerToDisk();

        addSwarmEvent({
          type: "report",
          fromTab: workerTabId,
          fromAi: workerInfo ? workerInfo.aiName : "Obrero",
          toTab: archTabId,
          toAi: archInfo ? archInfo.aiName : "Arquitecto",
          subtaskId: msg.subtaskId,
          success: msg.success !== false,
          report: msg.report,
        });

        if (!archTabId) {
          for (const [tId, r] of tabRoles.entries()) {
            if (tId !== workerTabId && (r === "architect" || r === "gem")) {
              archTabId = tId;
              archInfo = tabDetails.get(tId);
              break;
            }
          }
        }

        if (archTabId) {
          addSystemLog("INFO", "SWARM", `Reporte del Obrero recibido. Regresando a la pestaña del Arquitecto (${archInfo ? archInfo.aiName : "Arquitecto"})...`);
          await focusTab(archTabId);
          await sendToTabSafe(archTabId, {
            type: "swarm_incoming_report",
            subtaskId: msg.subtaskId,
            report: msg.report,
            success: msg.success !== false,
          }, 3);
        }
        sendResponse({ ok: true });
        break;
      }
      case "swarm_mission_complete": {
        const tabId = _sender && _sender.tab && _sender.tab.id;
        const tInfo = tabId ? tabDetails.get(tabId) : null;
        projectState.currentTask = "COMPLETED";
        projectState.status = "completed";
        projectState.concludedAt = Date.now();
        projectState.url = msg.url || "";
        syncStateLedgerToDisk();

        addSwarmEvent({
          type: "mission_complete",
          fromTab: tabId,
          aiName: tInfo ? tInfo.aiName : "Arquitecto",
          summary: msg.summary,
          url: msg.url || "",
        });
        sendResponse({ ok: true });
        break;
      }
      case "swarm_generate_blueprint_ai": {
        const idea = msg.idea || "";
        if (!idea.trim()) {
          sendResponse({ ok: false, error: "La idea del proyecto está vacía." });
          break;
        }

        let gemTabId = null;
        let gemInfo = null;
        for (const [tId, r] of tabRoles.entries()) {
          if (r === "gem" || r === "gem_builder") {
            gemTabId = tId;
            gemInfo = tabDetails.get(tId);
            break;
          }
        }
        // If no tab explicitly set to GEM, pick any open AI tab (active tab or first available)
        if (!gemTabId) {
          const allTabs = Array.from(tabDetails.values());
          if (allTabs.length > 0) {
            const activeAiTab = allTabs.find((t) => t.active) || allTabs[0];
            gemTabId = activeAiTab.tabId;
            gemInfo = activeAiTab;
            tabRoles.set(gemTabId, "gem");
            activeAiTab.role = "gem";
            broadcastStatus();
            broadcastSwarmUpdate();
          }
        }

        if (!gemTabId) {
          sendResponse({
            ok: false,
            error: "No se encontró ninguna pestaña de IA abierta. Abre una pestaña de IA (DeepSeek, Gemini, ChatGPT, etc.) para que el Agente GEM razone y cree el Blueprint Maestro.",
          });
          break;
        }

        addSwarmEvent({
          type: "gem_start",
          toTab: gemTabId,
          aiName: gemInfo ? gemInfo.aiName : "Agente GEM Builder",
          idea: idea,
        });
        addSystemLog("INFO", "GEM", `Iniciando Agente GEM Builder en ${gemInfo ? gemInfo.aiName : "Pestaña"} para diseñar Blueprint de: "${idea.slice(0, 60)}..."`);

        await focusTab(gemTabId);
        chrome.tabs.sendMessage(gemTabId, {
          type: "swarm_gem_prompt",
          idea: idea,
        }).then(() => {
          sendResponse({ ok: true, targetTabId: gemTabId });
        }).catch((err) => {
          sendResponse({ ok: false, error: String(err) });
        });
        break;
      }
      case "swarm_gem_blueprint_received": {
        const tabId = _sender && _sender.tab && _sender.tab.id;
        const tInfo = tabId ? tabDetails.get(tabId) : null;
        const blueprint = msg.blueprint || "";
        const niche = msg.niche || "";
        const aesthetic = msg.aesthetic || "";

        projectState.masterBlueprint = blueprint;
        if (niche) projectState.niche = niche;
        if (aesthetic) projectState.aesthetic = aesthetic;
        syncStateLedgerToDisk();

        addSwarmEvent({
          type: "gem_blueprint_ready",
          fromTab: tabId,
          aiName: tInfo ? tInfo.aiName : "Agente GEM",
          niche: niche,
          aesthetic: aesthetic,
          len: blueprint.length,
        });
        addSystemLog("SUCCESS", "GEM", `✓ Blueprint Maestro World-Class generado con éxito (${blueprint.length} caracteres). Transmitido al Dashboard y Enjambre.`);

        // Broadcast to dashboard and all open tabs
        chrome.runtime.sendMessage({
          type: "swarm_blueprint_updated",
          blueprint: blueprint,
          niche: niche,
          aesthetic: aesthetic,
        }).catch(() => { });

        // Determine effective mode: if distributed selected, or if any worker tab exists
        const hasWorkerTab = Array.from(tabRoles.values()).some((r) => r === "worker");
        const effectiveMode = (swarmMode === "distributed" || hasWorkerTab) ? "distributed" : swarmMode;
        const assignedRole = effectiveMode === "distributed" ? "architect" : "all_in_one";

        if (tabId) {
          tabRoles.set(tabId, assignedRole);
          const inf = tabDetails.get(tabId);
          if (inf) inf.role = assignedRole;
          chrome.tabs.sendMessage(tabId, { type: "zs_assign_role", role: assignedRole }).catch(() => { });
          broadcastStatus();
          broadcastSwarmUpdate();
        }

        // AUTOMATIC CHAIN: Always forward the Blueprint to the construction team
        projectState.pendingGemLaunch = false;
        addSystemLog("INFO", "SWARM", `🚀 Traspasando Blueprint Maestro al equipo de construcción (${effectiveMode === "distributed" ? "Arquitecto / Obrero" : "Mono-Pestaña"})...`);
        addSwarmEvent({
          type: "gem_to_architect",
          fromAi: tInfo ? tInfo.aiName : "GEM Builder",
          toAi: effectiveMode === "distributed" ? "Arquitecto / Obrero" : "Mono-Pestaña",
          niche: niche,
          blueprintLen: blueprint.length,
        });
        startSwarmMissionInternal(blueprint, effectiveMode, tabId).then((res) => {
          if (!res.ok) {
            addSystemLog("ERROR", "SWARM", `Error al traspasar al Arquitecto: ${res.error}`);
          }
        });

        sendResponse({ ok: true, assignedRole });
        break;
      }
      case "swarm_direct_dispatch_worker": {
        const task = msg.task || projectState.currentTask || "Iniciar construcción del proyecto según el Blueprint";
        const subtaskId = msg.subtaskId || "01_init";
        const projectDir = msg.projectDir || projectState.projectDir || "";

        const workerTarget = await findOrSelectWorkerTab(null);
        if (!workerTarget) {
          sendResponse({ ok: false, error: "No se encontró ninguna pestaña con rol [⚙️ Obrero] ni otra pestaña de IA abierta para recibir la tarea." });
          break;
        }
        addSystemLog("INFO", "SWARM", `⚡ Despachando directamente subtarea ${subtaskId} al Obrero (${workerTarget.info.aiName})...`);
        await focusTab(workerTarget.tabId);
        const sendRes = await sendToTabSafe(workerTarget.tabId, {
          type: "swarm_incoming_task",
          task: task,
          subtaskId: subtaskId,
          projectDir: projectDir,
          fromTab: null,
        }, 3);
        sendResponse(sendRes);
        break;
      }
      case "swarm_get_session_state": {
        sendResponse({
          ok: true,
          state: projectState,
          devServers: cachedDevServers,
        });
        break;
      }
      case "swarm_conclude_session": {
        projectState.status = "completed";
        projectState.concludedAt = Date.now();
        await syncStateLedgerToDisk();

        addSystemLog("SUCCESS", "SESSION", `✓ Sesión de proyecto "${projectState.projectName || 'Activo'}" CONCLUIDA y archivada en disco.`);
        addSwarmEvent({
          type: "session_concluded",
          sessionId: projectState.sessionId,
          projectName: projectState.projectName,
          projectDir: projectState.projectDir,
          timestamp: new Date().toLocaleTimeString(),
        });
        broadcastSwarmUpdate();
        sendResponse({ ok: true, state: projectState });
        break;
      }
      case "swarm_reset_session": {
        projectState.sessionId = null;
        projectState.projectName = "";
        projectState.mission = "";
        projectState.projectDir = "Downloads";
        projectState.status = "idle";
        projectState.currentTask = "";
        projectState.completedTasks = [];
        projectState.visualAudit = null;
        projectState.masterBlueprint = "";
        projectState.niche = "";
        projectState.aesthetic = "";
        projectState.pendingGemLaunch = false;
        projectState.originalIdea = "";
        projectState.devServerUrl = "";
        projectState.startedAt = null;
        projectState.concludedAt = null;
        projectState.lastUpdated = Date.now();
        await syncStateLedgerToDisk();

        addSystemLog("INFO", "SESSION", `Panel de sesión reiniciado a estado limpio para nuevo proyecto.`);
        addSwarmEvent({
          type: "session_reset",
          timestamp: new Date().toLocaleTimeString(),
        });
        broadcastSwarmUpdate();
        sendResponse({ ok: true, state: projectState });
        break;
      }
      case "swarm_resume_session_tab": {
        const handoverPrompt = msg.prompt || "";
        let targetTabId = null;
        let targetInfo = null;

        const allTabs = Array.from(tabDetails.values());
        const activeTab = allTabs.find((t) => t.active);
        if (activeTab) {
          targetTabId = activeTab.tabId;
          targetInfo = activeTab;
        } else if (allTabs.length > 0) {
          targetTabId = allTabs[0].tabId;
          targetInfo = allTabs[0];
        }

        if (!targetTabId) {
          sendResponse({
            ok: false,
            error: "No se encontró ninguna pestaña de IA abierta. Abre una pestaña con tu nueva cuenta (Gemini, ChatGPT, Claude o DeepSeek) y vuelve a hacer clic en 'Reanudar en Pestaña Activa'."
          });
          break;
        }

        projectState.status = "active";
        await syncStateLedgerToDisk();

        addSystemLog("INFO", "SWARM", `⚡ Reanudando sesión mediante Handover Packet en pestaña: ${targetInfo ? targetInfo.aiName : "IA"} (Tab ${targetTabId})...`);
        await focusTab(targetTabId);

        const sendRes = await sendToTabSafe(targetTabId, {
          type: "swarm_incoming_task",
          task: handoverPrompt,
          subtaskId: projectState.currentTask || "relevo_continuacion",
          projectDir: projectState.projectDir,
          fromTab: null,
        }, 3);

        addSwarmEvent({
          type: "session_handover",
          toTab: targetTabId,
          aiName: targetInfo ? targetInfo.aiName : "IA",
          projectName: projectState.projectName,
          status: "relevo_exitoso",
        });

        broadcastSwarmUpdate();
        sendResponse({ ok: true, targetAi: targetInfo ? targetInfo.aiName : "IA", sendRes });
        break;
      }
      case "list_tools": {
        // Prefer a live refresh; fall back to cache so the loop never stalls.
        // 10s, not 25s: a catalogue request only blocks this long when one of the
        // MCP servers is dead (typically Roblox in a degraded, Blender-only
        // session), and in that exact case we already hold a perfectly good cached
        // catalogue. Waiting the full 25s just froze the boot for no new data.
        const r = await send({ type: "list_tools" }, 18000);
        if (r.ok) sendResponse({ ok: true, tools: r.tools });
        else sendResponse({ ok: toolsCache.length > 0, tools: toolsCache, error: r.error });
        break;
      }
      case "call_tool": {
        const timeout = (msg.timeout || 120000) + 10000;
        const toolName = msg.name || "unknown";
        const toolArgs = msg.arguments || {};
        const isPollingDevServer = toolName === "hermes_dev_server" && toolArgs && toolArgs.action === "list";

        if (isPollingDevServer) {
          if (lastDevServerPollResult && Date.now() - lastDevServerPollTime < 4000) {
            sendResponse(lastDevServerPollResult);
            break;
          }
          if (inFlightDevServerPoll) {
            try {
              const res = await inFlightDevServerPoll;
              sendResponse(res);
            } catch (err) {
              sendResponse({ ok: false, error: String(err) });
            }
            break;
          }
        }

        const t0 = Date.now();
        if (!isPollingDevServer) {
          addSystemLog("TOOL", "EXEC", `Ejecutando herramienta local: ${toolName}`, toolArgs);
          const tabId = _sender && _sender.tab && _sender.tab.id;
          const tInfo = tabId ? tabDetails.get(tabId) : null;
          addSwarmEvent({
            type: "tool_call",
            tabId: tabId,
            aiName: tInfo ? tInfo.aiName : "Enjambre",
            tool: toolName,
            params: toolArgs,
          });
        }
        const sendPromise = send(
          { type: "call_tool", name: msg.name, arguments: msg.arguments, timeout: msg.timeout },
          timeout
        );
        if (isPollingDevServer) inFlightDevServerPoll = sendPromise;
        const r = await sendPromise;
        if (isPollingDevServer) {
          inFlightDevServerPoll = null;
          lastDevServerPollResult = r;
          lastDevServerPollTime = Date.now();
        }
        const dt = Date.now() - t0;
        const textStr = typeof r?.text === "string" ? r.text : "";
        const hasExitZero = /Exit Code:\s*0\b/i.test(textStr);
        const hasNonZeroExit = /Exit Code:\s*[1-9]/i.test(textStr);
        const hasFatalErr = /PSSecurityException|Traceback \(most recent call last\)/i.test(textStr);
        const isFailed = !r?.ok || hasNonZeroExit || (!hasExitZero && hasFatalErr);
        if (!isPollingDevServer) {
          if (r && r.ok && !isFailed) {
            const preview = textStr.slice(0, 250) || (r.text ? JSON.stringify(r.text).slice(0, 250) : "OK");
            addSystemLog("SUCCESS", "EXEC", `✓ ${toolName} completado en ${dt}ms`, preview);
          } else {
            const errDetail = r?.error || textStr.slice(0, 300) || "Error desconocido";
            addSystemLog("ERROR", "EXEC", `✗ ${toolName} falló en ${dt}ms (Exit Code != 0 o error)`, errDetail);
          }
        }
        if (toolName === "hermes_dev_server" && r && r.ok) {
          try {
            const matchUrl = textStr.match(/http:\/\/(?:localhost|127\.0\.0\.1):\d+/i);
            if (matchUrl && matchUrl[0]) {
              projectState.devServerUrl = matchUrl[0];
              syncStateLedgerToDisk();
            }
          } catch (e) { }
        }
        sendResponse(r);
        break;
      }
      case "get_system_logs": {
        sendResponse({ ok: true, logs: systemLogs });
        break;
      }
      case "clear_system_logs": {
        systemLogs.length = 0;
        sendResponse({ ok: true });
        break;
      }
      case "client_diag": {
        const ai = (_sender && _sender.tab) ? inferAiProvider(_sender.tab.url, _sender.tab.title) : "Client";
        addSystemLog(msg.level || "INFO", msg.tag || "AGENT", `[${ai}] ${msg.message || ""}`, msg.detail || null);
        if (msg.level === "WARN" && _sender && _sender.tab && /circuit_breaker|limit|context_length/i.test(msg.message || "")) {
          triggerTabFailover(_sender.tab.id, msg.message);
        }
        sendResponse({ ok: true });
        break;
      }
      case "restart_mcp": {
        const r = await send({ type: "restart_mcp" }, 30000);
        sendResponse(r);
        break;
      }
      case "add_server": {
        const r = await send({
          type: "add_server", server_id: msg.server_id,
          command: msg.command, args: msg.args, env: msg.env,
        }, 15000);
        sendResponse(r);
        break;
      }
      case "remove_server": {
        const r = await send({ type: "remove_server", server_id: msg.server_id }, 15000);
        sendResponse(r);
        break;
      }
      case "get_security_config": {
        const r = await send({ type: "get_security_config" }, 8000);
        sendResponse(r);
        break;
      }
      case "set_security_config": {
        const r = await send({ type: "set_security_config", config: msg.config }, 8000);
        sendResponse(r);
        break;
      }
      case "reconnect":
        reconnectDelay = RECONNECT_MIN;
        connect();
        sendResponse({ ok: true });
        break;
      default:
        sendResponse({ ok: false, error: "unknown message" });
    }
  })();
  return true; // async sendResponse
});

// Wake/keepalive hooks.
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

connect();
