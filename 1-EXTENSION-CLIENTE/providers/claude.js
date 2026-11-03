// SPDX-License-Identifier: GPL-3.0-or-later
// providers/claude.js - Anthropic Claude (claude.ai) provider for GHS / GHS.
// Implements the unified ZSProvider interface so Claude can act as Architect,
// Fullstack Developer, or QA in the autonomous swarm.

// eslint-disable-next-line no-unused-vars
const ZSProvider = (() => {
  "use strict";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let diag = () => {};

  const S = {
    userItem: '[data-testid="user-message"], .font-user-message, div[class*="userMessage"]',
    assistantItem: '[data-testid="assistant-message"], .font-claude-message, [data-is-streaming], div[class*="aiMessage"]',
    reply: ".font-claude-message, .standard-markdown, div.grid",
    // Claude 3.7 / Extended Thinking reasoning container: exclude from tool parsing
    // so drafts in internal thoughts are never executed as live commands.
    thinking: '[data-testid="thinking-container"], div[class*="thinking"], div[class*="thought"], summary',
    editor: 'div[data-testid="chat-input"], div.tiptap.ProseMirror[contenteditable="true"], div.ProseMirror[contenteditable="true"], div[contenteditable="true"][data-placeholder], div[contenteditable="true"]',
    composerCard: 'div[data-tap-focuses-field], [class*="shadow-composer"], .rounded-\\[20px\\]',
    submitBtn: 'button[data-testid="chat-input-send"], button[aria-label*="Send" i], button[aria-label*="Enviar" i], button[aria-label*="send message" i]',
    stopBtn: 'button[aria-label*="Stop" i], button[aria-label*="Detener" i], button[aria-label*="Cancel" i]',
    codeWrap: 'pre, div[class*="code-block"], div[class*="codeBlock"]',
    errorSurfaces: '[role="alert"], [data-testid*="error"], [class*="error-message"], div[class*="danger"]',
  };

  const RE = {
    contextLimit: new RegExp(
      [
        "conversation.{0,20}(too long|trop long)",
        "context.{0,20}(limit|exceeded|length)",
        "message.{0,20}limit",
        "free plan.{0,20}limit",
        "usage limit",
        "limit reached",
        "resets at",
        "out of messages",
      ].join("|"),
      "i"
    ),
    tooLong: /conversation .{0,20}(too long|getting too long|length limit)|exceeded the length limit/i,
    busy: /something went wrong|try again later|claude is experiencing high traffic|rate.?limit|too many requests|temporarily unavailable/i,
    continueBtn: /^(continue generating|continue|continuer)$/i,
  };

  const timings = {
    GEN_IDLE_MS: 1500,
    REASON_IDLE_MS: 12000,
    WARMUP_MS: 45000,
    REASON_NOREPLY_MS: 90000,
    STABLE_MS: 9000,
    RESPONSE_TIMEOUT_MS: 300000,
  };

  // ── Turn classification ───────────────────────────────────────────────────
  function allItems() {
    const userNodes = [
      ...document.querySelectorAll(
        '[data-testid="user-message"], .font-user-message, div[class*="font-user"]'
      ),
    ];
    const asstNodes = [
      ...document.querySelectorAll(
        '[data-testid="assistant-message"], .font-claude-message, div[class*="font-claude"], div.standard-markdown, div[class*="standard-markdown"], [data-is-streaming]'
      ),
    ];

    // Fallback: if no explicit assistant nodes found, find rendered markdown containers outside user turns
    if (!asstNodes.length) {
      const candidates = [...document.querySelectorAll('div[class*="markdown"], div[class*="prose"], div[class*="message"]')];
      candidates.forEach((c) => {
        if (!userNodes.some((u) => u.contains(c))) asstNodes.push(c);
      });
    }

    const all = [...userNodes, ...asstNodes];
    all.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));

    const items = [];
    const seen = new Set();
    for (const n of all) {
      let wrap =
        n.closest('[data-test-render-count]') ||
        n.closest('[data-testid*="message"]') ||
        n.closest('div[class*="ChatMessage"]') ||
        n.closest('div[class*="group"]') ||
        n.closest('.grid') ||
        n;
      if (!seen.has(wrap)) {
        seen.add(wrap);
        items.push(wrap);
      }
    }
    return items;
  }

  const isUserItem = (item) => {
    if (!item) return false;
    if (item.matches && (item.matches(S.userItem) || item.matches('[data-testid="user-message"], .font-user-message'))) return true;
    return !!item.querySelector('[data-testid="user-message"], .font-user-message, ' + S.userItem);
  };

  const isAssistantItem = (item) => {
    if (!item) return false;
    if (isUserItem(item)) return false;
    if (
      item.matches &&
      (item.matches(S.assistantItem) ||
        item.matches(
          '[data-testid="assistant-message"], .font-claude-message, [data-is-streaming], div[class*="font-claude"], div.standard-markdown'
        ))
    )
      return true;
    return !!item.querySelector(
      '[data-testid="assistant-message"], .font-claude-message, [data-is-streaming], div[class*="font-claude"], div.standard-markdown, ' +
        S.assistantItem
    );
  };

  const assistantItems = () => allItems().filter(isAssistantItem);
  const assistantCount = () => assistantItems().length;
  const userCount = () => allItems().filter(isUserItem).length;

  const lastAssistant = () => {
    const it = assistantItems();
    return it.length ? it[it.length - 1] : null;
  };

  // ── Text extraction ───────────────────────────────────────────────────────
  const BLOCK_TAGS = /^(?:P|DIV|PRE|LI|UL|OL|BLOCKQUOTE|H[1-6]|TABLE|TR|SECTION|ARTICLE|HR)$/;

  function textWithout(root, excludeSel) {
    if (!root) return "";
    let t = "";
    const breakLine = () => {
      if (t && !t.endsWith("\n")) t += "\n";
    };
    const skip =
      ".zs-chip" +
      (excludeSel ? ", " + excludeSel : "") +
      (S.thinking ? ", " + S.thinking : "");

    const walk = (n) => {
      if (n.nodeType === 3) {
        t += n.nodeValue;
        return;
      }
      if (n.nodeType !== 1) return;
      if (skip && n.matches && n.matches(skip)) return;
      if (n.tagName === "BR") {
        t += "\n";
        return;
      }
      const isBlock = BLOCK_TAGS.test(n.tagName);
      if (isBlock) breakLine();
      for (const c of n.childNodes) walk(c);
      if (isBlock) breakLine();
    };
    walk(root);
    return t;
  }

  function itemText(item) {
    if (!item) return "";
    if (isAssistantItem(item)) {
      const replies = [...item.querySelectorAll(S.reply)];
      if (replies.length) {
        const joined = replies.map((r) => textWithout(r)).join("\n").trim();
        if (joined) return joined;
      }
    }
    return textWithout(item).trim();
  }

  function classifyText(item, excludeSel) {
    if (isAssistantItem(item)) {
      const replies = [...item.querySelectorAll(S.reply)];
      if (replies.length) {
        const joined = replies.map((r) => textWithout(r, excludeSel)).join("\n").trim();
        if (joined) return joined;
      }
    }
    return textWithout(item, excludeSel).trim();
  }

  function readAssistant() {
    const item = lastAssistant();
    if (!item) return { present: false, reply: "", thinking: "", item: null };
    const reply = itemText(item);
    return {
      present: true,
      reply: reply.trim(),
      thinking: "",
      item,
    };
  }

  function streamLen(item) {
    return (itemText(item) || "").length;
  }

  function snapshot() {
    return {
      assistantTurns: assistantCount(),
      userTurns: userCount(),
      lastId: assistantCount(),
    };
  }

  // ── Composer & controls ───────────────────────────────────────────────────
  function getEditor() {
    return document.querySelector(S.editor);
  }

  function editorText() {
    const ed = getEditor();
    return ed ? (ed.textContent || "").trim() : "";
  }

  function chatIsEmpty() {
    return allItems().length === 0;
  }

  function isFreshChat() {
    return (
      chatIsEmpty() &&
      (location.pathname === "/new" ||
        location.pathname === "/" ||
        location.pathname.endsWith("/new")) &&
      !!getEditor()
    );
  }

  // Claude is a React app with a rich-text composer. We do NOT insert foreign DOM nodes
  // into div[data-tap-focuses-field] (which causes React reconciliation collisions).
  // Instead, barAnchor returns the rounded composer card: the core positions #zs-bar
  // (fixed) to hug the card's top edge at exact full width, and reserves that strip
  // with paddingTop so the bar sits seamlessly integrated without overlapping the editor.
  function barAnchor() {
    const ed = getEditor();
    if (!ed) return null;
    return (
      ed.closest("div[data-tap-focuses-field]") ||
      ed.closest("[class*='shadow-composer']") ||
      ed.closest(".rounded-\\[20px\\]") ||
      null
    );
  }

  function composerFrame() {
    return barAnchor() || (getEditor() && getEditor().parentElement);
  }

  function coverTarget() {
    const ed = getEditor();
    if (!ed) return null;
    return ed.closest(".relative.font-large") || ed.parentElement;
  }

  function barMount() {
    return null; // Handled via barAnchor for clean integration
  }

  let _locked = false;
  function setInputLock(lock) {
    _locked = lock;
    const ed = getEditor();
    if (ed) ed.setAttribute("contenteditable", lock ? "false" : "true");
  }

  function stopButton() {
    return document.querySelector(S.stopBtn);
  }

  function isStopBtn(el) {
    if (!el) return false;
    const label = (el.getAttribute("aria-label") || "").toLowerCase();
    return (
      label.includes("stop") ||
      label.includes("detener") ||
      label.includes("cancel") ||
      label.includes("interrompre")
    );
  }

  function sendButton() {
    const candidates = [...document.querySelectorAll(S.submitBtn)];
    for (const b of candidates) {
      if (!isStopBtn(b)) return b;
    }
    return null;
  }

  function isGenerating() {
    if (stopButton()) return true;
    if (document.querySelector('[data-is-streaming="true"], .is-streaming')) return true;
    return false;
  }

  function isBusyNow() {
    return isGenerating();
  }

  function isHardGenerating() {
    return isGenerating();
  }

  function stopGeneration() {
    const b = stopButton();
    if (b) {
      try {
        b.click();
      } catch {}
    }
  }

  async function waitFor(pred, timeoutMs = 2000, stepMs = 50) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      try {
        if (pred()) return true;
      } catch {}
      await sleep(stepMs);
    }
    return false;
  }

  function selectAll(ed) {
    ed.focus();
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(ed);
    sel.removeAllRanges();
    sel.addRange(range);
  }

  async function typeEditorText(ed, text) {
    selectAll(ed);
    const lines = String(text).split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (lines[i]) document.execCommand("insertText", false, lines[i]);
      if (i < lines.length - 1) document.execCommand("insertLineBreak");
      if (i && i % 25 === 0) await sleep(0);
    }
    ed.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
    ed.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        cancelable: true,
        inputType: "insertText",
        data: text,
      })
    );
    await sleep(60);
  }

  const SEND_MAX_CHARS = 100000;
  function truncateForSend(text) {
    if (!text || text.length <= SEND_MAX_CHARS) return text;
    const budget = SEND_MAX_CHARS - 300;
    const head = Math.floor(budget * 0.85);
    const marker = `\n\n[…GHS: Salida truncada para evitar saturación del buffer en Claude…]\n\n`;
    return (
      text.slice(0, head) +
      marker +
      text.slice(text.length - (budget - head))
    );
  }

  async function typeAndSend(text, images) {
    const ed = getEditor();
    if (!ed) throw new Error("Claude input box not found");
    text = truncateForSend(text);

    const relock = _locked;
    if (relock) ed.setAttribute("contenteditable", "true");
    try {
      await typeEditorText(ed, text);

      if (images && images.length) {
        try {
          await attachImages(images);
        } catch {}
        await sleep(1200);
      }

      await waitFor(() => !!sendButton() || !!stopButton(), 2000);

      const btn = sendButton();
      if (btn && !btn.disabled) {
        btn.click();
        return;
      }

      // Fallback: Dispatch Enter event in ProseMirror
      const o = {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
        cancelable: true,
      };
      ed.dispatchEvent(new KeyboardEvent("keydown", o));
      ed.dispatchEvent(new KeyboardEvent("keyup", o));
    } finally {
      if (relock) {
        const e2 = getEditor();
        if (e2) e2.setAttribute("contenteditable", "false");
      }
    }
  }

  function enforceComposer() {
    return { ready: true };
  }

  async function ensureComposerReady(reason) {
    diag("mode_ready", { reason, provider: "claude" });
    return { ready: !!getEditor() };
  }

  // ── Error detection ───────────────────────────────────────────────────────
  function scanError() {
    const alerts = document.querySelectorAll(S.errorSurfaces);
    for (const el of alerts) {
      const t = (el.textContent || "").trim();
      if (!t) continue;
      if (RE.contextLimit.test(t)) {
        return { kind: "context_limit", text: t };
      }
      if (RE.busy.test(t)) {
        return { kind: "busy", text: t };
      }
    }
    return null;
  }

  const isTooLongMsg = (t) => RE.tooLong.test(t || "");
  const isBusyMsg = (t) => RE.busy.test(t || "");

  function turnHalted() {
    return false;
  }

  function findContinueBtn() {
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
      if (RE.continueBtn.test((b.textContent || "").trim())) return b;
    }
    return null;
  }

  function clickContinueBtn() {
    const b = findContinueBtn();
    if (b) {
      try {
        b.click();
        return true;
      } catch {}
    }
    return false;
  }

  // ── Vision & attachments ──────────────────────────────────────────────────
  async function attachImages(images) {
    if (!images || !images.length) return;
    const input = document.querySelector('input[type="file"]');
    if (!input) return;
    try {
      const dt = new DataTransfer();
      for (const img of images) {
        if (img instanceof File) {
          dt.items.add(img);
        } else if (img.data && img.mimeType) {
          const byteChars = atob(img.data);
          const byteNums = new Array(byteChars.length);
          for (let i = 0; i < byteChars.length; i++) {
            byteNums[i] = byteChars.charCodeAt(i);
          }
          const blob = new Blob([new Uint8Array(byteNums)], { type: img.mimeType });
          const file = new File([blob], img.name || "snapshot.png", { type: img.mimeType });
          dt.items.add(file);
        }
      }
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (e) {
      diag("attach.error", { err: String(e) });
    }
  }

  function clearAttachments() {}

  function conversationKey() {
    return location.pathname;
  }

  // ── User-send interception ────────────────────────────────────────────────
  function installSendHooks(handlers) {
    document.addEventListener(
      "keydown",
      (e) => {
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
      },
      true
    );

    document.addEventListener(
      "click",
      (e) => {
        if (!getEditor()) return;
        const t = e.target;
        const stop = t && t.closest && t.closest(S.stopBtn);
        if (stop) {
          handlers.onNativeStop();
          return;
        }
        const btn = t && t.closest && t.closest(S.submitBtn);
        if (!btn || isStopBtn(btn)) return;
        if (handlers.isBlocked()) return;

        if (!handlers.isStarted()) {
          if (!chatIsEmpty()) return;
          handlers.onBlockedAttempt();
          return;
        }
        handlers.onUserMessage(assistantCount());
      },
      true
    );
  }

  // ── Tool block camouflage ─────────────────────────────────────────────────
  const CMD_SHAPE = /"(?:command|tool)"\s*:\s*"|###\s*lua|###mcp_tool###/i;

  function findToolBlockSpot(item) {
    const replies = [...item.querySelectorAll(S.reply)];
    let hidAny = null;
    const hide = (el, mc) => {
      el.classList.add("zs-tool-hide");
      if (mc) mc.classList.add("zs-cmd-mask");
      hidAny = hidAny || { parent: el.parentElement, ref: el };
    };

    for (const mc of replies) {
      mc.querySelectorAll(S.codeWrap).forEach((pre) => {
        if (pre.closest(".zs-chip")) return;
        if (CMD_SHAPE.test(pre.textContent || "")) hide(pre, mc);
      });
      [...mc.children].forEach((el) => {
        if (el.classList.contains("zs-chip") || el.querySelector(S.codeWrap)) return;
        const t = el.textContent || "";
        if (t.length < 600 && CMD_SHAPE.test(t)) hide(el, null);
      });
    }
    return hidAny;
  }

  return {
    id: "claude",
    displayName: "Claude",
    supportsVision: true,
    timings,
    thinkingSel: S.thinking,
    chipAtItemLevel: true,
    reliableCounts: true,
    resendSystemEvery: 6,
    init({ diag: d } = {}) {
      if (d) diag = d;
      try {
        document.documentElement.setAttribute("data-zs-claude-ver", "1.5.5");
      } catch {}
    },
    allItems,
    isUserItem,
    isAssistantItem,
    itemText,
    classifyText,
    assistantCount,
    userCount,
    lastAssistant,
    readAssistant,
    streamLen,
    snapshot,
    getEditor,
    editorText,
    chatIsEmpty,
    isFreshChat,
    composerFrame,
    barMount,
    barAnchor,
    coverTarget,
    setInputLock,
    typeAndSend,
    stopGeneration,
    isGenerating,
    isBusyNow,
    isHardGenerating,
    enforceComposer,
    ensureComposerReady,
    turnHalted,
    findContinueBtn,
    clickContinueBtn,
    scanError,
    isTooLongMsg,
    isBusyMsg,
    attachImages,
    clearAttachments,
    conversationKey,
    installSendHooks,
    findToolBlockSpot,
  };
})();
