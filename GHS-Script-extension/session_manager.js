/**
 * GHS Project Session Manager (Módulo Autónomo de Gestión y Relevo de Sesión)
 *
 * Arquitectura Modular para:
 * 1. Visualización clara del Proyecto Activo (nombre, carpeta en disco, subtareas, dev server).
 * 2. Recuperación en Caliente (Hot-Swap) ante agotamiento de créditos/cuota de IA en cualquier pestaña.
 * 3. Relevo sin pérdida (Zero-Loss Handover Packet) hacia una nueva pestaña o cuenta.
 * 4. Conclusión limpia y archivo (.ghs_session.json) para separar proyectos y no mezclar estados.
 */

(function () {
  'use strict';

  class ProjectSessionManager {
    constructor() {
      this.state = {
        sessionId: null,
        projectName: "",
        projectDir: "c:\\Users\\USER\\Downloads",
        status: "idle", // 'idle' | 'active' | 'quota_exhausted' | 'completed'
        mission: "",
        niche: "",
        aesthetic: "",
        masterBlueprint: "",
        currentTask: "",
        completedTasks: [],
        devServerUrl: "",
        startedAt: null,
        lastUpdated: null,
        concludedAt: null,
      };

      this.elements = {};
      this.timerInterval = null;
    }

    init() {
      this._bindDomElements();
      this._setupEventListeners();
      this._startHeartbeatTimer();
      this.requestStateFromBackground();
    }

    _bindDomElements() {
      this.elements = {
        card: document.getElementById("project-session-card"),
        title: document.getElementById("session-proj-name"),
        badge: document.getElementById("session-status-badge"),
        subtitle: document.getElementById("session-dir-label"),
        body: document.getElementById("session-body-content"),
        idVal: document.getElementById("session-id-val"),
        devServerVal: document.getElementById("session-devserver-val"),
        timeVal: document.getElementById("session-time-val"),
        subtaskVal: document.getElementById("session-subtask-val"),
        tasksCount: document.getElementById("session-tasks-count"),
        progressBar: document.getElementById("session-progress-bar"),
        tasksDetails: document.getElementById("session-tasks-details"),
        tasksBadge: document.getElementById("session-tasks-badge"),
        tasksList: document.getElementById("session-tasks-list"),
        btnCopyHandover: document.getElementById("btn-session-copy-handover"),
        btnResumeTab: document.getElementById("btn-session-resume-tab"),
        btnConclude: document.getElementById("btn-session-conclude"),
        btnReset: document.getElementById("btn-session-reset"),
        navCounter: document.getElementById("nav-session-counter"),
      };
    }

    _setupEventListeners() {
      if (this.elements.btnCopyHandover) {
        this.elements.btnCopyHandover.addEventListener("click", () => this.copyHandoverPrompt());
      }
      if (this.elements.btnResumeTab) {
        this.elements.btnResumeTab.addEventListener("click", () => this.resumeSessionInActiveTab());
      }
      if (this.elements.btnConclude) {
        this.elements.btnConclude.addEventListener("click", () => this.concludeSession());
      }
      if (this.elements.btnReset) {
        this.elements.btnReset.addEventListener("click", () => this.resetSession());
      }
    }

    _startHeartbeatTimer() {
      if (this.timerInterval) clearInterval(this.timerInterval);
      this.timerInterval = setInterval(() => {
        if (this.state.status === "active" && this.elements.timeVal && this.state.startedAt) {
          const diffMs = Date.now() - this.state.startedAt;
          const mins = Math.floor(diffMs / 60000);
          this.elements.timeVal.textContent = mins === 0 ? "Hace un momento" : `Hace ${mins} min`;
        }
      }, 30000);
    }

    requestStateFromBackground() {
      if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "swarm_get_session_state" }, (res) => {
          if (res && res.ok && res.state) {
            this.update(res.state, res.devServers);
          }
        });
      }
    }

    update(newState = {}, devServers = []) {
      if (!newState) return;
      Object.assign(this.state, newState);

      // Si no tiene nombre explícito o tiene el fallback genérico 'proyecto', inferir del directorio o misión
      if (!this.state.projectName || this.state.projectName === "proyecto") {
        if (this.state.projectDir && this.state.projectDir !== "c:\\Users\\USER\\Downloads") {
          const parts = this.state.projectDir.replace(/\\/g, "/").split("/").filter(Boolean);
          this.state.projectName = parts[parts.length - 1] || "Proyecto Web";
        } else if (this.state.mission) {
          this.state.projectName = this.state.mission.slice(0, 30).trim() + "...";
        }
      }

      // Detectar servidor dev correspondiente al proyecto y sincronizar ruta activa real
      if (Array.isArray(devServers) && devServers.length > 0) {
        const found = devServers.find((s) => {
          if (!s || !s.cwd) return false;
          const normCwd = s.cwd.toLowerCase().replace(/\\/g, "/");
          const normProj = (this.state.projectDir || "").toLowerCase().replace(/\\/g, "/");
          return normCwd.includes(normProj) || normProj.includes(normCwd);
        }) || devServers[0];

        if (found) {
          if (found.url) {
            this.state.devServerUrl = found.url;
          }
          if (found.cwd && (!this.state.projectDir || this.state.projectDir === "c:\\Users\\USER\\Downloads" || this.state.projectDir.includes("apex-labs"))) {
            this.state.projectDir = found.cwd;
          }
          if (found.project && (!this.state.projectName || this.state.projectName === "proyecto")) {
            this.state.projectName = found.project;
          }
        }
      }

      this.render();
    }

    render() {
      if (!this.elements.card) return;

      const isIdle = !this.state.mission && (!this.state.completedTasks || this.state.completedTasks.length === 0) && !this.state.masterBlueprint;

      if (isIdle && this.state.status === "idle") {
        if (this.elements.title) this.elements.title.textContent = "Sin Proyecto Activo";
        if (this.elements.subtitle) this.elements.subtitle.textContent = "Inicia una misión o configura un proyecto para activar la sesión de desarrollo.";
        if (this.elements.badge) {
          this.elements.badge.textContent = "EN ESPERA";
          this.elements.badge.className = "session-status-badge badge-idle";
        }
        if (this.elements.body) this.elements.body.style.display = "none";
        if (this.elements.navCounter) this.elements.navCounter.style.display = "none";
        return;
      }

      // Estado activo / completado
      if (this.elements.body) this.elements.body.style.display = "block";

      const projDisplayName = this.state.projectName || "Proyecto Web Activo";
      if (this.elements.title) this.elements.title.textContent = projDisplayName;
      if (this.elements.subtitle) {
        this.elements.subtitle.innerHTML = `📁 <strong>Directorio:</strong> <span class="font-mono text-terracotta">${this.state.projectDir}</span>`;
      }

      // Badges
      if (this.elements.badge) {
        if (this.state.status === "completed") {
          this.elements.badge.textContent = "✓ CONCLUIDA / FINALIZADA";
          this.elements.badge.className = "session-status-badge badge-completed";
        } else if (this.state.status === "quota_exhausted") {
          this.elements.badge.textContent = "⚠️ ESPERANDO RELEVO (CUOTA)";
          this.elements.badge.className = "session-status-badge badge-warning";
        } else {
          this.elements.badge.textContent = "● SESIÓN EN CURSO";
          this.elements.badge.className = "session-status-badge badge-active";
        }
      }

      // Sidebar nav badge
      if (this.elements.navCounter) {
        this.elements.navCounter.style.display = "inline-block";
        if (this.state.status === "completed") {
          this.elements.navCounter.textContent = "LISTO";
          this.elements.navCounter.style.color = "#2e90fa";
          this.elements.navCounter.style.background = "rgba(46, 144, 250, 0.15)";
        } else {
          this.elements.navCounter.textContent = "ACTIVA";
          this.elements.navCounter.style.color = "#10b981";
          this.elements.navCounter.style.background = "rgba(16, 185, 129, 0.15)";
        }
      }

      // ID
      if (this.elements.idVal) {
        this.elements.idVal.textContent = this.state.sessionId || ("sess_" + (this.state.startedAt ? this.state.startedAt.toString(36) : "activa"));
      }

      // Dev Server
      if (this.elements.devServerVal) {
        if (this.state.devServerUrl) {
          this.elements.devServerVal.innerHTML = `<a href="${this.state.devServerUrl}" target="_blank" class="devserver-link">🌐 ${this.state.devServerUrl} ↗</a>`;
        } else {
          this.elements.devServerVal.innerHTML = `<span class="badge-offline">Verificando en puerto local...</span>`;
        }
      }

      // Elapsed Time
      if (this.elements.timeVal) {
        if (this.state.startedAt) {
          const mins = Math.floor((Date.now() - this.state.startedAt) / 60000);
          this.elements.timeVal.textContent = mins <= 0 ? "Hace un momento" : `Hace ${mins} min`;
        } else {
          this.elements.timeVal.textContent = "En curso";
        }
      }

      // Subtask
      if (this.elements.subtaskVal) {
        this.elements.subtaskVal.textContent = this.state.currentTask || "En progreso";
      }

      // Tasks progress
      const completedList = this.state.completedTasks || [];
      const totalEstimated = Math.max(completedList.length + (this.state.status === "completed" ? 0 : 1), 3);
      const percent = Math.min(Math.round((completedList.length / totalEstimated) * 100), 100);

      if (this.elements.tasksCount) {
        this.elements.tasksCount.textContent = `${completedList.length} subtareas completadas (${percent}%)`;
      }
      if (this.elements.progressBar) {
        this.elements.progressBar.style.width = `${percent}%`;
      }
      if (this.elements.tasksBadge) {
        this.elements.tasksBadge.textContent = String(completedList.length);
      }

      // Render timeline list
      if (this.elements.tasksList) {
        if (completedList.length === 0) {
          this.elements.tasksList.innerHTML = `<div class="tasks-empty-hint">Aún no se han registrado subtareas. Cuando el Obrero complete código, aparecerán aquí.</div>`;
        } else {
          this.elements.tasksList.innerHTML = completedList.map((t, i) => {
            const timeStr = t.timestamp ? new Date(t.timestamp).toLocaleTimeString() : "";
            const snip = t.report ? (t.report.length > 280 ? t.report.slice(0, 280) + "..." : t.report) : "Subtarea completada satisfactoriamente.";
            return `
              <div class="task-timeline-item">
                <div class="task-marker">✓</div>
                <div class="task-item-content">
                  <div class="task-item-header">
                    <span class="task-item-id">#${i + 1} - ${t.subtaskId || "Subtarea"}</span>
                    <span class="task-item-time">${timeStr}</span>
                  </div>
                  <div class="task-item-report">${this._escapeHtml(snip)}</div>
                </div>
              </div>
            `;
          }).join("");
        }
      }
    }

    _escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
    }

    /**
     * Genera el Paquete de Relevo Instantáneo (Zero-Loss Handover Packet).
     * Diseñado para que una nueva pestaña de IA (Gemini, ChatGPT, Claude, DeepSeek)
     * tome el control EXACTO del proyecto sin borrar nada de lo hecho en disco.
     */
    generateHandoverPrompt() {
      const projName = this.state.projectName || (this.state.projectDir ? this.state.projectDir.replace(/\\/g, "/").split("/").pop() : "Proyecto Web");
      const completedSummary = (this.state.completedTasks || []).map((t, idx) => {
        const reportSnippet = t.report ? (t.report.length > 250 ? t.report.slice(0, 250) + "..." : t.report) : "Completada con éxito";
        return `${idx + 1}. [✓] Subtarea: \`${t.subtaskId || 'subtask'}\` - ${reportSnippet}`;
      }).join("\n");

      const currentPending = this.state.currentTask && this.state.currentTask !== "COMPLETED" 
        ? this.state.currentTask 
        : "Continuar desarrollo, verificación visual y pulido de componentes";

      const devServer = this.state.devServerUrl || "http://localhost:5173";

      return `# ⚡ PROTOCOLO DE RELEVO DE SESIÓN - GHS AUTONOMOUS SWARM
**PROYECTO:** ${projName}
**DIRECTORIO EN DISCO:** \`${this.state.projectDir}\`
**ESTADO:** RELEVO EN CALIENTE (CONTINUACIÓN TRAS AGOTAMIENTO DE CUOTA / CAMBIO DE PESTAÑA)
**SERVIDOR DEV ACTIVO:** ${devServer}

## 📌 OBJETIVO / MISIÓN ORIGINAL:
${this.state.mission || this.state.originalIdea || "Construcción y refinamiento de aplicación web profesional."}

## 💎 BLUEPRINT MAESTRO DEL PROYECTO:
${this.state.masterBlueprint ? this.state.masterBlueprint : "Sigue la arquitectura estándar de Vite + React + Tailwind CSS v4 definida en el proyecto."}

## 📊 REGISTRO DE TRABAJO YA REALIZADO (NO REPETIR NI SOBRESCRIBIR):
${completedSummary || "Los archivos base del proyecto ya se encuentran creados e instalados en disco."}

## 🎯 PRÓXIMA SUBTAREA A EJECUTAR INMEDIATAMENTE:
**Subtarea:** \`${currentPending}\`

## ⚠️ DIRECTIVAS TÉCNICAS OBLIGATORIAS:
1. **NO mutilar ni recrear**: Todos los archivos creados en \`${this.state.projectDir}\` están intactos. Lee el código existente con \`hermes_file_read\` antes de modificar.
2. **Usa tus herramientas locales Hermes**: Ejecuta los comandos con \`hermes_terminal\`, edita archivos con \`hermes_file_write\`.
3. **Inspección visual continua**: Tras realizar tus cambios, ejecuta \`hermes_ui_inspect\` para auditar la interfaz y confirmar que no hay errores de sintaxis ni pantalla roja de Vite.
4. **Reporta tu avance**: Cuando completes esta subtarea, envía tu informe con \`REPORT_RESULT: ...\` para que el enjambre registre el progreso.

Continúa de inmediato a partir de la subtarea indicada.`;
    }

    copyHandoverPrompt() {
      const prompt = this.generateHandoverPrompt();
      navigator.clipboard.writeText(prompt).then(() => {
        if (window.showToast) {
          window.showToast("📋 ¡Prompt de Relevo copiado! Puedes pegarlo en una nueva pestaña o cuenta para continuar sin perder nada.", "success");
        } else {
          alert("✓ Prompt de Relevo copiado al portapapeles.");
        }
      }).catch((err) => {
        console.error("Error al copiar al portapapeles:", err);
      });
    }

    resumeSessionInActiveTab() {
      if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) {
        if (window.showToast) window.showToast("Extensión de Chrome no disponible.", "error");
        return;
      }

      if (this.elements.btnResumeTab) {
        this.elements.btnResumeTab.disabled = true;
        this.elements.btnResumeTab.textContent = "⏳ Transfiriendo sesión...";
      }

      const handoverPrompt = this.generateHandoverPrompt();

      chrome.runtime.sendMessage({
        type: "swarm_resume_session_tab",
        prompt: handoverPrompt,
        session: this.state
      }, (res) => {
        if (this.elements.btnResumeTab) {
          this.elements.btnResumeTab.disabled = false;
          this.elements.btnResumeTab.textContent = "⚡ Reanudar en Pestaña Activa";
        }

        if (res && res.ok) {
          if (window.showToast) {
            window.showToast(`✓ Sesión transferida exitosamente a la pestaña de IA (${res.targetAi || "Pestaña Activa"}). ¡Continuando trabajo!`, "success");
          }
          this.state.status = "active";
          this.render();
        } else {
          const err = (res && res.error) || "No se detectó ninguna pestaña de IA abierta. Abre una pestaña con tu nueva cuenta y vuelve a hacer clic.";
          if (window.showToast) window.showToast(err, "error");
          else alert(err);
        }
      });
    }

    concludeSession() {
      const confirmMsg = "Esta acción concluirá formalmente la sesión de trabajo y archivará el historial en la carpeta del proyecto (.ghs_session.json). Los archivos de tu proyecto NO se borrarán. ¿Deseas concluir la sesión?";
      if (!confirm(confirmMsg)) return;

      if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) return;

      if (this.elements.btnConclude) {
        this.elements.btnConclude.disabled = true;
        this.elements.btnConclude.textContent = "⏳ Archivando sesión...";
      }

      chrome.runtime.sendMessage({
        type: "swarm_conclude_session",
        sessionId: this.state.sessionId
      }, (res) => {
        if (this.elements.btnConclude) {
          this.elements.btnConclude.disabled = false;
          this.elements.btnConclude.textContent = "⏹️ Concluir Sesión";
        }

        if (res && res.ok) {
          this.state.status = "completed";
          this.state.concludedAt = Date.now();
          if (window.showToast) {
            window.showToast("✓ Sesión concluida y archivada con éxito. El proyecto está finalizado.", "success");
          }
          this.render();
        } else {
          if (window.showToast) window.showToast("Error concluyendo la sesión: " + ((res && res.error) || "desconocido"), "error");
        }
      });
    }

    resetSession() {
      const confirmReset = "¿Deseas reiniciar el panel y comenzar una nueva sesión de proyecto limpia? El historial anterior ya archivado en disco se mantendrá.";
      if (!confirm(confirmReset)) return;

      if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) return;

      chrome.runtime.sendMessage({ type: "swarm_reset_session" }, (res) => {
        if (res && res.ok) {
          this.state = {
            sessionId: null,
            projectName: "",
            projectDir: "c:\\Users\\USER\\Downloads",
            status: "idle",
            mission: "",
            niche: "",
            aesthetic: "",
            masterBlueprint: "",
            currentTask: "",
            completedTasks: [],
            devServerUrl: "",
            startedAt: null,
            lastUpdated: null,
            concludedAt: null,
          };
          if (window.showToast) window.showToast("✓ Panel de sesión reiniciado. Listo para un nuevo proyecto.", "info");
          this.render();
        }
      });
    }
  }

  // Exportar al ámbito global para integración modular
  window.GHS_SessionManager = new ProjectSessionManager();

  // Auto-inicializar cuando el DOM esté listo
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.GHS_SessionManager.init());
  } else {
    window.GHS_SessionManager.init();
  }
})();
