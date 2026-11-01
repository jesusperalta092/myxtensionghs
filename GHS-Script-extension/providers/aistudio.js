// SPDX-License-Identifier: GPL-3.0-or-later
// providers/aistudio.js - Google AI Studio Apps (aistudio.google.com/apps) provider.
// Autonomous generation of native Android (Kotlin) & Web applications directly in Google AI Studio Apps.

// eslint-disable-next-line no-unused-vars
const ZSProvider = (() => {
  "use strict";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let diag = () => {}; // injected by core via init()

  const S = {
    // Both screens: /apps (home/builder) and /apps/<id> (workspace/chat)
    userItem: "[class*='user-query'], [class*='user-message'], [class*='user-bubble']",
    assistantItem: "[class*='model-response'], [class*='assistant-message'], [class*='assistant-bubble'], div.output-container",
    anyItem: "[class*='user-query'], [class*='user-message'], [class*='model-response'], [class*='assistant-message'], div.output-container",
    reply: "div.model-response-text, div[class*='response-text'], div[class*='message-content'], div.markdown, div.output-container",
    codeWrap: "pre, code-block, [class*='code-block']",
    initialEditor: "div.prompt-textarea[contenteditable], [role='textbox'].prompt-textarea, div[contenteditable='plain-text-only'], div[contenteditable='true']",
    activeEditor: "textarea.cdk-textarea-autosize, textarea[placeholder*='Make changes' i], textarea[placeholder*='ask for anything' i], rich-textarea textarea, textarea",
    inputWrapper: "div.prompt-input-wrapper, div[class*='prompt-input'], div.bottom-container, div.input-container, rich-textarea, form",
    sendBtn: "button.send-button, button[aria-label*='Send' i], button[aria-label*='Enviar' i], button[aria-label*='Submit' i], button[ms-button][variant='icon-primary']",
    stopBtn: "button.stop-button, button[aria-label*='Stop' i], button[aria-label*='Detener' i], button[aria-label*='Cancel' i]",
    errorSurfaces: "mat-snack-bar-container, [role='alert'], [class*='error-message']"
  };

  const RE = {
    quotaLimit: /se superaron los l[ií]mites de cuota|quota limit exceeded|rate limit exceeded|resource has been exhausted|demasiadas solicitudes/i,
    contextLimit: /context limit exceeded|conversation too long|l[ií]mite de contexto alcanzado/i,
    busy: /something went wrong|ha ocurrido un error|int[eé]ntalo de nuevo|temporarily unavailable/i,
    tooLong: /conversation .{0,20}(too long|getting too long|demasiado larga)/i
  };

  const timings = {
    GEN_IDLE_MS: 4000,
    REASON_IDLE_MS: 15000,
    WARMUP_MS: 40000,
    REASON_NOREPLY_MS: 60000,
    STABLE_MS: 12000,
    RESPONSE_TIMEOUT_MS: 360000 // Android code builds can take up to 6 min in complex Kotlin builds
  };

  // ── Helpers ───────────────────────────────────────────────────────────────
  const isVisible = (el) => {
    if (!el) return false;
    if (el.checkVisibility) {
      try { return el.checkVisibility({ checkOpacity: false, checkVisibilityCSS: false }); } catch {}
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  function getEditor() {
    // 1. Check for active project textarea (/apps/<app-id>)
    for (const sel of [
      "textarea.cdk-textarea-autosize",
      "textarea[placeholder*='Make changes' i]",
      "textarea[placeholder*='ask for anything' i]",
      "rich-textarea textarea",
      "div.prompt-textarea[contenteditable]",
      "[role='textbox'].prompt-textarea",
      "div[contenteditable='plain-text-only']",
      "div[contenteditable='true']",
      "textarea"
    ]) {
      for (const el of document.querySelectorAll(sel)) {
        if (!el.closest("#zs-root") && isVisible(el)) {
          return el;
        }
      }
    }
    return null;
  }

  function editorText() {
    const ed = getEditor();
    if (!ed) return "";
    if (ed.tagName === "TEXTAREA" || ed.tagName === "INPUT") {
      return ed.value || "";
    }
    return ed.textContent || "";
  }

  function composerFrame() {
    const ed = getEditor();
    if (!ed) return null;
    for (const sel of [
      "div.prompt-input-wrapper",
      "div[class*='prompt-input']",
      "div.bottom-container",
      "div.input-container",
      "div[class*='input-wrapper']",
      "div[class*='chat-input']",
      "rich-textarea",
      "form"
    ]) {
      const match = ed.closest(sel);
      if (match && !match.closest("#zs-root") && isVisible(match)) {
        return match;
      }
    }
    return ed.parentElement || ed;
  }

  function barMount() {
    const cf = composerFrame();
    if (!cf || !cf.parentElement) return null;
    // On the initial screen (/apps) or chat (/apps/<id>), mount above the composer box
    const col = cf.parentElement;
    try {
      const style = window.getComputedStyle(col);
      // If it's a column or block container, mount neatly in-flow
      if (style.display.includes("flex") && style.flexDirection.includes("row")) {
        // In horizontal flex row, prefer clean floating positioning to avoid squeezing
        return null;
      }
      return { parent: col, before: cf };
    } catch {
      return { parent: col, before: cf };
    }
  }

  function sendButton() {
    for (const sel of [
      "button.send-button",
      "button[aria-label*='Send' i]",
      "button[aria-label*='Enviar' i]",
      "button[aria-label*='Submit' i]",
      "button[ms-button][variant='icon-primary']",
      "button[aria-label*='Generate' i]"
    ]) {
      for (const btn of document.querySelectorAll(sel)) {
        if (btn && isVisible(btn) && !btn.closest("#zs-root")) {
          return btn;
        }
      }
    }
    // Search within composerFrame
    const area = composerFrame() || document;
    for (const b of area.querySelectorAll("button, [role='button']")) {
      if (b.closest("#zs-root") || !isVisible(b)) continue;
      const aria = (b.getAttribute("aria-label") || "").toLowerCase();
      const cls = (b.className || "").toLowerCase();
      if ((aria.includes("send") || aria.includes("enviar") || cls.includes("send")) && !aria.includes("mic")) {
        return b;
      }
    }
    return null;
  }

  function stopButton() {
    for (const sel of [
      "button.stop-button",
      "button[aria-label*='Stop' i]",
      "button[aria-label*='Detener' i]",
      "button[aria-label*='Cancel' i]"
    ]) {
      const btn = document.querySelector(sel);
      if (btn && isVisible(btn) && !btn.closest("#zs-root")) {
        return btn;
      }
    }
    return null;
  }

  function isGenerating() {
    if (stopButton()) return true;
    const spinner = document.querySelector("mat-progress-spinner, mat-spinner, .mat-mdc-progress-spinner, [class*='loading'], [class*='spinner']");
    if (spinner && isVisible(spinner) && !spinner.closest("#zs-root")) {
      return true;
    }
    return false;
  }

  function stopGeneration() {
    const btn = stopButton();
    if (btn) {
      btn.click();
      return true;
    }
    return false;
  }

  // ── Turn Classification ───────────────────────────────────────────────────
  function allItems() {
    const items = [...document.querySelectorAll(S.anyItem)];
    return items.filter((it) => isVisible(it) && !it.closest("#zs-root"));
  }

  function isUserItem(item) {
    if (!item) return false;
    const cls = item.className || "";
    return cls.includes("user") || !!item.querySelector(S.userItem);
  }

  function isAssistantItem(item) {
    if (!item) return false;
    return !isUserItem(item);
  }

  function itemText(item) {
    if (!item) return "";
    return item.textContent || "";
  }

  function classifyText(item, excludeSel) {
    if (!item) return "";
    if (excludeSel) {
      const clone = item.cloneNode(true);
      clone.querySelectorAll(excludeSel).forEach((e) => e.remove());
      return clone.textContent || "";
    }
    return item.textContent || "";
  }

  function lastAssistant() {
    const items = allItems().filter(isAssistantItem);
    return items.length ? items[items.length - 1] : null;
  }

  function readAssistant(item) {
    return item ? itemText(item) : "";
  }

  function userCount() {
    return allItems().filter(isUserItem).length;
  }

  function assistantCount() {
    return allItems().filter(isAssistantItem).length;
  }

  function chatIsEmpty() {
    return allItems().length === 0;
  }

  function isFreshChat() {
    return chatIsEmpty() && !!getEditor();
  }

  // ── Text Injection & Submission ───────────────────────────────────────────
  async function typeAndSend(text, images = []) {
    const ed = getEditor();
    if (!ed) {
      diag("aistudio.error", { msg: "Editor not found" });
      return false;
    }

    try { ed.focus(); } catch {}

    if (ed.tagName === "TEXTAREA" || ed.tagName === "INPUT") {
      const proto = ed.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) {
        setter.call(ed, text);
      } else {
        ed.value = text;
      }
      ed.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
      ed.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));
    } else {
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(ed);
      sel.removeAllRanges();
      sel.addRange(range);
      document.execCommand("delete", false, null);
      document.execCommand("insertText", false, text);
      ed.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
      ed.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));
    }

    await sleep(250);

    const btn = sendButton();
    if (btn) {
      if (btn.disabled) {
        btn.disabled = false;
        btn.removeAttribute("disabled");
        btn.removeAttribute("aria-disabled");
      }
      btn.click();
      diag("aistudio.sendClicked", { textLen: text.length });
      return true;
    }

    // Enter key fallback
    ed.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
    return true;
  }

  function setInputLock(on) {
    const ed = getEditor();
    if (!ed) return;
    const box = composerFrame() || ed;
    if (on) {
      box.setAttribute("data-zs-locked", "1");
      box.style.pointerEvents = "none";
    } else {
      box.removeAttribute("data-zs-locked");
      box.style.pointerEvents = "";
    }
  }

  function scanError() {
    const bodyText = document.body.innerText || "";
    if (RE.quotaLimit.test(bodyText)) {
      return "⚠️ Se superaron los límites de cuota en Google AI Studio para este modelo/cuenta. Puedes cambiar de cuenta o reanudar más tarde.";
    }
    if (RE.contextLimit.test(bodyText)) {
      return "⚠️ Límite de contexto alcanzado en AI Studio.";
    }
    return null;
  }

  function installSendHooks(handlers) {
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      const ed = getEditor();
      if (!ed || !ed.contains(e.target)) return;
      if (editorText().trim() === "") return;
      if (handlers.isBlocked()) return;

      if (!handlers.isStarted()) {
        if (!chatIsEmpty()) return;
        handlers.onBlockedAttempt();
        return;
      }
      handlers.onUserMessage(assistantCount());
    }, true);

    document.addEventListener("click", (e) => {
      if (!getEditor()) return;
      const t = e.target;
      const stop = t && t.closest && t.closest(S.stopBtn);
      if (stop) {
        handlers.onNativeStop();
        return;
      }
      const btn = t && t.closest && t.closest(S.sendBtn);
      if (!btn) return;
      if (handlers.isBlocked()) return;

      if (!handlers.isStarted()) {
        if (!chatIsEmpty()) return;
        handlers.onBlockedAttempt();
        return;
      }
      handlers.onUserMessage(assistantCount());
    }, true);
  }

  const CMD_SHAPE = /(?:###LUA###|```(?:json)?\s*\{\s*"tool"|"tool"\s*:\s*"hermes_|"tool"\s*:\s*"android_)/i;
  function findToolBlockSpot(item) {
    const replies = [...item.querySelectorAll(S.reply)];
    let hidAny = null;
    for (const mc of replies) {
      mc.querySelectorAll(S.codeWrap).forEach((cb) => {
        if (cb.closest(".zs-chip")) return;
        if (CMD_SHAPE.test(cb.textContent || "")) {
          cb.classList.add("zs-tool-hide");
          mc.classList.add("zs-cmd-mask");
          hidAny = hidAny || { parent: cb.parentElement, ref: cb };
        }
      });
    }
    return hidAny;
  }

  const PROMPT_EXTRA = `
━━━ DIRECTIVAS OBLIGATORIAS PARA GOOGLE AI STUDIO (MODO DESARROLLO ANDROID NATIVO & AUTOMATIZACIÓN GHS) ━━━
1. REGLA ESTRICTA DE CONTINUIDAD TÉCNICA (CERO TEXTO SIN HERRAMIENTAS):
   - En cada turno de trabajo de desarrollo, creación de código o compilación, DEBES emitir OBLIGATORIAMENTE un bloque de código JSON con herramientas locales ('android_sync_artifacts', 'android_build_apk', 'android_install_adb', 'hermes_terminal', 'hermes_file_write').
   - Queda estrictamente prohibido responder solo con texto explicativo sin emitir el bloque JSON de herramienta.
2. SINCRONIZACIÓN Y COMPILACIÓN AUTOMÁTICA DE ANDROID:
   - Para aplicaciones Android (Kotlin/Gradle), sincroniza los artefactos generados en tu disco local mediante 'android_sync_artifacts'.
   - Compila el proyecto con 'android_build_apk' o 'hermes_terminal' ejecutando './gradlew assembleDebug'.
   - Si el teléfono está conectado por USB o WiFi ADB, instálalo con 'android_install_adb'.
3. BUCLE DE AUTO-SANACIÓN ACTIVA (SELF-HEALING):
   - Si recibes un mensaje que comienza con '[AUTO-SANACIÓN ACTIVA: ...]', significa que la compilación o terminal falló. Analiza el error específico de Gradle o Kotlin y emite de inmediato la corrección en tu siguiente bloque de herramienta.`;

  return {
    id: "aistudio",
    displayName: "AI Studio",
    supportsVision: true,
    timings,
    thinkingSel: null,
    chipAtItemLevel: true,
    coverPad: 6,
    coverOffsetY: -4,
    reliableCounts: true,
    resendSystemEvery: 8,
    init({ diag: d } = {}) { if (d) diag = d; },
    // turns
    allItems,
    isUserItem,
    isAssistantItem,
    itemText,
    classifyText,
    assistantCount,
    userCount,
    lastAssistant,
    readAssistant,
    streamLen: (item) => (item ? (item.textContent || "").length : 0),
    snapshot: () => ({
      editor: !!getEditor(),
      editorTextLen: editorText().length,
      sendBtn: !!sendButton(),
      isGen: isGenerating(),
      url: window.location.href
    }),
    // composer / state
    getEditor,
    editorText,
    chatIsEmpty,
    isFreshChat,
    composerFrame,
    barMount,
    barAnchor: () => null,
    setInputLock,
    typeAndSend,
    stopGeneration,
    isGenerating,
    isBusyNow: () => false,
    isHardGenerating: () => isGenerating(),
    enforceComposer: () => {},
    ensureComposerReady: async () => true,
    turnHalted: () => false,
    findContinueBtn: () => null,
    clickContinueBtn: () => false,
    scanError,
    isTooLongMsg: (msg) => RE.tooLong.test(msg || ""),
    isBusyMsg: (msg) => RE.busy.test(msg || ""),
    // actions
    attachImages: async () => false,
    clearAttachments: () => {},
    conversationKey: () => window.location.pathname,
    installSendHooks,
    findToolBlockSpot,
    promptExtra: PROMPT_EXTRA
  };
})();
