// GHS Mission Control - Dashboard Controller
(() => {
  "use strict";

  const BRIDGE_WS_URL = "ws://127.0.0.1:17613";
  let ws = null;
  let reconnectTimer = null;
  let activePlaybooks = [
    {
      id: "calificar_examenes_campus",
      name: "Calificar Exámenes Campus",
      platform: "Campus Virtual",
      description: "Evaluación automática de entregas de estudiantes usando rúbrica de video.",
      steps: [
        "Abrir la pestaña con la sesión activa del Campus Virtual y acceder a la lista de tareas pendientes.",
        "Por cada estudiante en la cola de evaluación, hacer clic en su enlace y descargar o visualizar el documento entregado.",
        "Aplicar la rúbrica de calificación evaluando metodología, desarrollo y conclusiones (escala 0-20).",
        "Digitar la calificación en la casilla correspondiente, insertar la retroalimentación constructiva y pulsar 'Guardar y siguiente'."
      ]
    },
    {
      id: "descargar_reportes_ventas",
      name: "Descargar Reportes Ventas",
      platform: "Tienda / E-commerce",
      description: "Extracción de pedidos diarios, consolidación en CSV y reporte de métricas.",
      steps: [
        "Acceder al panel de administración de Shopify / WooCommerce.",
        "Navegar a la sección de Informes y Estadísticas > Exportar pedidos.",
        "Seleccionar el rango de fechas del día en curso y formato CSV.",
        "Descargar el archivo a ~/Downloads y notificar al usuario con el resumen total de ventas."
      ]
    }
  ];

  let batchItems = [
    { id: "ALUM-2026-001", name: "Juan Pérez Silva", status: "evaluated", grade: "18 / 20", feedback: "Excelente formulación del problema y bibliografía actualizada.", date: "20/09/2026" },
    { id: "ALUM-2026-002", name: "María García Quispe", status: "evaluated", grade: "16 / 20", feedback: "Desarrollo completo; se sugiere profundizar en el análisis comparativo.", date: "20/09/2026" },
    { id: "ALUM-2026-003", name: "Carlos Mendoza Ramos", status: "pending", grade: "-", feedback: "En cola de procesamiento por el agente.", date: "20/09/2026" },
    { id: "ALUM-2026-004", name: "Lucía Fernández Flores", status: "pending", grade: "-", feedback: "En cola de procesamiento por el agente.", date: "20/09/2026" },
    { id: "ALUM-2026-005", name: "Diego Torres Vega", status: "pending", grade: "-", feedback: "En cola de procesamiento por el agente.", date: "20/09/2026" }
  ];

  // ── Tab Navigation ────────────────────────────────────────────────
  function initTabs() {
    const navItems = document.querySelectorAll(".nav-item");
    const panes = document.querySelectorAll(".tab-pane");

    navItems.forEach((btn) => {
      btn.addEventListener("click", () => {
        const tab = btn.dataset.tab;
        navItems.forEach((b) => b.classList.remove("active"));
        panes.forEach((p) => p.classList.remove("active"));

        btn.classList.add("active");
        const targetPane = document.getElementById(`pane-${tab}`);
        if (targetPane) targetPane.classList.add("active");
      });
    });
  }

  // ── WebSocket Connection to Bridge ────────────────────────────────
  function connectBridge() {
    const statusPill = document.getElementById("bridge-status-pill");
    const statusDot = document.getElementById("status-dot");
    const statusText = document.getElementById("status-text");

    if (ws) {
      try { ws.close(); } catch (e) {}
    }

    statusDot.className = "status-dot pulsing";
    statusText.textContent = "Conectando con el Puente (ws://127.0.0.1:17613)...";

    try {
      ws = new WebSocket(BRIDGE_WS_URL);
    } catch (e) {
      handleDisconnect();
      return;
    }

    ws.onopen = () => {
      statusDot.className = "status-dot";
      statusDot.style.background = "#10b981";
      statusText.textContent = "● Puente Conectado: " + (BRIDGE_WS_URL.indexOf("render") !== -1 ? "Nube (Render)" : "Local (127.0.0.1:17613)");
      appendLog("[WEBSOCKET] Conexión establecida con el puente local.");
      if (reconnectTimer) {
        clearInterval(reconnectTimer);
        reconnectTimer = null;
      }
      // Request initial tools/servers snapshot
      ws.send(jsonMsg("ping"));
      // Pedir lista de skills del MCP
      ws.send(JSON.stringify({
        type: "call_tool",
        id: "skills_list_init",
        name: "hermes_skills_list",
        arguments: {}
      }));
    };

    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        handleBridgeMessage(data);
      } catch (e) {
        appendLog(`[RAW] ${evt.data}`);
      }
    };

    ws.onerror = () => {
      handleDisconnect();
    };

    ws.onclose = () => {
      handleDisconnect();
    };
  }

  function handleDisconnect() {
    const statusDot = document.getElementById("status-dot");
    const statusText = document.getElementById("status-text");
    if (statusDot) {
      statusDot.className = "status-dot";
      statusDot.style.background = "#ef4444";
    }
    if (statusText) statusText.textContent = "○ Desconectado (Reintentando conexión)...";

    if (!reconnectTimer) {
      reconnectTimer = setInterval(connectBridge, 4000);
    }
  }

  function jsonMsg(type, payload = {}) {
    return JSON.stringify({ type, id: "dash_" + Date.now(), ...payload });
  }

  function handleBridgeMessage(msg) {
    const type = msg.type;
    if (type === "connected") {
      appendLog(`[STATUS] Servidores MCP activos: ${(msg.servers || []).length}. Total herramientas: ${(msg.tools || []).length}.`);
      updateMcpCards(msg.servers || []);
      const mcpCountEl = document.getElementById("mcp-count");
      if (mcpCountEl) mcpCountEl.textContent = (msg.servers || []).filter(s => s.alive).length;
    } else if (type === "tool_result") {
      if (handleProjectMessages(msg)) return;
      if (msg.id && msg.id.startsWith("read_brain_")) {
          const key = msg.id.replace("read_brain_", "");
          // El bridge devuelve {ok: true, text: "..."} o {ok: false, error: "..."}
          let text = "[Archivo vacio]";
          if (msg.ok && msg.text) {
              text = msg.text;
          } else if (msg.error) {
              text = "ERROR: " + msg.error;
          } else if (msg.content && msg.content[0] && msg.content[0].text) {
              text = msg.content[0].text;
          }
          _brainCache[key] = text;
          _updateBrainStats(key, text);
          if (key === _brainActiveFile) {
              const ta = document.getElementById('ta-brain');
              if (ta) { ta.value = text; _updateCharCount(); }
          }
          return;
      }
      if (msg.id && msg.id.startsWith("save_brain_")) {
          appendLog("[BRAIN] Archivo guardado: " + msg.id.replace("save_brain_", "") + ".md");
          return;
      }
      // ── SKILLS LIST DINAMICO ──
      if (msg.id === "skills_list_init") {
        const raw = msg.text || (msg.content && msg.content[0] && msg.content[0].text) || "";
        if (raw) {
          try {
            const parsed = typeof raw === "object" ? raw : JSON.parse(raw);
            if (parsed && Array.isArray(parsed.skills) && parsed.skills.length > 0) {
              renderSkillCards(parsed.skills);
              return;
            }
          } catch(e) { console.warn("Skills parse error:", e); }
        }
      }
      // ── TOGGLE SKILL ACK ──
      if (msg.id && msg.id.startsWith("toggle_") && msg.text) {
        appendLog(`[SKILL] ${msg.text}`);
        return;
      }
      appendLog(`[TOOL RESULT] ${msg.id}: ${JSON.stringify(msg.text || msg.error || "").slice(0, 120)}`);
      if (msg.images && msg.images.length > 0) {
        displayScreenshot(msg.images[0]);
      }
    } else if (type === "toggle_mcp_ack") {
      appendLog(`[MCP] Confirmación de puente: "${msg.server}" ${msg.enabled ? "activado" : "desactivado"}.`);
    } else {
      appendLog(`[EVENT] ${JSON.stringify(msg)}`);
    }
  }

  function updateMcpCards(servers) {
    servers.forEach((s) => {
      const card = document.querySelector(`.power-card[data-server="${s.id}"]`);
      if (card) {
        const statusEl = card.querySelector(".card-status");
        const switchEl = card.querySelector("input[type='checkbox']");
        if (statusEl) {
          statusEl.textContent = s.alive ? "● Conectado y Activo" : "○ Inactivo / Esperando";
          statusEl.style.color = s.alive ? "#34d399" : "#94a3b8";
        }
        if (switchEl) switchEl.checked = s.alive;
      }
    });
  }

  // ── Logging Console ──────────────────────────────────────────────
  const systemLogsList = [];
  let activeLogFilter = "";

  function formatLogLine(item) {
    if (!item) return "";
    if (typeof item === "string") return item;
    const time = item.timestamp || new Date().toLocaleTimeString();
    const lvl = (item.level || "INFO").toUpperCase();
    const tag = item.tag ? `[${item.tag}]` : "";
    let line = `[${time}] [${lvl}] ${tag} ${item.text || ""}`;
    if (item.detail) {
      const detailStr = typeof item.detail === "object" ? JSON.stringify(item.detail) : String(item.detail);
      line += `\n    └─ ${detailStr}`;
    }
    return line;
  }

  function renderLogs() {
    const logBox = document.getElementById("log-output");
    if (!logBox) return;
    const q = activeLogFilter.trim().toLowerCase();
    const filtered = q
      ? systemLogsList.filter((l) => {
          const formatted = formatLogLine(l).toLowerCase();
          return formatted.includes(q);
        })
      : systemLogsList;

    if (filtered.length === 0) {
      logBox.textContent = q ? `[Sin eventos coincidentes con "${q}"]` : `[Esperando eventos del sistema...]`;
    } else {
      logBox.textContent = filtered.map(formatLogLine).join("\n");
    }
    logBox.scrollTop = logBox.scrollHeight;
  }

  function appendLog(strOrItem) {
    const item = typeof strOrItem === "string"
      ? {
          id: "dash_" + Date.now() + "_" + Math.random().toString(36).substring(2, 6),
          timestamp: new Date().toLocaleTimeString(),
          level: "INFO",
          tag: "DASH",
          text: strOrItem,
        }
      : strOrItem;

    systemLogsList.push(item);
    if (systemLogsList.length > 500) systemLogsList.shift();

    const logBox = document.getElementById("log-output");
    if (!logBox) return;

    const formatted = formatLogLine(item);
    const q = activeLogFilter.trim().toLowerCase();
    if (!q || formatted.toLowerCase().includes(q)) {
      if (logBox.textContent.startsWith("[Esperando") || logBox.textContent.startsWith("[Sin eventos")) {
        logBox.textContent = formatted;
      } else {
        logBox.textContent += (logBox.textContent ? "\n" : "") + formatted;
      }
      logBox.scrollTop = logBox.scrollHeight;
    }
  }

  function initLogActions() {
    const btnClear = document.getElementById("btn-clear-logs");
    const btnDownload = document.getElementById("btn-download-logs");
    const searchInput = document.getElementById("log-search");
    const logBox = document.getElementById("log-output");

    // Fetch initial logs from background
    if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage({ type: "get_system_logs" }, (res) => {
        if (res && res.ok && Array.isArray(res.logs) && res.logs.length) {
          systemLogsList.length = 0;
          for (const l of res.logs) systemLogsList.push(l);
          renderLogs();
        }
      });
    }

    if (btnClear && logBox) {
      btnClear.addEventListener("click", () => {
        systemLogsList.length = 0;
        logBox.textContent = `[${new Date().toLocaleTimeString()}] Consola reiniciada por el usuario.`;
        if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ type: "clear_system_logs" }).catch(() => {});
        }
      });
    }

    const btnCopy = document.getElementById("btn-copy-logs");
    if (btnCopy && logBox) {
      btnCopy.addEventListener("click", () => {
        const content = systemLogsList.length ? systemLogsList.map(formatLogLine).join("\n") : logBox.textContent;
        const copySuccess = () => {
          const orig = btnCopy.innerHTML;
          btnCopy.innerHTML = "✓ ¡Copiado!";
          btnCopy.style.color = "var(--olive)";
          btnCopy.style.borderColor = "var(--olive)";
          setTimeout(() => {
            btnCopy.innerHTML = orig;
            btnCopy.style.color = "";
            btnCopy.style.borderColor = "";
          }, 2000);
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(content).then(copySuccess).catch(() => {
            fallbackCopy(content);
          });
        } else {
          fallbackCopy(content);
        }

        function fallbackCopy(text) {
          try {
            const ta = document.createElement("textarea");
            ta.value = text;
            ta.style.position = "fixed";
            ta.style.opacity = "0";
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
            copySuccess();
          } catch (e) {}
        }
      });
    }

    if (btnDownload && logBox) {
      btnDownload.addEventListener("click", () => {
        const content = systemLogsList.length ? systemLogsList.map(formatLogLine).join("\n") : logBox.textContent;
        const blob = new Blob([content], { type: "text/plain" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `ghs_system_${Date.now()}.log`;
        a.click();
        URL.revokeObjectURL(url);
      });
    }

    if (searchInput) {
      searchInput.addEventListener("input", () => {
        activeLogFilter = searchInput.value;
        renderLogs();
      });
    }
  }

  // ── Playbooks Manager ─────────────────────────────────────────────
  function initPlaybooks() {
    const listEl = document.getElementById("playbook-list");
    const countEl = document.getElementById("playbook-count");
    if (countEl) countEl.textContent = activePlaybooks.length;

    renderPlaybookList();

    const btnNew = document.getElementById("btn-new-playbook");
    if (btnNew) {
      btnNew.addEventListener("click", () => {
        const name = prompt("Nombre único del nuevo Playbook (ej: auditar_servidores):");
        if (!name) return;
        const platform = prompt("Plataforma o software objetivo (ej: moodle, shopify, windows):", "general");
        const stepsRaw = prompt("Escribe los pasos separados por punto y coma (;):", "1. Abrir aplicación; 2. Ejecutar acción; 3. Validar resultado");
        const steps = (stepsRaw || "").split(";").map(s => s.trim()).filter(Boolean);

        const newPb = {
          id: name.toLowerCase().replace(/[^a-z0-9_]/g, "_"),
          name: name,
          platform: platform || "general",
          description: `Procedimiento autónomo para ${name}`,
          steps: steps.length ? steps : ["Paso 1: Iniciar tarea", "Paso 2: Confirmar"]
        };

        activePlaybooks.push(newPb);
        renderPlaybookList();
        selectPlaybook(newPb.id);

        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "call_tool",
            id: "save_pb_" + Date.now(),
            name: "save_playbook",
            arguments: { name: newPb.id, description: newPb.description, platform: newPb.platform, steps: newPb.steps },
            timeout: 10
          }));
        }
        appendLog(`[PLAYBOOK] Creado y guardado en memoria: ${newPb.name}`);
      });
    }

    const btnDelete = document.getElementById("btn-delete-playbook");
    if (btnDelete) {
      btnDelete.addEventListener("click", () => {
        const activeItem = document.querySelector(".playbook-item.active");
        if (!activeItem) return;
        const id = activeItem.dataset.id;
        if (confirm(`¿Eliminar el Playbook '${id}'?`)) {
          activePlaybooks = activePlaybooks.filter(p => p.id !== id);
          renderPlaybookList();
          if (activePlaybooks.length > 0) selectPlaybook(activePlaybooks[0].id);
        }
      });
    }
  }

  function renderPlaybookList() {
    const listEl = document.getElementById("playbook-list");
    if (!listEl) return;
    listEl.innerHTML = activePlaybooks.map((pb, idx) => `
      <div class="playbook-item ${idx === 0 ? "active" : ""}" data-id="${pb.id}">
        <div class="playbook-item-header">
          <span class="playbook-platform-pill">${pb.platform}</span>
          <span class="playbook-steps-pill">${pb.steps.length} pasos</span>
        </div>
        <h4 class="playbook-item-title">${pb.name}</h4>
        <p class="playbook-item-desc">${pb.description}</p>
      </div>
    `).join("");

    listEl.querySelectorAll(".playbook-item").forEach(item => {
      item.addEventListener("click", () => {
        selectPlaybook(item.dataset.id);
      });
    });

    const countEl = document.getElementById("playbook-count");
    if (countEl) countEl.textContent = activePlaybooks.length;
  }

  function selectPlaybook(id) {
    const pb = activePlaybooks.find(p => p.id === id);
    if (!pb) return;

    document.querySelectorAll(".playbook-item").forEach(el => {
      el.classList.toggle("active", el.dataset.id === id);
    });

    const titleEl = document.getElementById("detail-title");
    const descEl = document.getElementById("detail-desc");
    const stepsEl = document.getElementById("detail-steps");

    if (titleEl) titleEl.textContent = pb.name;
    if (descEl) descEl.textContent = pb.description;
    if (stepsEl) {
      stepsEl.innerHTML = pb.steps.map(s => `<li>${s}</li>`).join("");
    }
  }

  // ── Batch Pipeline Table ─────────────────────────────────────────
  function initBatchTable() {
    renderBatchTable();

    const searchInput = document.getElementById("batch-search");
    if (searchInput) {
      searchInput.addEventListener("input", () => {
        const q = searchInput.value.toLowerCase();
        renderBatchTable(q);
      });
    }

    const filterSelect = document.getElementById("batch-filter-status");
    if (filterSelect) {
      filterSelect.addEventListener("change", () => {
        renderBatchTable(searchInput ? searchInput.value.toLowerCase() : "");
      });
    }

    const btnExport = document.getElementById("btn-export-csv");
    if (btnExport) {
      btnExport.addEventListener("click", exportBatchCSV);
    }

    const btnImport = document.getElementById("btn-import-batch");
    if (btnImport) {
      btnImport.addEventListener("click", () => {
        const raw = prompt("Pega la lista de alumnos (Nombre, ID) separados por salto de línea:");
        if (!raw) return;
        const lines = raw.split("\n").map(l => l.trim()).filter(Boolean);
        lines.forEach((line, idx) => {
          const parts = line.split(/[,;\t]/);
          const name = parts[0] || `Estudiante ${batchItems.length + 1}`;
          const id = parts[1] || `ALUM-2026-0${batchItems.length + 1}`;
          batchItems.push({
            id: id.trim(),
            name: name.trim(),
            status: "pending",
            grade: "-",
            feedback: "En cola de evaluación.",
            date: new Date().toLocaleDateString()
          });
        });
        renderBatchTable();
        appendLog(`[BATCH] Cargados ${lines.length} nuevos estudiantes a la cola.`);
      });
    }

    const btnClear = document.getElementById("btn-clear-batch");
    if (btnClear) {
      btnClear.addEventListener("click", () => {
        if (confirm("¿Vaciar toda la cola de lotes?")) {
          batchItems = [];
          renderBatchTable();
        }
      });
    }
  }

  function renderBatchTable(filterQuery = "") {
    const tbody = document.getElementById("batch-tbody");
    const countEl = document.getElementById("batch-count");
    if (countEl) countEl.textContent = batchItems.length;
    if (!tbody) return;

    const filterStatus = document.getElementById("batch-filter-status")?.value || "all";

    const filtered = batchItems.filter(item => {
      const matchText = (item.name + " " + item.id + " " + item.grade).toLowerCase().includes(filterQuery);
      if (filterStatus === "all") return matchText;
      return matchText && item.status === filterStatus;
    });

    tbody.innerHTML = filtered.map((item, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td><strong>${item.name}</strong></td>
        <td><code>${item.id}</code></td>
        <td><span class="${item.status === "evaluated" ? "badge-success" : "badge-warning"}">${item.status === "evaluated" ? "✅ Calificado" : "⏳ Pendiente"}</span></td>
        <td><strong class="grade-text ${item.grade === "-" ? "muted" : ""}">${item.grade}</strong></td>
        <td>${item.feedback}</td>
        <td>${item.date}</td>
      </tr>
    `).join("");
  }

  function exportBatchCSV() {
    if (!batchItems.length) {
      alert("No hay registros en el lote para exportar.");
      return;
    }
    let csv = "ID,Nombre,Estado,Nota,Feedback,Fecha\n";
    batchItems.forEach(i => {
      csv += `"${i.id}","${i.name}","${i.status}","${i.grade}","${i.feedback.replace(/"/g, '""')}","${i.date}"\n`;
    });
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `reporte_evaluaciones_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    appendLog(`[EXPORT] Descargado archivo CSV con ${batchItems.length} registros.`);
  }

  // ── Desktop Operator (Computer Use) ──────────────────────────────
  function initDesktopOperator() {
    const btnScreen = document.getElementById("btn-take-screenshot");
    const container = document.getElementById("screen-container");
    const coordHud = document.getElementById("coord-hud");
    const img = document.getElementById("screen-img");
    const placeholder = document.getElementById("screen-placeholder");

    if (btnScreen) {
      btnScreen.addEventListener("click", () => {
        appendLog("[HERMES] Solicitando captura de pantalla a hermes_computer_use...");
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "call_tool",
            id: "screen_" + Date.now(),
            name: "hermes_computer_use",
            arguments: { action: "capture" },
            timeout: 10
          }));
        } else {
          appendLog("[HERMES] Error: Socket desconectado.");
        }
      });
    }

    if (container && coordHud) {
      container.addEventListener("mousemove", (e) => {
        const rect = container.getBoundingClientRect();
        const relX = Math.round((e.clientX - rect.left) * (1920 / rect.width));
        const relY = Math.round((e.clientY - rect.top) * (1080 / rect.height));
        coordHud.textContent = `Cursor: X=${relX}, Y=${relY}`;
      });
      container.addEventListener("click", (e) => {
        const rect = container.getBoundingClientRect();
        const relX = Math.round((e.clientX - rect.left) * (1920 / rect.width));
        const relY = Math.round((e.clientY - rect.top) * (1080 / rect.height));
        const inpX = document.getElementById("inp-click-x");
        const inpY = document.getElementById("inp-click-y");
        if (inpX) inpX.value = relX;
        if (inpY) inpY.value = relY;
      });
    }

    const btnTestClick = document.getElementById("btn-test-click");
    if (btnTestClick) {
      btnTestClick.addEventListener("click", () => {
        const x = parseInt(document.getElementById("inp-click-x")?.value || 0, 10);
        const y = parseInt(document.getElementById("inp-click-y")?.value || 0, 10);
        appendLog(`[HERMES] Ejecutando Clic en Windows en X=${x}, Y=${y}...`);
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "call_tool",
            id: "click_" + Date.now(),
            name: "hermes_computer_use",
            arguments: { action: "click", coordinate: [x, y] },
            timeout: 10
          }));
        }
      });
    }

    const btnTestType = document.getElementById("btn-test-type");
    if (btnTestType) {
      btnTestType.addEventListener("click", () => {
        const text = document.getElementById("inp-type-text")?.value || "";
        if (!text) return;
        appendLog(`[HERMES] Escribiendo texto en Windows: "${text}"...`);
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "call_tool",
            id: "type_" + Date.now(),
            name: "hermes_computer_use",
            arguments: { action: "type", text: text },
            timeout: 10
          }));
        }
      });
    }

    const btnTestHotkey = document.getElementById("btn-test-hotkey");
    if (btnTestHotkey) {
      btnTestHotkey.addEventListener("click", () => {
        const hotkey = document.getElementById("sel-hotkey")?.value || "enter";
        appendLog(`[HERMES] Pulsando atajo en Windows: "${hotkey}"...`);
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "call_tool",
            id: "key_" + Date.now(),
            name: "hermes_computer_use",
            arguments: { action: "key", key: hotkey },
            timeout: 10
          }));
        }
      });
    }
  }

  function displayScreenshot(base64Data) {
    const raw = typeof base64Data === "object" && base64Data ? (base64Data.data || "") : (base64Data || "");
    const img = document.getElementById("screen-img");
    const placeholder = document.getElementById("screen-placeholder");
    if (img && raw) {
      img.src = raw.startsWith("data:") ? raw : `data:image/png;base64,${raw}`;
      img.style.display = "block";
      if (placeholder) placeholder.style.display = "none";
      appendLog("[HERMES] Captura de pantalla de Windows renderizada en el visor.");
    }
  }

  // ── Swarm Multi-Agent Monitor ─────────────────────────────────────
  function initSwarmMonitor() {
    const tabsContainer = document.getElementById("swarm-tabs-container");
    const overallStatus = document.getElementById("swarm-overall-status");
    const archAi = document.getElementById("swarm-arch-ai");
    const archStatus = document.getElementById("swarm-arch-status");
    const workAi = document.getElementById("swarm-work-ai");
    const workStatus = document.getElementById("swarm-work-status");
    const flowArrow = document.getElementById("swarm-arrow-line");
    const feed = document.getElementById("swarm-feed");
    const btnLaunch = document.getElementById("btn-launch-mission");
    const inpMission = document.getElementById("swarm-mission-input");
    const toastMission = document.getElementById("mission-toast");
    const btnClearFeed = document.getElementById("btn-clear-swarm-feed");
    const btnRefreshSwarm = document.getElementById("btn-refresh-swarm");
    const btnModeAllInOne = document.getElementById("btn-mode-allinone");
    const btnModeDistributed = document.getElementById("btn-mode-distributed");
    const modeExplanation = document.getElementById("swarm-mode-explanation");
    const btnTriggerInspect = document.getElementById("btn-trigger-ui-inspect");
    const btnClearInspect = document.getElementById("btn-clear-ui-inspect");
    const visualAuditBadge = document.getElementById("visual-audit-badge");
    const visualAuditPreview = document.getElementById("visual-audit-preview");
    const visualAuditPreviewTxt = document.getElementById("visual-audit-preview-txt");

    let currentSwarmMode = "all_in_one";

    function setSwarmModeUI(mode) {
      currentSwarmMode = mode;
      if (btnModeAllInOne && btnModeDistributed) {
        if (mode === "all_in_one") {
          btnModeAllInOne.classList.add("active");
          btnModeDistributed.classList.remove("active");
          btnModeDistributed.style.background = "transparent";
          btnModeDistributed.style.color = "#94a3b8";
          if (modeExplanation) {
            modeExplanation.innerHTML = `<strong>Modo Mono-Pestaña Activo:</strong> Todo el equipo de especialistas (Arquitecto, Obrero, Diseñador con Ojos y QA) opera dentro de <em>una sola pestaña de chat</em> (ej. Gemini o DeepSeek) alternando turnos y ejecutando herramientas locales sin saltar de pestaña.`;
          }
        } else {
          btnModeDistributed.classList.add("active");
          btnModeAllInOne.classList.remove("active");
          btnModeAllInOne.style.background = "transparent";
          btnModeAllInOne.style.color = "#94a3b8";
          if (modeExplanation) {
            modeExplanation.innerHTML = `<strong>Modo Multi-Pestaña Activo:</strong> Las subtareas se delegan entre diferentes pestañas dedicadas (DeepSeek como Arquitecto, Gemini como Obrero, etc.) coordinadas por GHS.`;
          }
        }
      }
    }

    if (btnModeAllInOne) {
      btnModeAllInOne.addEventListener("click", () => {
        setSwarmModeUI("all_in_one");
        if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ type: "set_swarm_mode", mode: "all_in_one" });
        }
      });
    }

    if (btnModeDistributed) {
      btnModeDistributed.addEventListener("click", () => {
        setSwarmModeUI("distributed");
        if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ type: "set_swarm_mode", mode: "distributed" });
        }
      });
    }

    if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage({ type: "get_swarm_mode" }, (r) => {
        if (r && r.mode) setSwarmModeUI(r.mode);
      });
    }

    // ── Interactive Swarm Theater Canvas 2D (Procedural Vector Badges AAA) ──
    const theaterCanvas = document.getElementById("swarm-theater-canvas");
    let theaterCtx = theaterCanvas ? theaterCanvas.getContext("2d") : null;
    let theaterParticles = [];
    let theaterPulses = [];
    let theaterStars = [];
    let theaterAnimationId = null;

    const theaterNodes = {
      master: {
        x: 0.5,
        y: 0.16,
        name: "Maestro Orquestador",
        role: "master",
        color: "#a855f7",
        secondaryColor: "#fbbf24",
        active: true,
        assignedTool: "En espera de misión",
      },
      gem: {
        x: 0.31,
        y: 0.40,
        name: "GEM Builder",
        role: "gem",
        color: "#06b6d4",
        secondaryColor: "#38bdf8",
        active: false,
        assignedTool: "En espera de misión",
      },
      architect: {
        x: 0.14,
        y: 0.72,
        name: "Arquitecto",
        role: "architect",
        color: "#3b82f6",
        secondaryColor: "#38bdf8",
        active: false,
        assignedTool: "En espera de misión",
      },
      worker: {
        x: 0.50,
        y: 0.75,
        name: "Obrero FullStack",
        role: "worker",
        color: "#10b981",
        secondaryColor: "#34d399",
        active: false,
        assignedTool: "En espera de misión",
      },
      designer: {
        x: 0.84,
        y: 0.65,
        name: "Diseñador con Ojos",
        role: "ui_designer",
        color: "#ec4899",
        secondaryColor: "#f472b6",
        active: false,
        assignedTool: "En espera de misión",
      },
    };

    function updateToolbeltPill(role, toolShortName) {
      const activeTextEl = document.getElementById(`active-tool-${role}`);
      const badgeEl = document.getElementById(`badge-${role}`);
      if (activeTextEl) activeTextEl.textContent = toolShortName || "En espera";
      if (badgeEl) {
        badgeEl.textContent = "⚡ ACTIVO";
        badgeEl.style.boxShadow = "0 0 8px currentColor";
        setTimeout(() => {
          if (badgeEl) {
            badgeEl.textContent = "EN LÍNEA";
            badgeEl.style.boxShadow = "none";
          }
        }, 4000);
      }
      const container = document.getElementById(`pills-${role}`);
      if (!container) return;
      const pills = container.querySelectorAll(".tool-pill");
      pills.forEach((p) => {
        if (toolShortName && p.textContent.toLowerCase().includes(toolShortName.toLowerCase())) {
          p.className = `tool-pill active-${role}`;
        } else {
          p.className = "tool-pill";
        }
      });
    }

    function triggerTheaterPulse(fromKey, toKey, color = "#a855f7", label = "") {
      const from = theaterNodes[fromKey];
      const to = theaterNodes[toKey];
      if (!from || !to) return;
      theaterPulses.push({ from, to, progress: 0, speed: 0.018, color, label });
      for (let i = 0; i < 10; i++) {
        theaterParticles.push({
          x: from.x,
          y: from.y,
          vx: (Math.random() - 0.5) * 0.008,
          vy: (Math.random() - 0.5) * 0.008,
          alpha: 1,
          color,
          size: Math.random() * 2.5 + 1.5,
        });
      }
    }

    // ── Procedural Vector Badge Renderers (Cero Emojis Antiguos) ─────────
    function drawMasterApexBadge(ctx, cx, cy, radius, time, active) {
      const grad = ctx.createRadialGradient(cx, cy, 2, cx, cy, radius * 1.6);
      grad.addColorStop(0, "rgba(168, 85, 247, 0.45)");
      grad.addColorStop(1, "rgba(168, 85, 247, 0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.6, 0, Math.PI * 2);
      ctx.fill();

      // Rotating dashed quantum ring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(time * 0.8);
      ctx.beginPath();
      ctx.arc(0, 0, radius * 1.25, 0, Math.PI * 2);
      ctx.strokeStyle = active ? "#c084fc" : "rgba(168, 85, 247, 0.35)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 5]);
      ctx.stroke();
      ctx.restore();

      // Base shield
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = "#1b1035";
      ctx.fill();
      ctx.strokeStyle = active ? "#c084fc" : "#6b21a8";
      ctx.lineWidth = 2.5;
      ctx.shadowColor = "#a855f7";
      ctx.shadowBlur = active ? 18 : 6;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Faceted Cyber Crown
      ctx.save();
      ctx.translate(cx, cy);
      const bob = Math.sin(time * 2.5) * 1.5;
      ctx.translate(0, bob);

      ctx.beginPath();
      ctx.moveTo(-15, 6);
      ctx.lineTo(-13, -7);
      ctx.lineTo(-6, 0);
      ctx.lineTo(0, -12);
      ctx.lineTo(6, 0);
      ctx.lineTo(13, -7);
      ctx.lineTo(15, 6);
      ctx.closePath();
      const goldGrad = ctx.createLinearGradient(-15, -12, 15, 6);
      goldGrad.addColorStop(0, "#fef08a");
      goldGrad.addColorStop(0.5, "#f59e0b");
      goldGrad.addColorStop(1, "#b45309");
      ctx.fillStyle = goldGrad;
      ctx.fill();
      ctx.strokeStyle = "#fffbeb";
      ctx.lineWidth = 1.2;
      ctx.stroke();

      // Floating central apex diamond
      ctx.beginPath();
      ctx.moveTo(0, -16);
      ctx.lineTo(3.5, -12.5);
      ctx.lineTo(0, -9);
      ctx.lineTo(-3.5, -12.5);
      ctx.closePath();
      ctx.fillStyle = "#e9d5ff";
      ctx.shadowColor = "#e9d5ff";
      ctx.shadowBlur = 8;
      ctx.fill();
      ctx.shadowBlur = 0;

      // Side jewels
      ctx.fillStyle = "#38bdf8";
      ctx.beginPath();
      ctx.arc(-13, -7, 1.8, 0, Math.PI * 2);
      ctx.arc(13, -7, 1.8, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    function drawArchitectBlueprintBadge(ctx, cx, cy, radius, time, active) {
      const grad = ctx.createRadialGradient(cx, cy, 2, cx, cy, radius * 1.6);
      grad.addColorStop(0, "rgba(59, 130, 246, 0.45)");
      grad.addColorStop(1, "rgba(59, 130, 246, 0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.6, 0, Math.PI * 2);
      ctx.fill();

      // Rotating dashed telemetry ring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(-time * 0.7);
      ctx.beginPath();
      ctx.arc(0, 0, radius * 1.25, 0, Math.PI * 2);
      ctx.strokeStyle = active ? "#60a5fa" : "rgba(59, 130, 246, 0.35)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 6]);
      ctx.stroke();
      ctx.restore();

      // Base shield
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = "#0c182d";
      ctx.fill();
      ctx.strokeStyle = active ? "#38bdf8" : "#1e40af";
      ctx.lineWidth = 2.5;
      ctx.shadowColor = "#38bdf8";
      ctx.shadowBlur = active ? 18 : 6;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Isometric Blueprint Cube
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(time * 0.25);
      const s = 10.5;

      ctx.beginPath();
      ctx.moveTo(0, -s);
      ctx.lineTo(s * 0.86, -s * 0.5);
      ctx.lineTo(0, 0);
      ctx.lineTo(-s * 0.86, -s * 0.5);
      ctx.closePath();
      ctx.fillStyle = "rgba(56, 189, 248, 0.6)";
      ctx.fill();
      ctx.strokeStyle = "#7dd3fc";
      ctx.lineWidth = 1.2;
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(-s * 0.86, -s * 0.5);
      ctx.lineTo(0, 0);
      ctx.lineTo(0, s);
      ctx.lineTo(-s * 0.86, s * 0.5);
      ctx.closePath();
      ctx.fillStyle = "rgba(37, 99, 235, 0.65)";
      ctx.fill();
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(s * 0.86, -s * 0.5);
      ctx.lineTo(s * 0.86, s * 0.5);
      ctx.lineTo(0, s);
      ctx.closePath();
      ctx.fillStyle = "rgba(29, 78, 216, 0.75)";
      ctx.fill();
      ctx.stroke();

      // Nexus corner nodes
      ctx.fillStyle = "#bae6fd";
      [[0, -s], [s * 0.86, -s * 0.5], [s * 0.86, s * 0.5], [0, s], [-s * 0.86, s * 0.5], [-s * 0.86, -s * 0.5]].forEach(([nx, ny]) => {
        ctx.beginPath();
        ctx.arc(nx, ny, 1.8, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.restore();
    }

    function drawWorkerMatrixBadge(ctx, cx, cy, radius, time, active) {
      const grad = ctx.createRadialGradient(cx, cy, 2, cx, cy, radius * 1.6);
      grad.addColorStop(0, "rgba(16, 185, 129, 0.45)");
      grad.addColorStop(1, "rgba(16, 185, 129, 0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.6, 0, Math.PI * 2);
      ctx.fill();

      // Outer rotating gear ring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(time * 0.9);
      ctx.beginPath();
      ctx.arc(0, 0, radius * 1.25, 0, Math.PI * 2);
      ctx.strokeStyle = active ? "#34d399" : "rgba(16, 185, 129, 0.35)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 5]);
      ctx.stroke();
      ctx.restore();

      // Base shield
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = "#022c22";
      ctx.fill();
      ctx.strokeStyle = active ? "#34d399" : "#065f46";
      ctx.lineWidth = 2.5;
      ctx.shadowColor = "#10b981";
      ctx.shadowBlur = active ? 18 : 6;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Outer Tech Gear (Vector)
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(time * 1.1);

      const teeth = 8;
      const rOuter = 15;
      const rInner = 11;
      ctx.beginPath();
      for (let i = 0; i < teeth; i++) {
        const a1 = (i * 2 * Math.PI) / teeth;
        const a2 = a1 + Math.PI / teeth / 2;
        const a3 = a1 + Math.PI / teeth;
        const a4 = a1 + (3 * Math.PI) / teeth / 2;
        if (i === 0) ctx.moveTo(Math.cos(a1) * rOuter, Math.sin(a1) * rOuter);
        else ctx.lineTo(Math.cos(a1) * rOuter, Math.sin(a1) * rOuter);
        ctx.lineTo(Math.cos(a2) * rOuter, Math.sin(a2) * rOuter);
        ctx.lineTo(Math.cos(a3) * rInner, Math.sin(a3) * rInner);
        ctx.lineTo(Math.cos(a4) * rInner, Math.sin(a4) * rInner);
      }
      ctx.closePath();
      const gearGrad = ctx.createLinearGradient(-15, -15, 15, 15);
      gearGrad.addColorStop(0, "#34d399");
      gearGrad.addColorStop(1, "#059669");
      ctx.fillStyle = gearGrad;
      ctx.fill();
      ctx.strokeStyle = "#d1fae5";
      ctx.lineWidth = 1.2;
      ctx.stroke();

      // Counter-rotating inner hub
      ctx.rotate(-time * 2.2);
      ctx.beginPath();
      ctx.arc(0, 0, 5.5, 0, Math.PI * 2);
      ctx.fillStyle = "#064e3b";
      ctx.fill();
      ctx.strokeStyle = "#6ee7b7";
      ctx.lineWidth = 1.4;
      ctx.stroke();

      // Central Plasma Core
      ctx.beginPath();
      ctx.arc(0, 0, 2.8, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.shadowColor = "#34d399";
      ctx.shadowBlur = 8;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.restore();
    }

    function drawDesignerOpticBadge(ctx, cx, cy, radius, time, active) {
      const grad = ctx.createRadialGradient(cx, cy, 2, cx, cy, radius * 1.6);
      grad.addColorStop(0, "rgba(236, 72, 153, 0.45)");
      grad.addColorStop(1, "rgba(236, 72, 153, 0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.6, 0, Math.PI * 2);
      ctx.fill();

      // Rotating chromatic dashed ring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(-time * 1.0);
      ctx.beginPath();
      ctx.arc(0, 0, radius * 1.25, 0, Math.PI * 2);
      ctx.strokeStyle = active ? "#f472b6" : "rgba(236, 72, 153, 0.35)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.restore();

      // Base shield
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = "#3b0722";
      ctx.fill();
      ctx.strokeStyle = active ? "#f472b6" : "#831843";
      ctx.lineWidth = 2.5;
      ctx.shadowColor = "#ec4899";
      ctx.shadowBlur = active ? 18 : 6;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Optical Camera Aperture Blades
      ctx.save();
      ctx.translate(cx, cy);
      const blades = 6;
      const bRad = 14;
      ctx.rotate(time * 0.6);
      for (let i = 0; i < blades; i++) {
        ctx.save();
        ctx.rotate((i * 2 * Math.PI) / blades);
        ctx.beginPath();
        ctx.moveTo(bRad * 0.3, -bRad * 0.9);
        ctx.lineTo(bRad * 0.9, -bRad * 0.3);
        ctx.lineTo(bRad * 0.4, 0);
        ctx.closePath();
        ctx.fillStyle = i % 2 === 0 ? "rgba(244, 114, 182, 0.65)" : "rgba(219, 39, 119, 0.75)";
        ctx.fill();
        ctx.strokeStyle = "#fbcfe8";
        ctx.lineWidth = 0.8;
        ctx.stroke();
        ctx.restore();
      }

      // Central Camera Pupil / Lens
      ctx.beginPath();
      ctx.arc(0, 0, 5, 0, Math.PI * 2);
      ctx.fillStyle = "#0f172a";
      ctx.fill();
      ctx.strokeStyle = "#f472b6";
      ctx.lineWidth = 1.4;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(-1.5, -1.5, 1.4, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.fill();

      // Sweeping Laser Scanline
      const scanY = Math.sin(time * 3.5) * 10;
      ctx.beginPath();
      ctx.moveTo(-11, scanY);
      ctx.lineTo(11, scanY);
      ctx.strokeStyle = "rgba(244, 114, 182, 0.9)";
      ctx.lineWidth = 1.5;
      ctx.shadowColor = "#ec4899";
      ctx.shadowBlur = 8;
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.restore();
    }

    function drawGemBuilderBadge(ctx, cx, cy, radius, time, active) {
      const grad = ctx.createRadialGradient(cx, cy, 2, cx, cy, radius * 1.6);
      grad.addColorStop(0, "rgba(6, 182, 212, 0.45)");
      grad.addColorStop(1, "rgba(6, 182, 212, 0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.6, 0, Math.PI * 2);
      ctx.fill();

      // Rotating faceted ring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(time * 0.9);
      ctx.beginPath();
      ctx.arc(0, 0, radius * 1.25, 0, Math.PI * 2);
      ctx.strokeStyle = active ? "#38bdf8" : "rgba(6, 182, 212, 0.35)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 5]);
      ctx.stroke();
      ctx.restore();

      // Base shield
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = "#082f49";
      ctx.fill();
      ctx.strokeStyle = active ? "#38bdf8" : "#0284c7";
      ctx.lineWidth = 2.5;
      ctx.shadowColor = "#06b6d4";
      ctx.shadowBlur = active ? 18 : 6;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Faceted Gem / Diamond geometry
      ctx.save();
      ctx.translate(cx, cy);
      ctx.scale(0.85, 0.85);

      // Top polygon table
      ctx.beginPath();
      ctx.moveTo(-9, -4);
      ctx.lineTo(9, -4);
      ctx.lineTo(13, 2);
      ctx.lineTo(0, 13);
      ctx.lineTo(-13, 2);
      ctx.closePath();
      const gemGrad = ctx.createLinearGradient(-10, -4, 10, 13);
      gemGrad.addColorStop(0, "#38bdf8");
      gemGrad.addColorStop(0.5, "#06b6d4");
      gemGrad.addColorStop(1, "#0284c7");
      ctx.fillStyle = gemGrad;
      ctx.fill();
      ctx.strokeStyle = "#e0f2fe";
      ctx.lineWidth = 1.2;
      ctx.stroke();

      // Inner facets
      ctx.beginPath();
      ctx.moveTo(-5, -4);
      ctx.lineTo(0, 2);
      ctx.lineTo(5, -4);
      ctx.moveTo(0, 2);
      ctx.lineTo(0, 13);
      ctx.moveTo(-13, 2);
      ctx.lineTo(0, 2);
      ctx.lineTo(13, 2);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.7)";
      ctx.lineWidth = 0.9;
      ctx.stroke();

      // Gleam pulse on gem
      const gleamAlpha = 0.5 + 0.5 * Math.sin(time * 3);
      ctx.beginPath();
      ctx.arc(-4, -1, 1.8, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 255, 255, ${gleamAlpha})`;
      ctx.shadowColor = "#ffffff";
      ctx.shadowBlur = 6;
      ctx.fill();
      ctx.shadowBlur = 0;

      ctx.restore();
    }

    function initTheaterLoop() {
      if (!theaterCanvas || !theaterCtx) return;

      const render = () => {
        const w = theaterCanvas.width;
        const h = theaterCanvas.height;
        const time = Date.now() * 0.0025;
        theaterCtx.clearRect(0, 0, w, h);

        // Cosmic Starfield Background
        if (!theaterStars.length) {
          for (let i = 0; i < 40; i++) {
            theaterStars.push({
              x: Math.random(),
              y: Math.random(),
              size: Math.random() * 1.5 + 0.6,
              alpha: Math.random() * 0.6 + 0.2,
              pulseSpeed: Math.random() * 0.03 + 0.01,
            });
          }
        }
        theaterStars.forEach((s) => {
          s.alpha += Math.sin(Date.now() * s.pulseSpeed) * 0.004;
          const a = Math.max(0.12, Math.min(0.85, s.alpha));
          theaterCtx.beginPath();
          theaterCtx.arc(s.x * w, s.y * h, s.size, 0, Math.PI * 2);
          theaterCtx.fillStyle = `rgba(148, 163, 184, ${a})`;
          theaterCtx.fill();
        });

        // Curved Bezier Energy Splines
        const edges = [
          ["master", "gem"],
          ["gem", "architect"],
          ["master", "architect"],
          ["master", "worker"],
          ["master", "designer"],
          ["architect", "worker"],
          ["worker", "designer"],
        ];

        edges.forEach(([k1, k2]) => {
          const n1 = theaterNodes[k1];
          const n2 = theaterNodes[k2];
          const x1 = n1.x * w;
          const y1 = n1.y * h;
          const x2 = n2.x * w;
          const y2 = n2.y * h;
          const midX = (x1 + x2) / 2;
          const midY = (y1 + y2) / 2 + (k1 === "master" ? -16 : 14);

          // Energy spline track
          theaterCtx.beginPath();
          theaterCtx.moveTo(x1, y1);
          theaterCtx.quadraticCurveTo(midX, midY, x2, y2);
          theaterCtx.strokeStyle = "rgba(255, 255, 255, 0.09)";
          theaterCtx.lineWidth = 2;
          theaterCtx.stroke();

          // Flowing data quantum pulse along spline
          const pulseT = ((Date.now() * 0.0008) + (k1.charCodeAt(0) * 0.2)) % 1;
          const qx = (1 - pulseT) * (1 - pulseT) * x1 + 2 * (1 - pulseT) * pulseT * midX + pulseT * pulseT * x2;
          const qy = (1 - pulseT) * (1 - pulseT) * y1 + 2 * (1 - pulseT) * pulseT * midY + pulseT * pulseT * y2;

          theaterCtx.beginPath();
          theaterCtx.arc(qx, qy, 2.5, 0, Math.PI * 2);
          theaterCtx.fillStyle = n1.color;
          theaterCtx.shadowColor = n1.color;
          theaterCtx.shadowBlur = 8;
          theaterCtx.fill();
          theaterCtx.shadowBlur = 0;
        });

        // Active Event Pulses
        for (let i = theaterPulses.length - 1; i >= 0; i--) {
          const p = theaterPulses[i];
          p.progress += p.speed;
          if (p.progress >= 1) {
            theaterPulses.splice(i, 1);
            continue;
          }
          const px = (p.from.x + (p.to.x - p.from.x) * p.progress) * w;
          const py = (p.from.y + (p.to.y - p.from.y) * p.progress) * h;

          theaterCtx.beginPath();
          theaterCtx.arc(px, py, 5.5, 0, Math.PI * 2);
          theaterCtx.fillStyle = p.color;
          theaterCtx.shadowColor = p.color;
          theaterCtx.shadowBlur = 12;
          theaterCtx.fill();
          theaterCtx.shadowBlur = 0;

          if (p.label) {
            theaterCtx.font = "bold 10px monospace";
            theaterCtx.fillStyle = "#ffffff";
            theaterCtx.shadowColor = "#000000";
            theaterCtx.shadowBlur = 4;
            theaterCtx.fillText(p.label, px + 9, py - 4);
            theaterCtx.shadowBlur = 0;
          }
        }

        // Floating Sparks
        for (let i = theaterParticles.length - 1; i >= 0; i--) {
          const pt = theaterParticles[i];
          pt.x += pt.vx;
          pt.y += pt.vy;
          pt.alpha -= 0.02;
          if (pt.alpha <= 0) {
            theaterParticles.splice(i, 1);
            continue;
          }
          theaterCtx.beginPath();
          theaterCtx.arc(pt.x * w, pt.y * h, pt.size || 2, 0, Math.PI * 2);
          theaterCtx.fillStyle = pt.color;
          theaterCtx.globalAlpha = Math.max(0, pt.alpha);
          theaterCtx.fill();
          theaterCtx.globalAlpha = 1;
        }

        // Render Each Agent Node with Vector Badges & Tool HUD
        Object.keys(theaterNodes).forEach((k) => {
          const n = theaterNodes[k];
          const nx = n.x * w;
          const ny = n.y * h;
          const radius = 23;

          // Vector Badge Renderer per role
          if (k === "master") drawMasterApexBadge(theaterCtx, nx, ny, radius, time, n.active);
          else if (k === "gem") drawGemBuilderBadge(theaterCtx, nx, ny, radius, time, n.active);
          else if (k === "architect") drawArchitectBlueprintBadge(theaterCtx, nx, ny, radius, time, n.active);
          else if (k === "worker") drawWorkerMatrixBadge(theaterCtx, nx, ny, radius, time, n.active);
          else if (k === "designer") drawDesignerOpticBadge(theaterCtx, nx, ny, radius, time, n.active);

          // Node Name Label
          theaterCtx.font = "bold 11.5px sans-serif";
          theaterCtx.fillStyle = "#f8fafc";
          theaterCtx.textAlign = "center";
          theaterCtx.textBaseline = "middle";
          theaterCtx.fillText(n.name, nx, ny + 35);

          // Live Tool HUD Badge on Canvas
          const toolText = n.assignedTool || (n.active ? "Activo" : "En Espera");
          theaterCtx.font = "9.5px monospace";
          const badgeWidth = theaterCtx.measureText(toolText).width + 16;
          const badgeHeight = 18;
          const badgeX = nx - badgeWidth / 2;
          const badgeY = ny + 46;

          theaterCtx.beginPath();
          theaterCtx.roundRect(badgeX, badgeY, badgeWidth, badgeHeight, 5);
          theaterCtx.fillStyle = n.active ? "rgba(15, 23, 42, 0.85)" : "rgba(15, 23, 42, 0.5)";
          theaterCtx.fill();
          theaterCtx.strokeStyle = n.active ? n.color : "rgba(255, 255, 255, 0.1)";
          theaterCtx.lineWidth = 1;
          theaterCtx.stroke();

          theaterCtx.fillStyle = n.active ? (n.secondaryColor || n.color) : "#64748b";
          theaterCtx.fillText(toolText, nx, badgeY + 9);
        });

        theaterAnimationId = requestAnimationFrame(render);
      };

      if (theaterAnimationId) cancelAnimationFrame(theaterAnimationId);
      theaterAnimationId = requestAnimationFrame(render);
    }
    initTheaterLoop();

    // UI Inspection Trigger Button
    if (btnTriggerInspect) {
      btnTriggerInspect.addEventListener("click", () => {
        if (visualAuditBadge) {
          visualAuditBadge.textContent = "Tomando captura...";
          visualAuditBadge.style.background = "rgba(236, 72, 153, 0.25)";
          visualAuditBadge.style.color = "#f472b6";
        }
        btnTriggerInspect.disabled = true;
        triggerTheaterPulse("designer", "master", "#ec4899", "hermes_ui_inspect");
        if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({
            type: "call_tool",
            name: "hermes_ui_inspect",
            arguments: { url: "http://localhost:5173", wait_seconds: 3 },
          }, (res) => {
            btnTriggerInspect.disabled = false;
            if (visualAuditBadge) {
              visualAuditBadge.textContent = "✓ Captura realizada";
              visualAuditBadge.style.background = "rgba(34, 197, 94, 0.2)";
              visualAuditBadge.style.color = "#4ade80";
            }
            if (visualAuditPreview) {
              visualAuditPreview.innerHTML = '<span style="font-size: 11px; color: #4ade80; text-align: center; padding: 6px; font-weight: 600;">✓ Captura guardada en<br><code style="font-size: 9.5px; color: #cbd5e1;">Downloads/ghs_ui_snapshot.png</code></span>';
            }
            showToast("Inspección visual completada y guardada en Downloads.", "success");
          });
        }
      });
    }

    if (btnClearInspect) {
      btnClearInspect.addEventListener("click", () => {
        if (visualAuditBadge) {
          visualAuditBadge.textContent = "En reposo";
          visualAuditBadge.style.background = "rgba(236, 72, 153, 0.15)";
          visualAuditBadge.style.color = "#f472b6";
        }
        if (visualAuditPreview) {
          visualAuditPreview.innerHTML = '<span id="visual-audit-preview-txt" style="font-size: 11px; color: #64748b; text-align: center; padding: 6px;">Sin captura previa</span>';
        }
        showToast("Visor de auditoría visual reiniciado.", "info");
      });
    }

    function renderTabs(tabs) {
      if (!tabsContainer) return;
      if (!tabs || !tabs.length) {
        tabsContainer.innerHTML = `<span style="font-size: 11px; color: #64748b; font-style: italic;">No hay pestañas de IA abiertas. Abre DeepSeek, ChatGPT o Gemini.</span>`;
        return;
      }
      tabsContainer.innerHTML = tabs.map(t => {
        const roleName = t.role === "all_in_one"
          ? "🐝 Mono-Pestaña"
          : t.role === "gem"
          ? "💎 GEM Builder"
          : t.role === "architect"
          ? "🏛️ Arquitecto"
          : t.role === "worker"
          ? "⚙️ Obrero"
          : t.role === "ui_designer"
          ? "🎨 Diseñador UI"
          : "👤 Solo";
        return `<div class="tab-pill role-${t.role}"><strong>${escapeHtml(t.aiName)}</strong> · ${roleName}</div>`;
      }).join("");
    }

    function updateToolbeltPill(role, toolName) {
      if (!toolName) return;
      const cleanTool = toolName.replace(/^hermes_/, "");

      const activeEl = document.getElementById(`active-tool-${role}`);
      if (activeEl) {
        activeEl.textContent = toolName;
      }
      const badgeEl = document.getElementById(`badge-${role}`);
      if (badgeEl) {
        badgeEl.textContent = "EJECUTANDO";
        badgeEl.style.background = role === "master"
          ? "rgba(168,85,247,0.4)"
          : role === "gem"
          ? "rgba(6,182,212,0.4)"
          : role === "architect"
          ? "rgba(59,130,246,0.4)"
          : role === "worker"
          ? "rgba(16,185,129,0.4)"
          : "rgba(236,72,153,0.4)";
        setTimeout(() => {
          if (badgeEl && badgeEl.textContent === "EJECUTANDO") {
            badgeEl.textContent = "EN LÍNEA";
            badgeEl.style.background = "";
          }
        }, 3500);
      }

      const container = document.getElementById(`pills-${role}`);
      if (!container) return;

      const pills = container.querySelectorAll(".tool-pill");
      let matched = false;
      pills.forEach(p => {
        const text = p.textContent.trim();
        if (text === cleanTool || cleanTool.includes(text) || text.includes(cleanTool)) {
          p.className = `tool-pill active-${role}`;
          matched = true;
        } else {
          p.className = "tool-pill";
        }
      });

      if (!matched && cleanTool) {
        const customPill = document.createElement("span");
        customPill.className = `tool-pill active-${role}`;
        customPill.textContent = cleanTool;
        container.appendChild(customPill);
      }
    }

    function renderNodes(tabs, latestEvent) {
      const archTab = tabs && tabs.find(t => t.role === "architect");
      const workTab = tabs && tabs.find(t => t.role === "worker");
      const designerTab = tabs && tabs.find(t => t.role === "ui_designer");
      const gemTab = tabs && tabs.find(t => t.role === "gem" || t.role === "gem_builder");
      const allInOneTab = tabs && tabs.find(t => t.role === "all_in_one");

      // Update theater nodes activity
      theaterNodes.master.active = true;
      theaterNodes.gem.active = !!(gemTab || allInOneTab);
      theaterNodes.architect.active = !!(archTab || allInOneTab);
      theaterNodes.worker.active = !!(workTab || allInOneTab);
      theaterNodes.designer.active = !!(designerTab || allInOneTab);

      // Update badges
      const bMaster = document.getElementById("badge-master");
      const bGem = document.getElementById("badge-gem");
      const bArch = document.getElementById("badge-architect");
      const bWork = document.getElementById("badge-worker");
      const bDesign = document.getElementById("badge-designer");

      if (bMaster) bMaster.textContent = "EN LÍNEA";
      if (bGem) bGem.textContent = (gemTab || allInOneTab) ? "EN LÍNEA" : "EN ESPERA";
      if (bArch) bArch.textContent = (archTab || allInOneTab) ? "EN LÍNEA" : "EN ESPERA";
      if (bWork) bWork.textContent = (workTab || allInOneTab) ? "EN LÍNEA" : "EN ESPERA";
      if (bDesign) bDesign.textContent = (designerTab || allInOneTab) ? "EN LÍNEA" : "EN ESPERA";

      if (latestEvent) {
        if (latestEvent.type === "delegate") {
          theaterNodes.architect.assignedTool = "delegate_task";
          updateToolbeltPill("architect", "delegate_task");
          triggerTheaterPulse("architect", "worker", "#3b82f6", "delegate_task");
        } else if (latestEvent.type === "tool_call") {
          const rawTool = latestEvent.tool || "";
          const tool = rawTool.toLowerCase();
          if (tool.includes("ui_inspect") || tool.includes("computer_use")) {
            theaterNodes.designer.assignedTool = "ui_inspect";
            updateToolbeltPill("designer", rawTool);
            triggerTheaterPulse("designer", "master", "#ec4899", "inspección visual");
            if (visualAuditBadge) {
              visualAuditBadge.textContent = "✓ Inspección activa";
              visualAuditBadge.style.background = "rgba(236, 72, 153, 0.25)";
              visualAuditBadge.style.color = "#f472b6";
            }
            if (visualAuditPreview) {
              visualAuditPreview.innerHTML = '<span style="font-size: 11px; color: #ec4899; text-align: center; padding: 6px;">✓ Captura en Downloads/ghs_ui_snapshot.png</span>';
            }
          } else if (tool.includes("blueprint") || tool.includes("gem")) {
            theaterNodes.gem.assignedTool = "gem_blueprint_ready";
            updateToolbeltPill("gem", rawTool);
            triggerTheaterPulse("gem", "master", "#06b6d4", "blueprint");
          } else if (tool.includes("skills_list") || tool.includes("web_search") || tool.includes("tools_list") || tool.includes("delegate")) {
            theaterNodes.architect.assignedTool = rawTool.replace(/^hermes_/, "");
            updateToolbeltPill("architect", rawTool);
            triggerTheaterPulse("architect", "master", "#3b82f6", rawTool);
          } else {
            // Build, files and terminal operations
            theaterNodes.worker.assignedTool = rawTool.replace(/^hermes_/, "") || "terminal";
            updateToolbeltPill("worker", rawTool || "terminal");
            triggerTheaterPulse("worker", "master", "#10b981", rawTool);
          }
        } else if (latestEvent.type === "report") {
          theaterNodes.worker.assignedTool = "task_report";
          updateToolbeltPill("worker", "task_report");
          triggerTheaterPulse("worker", "architect", "#10b981", "task_report");
        } else if (latestEvent.type === "failover") {
          theaterNodes.master.assignedTool = "hot_swap_failover";
          updateToolbeltPill("master", "hot_swap_failover");
          triggerTheaterPulse("master", "worker", "#f59e0b", "RELEVO HOT-SWAP");
        } else if (latestEvent.type === "mission_start") {
          theaterNodes.master.assignedTool = "skills_list";
          updateToolbeltPill("master", "skills_list");
        } else if (latestEvent.type === "mission_complete") {
          if (visualAuditBadge) {
            visualAuditBadge.textContent = "✓ Auditoría completada";
            visualAuditBadge.style.background = "rgba(34, 197, 94, 0.2)";
            visualAuditBadge.style.color = "#4ade80";
          }
        }
      }

      if (archAi && archStatus) {
        if (archTab) {
          archAi.textContent = `${archTab.aiName}`;
          archStatus.textContent = "Conectado & Listo para Planificar";
          archStatus.style.background = "rgba(59, 130, 246, 0.15)";
          archStatus.style.color = "#93c5fd";
        } else {
          archAi.textContent = "Sin asignar (Abre DeepSeek / Claude)";
          archStatus.textContent = "Esperando pestaña con rol Arquitecto";
          archStatus.style.background = "rgba(255, 255, 255, 0.05)";
          archStatus.style.color = "#64748b";
        }
      }

      if (workAi && workStatus) {
        if (workTab) {
          workAi.textContent = `${workTab.aiName} (Powers: PC/Files/SQLite/Blender)`;
          workStatus.textContent = "Conectado & Listo para Ejecutar";
          workStatus.style.background = "rgba(16, 185, 129, 0.15)";
          workStatus.style.color = "#6ee7b7";
        } else {
          workAi.textContent = "Sin asignar (Abre ChatGPT / Gemini)";
          workStatus.textContent = "Esperando pestaña con rol Obrero";
          workStatus.style.background = "rgba(255, 255, 255, 0.05)";
          workStatus.style.color = "#64748b";
        }
      }

      // Flow arrow animation based on latest event
      if (flowArrow) {
        if (latestEvent && latestEvent.type === "delegate") {
          flowArrow.className = "arrow-line flow-delegate";
          if (archStatus) archStatus.textContent = `Delegando subtarea #${latestEvent.subtaskId}...`;
          if (workStatus) workStatus.textContent = `Recibiendo instrucciones de subtarea #${latestEvent.subtaskId}...`;
        } else if (latestEvent && latestEvent.type === "tool_call") {
          flowArrow.className = "arrow-line";
          if (workStatus) workStatus.textContent = `Ejecutando comando: ${latestEvent.tool}...`;
        } else if (latestEvent && latestEvent.type === "report") {
          flowArrow.className = "arrow-line flow-report";
          if (workStatus) workStatus.textContent = `Subtarea #${latestEvent.subtaskId} reportada al Arquitecto`;
          if (archStatus) archStatus.textContent = `Evaluando reporte de subtarea #${latestEvent.subtaskId}...`;
        } else if (latestEvent && latestEvent.type === "mission_complete") {
          flowArrow.className = "arrow-line";
          if (archStatus) archStatus.textContent = "Misión global completada con éxito 🏆";
          if (workStatus) workStatus.textContent = "En espera de nueva misión";
        } else {
          flowArrow.className = "arrow-line";
        }
      }

      if (overallStatus) {
        const archCount = tabs ? tabs.filter(t => t.role === "architect").length : 0;
        const workCount = tabs ? tabs.filter(t => t.role === "worker").length : 0;
        overallStatus.textContent = `● Enjambre Activo (${archCount} Arquitecto${archCount === 1 ? "" : "s"}, ${workCount} Obrero${workCount === 1 ? "" : "s"})`;
      }
    }

    function renderFeed(events) {
      if (!feed) return;
      if (!events || !events.length) {
        feed.innerHTML = `
          <div id="swarm-feed-empty" style="text-align: center; padding: 32px 16px; color: #64748b;">
            <div style="font-size: 32px; margin-bottom: 8px;">🐝</div>
            <div style="font-size: 13px; font-weight: 500; color: #94a3b8;">El enjambre está a la espera de misiones</div>
            <div style="font-size: 11px; margin-top: 4px;">Cuando el Arquitecto delegue tareas al Obrero, los eventos en tiempo real aparecerán aquí.</div>
          </div>`;
        return;
      }

      feed.innerHTML = events.map(ev => {
        let badgeClass = "badge-delegate";
        let badgeText = "DELEGACIÓN";
        let itemClass = "feed-delegate";
        let headerTitle = "";
        let bodyText = "";
        let extraHtml = "";

        if (ev.type === "delegate") {
          badgeClass = "badge-delegate";
          badgeText = "DELEGACIÓN";
          itemClass = "feed-delegate";
          headerTitle = `<strong>🏛️ [Arquitecto ${escapeHtml(ev.fromAi || "")}]</strong> delegó Subtarea #${escapeHtml(String(ev.subtaskId || "1"))} a <strong>[Obrero ${escapeHtml(ev.toAi || "")}]</strong>`;
          bodyText = (ev.projectDir ? `Directorio: ${ev.projectDir}\n` : "") + (ev.task || "");
        } else if (ev.type === "tool_call") {
          badgeClass = "badge-tool";
          badgeText = "HERRAMIENTA PC";
          itemClass = "feed-tool";
          headerTitle = `<strong>⚙️ [Obrero ${escapeHtml(ev.aiName || "")}]</strong> ejecutó comando local: <code>${escapeHtml(ev.tool || "")}</code>`;
          bodyText = JSON.stringify(ev.params || {}, null, 2);
        } else if (ev.type === "report") {
          badgeClass = ev.success ? "badge-report" : "badge-error";
          badgeText = ev.success ? "REPORTE ÉXITO" : "REPORTE ERROR";
          itemClass = ev.success ? "feed-report" : "feed-error";
          headerTitle = `<strong>📨 [Obrero ${escapeHtml(ev.fromAi || "")}]</strong> reportó Subtarea #${escapeHtml(String(ev.subtaskId || "1"))} a [Arquitecto]`;
          bodyText = ev.report || "";
        } else if (ev.type === "mission_start") {
          badgeClass = "badge-delegate";
          badgeText = "INICIO MISIÓN";
          itemClass = "feed-delegate";
          headerTitle = `<strong>🚀 Misión iniciada para Arquitecto [${escapeHtml(ev.aiName || "")}]</strong>`;
          bodyText = ev.prompt || "";
        } else if (ev.type === "mission_complete") {
          badgeClass = "badge-complete";
          badgeText = "MISIÓN COMPLETADA";
          itemClass = "feed-complete";
          headerTitle = `<strong>🏆 Misión Global Finalizada con Éxito [${escapeHtml(ev.aiName || "Arquitecto")}]</strong>`;
          bodyText = ev.summary || "";
          const targetUrl = ev.url || (bodyText.match(/https?:\/\/[^\s\)]+/) || [])[0] || "";
          if (targetUrl) {
            extraHtml = `
              <div style="margin-top: 12px; display: flex; align-items: center; gap: 10px;">
                <a href="${escapeHtml(targetUrl)}" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; padding: 10px 20px; background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: #ffffff; text-decoration: none; border-radius: 8px; font-weight: 700; font-size: 13px; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.35); transition: transform 0.2s, box-shadow 0.2s;">
                  <span>🌐 Abrir Proyecto en Vivo (${escapeHtml(targetUrl)})</span>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                </a>
              </div>
            `;
          }
        } else if (ev.type === "error") {
          badgeClass = "badge-error";
          badgeText = "ALERTA";
          itemClass = "feed-error";
          headerTitle = `<strong>⚠️ Alerta del Enjambre [${escapeHtml(ev.fromAi || "Sistema")}]</strong>`;
          bodyText = ev.message || "";
        }

        return `
          <div class="feed-item ${itemClass}">
            <div class="feed-header">
              <div>
                <span class="feed-badge ${badgeClass}" style="margin-right: 8px;">${badgeText}</span>
                ${headerTitle}
              </div>
              <span class="feed-time">${escapeHtml(ev.timestamp || "")}</span>
            </div>
            <pre class="feed-payload">${escapeHtml(bodyText)}</pre>
            ${extraHtml || ""}
          </div>
        `;
      }).join("");
    }

    function escapeHtml(str) {
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function refreshSwarmState() {
      if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "get_swarm_state" }, (res) => {
          if (chrome.runtime.lastError || !res || !res.ok) return;
          renderTabs(res.tabs);
          renderNodes(res.tabs, res.events && res.events[0]);
          renderFeed(res.events);
          if (res.state && res.state.masterBlueprint && txtBlueprint && !txtBlueprint.value) {
            txtBlueprint.value = res.state.masterBlueprint;
          }
          if (res.state && window.GHS_SessionManager) {
            window.GHS_SessionManager.update(res.state);
          }
        });
      }
    }

    // Extension runtime messaging
    if (window.chrome && chrome.runtime && chrome.runtime.onMessage) {
      chrome.runtime.onMessage.addListener((msg) => {
        if (msg && msg.type === "new_system_log" && msg.log) {
          appendLog(msg.log);
        }
        if (msg && msg.type === "swarm_state_update") {
          renderTabs(msg.tabs);
          renderNodes(msg.tabs, msg.latestEvent || (msg.events && msg.events[0]));
          renderFeed(msg.events);
          if (msg.state && window.GHS_SessionManager) {
            window.GHS_SessionManager.update(msg.state);
          }
        }
        if (msg && msg.type === "zs-status" && msg.swarm && msg.swarm.tabs) {
          renderTabs(msg.swarm.tabs);
          renderNodes(msg.swarm.tabs);
        }
        if (msg && msg.type === "swarm_blueprint_updated") {
          if (txtBlueprint) {
            txtBlueprint.value = msg.blueprint || "";
            if (modalBlueprint) {
              modalBlueprint.style.display = "block";
              modalBlueprint.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }
          }
          const badge = document.getElementById("blueprint-pipeline-badge");
          if (badge) {
            badge.textContent = "2/2 EN CONSTRUCCIÓN POR EL OBRERO";
            badge.style.background = "rgba(16,185,129,0.2)";
            badge.style.color = "#34d399";
          }
          triggerTheaterPulse("gem", "architect", "#06b6d4", "Blueprint Listo ➔ Construcción");
          showToast("💎 ¡Blueprint Maestro generado! Traspasado automáticamente a la cuadrilla técnica para construir.", "success");
          if (inpMission) inpMission.value = "";
          refreshSwarmState();
        }
      });
    }

    // ── GEM Agent Pipeline Toggle & Configuration ─────────────────────
    const chkUseGem = document.getElementById("chk-use-gem");
    if (chkUseGem) {
      chkUseGem.checked = localStorage.getItem("zs_use_gem") !== "false";
      chkUseGem.addEventListener("change", () => {
        localStorage.setItem("zs_use_gem", chkUseGem.checked ? "true" : "false");
      });
    }

    const modalGemConfig = document.getElementById("gem-prompt-modal");
    const btnOpenGemConfig = document.getElementById("btn-open-gem-config");
    const btnCloseGemModal = document.getElementById("btn-close-gem-modal");
    const btnCancelGemModal = document.getElementById("btn-cancel-gem-modal");
    const btnSaveGemPrompt = document.getElementById("btn-save-gem-prompt");
    const btnResetGemPrompt = document.getElementById("btn-reset-gem-prompt");
    const txtGemPrompt = document.getElementById("gem-prompt-textarea");

    if (btnOpenGemConfig && modalGemConfig && txtGemPrompt) {
      btnOpenGemConfig.addEventListener("click", () => {
        const stored = localStorage.getItem("zs_gem_custom_prompt");
        const defaultPrompt = (typeof GHSSwarm !== "undefined" && GHSSwarm.DEFAULT_GEM_PROMPT) || "";
        txtGemPrompt.value = stored || defaultPrompt;
        modalGemConfig.style.display = "flex";
      });

      const closeGemModal = () => { modalGemConfig.style.display = "none"; };
      if (btnCloseGemModal) btnCloseGemModal.addEventListener("click", closeGemModal);
      if (btnCancelGemModal) btnCancelGemModal.addEventListener("click", closeGemModal);

      if (btnSaveGemPrompt) {
        btnSaveGemPrompt.addEventListener("click", () => {
          const val = txtGemPrompt.value.trim();
          localStorage.setItem("zs_gem_custom_prompt", val);
          if (window.chrome && chrome.storage && chrome.storage.local) {
            chrome.storage.local.set({ zs_gem_custom_prompt: val });
          }
          closeGemModal();
          showToast("✓ Instrucciones del Agente GEM guardadas con éxito.", "success");
        });
      }

      if (btnResetGemPrompt) {
        btnResetGemPrompt.addEventListener("click", () => {
          if (confirm("¿Restaurar las instrucciones del Agente GEM a su configuración original?")) {
            localStorage.removeItem("zs_gem_custom_prompt");
            if (window.chrome && chrome.storage && chrome.storage.local) {
              chrome.storage.local.remove("zs_gem_custom_prompt");
            }
            const defaultPrompt = (typeof GHSSwarm !== "undefined" && GHSSwarm.DEFAULT_GEM_PROMPT) || "";
            txtGemPrompt.value = defaultPrompt;
            showToast("↺ Prompt Maestro restaurado por defecto.", "info");
          }
        });
      }
    }

    // ── Unified Mission Launcher (Single Action) ──────────────────────
    const modalBlueprint = document.getElementById("blueprint-modal");
    const txtBlueprint = document.getElementById("blueprint-content");
    const btnCopyBlueprint = document.getElementById("btn-copy-blueprint");
    const btnCloseBlueprint = document.getElementById("btn-close-blueprint");
    const btnDispatchWorker = document.getElementById("btn-dispatch-worker");

    if (btnDispatchWorker) {
      btnDispatchWorker.addEventListener("click", () => {
        if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) {
          showToast("La extensión no está disponible.", "error");
          return;
        }
        btnDispatchWorker.disabled = true;
        btnDispatchWorker.textContent = "⏳ Conectando con Obrero...";
        chrome.runtime.sendMessage({ type: "swarm_direct_dispatch_worker" }, (res) => {
          btnDispatchWorker.disabled = false;
          btnDispatchWorker.textContent = "⚡ Despachar al Obrero (Gemini)";
          if (res && res.ok) {
            showToast("✓ Subtarea enviada exitosamente a la pestaña del Obrero. ¡Construcción iniciada!", "success");
            triggerTheaterPulse("master", "worker", "#10b981", "Subtarea Despachada");
          } else {
            showToast(`Error despachando al Obrero: ${(res && res.error) || "Revisa que la pestaña del Obrero esté abierta y activa."}`, "error");
          }
        });
      });
    }

    if (btnLaunch && inpMission) {
      const launch = () => {
        const text = inpMission.value.trim();
        if (!text) {
          showToast("Escribe una descripción u objetivo para la misión.", "warn");
          inpMission.focus();
          return;
        }
        if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) {
          showToast("El control del enjambre requiere la extensión de Chrome activa.", "error");
          return;
        }

        const useGem = chkUseGem ? chkUseGem.checked : true;
        const customPrompt = localStorage.getItem("zs_gem_custom_prompt") || "";

        btnLaunch.disabled = true;
        btnLaunch.textContent = useGem ? "Razonando con GEM..." : "Lanzando...";

        if (useGem) {
          triggerTheaterPulse("master", "gem", "#06b6d4", "Razonando Nicho");
          showToast("💎 [Paso 1/2] Agente GEM activado: Analizando nicho y redactando Blueprint Maestro AAA...", "info");

          if (modalBlueprint && txtBlueprint) {
            txtBlueprint.value = `💎 [AGENTE GEM BUILDER EN EJECUCIÓN]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Analizando nicho: "${text}"
Psicología de usuario, paleta cromática de autor y arquitectura técnica...

La IA está redactando el Blueprint Maestro World-Class de 7 Secciones.
Tan pronto finalice, se traspasará AUTOMÁTICAMENTE al equipo de construcción (Arquitecto/Obrero) para empezar a crear los archivos en Windows.
Por favor espera unos segundos mientras la IA razona y estructura el blueprint...`;
            modalBlueprint.style.display = "block";
            modalBlueprint.scrollIntoView({ behavior: "smooth", block: "nearest" });
            const badge = document.getElementById("blueprint-pipeline-badge");
            if (badge) {
              badge.textContent = "1/2 REDACTANDO BLUEPRINT";
              badge.style.background = "rgba(6,182,212,0.2)";
              badge.style.color = "#38bdf8";
            }
          }
        } else {
          triggerTheaterPulse("master", "architect", "#a855f7", "Misión iniciada");
          showToast("🚀 Misión iniciada directamente en el Enjambre (Modo GEM omitido)...", "info");
        }

        chrome.runtime.sendMessage({
          type: "swarm_start_mission",
          prompt: text,
          mode: currentSwarmMode,
          useGem: useGem,
          customGemPrompt: customPrompt
        }, (res) => {
          btnLaunch.disabled = false;
          btnLaunch.innerHTML = "<span>🚀 Lanzar Misión</span>";
          if (chrome.runtime.lastError || !res || !res.ok) {
            showToast((res && res.error) || "Error lanzando misión.", "error");
          } else {
            if (!useGem) {
              const startMsg = currentSwarmMode === "all_in_one"
                ? "🚀 Misión iniciada en Modo Mono-Pestaña (All-in-One)..."
                : "🚀 Misión iniciada en Modo Multi-Pestaña Coordinado...";
              showToast(startMsg, "success");
              inpMission.value = "";
              refreshSwarmState();
            }
          }
        });
      };

      btnLaunch.addEventListener("click", launch);
      inpMission.addEventListener("keydown", (e) => {
        if (e.key === "Enter") launch();
      });
    }

    if (btnCloseBlueprint && modalBlueprint) {
      btnCloseBlueprint.addEventListener("click", () => {
        modalBlueprint.style.display = "none";
      });
    }

    if (btnCopyBlueprint && txtBlueprint) {
      btnCopyBlueprint.addEventListener("click", () => {
        navigator.clipboard.writeText(txtBlueprint.value).then(() => {
          btnCopyBlueprint.textContent = "✓ ¡Copiado!";
          setTimeout(() => { btnCopyBlueprint.textContent = "📋 Copiar para AI Studio"; }, 2000);
          showToast("✓ Blueprint copiado al portapapeles. Listo para AI Studio o tu chat de IA.", "success");
        });
      });
    }

    function showToast(msg, type = "info") {
      if (!toastMission) return;
      toastMission.textContent = msg;
      toastMission.style.display = "block";
      if (type === "error") {
        toastMission.style.background = "rgba(239, 68, 68, 0.15)";
        toastMission.style.color = "#f87171";
        toastMission.style.border = "1px solid rgba(239, 68, 68, 0.3)";
      } else if (type === "success") {
        toastMission.style.background = "rgba(16, 185, 129, 0.15)";
        toastMission.style.color = "#34d399";
        toastMission.style.border = "1px solid rgba(16, 185, 129, 0.3)";
      } else {
        toastMission.style.background = "rgba(245, 158, 11, 0.15)";
        toastMission.style.color = "#fbbf24";
        toastMission.style.border = "1px solid rgba(245, 158, 11, 0.3)";
      }
      setTimeout(() => {
        toastMission.style.display = "none";
      }, 5000);
    }

    // Clear Feed
    if (btnClearFeed) {
      btnClearFeed.addEventListener("click", () => {
        if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ type: "swarm_clear_events" }, () => {
            refreshSwarmState();
          });
        }
      });
    }

    // Refresh Swarm State
    if (btnRefreshSwarm) {
      btnRefreshSwarm.addEventListener("click", () => {
        refreshSwarmState();
      });
    }

    // Initial load + periodic poll
    refreshSwarmState();
    setInterval(refreshSwarmState, 3500);
  }

  // ── Local Dev Servers & Emergency Stop Manager ─────────────────────
  function initDevServerManager() {
    const listEl = document.getElementById("local-servers-list");
    const dedicatedListEl = document.getElementById("dedicated-servers-list");
    const countBadge = document.getElementById("servers-count-badge");
    const navCountBadge = document.getElementById("nav-servers-counter");
    const topBadge = document.getElementById("top-dev-server-badge");
    const topLabel = document.getElementById("top-servers-label");
    const topDot = document.getElementById("server-status-dot");
    const btnRefresh = document.getElementById("btn-refresh-servers");
    const btnRefreshDedicated = document.getElementById("btn-refresh-dedicated-servers");
    const btnStopAll = document.getElementById("btn-stop-all-servers");
    const btnStopAllDedicated = document.getElementById("btn-stop-all-dedicated-servers");
    const btnEmergencyStop = document.getElementById("btn-emergency-stop");

    if (topBadge) {
      topBadge.style.cursor = "pointer";
      topBadge.addEventListener("click", () => {
        const navItem = document.getElementById("nav-item-servers");
        if (navItem) navItem.click();
      });
    }

    function executeDevServerTool(args, callback) {
      if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({
          type: "call_tool",
          name: "hermes_dev_server",
          arguments: args
        }, (res) => {
          let text = res && res.text ? res.text : (res && res.error ? res.error : "");
          let parsed = null;
          try {
            parsed = JSON.parse(text);
          } catch (e) {
            try {
              const m = text.match(/\{[\s\S]*\}/);
              if (m) parsed = JSON.parse(m[0]);
            } catch (err) {}
          }
          if (callback) callback(parsed, text);
          if (window.GHS_SessionManager && Array.isArray(parsed)) {
            window.GHS_SessionManager.update(null, parsed);
          }
        });
      }
    }

    function renderServersHtml(servers, isCompact = false) {
      if (!servers || servers.length === 0) {
        return `<div class="empty-servers-msg" style="padding: 18px 0; color: var(--ink-muted); font-size: 13px;">No hay servidores de desarrollo activos en este momento. Cuando la IA o tú ejecuten un proyecto con Vite o Node.js, aparecerá aquí automáticamente.</div>`;
      }
      return servers.map(s => `
        <div class="dev-server-card" style="display: flex; align-items: center; justify-content: space-between; padding: ${isCompact ? '12px 16px' : '16px 20px'}; background: var(--surface-card); border: 1px solid var(--border-stone); border-radius: var(--radius-md); box-shadow: var(--shadow-card); margin-bottom: 10px;">
          <div style="display: flex; align-items: center; gap: 14px;">
            <div style="width: 38px; height: 38px; border-radius: 8px; background: rgba(16, 185, 129, 0.12); color: #10b981; display: flex; align-items: center; justify-content: center; font-size: 18px;">
              🌐
            </div>
            <div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-family: var(--font-display); font-size: 15px; font-weight: 700; color: var(--ink-primary);">${s.project || 'Proyecto Node/Vite'}</span>
                <span style="font-family: var(--font-mono); font-size: 10px; padding: 2px 8px; border-radius: 4px; background: var(--olive-light); color: var(--olive); font-weight: 700; border: 1px solid var(--olive-border);">ACTIVO ● :${s.port}</span>
              </div>
              <div style="font-size: 11.5px; color: var(--ink-muted); display: flex; gap: 14px; margin-top: 4px; align-items: center; flex-wrap: wrap;">
                <span>PID: <code style="font-family: var(--font-mono); color: var(--ink-primary); background: var(--surface-elevated); padding: 1px 4px; border-radius: 3px;">${s.pid}</code></span>
                <span>Ruta: <code style="font-family: var(--font-mono); color: var(--ink-secondary);">${s.cwd || 'Downloads'}</code></span>
                <a href="${s.url}" target="_blank" style="color: var(--terracotta); text-decoration: none; font-weight: 600; display: inline-flex; align-items: center; gap: 4px;">
                  <span>🔗</span> Abrir ${s.url}
                </a>
              </div>
            </div>
          </div>
          <div style="display: flex; gap: 8px; align-items: center;">
            <a href="${s.url}" target="_blank" class="btn-secondary" style="font-size: 11.5px; padding: 6px 12px; text-decoration: none;">
              Abrir Web
            </a>
            <button class="btn-stop-single-server btn-ghost danger-text" data-pid="${s.pid}" data-port="${s.port}" style="font-size: 11.5px; font-weight: 600; padding: 6px 12px; cursor: pointer; display: flex; align-items: center; gap: 4px; border: 1px solid rgba(153, 27, 27, 0.2); border-radius: 6px;">
              ⏹️ Detener
            </button>
          </div>
        </div>
      `).join("");
    }

    function wireStopButtons(container) {
      if (!container) return;
      container.querySelectorAll(".btn-stop-single-server").forEach(btn => {
        btn.addEventListener("click", () => {
          const port = btn.dataset.port;
          const pid = btn.dataset.pid;
          btn.disabled = true;
          btn.textContent = "Deteniendo...";
          executeDevServerTool({ action: "stop", port: port ? parseInt(port, 10) : undefined, pid: pid ? parseInt(pid, 10) : undefined }, () => {
            appendLog(`[DEV SERVER] Servidor detenido en puerto ${port} (PID ${pid})`);
            refreshServers();
          });
        });
      });
    }

    function refreshServers() {
      executeDevServerTool({ action: "list" }, (data) => {
        const servers = (data && data.servers) ? data.servers : [];
        const count = servers.length;

        // Update Top Navigation Badge
        if (topBadge && topLabel && topDot) {
          if (count > 0) {
            topDot.style.background = "#10b981";
            topDot.className = "status-dot pulsing";
            const firstPort = servers[0].port ? `:${servers[0].port}` : "";
            topLabel.textContent = `${count} Servidor${count === 1 ? '' : 'es'} Activo${count === 1 ? '' : 's'} (${firstPort})`;
            topBadge.style.borderColor = "rgba(16, 185, 129, 0.4)";
            topBadge.style.color = "#047857";
            topBadge.style.background = "rgba(16, 185, 129, 0.12)";
          } else {
            topDot.style.background = "#94a3b8";
            topDot.className = "status-dot";
            topLabel.textContent = "0 Servidores Web";
            topBadge.style.borderColor = "var(--border-stone)";
            topBadge.style.color = "var(--ink-muted)";
            topBadge.style.background = "var(--surface-subtle)";
          }
        }

        // Update Sidebar Counter
        if (navCountBadge) {
          navCountBadge.textContent = count;
          navCountBadge.style.background = count > 0 ? "rgba(16, 185, 129, 0.2)" : "var(--surface-elevated)";
          navCountBadge.style.color = count > 0 ? "#10b981" : "var(--ink-muted)";
        }

        // Update Swarm Tab Count Badge
        if (countBadge) {
          countBadge.textContent = `${count} activo${count === 1 ? '' : 's'}`;
          countBadge.style.color = count > 0 ? "var(--olive)" : "var(--terracotta)";
          countBadge.style.background = count > 0 ? "var(--olive-light)" : "var(--terracotta-light)";
        }

        // Render in Swarm Tab
        if (listEl) {
          listEl.innerHTML = renderServersHtml(servers, true);
          wireStopButtons(listEl);
        }

        // Render in Dedicated Tab
        if (dedicatedListEl) {
          dedicatedListEl.innerHTML = renderServersHtml(servers, false);
          wireStopButtons(dedicatedListEl);
        }
      });
    }

    if (btnRefresh) btnRefresh.addEventListener("click", refreshServers);
    if (btnRefreshDedicated) btnRefreshDedicated.addEventListener("click", refreshServers);

    const handleStopAll = (btn) => {
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Deteniendo todos...";
      }
      executeDevServerTool({ action: "stop_all" }, () => {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "🛑 Detener Todos los Servidores";
        }
        appendLog("[DEV SERVER] Todos los servidores locales han sido detenidos.");
        refreshServers();
      });
    };

    if (btnStopAll) btnStopAll.addEventListener("click", () => handleStopAll(btnStopAll));
    if (btnStopAllDedicated) btnStopAllDedicated.addEventListener("click", () => handleStopAll(btnStopAllDedicated));

    if (btnEmergencyStop) {
      btnEmergencyStop.addEventListener("click", () => {
        btnEmergencyStop.disabled = true;
        btnEmergencyStop.textContent = "🛑 DETENIENDO...";
        executeDevServerTool({ action: "stop_all" }, () => {
          btnEmergencyStop.disabled = false;
          btnEmergencyStop.textContent = "🛑 Parada de Emergencia";
          appendLog("[EMERGENCIA] Parada de emergencia ejecutada: todos los dev servers locales y procesos huérfanos han sido aniquilados.");
          alert("🛑 PARADA DE EMERGENCIA COMPLETADA:\nTodos los servidores de desarrollo Vite/Node.js activos han sido terminados.");
          refreshServers();
        });
      });
    }

    // Initial check and auto-refresh every 12s only when the tab is visible
    refreshServers();
    setInterval(() => {
      if (!document.hidden) refreshServers();
    }, 12000);
  }

  // ── Initialize App ────────────────────────────────────────────────
  
  // ── MCP Superpowers & Design Skills Toggles ───────────────────────
  function initMcpToggles() {
    const cards = document.querySelectorAll(".power-card");
    const updateCount = () => {
      const activeCount = Array.from(document.querySelectorAll(".power-card input[type='checkbox']:checked")).length;
      const mcpCountEl = document.getElementById("mcp-count");
      if (mcpCountEl) mcpCountEl.textContent = activeCount;
    };

    cards.forEach((card) => {
      const serverId = card.getAttribute("data-server");
      const checkbox = card.querySelector("input[type='checkbox']");
      const statusEl = card.querySelector(".card-status");
      if (!checkbox || !serverId) return;

      // Restore saved preference if any
      const saved = localStorage.getItem("ghs_mcp_" + serverId);
      if (saved !== null) {
        checkbox.checked = saved === "true";
      }

      // Update initial status text
      if (statusEl) {
        if (checkbox.checked) {
          statusEl.textContent = statusEl.textContent.includes("Conectado") ? statusEl.textContent : "● Activo";
          statusEl.style.color = "#34d399";
        } else {
          statusEl.textContent = "○ Inactivo";
          statusEl.style.color = "#94a3b8";
        }
      }

      checkbox.addEventListener("change", () => {
        localStorage.setItem("ghs_mcp_" + serverId, checkbox.checked ? "true" : "false");
        if (statusEl) {
          statusEl.textContent = checkbox.checked ? "● Activo" : "○ Inactivo";
          statusEl.style.color = checkbox.checked ? "#34d399" : "#94a3b8";
        }
        updateCount();
        appendLog(`[MCP] Superpoder "${serverId}" cambiado a ${checkbox.checked ? "ACTIVO" : "INACTIVO"}.`);

        // Send to WebSocket bridge if available
        if (typeof ws !== "undefined" && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "toggle_mcp",
            server: serverId,
            enabled: checkbox.checked
          }));
        }
      });
    });

    updateCount();
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initLogActions();
    initPlaybooks();
    initBatchTable();
    initDesktopOperator();
    initSwarmMonitor();
    initDevServerManager();
    initMcpToggles();
    initAgentBrain();
    initProjects();
    connectBridge();

    const btnRefresh = document.getElementById("btn-refresh");
    if (btnRefresh) {
      btnRefresh.addEventListener("click", () => {
        connectBridge();
        appendLog("[APP] Estado refrescado manualmente.");
      });
    }

    const btnReloadMcp = document.getElementById("btn-reload-mcp");
    if (btnReloadMcp) {
      btnReloadMcp.addEventListener("click", () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "call_tool",
            id: "skills_list_init",
            name: "hermes_skills_list",
            arguments: {}
          }));
          appendLog("[MCP] Solicitando recarga de catálogo de herramientas...");
        } else {
          connectBridge();
        }
      });
    }
  });

  // ── INICIALIZAR COCKPIT DEL CEREBRO ──
  function initAgentBrain() {
    // Tabs
    document.querySelectorAll('.brain-tab').forEach(function(t) {
      t.addEventListener('click', function() {
        const ta = document.getElementById('ta-brain');
        if (ta) _brainCache[_brainActiveFile] = ta.value;
        _switchBrainFile(t.dataset.brainfile);
      });
    });
    // Contador de caracteres
    const taBrain = document.getElementById('ta-brain');
    if (taBrain) taBrain.addEventListener('input', _updateCharCount);
    // Botón Guardar
    const btnSaveBrain = document.getElementById('btn-save-brain');
    if (btnSaveBrain) btnSaveBrain.addEventListener('click', saveBrainFile);
    // Botón Recargar
    const btnReloadBrain = document.getElementById('btn-reload-brain');
    if (btnReloadBrain) btnReloadBrain.addEventListener('click', loadAgentBrain);
    // Auto-cargar al abrir la pestaña
    const navBrain = document.querySelector('[data-tab="agent-brain"]');
    if (navBrain) {
      navBrain.addEventListener('click', function() {
        setTimeout(loadAgentBrain, 100);
      });
    }
  }


  // ═══════════════════════════════════════════════════════════
  // PROYECTOS Y CLOUDFLARE
  // ═══════════════════════════════════════════════════════════
  function loadProjects() {
    const list = document.getElementById('projects-list');
    if (!list) return;
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#8b8579;">Cargando...</div>';
    const pyCode = "import os, json\nbase = os.path.join(os.path.expanduser('~'), 'Downloads')\nprojects = []\nfor d in os.listdir(base):\n    full = os.path.join(base, d)\n    if os.path.isdir(full) and not d.startswith('_') and d not in ['agent_workspace', 'GHS-Script-Free']:\n        projects.append({'name': d, 'path': full})\nprint(json.dumps(projects))";
    ws.send(JSON.stringify({
      type: "call_tool",
      id: "list_projects",
      name: "hermes_execute_code",
      arguments: { code: pyCode }
    }));
  }

  function loadProjectFolder(folderPath) {
    const pyCode = "import os, json\nbase = r'" + folderPath + "'\nfiles = []\nfor f in os.listdir(base):\n    full = os.path.join(base, f)\n    if os.path.isfile(full):\n        files.append({'name': f, 'size': os.path.getsize(full)})\nprint(json.dumps(files))";
    ws.send(JSON.stringify({
      type: "call_tool",
      id: "load_folder",
      name: "hermes_execute_code",
      arguments: { code: pyCode }
    }));
  }

  async function exposeCloudflare() {
    const port = document.getElementById('cf-port').value.trim();
    if (!port) { alert('Por favor, ingresa un puerto.'); return; }
    
    const resultBox = document.getElementById('cf-result');
    
    // Helper para check de puerto (Promesa)
    const isPortActive = function() {
        return new Promise(function(resolve) {
            const checkId = 'check_port_' + Date.now();
            const handler = function(e) {
                const m = JSON.parse(e.data);
                if (m.id === checkId) {
                    ws.removeEventListener('message', handler);
                    resolve((m.text || '').includes('True'));
                }
            };
            ws.addEventListener('message', handler);
            ws.send(JSON.stringify({ type: "call_tool", id: checkId, name: "hermes_terminal", arguments: { command: 'powershell -Command "(Test-NetConnection -ComputerName 127.0.0.1 -Port ' + port + ').TcpTestSucceeded"', is_daemon: false } }));
        });
    };

    resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[WAIT]</div><div style="font-family:JetBrains Mono,monospace;font-size:12px;color:#8b8579;">Verificando puerto ' + port + '...</div>';
    let active = await isPortActive();

    if (!active) {
        // AUTOPILOT: Intentar levantar el servidor
        const folder = window._selectedProjectPath;
        if (!folder) {
            resultBox.innerHTML = '<div style="font-size:24px;">[ERR]</div><div style="font-size:12px;color:#ef4444;">Puerto cerrado y no hay proyecto seleccionado en el Explorador.</div>';
            return;
        }
        
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[WAIT]</div><div style="font-family:JetBrains Mono,monospace;font-size:12px;color:#8b8579;">Puerto cerrado. Autopilot: Levantando servidor en ' + folder + '...</div>';
        
        // Script de Python que decide como levantar el servidor
        const pyCode = "import os, json, subprocess\nfolder = r'" + folder + "'\np = os.path.join(folder, 'package.json')\ncmd = None\nif os.path.exists(p):\n    try:\n        s = json.load(open(p, encoding='utf-8')).get('scripts', {})\n        if 'dev' in s: cmd = 'npm run dev'\n        elif 'start' in s: cmd = 'npm start'\n    except: pass\nif not cmd:\n    cmd = 'python -m http.server " + port + "'\nsubprocess.Popen(cmd, cwd=folder, shell=True, creationflags=0x00000010)\nprint('STARTED:' + cmd)";
        
        ws.send(JSON.stringify({ type: "call_tool", id: "start_server_auto", name: "hermes_execute_code", arguments: { code: pyCode } }));
        
        // Esperar 8 segundos (tiempo de Vite para arrancar)
        await new Promise(r => setTimeout(r, 8000));
        
        // Volver a chequear
        active = await isPortActive();
        if (!active) {
            resultBox.innerHTML = '<div style="font-size:24px;">[ERR]</div><div style="font-size:12px;color:#ef4444;">Autopilot fallo. El servidor no responde en ' + port + ' tras 8 segundos.</div>';
            return;
        }
    }

    // Puerto OK. Lanzar Cloudflare
    resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[WAIT]</div><div style="font-family:JetBrains Mono,monospace;font-size:12px;color:#8b8579;">Servidor en puerto ' + port + ' OK. Iniciando tunel...</div>';
    
    ws.send(JSON.stringify({ type: "call_tool", id: "del_cf_txt", name: "hermes_terminal", arguments: { command: "del /f /q cf_url.txt", is_daemon: false } }));
    
    const cmd = 'cd /d C:\\Users\\USER\\Downloads\\GHS-Script-Free && run_tunnel.bat ' + port;
    ws.send(JSON.stringify({ type: "call_tool", id: "run_tunnel_daemon", name: "hermes_terminal", arguments: { command: cmd, is_daemon: true } }));
    
    // Polling
    let attempts = 0;
    const maxAttempts = 15;
    const pollInterval = setInterval(function() {
        attempts++;
        if (attempts > maxAttempts) {
            clearInterval(pollInterval);
            resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[ERR]</div><div style="font-size:12px;color:#ef4444;">Timeout en Cloudflare.</div>';
            return;
        }
        ws.send(JSON.stringify({ type: "call_tool", id: "read_tunnel_url_poll", name: "hermes_file_read", arguments: { path: "cf_url.txt" } }));
    }, 2000);
    
    window._cfPollInterval = pollInterval;
  }

  function initProjects() {
    const navProjects = document.querySelector('[data-tab="projects"]');
    if (navProjects) {
      navProjects.addEventListener('click', function() {
        setTimeout(loadProjects, 100);
      });
    }
    // Conectar el boton de Exponer Cloudflare (Evitando CSP)
    const btnExpose = document.getElementById('btn-expose-cf');
    if (btnExpose) {
      btnExpose.addEventListener('click', exposeCloudflare);
    }
    // Conectar boton Refrescar del explorador
    const btnRefreshProj = document.getElementById('btn-refresh-projects');
    if (btnRefreshProj) {
      btnRefreshProj.addEventListener('click', loadProjects);
    }
  }

  function _parseProjectsResult(raw) {
    if (typeof raw !== 'string') {
      if (Array.isArray(raw)) return raw;
      if (raw && Array.isArray(raw.result)) return raw.result;
      if (raw && Array.isArray(raw.data)) return raw.data;
      if (raw && raw.output) return _parseProjectsResult(raw.output);
      return [];
    }
    try {
      var parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
      if (parsed && Array.isArray(parsed.result)) return parsed.result;
      if (parsed && Array.isArray(parsed.data)) return parsed.data;
      if (parsed && parsed.output) return _parseProjectsResult(parsed.output);
    } catch(e) {}
    try {
      var m = raw.match(/\[\s*\{[\s\S]*\}\s*\]/);
      if (m) {
        var parsed2 = JSON.parse(m[0]);
        if (Array.isArray(parsed2)) return parsed2;
      }
    } catch(e) {}
    return [];
  }

  function handleProjectMessages(msg) {
    if (msg.id === 'list_projects') {
      const list = document.getElementById('projects-list');
      if (!list) return false;
      let raw = msg.text || (msg.content && msg.content[0] && msg.content[0].text) || '';
      if (typeof raw !== 'string') raw = JSON.stringify(raw);

      let data = _parseProjectsResult(raw);

      list.innerHTML = '';
      if (!Array.isArray(data) || data.length === 0) {
          list.innerHTML = '<div style="padding:20px;text-align:center;color:#8b8579;">No hay proyectos en Downloads.</div>';
          if (raw && !raw.includes('[]')) {
              list.innerHTML += '<div style="padding:10px;font-size:10px;color:#ef4444;max-height:100px;overflow:auto;">' + raw.substring(0,300) + '</div>';
          }
      } else {
          data.forEach(function(p) {
            const item = document.createElement('div');
            item.style.cssText = 'padding:12px;border-bottom:1px solid #e5e2dc;cursor:pointer;display:flex;align-items:center;gap:8px;';
            item.onmouseover = function() { item.style.background = '#faf9f6'; };
            item.onmouseout = function() { item.style.background = ''; };
            item.innerHTML = '<span style="font-size:16px;">[DIR]</span> <b>' + p.name + '</b>';
            item.onclick = function() { 
                // Deseleccionar otros
                document.querySelectorAll('#projects-list > div').forEach(function(el) { el.style.background = ''; });
                item.style.background = '#e2e8f0';
                // Actualizar titulo del panel derecho y guardar ruta
                const cfTitle = document.getElementById('cf-panel-title');
                if (cfTitle) cfTitle.textContent = 'Exponer: ' + p.name;
                window._selectedProjectPath = p.path; // <-- NUEVO
            };
            item.ondblclick = function() { loadProjectFolder(p.path); };
            list.appendChild(item);
          });
      }
      return true;
    }

    if (msg.id === 'load_folder') {
      const list = document.getElementById('projects-list');
      if (!list) return false;
      let raw = msg.text || (msg.content && msg.content[0] && msg.content[0].text) || '';
      if (typeof raw !== 'string') raw = JSON.stringify(raw);

      let data = _parseProjectsResult(raw);

      list.innerHTML = '';
        const backBtn = document.createElement('div');
        backBtn.style.cssText = 'padding:10px;cursor:pointer;color:#0ea5e9;font-weight:600;';
        backBtn.textContent = '[BACK] Volver a Proyectos';
        backBtn.addEventListener('click', loadProjects);
        list.appendChild(backBtn);
      if (!Array.isArray(data) || data.length === 0) {
          list.innerHTML += '<div style="padding:20px;text-align:center;color:#8b8579;">Carpeta vacia o error al leer.</div>';
      } else {
          data.forEach(function(f) {
            const item = document.createElement('div');
            item.style.cssText = 'padding:10px;border-bottom:1px solid #e5e2dc;display:flex;align-items:center;gap:8px;';
            item.innerHTML = '<span style="font-size:16px;">[FILE]</span> ' + f.name + ' <span style="font-size:11px;color:#8b8579;margin-left:auto;">' + Math.round(f.size/1024) + ' KB</span>';
            list.appendChild(item);
          });
      }
      return true;
    }

    if (msg.id === 'read_tunnel_url_poll') {
      const resultBox = document.getElementById('cf-result');
      if (!resultBox) return false;
      let raw = msg.text || (msg.content && msg.content[0] && msg.content[0].text) || '';
      const urlMatch = raw.match(/https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com/);
      if (urlMatch) {
        if (window._cfPollInterval) { clearInterval(window._cfPollInterval); window._cfPollInterval = null; }
        const url = urlMatch[0];
        
        // PASO 4: Health Check de la URL publica
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[WAIT]</div><div style="font-size:12px;color:#8b8579;">URL obtenida. Verificando respuesta HTTP...</div>';
        
        // Hacemos un fetch a la URL publica para ver si responde 200
        fetch(url, { method: 'GET', mode: 'cors' })
          .then(function(res) {
            if (res.ok || res.status === 0) {
              resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[OK]</div><div style="font-family:JetBrains Mono,monospace;font-size:13px;color:#10b981;font-weight:bold;word-break:break-all;padding:10px;">' + url + '</div><div style="font-size:11px;color:#8b8579;margin-top:10px;">Tunel verificado y activo.</div>';
            } else {
              resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[WARN]</div><div style="font-family:JetBrains Mono,monospace;font-size:12px;color:#f59e0b;word-break:break-all;">' + url + '</div><div style="font-size:11px;color:#8b8579;margin-top:10px;">El tunel responde con codigo ' + res.status + '. Puede que el servidor local no este listo.</div>';
            }
          })
          .catch(function(err) {
            resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[WARN]</div><div style="font-family:JetBrains Mono,monospace;font-size:12px;color:#f59e0b;word-break:break-all;">' + url + '</div><div style="font-size:11px;color:#8b8579;margin-top:10px;">El tunel se creo pero la verificacion CORS fallo.</div>';
          });
      }
      return true;
    }
    if (msg.id === 'read_tunnel_url') {
      const resultBox = document.getElementById('cf-result');
      if (!resultBox) return false;
      let raw = msg.text || (msg.content && msg.content[0] && msg.content[0].text) || '';
      const urlMatch = raw.match(/https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com/);
      if (urlMatch) {
        // URL encontrada! Limpiar polling y mostrar resultado
        if (window._cfPollInterval) { clearInterval(window._cfPollInterval); window._cfPollInterval = null; }
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[OK]</div><div style="font-family:JetBrains Mono,monospace;font-size:13px;color:#10b981;font-weight:bold;word-break:break-all;padding:10px;">' + urlMatch[0] + '</div><div style="font-size:11px;color:#8b8579;margin-top:10px;">Tunel activo. Comparte este enlace.</div>';
      }
      return true;
    }
    if (msg.id === 'read_tunnel_url') {
      const resultBox = document.getElementById('cf-result');
      if (!resultBox) return false;
      let raw = msg.text || (msg.content && msg.content[0] && msg.content[0].text) || '';
      const urlMatch = raw.match(/https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com/);
      if (urlMatch) {
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[OK]</div><div style="font-family:JetBrains Mono,monospace;font-size:13px;color:#10b981;font-weight:bold;word-break:break-all;padding:10px;">' + urlMatch[0] + '</div><div style="font-size:11px;color:#8b8579;margin-top:10px;">Tunel activo. Comparte este enlace.</div>';
      } else {
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[ERR]</div><div style="font-size:12px;color:#ef4444;">No se pudo extraer la URL. Revisa la Consola de Logs.</div>';
      }
      return true;
    }
    if (msg.id === 'cloudflare_tunnel') {
      const resultBox = document.getElementById('cf-result');
      if (!resultBox) return false;
      const raw = msg.text || '';
      const urlMatch = raw.match(/https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com/);
      if (urlMatch) {
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[OK]</div><div style="font-family:JetBrains Mono,monospace;font-size:13px;color:#10b981;font-weight:bold;word-break:break-all;padding:10px;">' + urlMatch[0] + '</div><div style="font-size:11px;color:#8b8579;margin-top:10px;">Tunel activo. Comparte este enlace.</div>';
      } else if (msg.error) {
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[ERR]</div><div style="font-size:12px;color:#ef4444;">Error: ' + msg.error + '</div>';
      } else {
        resultBox.innerHTML = '<div style="font-size:24px;margin-bottom:10px;">[WAIT]</div><div style="font-family:JetBrains Mono,monospace;font-size:12px;color:#8b8579;">Iniciando... Espera unos segundos.</div>';
      }
      return true;
    }
    return false;
  }

  // ── RENDERIZADO DINAMICO DE SKILLS DESDE EL MCP ──
  function renderSkillCards(skills) {
    const grid = document.getElementById("mcp-grid");
    if (!grid) return;
    grid.innerHTML = "";
    const catMap = {
      creative: { color: "#f43f5e", label: "ARTE" },
      software: { color: "#10b981", label: "CODE" },
      devops: { color: "#8b5cf6", label: "DEVOPS" },
      research: { color: "#0ea5e9", label: "RESEARCH" },
      "autonomous-ai-agents": { color: "#6366f1", label: "AI AGENTS" },
      media: { color: "#f59e0b", label: "MEDIA" },
      productivity: { color: "#3b82f6", label: "PRODUCTIVITY" },
      email: { color: "#ec4899", label: "EMAIL" },
      "note-taking": { color: "#14b8a6", label: "NOTES" },
      web: { color: "#6366f1", label: "WEB" },
      apple: { color: "#94a3b8", label: "APPLE" },
      "data-science": { color: "#f59e0b", label: "DATA" },
      mlops: { color: "#ef4444", label: "MLOPS" }
    };
    // Actualizar el contador dinámico en el menú lateral
    const countEl = document.getElementById("mcp-count");
    if (countEl) countEl.textContent = skills.length;

    // Diccionario de traducciones y mejoras
    const translations = {
      'yuanbao': 'Gestión de grupos de Yuanbao (Tencent). Permite buscar miembros, stickers y enviar mensajes.',
      'claude-code': 'Delega la programación a la CLI de Claude Code. Útil para PRs y features complejas.',
      'codex': 'Delega la programación a la CLI de OpenAI Codex. Especializado en lógica y matemáticas.',
      'computer-use': 'Controla el escritorio de Windows: clics, teclado, capturas de pantalla y navegación.',
      'requesting-code-review': 'Revisión automática de código antes de hacer commit (seguridad y calidad).',
      'simplify-code': 'Refactorización y limpieza de código por 4 agentes en paralelo.',
      'spike': 'Experimentación rápida. Valida una idea con código descartable antes de invertir tiempo.',
      'systematic-debugging': 'Depuración sistemática de 4 fases. Analiza la causa raíz antes de tocar el código.',
      'test-driven-development': 'Desarrollo guiado por pruebas (TDD). Escribe el test primero, luego el código.',
      'blocked-page-recovery': 'Bypass de bloqueos web. Útil para páginas con CAPTCHAs, paywalls o Cloudflare.',
      'requesting-code-review': 'Revisión de código pre-commit: escaneo de seguridad y auto-fixes.',

      'systematic-debugging': 'Depuración sistemática de 4 fases. Analiza la causa raíz antes de tocar el código.',
      'test-driven-development': 'Desarrollo guiado por pruebas (TDD). Escribe el test primero, luego el código.',
      'spike': 'Experimentación rápida para validar ideas antes de implementar.',
      'requesting-code-review': 'Revisión automática de código (seguridad, calidad, fixes).',
      'simplify-code': 'Limpieza y refactorización de código reciente por 4 agentes en paralelo.',
      'impeccable-design': 'Sistema de diseño estricto de 8 puntos, contraste WCAG AAA y glassmorphism.',
      'taste-skill': 'Filtro anti-diseño genérico. Evita gradientes de IA y falta de jerarquía.',
      'prompt-builder-master': 'Orquestador de diseño artesanal. Pipeline de 3 fases para landings de $500k.',
      'fullstack-app-builder': 'Arquitecto Full-Stack. Crea apps en React/Vite con estado global.',
      'emil-motion': 'Animaciones físicas y micro-interacciones <300ms. Nada de animaciones lentas.',
      'cloudflare-tunnel': 'Expone un servidor local a internet usando Cloudflare.',
      'popular-web-designs': 'Referencias visuales de las mejores webs (Stripe, Linear, Vercel).',
      'claude-design': 'Patrones de diseño y tipografía para artefactos HTML one-off.',
      'design-md': 'Google DESIGN.md. Define tokens de diseño exportables a CSS/Tailwind.'
    };

    skills.forEach(function(sk) {
      const card = document.createElement("div");
      card.className = "power-card";
      card.dataset.server = sk.name;
      const info = catMap[sk.category] || { color: "#f59e0b", label: (sk.category || "SKILL").toUpperCase() };
      const initials = (sk.category || "S").substring(0, 3).toUpperCase();
      const isActive = sk.active !== false;
      card.innerHTML = '<div class="card-header">' +
        '<div class="card-avatar" style="background:' + info.color + '22; color:' + info.color + '; font-weight:700; font-size:11px;">' + initials + '</div>' +
        '<div class="card-meta"><span class="card-tag">' + info.label + '</span><h3 class="card-title">' + sk.name + '</h3></div>' +
        '<label class="switch"><input type="checkbox" class="skill-toggle" data-skill="' + sk.name + '" ' + (isActive ? 'checked' : '') + '><span class="slider"></span></label>' +
        '</div>' +
        '<p class="card-desc">' + (translations[sk.name] || sk.description || "Herramienta del sistema") + '</p>' +
        '<div class="card-footer"><span class="card-status ' + (isActive ? 'status-active' : '') + '">' + (isActive ? 'ACTIVO' : 'DESACTIVADO') + '</span><span class="card-tools-badge">' + (sk.category || "—") + '</span></div>';
      grid.appendChild(card);
    });
    document.querySelectorAll(".skill-toggle").forEach(function(toggle) {
      toggle.addEventListener("change", function(e) {
        const skillName = e.target.dataset.skill;
        const isActive = e.target.checked;
        ws.send(JSON.stringify({
          type: "call_tool",
          id: "toggle_" + skillName + "_" + Date.now(),
          name: "hermes_toggle_skill",
          arguments: { name: skillName, active: isActive }
        }));
        const card = e.target.closest(".power-card");
        const statusEl = card.querySelector(".card-status");
        if (isActive) { statusEl.textContent = "ACTIVO"; statusEl.classList.add("status-active"); }
        else { statusEl.textContent = "DESACTIVADO"; statusEl.classList.remove("status-active"); }
      });
    });
  }




  // ── LIMPIADOR DE UI: Eliminar pestañas obsoletas ──
  function removeUselessTabs() {
    // Ocultar botones del menú lateral
    // 1. Eliminar Playbooks
    const playbooksBtn = document.querySelector('[data-tab="playbooks"]');
    if (playbooksBtn) playbooksBtn.remove();
    const playbooksPane = document.getElementById('pane-playbooks');
    if (playbooksPane) playbooksPane.remove();
    
    // 2. Eliminar Pipeline (Batch)
    const batchBtn = document.querySelector('[data-tab="batch"]');
    if (batchBtn) batchBtn.remove();
    const batchPane = document.getElementById('pane-batch');
    if (batchPane) batchPane.remove();

    // 3. Eliminar Enjambre (Swarm) - Ya no es util
    const swarmBtn = document.querySelector('[data-tab="swarm"]');
    if (swarmBtn) swarmBtn.remove();
    const swarmPane = document.getElementById('pane-swarm');
    if (swarmPane) swarmPane.remove();
    
    // Si el panel activo era uno de los que borramos, volver al inicio (MCP)
    if (playbooksPane || batchPane) {
        const mcpBtn = document.querySelector('[data-tab="mcp"]');
        if (mcpBtn) mcpBtn.click();
    }
    
    console.log('[GHS UI] Pestañas de Playbooks y Batch removidas del DOM.');
  }

  // Ejecutar la limpieza en cuanto el DOM esté listo
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', removeUselessTabs);
  } else {
    removeUselessTabs();
  }


  // ── CEREBRO DEL AGENTE (MEMORIA 3 CAPAS) ──
  let _brainActiveFile = 'soul';
  const _brainPaths = {
    soul: 'memory/SOUL.md',
    user: 'memory/USER.md',
    memory: 'memory/MEMORY.md'
  };
  const _brainCache = { soul: '', user: '', memory: '' };

  function loadAgentBrain() {
    const ta = document.getElementById('ta-brain');
    if (ta) ta.value = 'Cargando...';
    ['soul', 'user', 'memory'].forEach(function(key) {
      ws.send(JSON.stringify({
        type: "call_tool",
        id: "read_brain_" + key,
        name: "hermes_file_read",
        arguments: { path: _brainPaths[key] }
      }));
    });
  }

  function _switchBrainFile(key) {
    const ta = document.getElementById('ta-brain');
    if (ta) _brainCache[_brainActiveFile] = ta.value;
    _brainActiveFile = key;
    document.querySelectorAll('.brain-tab').forEach(function(t) {
      t.classList.toggle('active', t.dataset.brainfile === key);
    });
    const labels = { soul: 'SOUL.md', user: 'USER.md', memory: 'MEMORY.md' };
    const el = document.getElementById('editor-active-file');
    if (el) el.textContent = labels[key];
    if (ta) { ta.value = _brainCache[key] || ''; _updateCharCount(); }
  }

  function _updateCharCount() {
    const ta = document.getElementById('ta-brain');
    const counter = document.getElementById('editor-charcount');
    if (ta && counter) counter.textContent = ta.value.length.toLocaleString() + ' caracteres';
  }

  function _updateBrainStats(key, text) {
    const kb = (text.length / 1024).toFixed(1);
    const words = text.split(/[\s]+/).filter(function(w) { return w.length > 0; }).length;
    const el = document.getElementById('stat-' + key + '-info');
    if (el) el.textContent = kb + ' KB · ' + words + ' palabras';
  }

  function saveBrainFile() {
    const ta = document.getElementById('ta-brain');
    if (!ta) return;
    const btn = document.getElementById('btn-save-brain');
    ws.send(JSON.stringify({
      type: "call_tool",
      id: "save_brain_" + _brainActiveFile,
      name: "hermes_file_write",
      arguments: { path: _brainPaths[_brainActiveFile], content: ta.value }
    }));
    if (btn) {
      btn.classList.add('saved');
      btn.textContent = '✓ Guardado';
      setTimeout(function() {
        btn.classList.remove('saved');
        btn.textContent = '💾 Guardar Cambios';
      }, 2000);
    }
  }

  

})();
