/**
 * The floating panel, top frame only.
 *
 * Lives in a shadow root so no portal stylesheet can reach it — Workday in
 * particular sets aggressive global styles on buttons and lists.
 */
(function () {
  const TA = (window.__TA = window.__TA || {});

  // Colors live in tokens.css, loaded into this shadow root. The root cannot
  // inherit the page or the dashboard stylesheet.
  const STYLES = `
    :host { all: initial; }

    .panel {
      position: fixed; right: 20px; bottom: 20px; z-index: 2147483647;
      width: 304px;
      border-radius: 22px;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 13px; line-height: 1.45;
      -webkit-font-smoothing: antialiased;
    }
    * { box-sizing: border-box; font-family: inherit; }
    .panel.collapsed { width: auto; }

    .head {
      display: flex; align-items: center; gap: 8px;
      padding: 11px 12px; cursor: grab; user-select: none;
      border-bottom: 1px solid var(--border);
    }
    .panel.collapsed .head { border-bottom: none; }
    .head:active { cursor: grabbing; }
    .bolt {
      width: 22px; height: 22px; border-radius: 6px; flex: none;
      display: block; object-fit: cover;
    }
    .name {
      flex: 1; font-weight: 600; font-size: 13px;
      letter-spacing: -.01em; color: var(--text-primary);
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--surface-3); flex: none; }
    .dot.up { background: var(--success); }
    .dot.down { background: var(--danger); }
    .icon-btn {
      border: none; background: transparent; cursor: pointer;
      padding: 2px 5px; color: var(--text-muted); font-size: 14px;
      line-height: 1; border-radius: 6px;
    }
    .icon-btn:hover { background: var(--surface-2); color: var(--text-primary); }

    .body { padding: 12px; display: grid; gap: 10px; }
    .panel.collapsed .body { display: none; }

    .job { font-size: 12px; color: var(--text-secondary); }
    .job b { color: var(--text-primary); font-weight: 600; }
    .job .ats {
      display: inline-block; margin-top: 4px; padding: 2px 7px; border-radius: 6px;
      background: var(--surface-2); color: var(--text-secondary);
      font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em;
    }

    .step {
      justify-self: start; padding: 3px 8px; border-radius: 6px;
      background: var(--surface-2); color: var(--text-secondary);
      font-size: 10.5px; font-weight: 600;
    }

    .actions { display: grid; gap: 6px; }

    .btn {
      width: 100%; border: none; border-radius: 12px; padding: 9px 12px;
      font-size: 13px; font-weight: 500; cursor: pointer;
      transition: transform .06s ease, filter .15s ease, background .15s ease;
    }
    .btn:active { transform: scale(.985); }
    .btn:disabled { opacity: .5; cursor: not-allowed; }
    .btn.primary { background: var(--accent); color: var(--on-signal); }
    .btn.primary:hover:not(:disabled) { filter: brightness(1.08); }
    .btn.ghost {
      background: var(--surface-2); color: var(--text-primary);
      border: 1px solid var(--border);
    }
    .btn.ghost:hover:not(:disabled) { background: var(--surface-3); }
    .row { display: flex; gap: 6px; }
    .row .btn { flex: 1; }

    .stats { display: flex; gap: 6px; }
    .stat {
      flex: 1; text-align: center; padding: 8px 4px;
      border-radius: 10px; background: var(--surface-2);
      border: 1px solid var(--border);
    }
    .stat b { display: block; font-size: 15px; font-weight: 600; letter-spacing: -.02em; }
    .stat span {
      font-size: 10px; color: var(--text-muted);
      text-transform: uppercase; letter-spacing: .04em;
    }
    .stat.ok b { color: var(--success); }
    .stat.low b { color: var(--warning); }
    .stat.todo b { color: var(--danger); }

    .todo { display: grid; gap: 4px; max-height: 132px; overflow-y: auto; }
    .todo-item {
      width: 100%; text-align: left; cursor: pointer;
      background: var(--warning-bg); border: 1px solid transparent;
      color: var(--text-primary); border-radius: 8px;
      padding: 6px 9px; font-size: 11.5px;
    }
    .todo-item:hover { border-color: var(--warning); }

    .msg { font-size: 11.5px; padding: 8px 10px; border-radius: 8px; }
    .msg.err {
      background: var(--danger-bg); color: var(--danger);
      border: 1px solid var(--danger-border);
    }
    .msg.info { background: var(--surface-2); color: var(--text-secondary); }
    .msg a { color: var(--signal-ink); }
    .msg b { font-weight: 600; }

    .foot { font-size: 10.5px; color: var(--text-muted); text-align: center; }
    kbd {
      background: var(--surface-2); border: 1px solid var(--border);
      border-bottom-width: 2px; border-radius: 5px; padding: 0 4px;
      font-size: 10px; font-family: ui-monospace, SFMono-Regular, monospace;
    }

    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-thumb { background: var(--surface-3); border-radius: 3px; }
  `;

  const HTML = `
    <div class="panel liquid-glass" part="panel">
      <div class="head" data-drag>
        <img class="bolt" alt="" />
        <div class="name">Quill</div>
        <div class="dot" data-dot title="backend status"></div>
        <button class="icon-btn" data-toggle title="Collapse">–</button>
        <button class="icon-btn" data-close title="Hide on this page">×</button>
      </div>
      <div class="body">
        <div class="job" data-job></div>
        <div class="step" data-step hidden></div>
        <button class="btn primary" data-fill>Autofill</button>
        <button class="btn ghost" data-fill-section title="Fill only one part of this form">Fill a section</button>
        <button class="btn ghost" data-tailor title="Tailor your resume to this job's description. Select the description first if it isn't found.">Tailor resume</button>
        <div class="actions" data-actions-gate hidden></div>
        <div data-results hidden>
          <div class="stats">
            <div class="stat ok"><b data-n-ok>0</b><span>filled</span></div>
            <div class="stat low"><b data-n-low>0</b><span>check</span></div>
            <div class="stat todo"><b data-n-todo>0</b><span>you</span></div>
          </div>
        </div>
        <div class="todo" data-todo hidden></div>
        <div data-msg hidden></div>
        <div class="row" data-actions hidden>
          <button class="btn ghost" data-refill>Fill again</button>
          <button class="btn ghost" data-applied>Mark applied</button>
        </div>
        <div class="foot">Review before you submit · <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>F</kbd></div>
      </div>
    </div>
  `;

  let tokenCss = "";
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.getURL) {
    fetch(chrome.runtime.getURL("tokens.css"))
      .then((res) => res.text())
      .then((css) => {
        tokenCss = css;
        const style = root && root.querySelector("style");
        if (style) style.textContent = tokenCss + STYLES;
      })
      .catch(() => {});
  }

  let host = null;
  let root = null;
  let el = {};
  let wired = false;       // handlers attached to the current panel
  let onClose = null;

  /** Forget the panel entirely, so the next mount builds a fresh one. */
  function teardown() {
    if (host) host.remove();
    host = null;
    root = null;
    el = {};
    wired = false;
  }

  function build() {
    // A panel the page itself removed (a portal re-rendering <body>) is
    // rebuilt rather than written to while detached.
    if (host && !host.isConnected) teardown();
    if (host) return;
    host = document.createElement("div");
    host.id = "tempoapply-widget-host";
    host.style.cssText = "all: initial; position: static;";
    root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = tokenCss + STYLES;
    root.appendChild(style);
    const wrap = document.createElement("div");
    wrap.innerHTML = HTML;
    const mark = wrap.querySelector(".bolt");
    if (mark && typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.getURL) {
      mark.src = chrome.runtime.getURL("icons/icon32.png");
    }
    root.appendChild(wrap);
    (document.body || document.documentElement).appendChild(host);

    const q = (sel) => root.querySelector(sel);
    el = {
      panel: q(".panel"),
      head: q("[data-drag]"),
      job: q("[data-job]"),
      dot: q("[data-dot]"),
      fill: q("[data-fill]"),
      fillSection: q("[data-fill-section]"),
      tailor: q("[data-tailor]"),
      gate: q("[data-actions-gate]"),
      step: q("[data-step]"),
      results: q("[data-results]"),
      nOk: q("[data-n-ok]"),
      nLow: q("[data-n-low]"),
      nTodo: q("[data-n-todo]"),
      todo: q("[data-todo]"),
      msg: q("[data-msg]"),
      actions: q("[data-actions]"),
      refill: q("[data-refill]"),
      applied: q("[data-applied]"),
      toggle: q("[data-toggle]"),
      close: q("[data-close]"),
    };

    el.toggle.addEventListener("click", () => {
      const collapsed = el.panel.classList.toggle("collapsed");
      el.toggle.textContent = collapsed ? "+" : "–";
    });
    // Closing clears `host` too. Leaving it set meant `build` returned early
    // forever after, so Show could never bring the panel back, while every
    // later mount wired more listeners onto the detached copy.
    el.close.addEventListener("click", () => {
      teardown();
      if (onClose) onClose();
    });
    makeDraggable();
  }

  // Drag state lives here, and the window listeners are added once: the panel
  // can be closed and rebuilt, and each rebuild must not stack another pair.
  let startX = 0, startY = 0, originX = 0, originY = 0, dragging = false;
  let windowDragWired = false;

  function makeDraggable() {
    el.head.addEventListener("mousedown", (e) => {
      if (e.target.closest(".icon-btn")) return;
      dragging = true;
      const rect = el.panel.getBoundingClientRect();
      startX = e.clientX; startY = e.clientY;
      originX = rect.left; originY = rect.top;
      e.preventDefault();
    });
    if (windowDragWired) return;
    windowDragWired = true;
    window.addEventListener("mousemove", (e) => {
      if (!dragging || !el.panel) return;
      const x = Math.max(4, Math.min(window.innerWidth - 60, originX + e.clientX - startX));
      const y = Math.max(4, Math.min(window.innerHeight - 40, originY + e.clientY - startY));
      el.panel.style.left = `${x}px`;
      el.panel.style.top = `${y}px`;
      el.panel.style.right = "auto";
      el.panel.style.bottom = "auto";
    });
    window.addEventListener("mouseup", () => { dragging = false; });
  }

  TA.widget = {
    /**
     * Show the panel and wire its buttons. Idempotent: returns true only when
     * a panel was actually built and wired by this call, so the caller does
     * its one-time work (the backend ping) once per panel, not once per DOM
     * mutation.
     */
    mount(handlers) {
      build();
      if (wired) return false;
      wired = true;
      el.fill.addEventListener("click", handlers.onFill);
      if (handlers.onFillSection) {
        el.fillSection.addEventListener("click", handlers.onFillSection);
      }
      if (handlers.onTailor) {
        el.tailor.addEventListener("click", handlers.onTailor);
      }
      el.refill.addEventListener("click", handlers.onRefill);
      el.applied.addEventListener("click", handlers.onApplied);
      this.onTodoClick = handlers.onTodoClick;
      onClose = handlers.onClose || null;
      return true;
    },

    /** Escape page- or server-derived text before it reaches `message`. */
    escape: (text) => escapeHtml(text == null ? "" : text),

    isMounted: () => !!host && host.isConnected,

    /** Disable Tailor while a run is in flight, and relabel it. */
    setTailoring(on, label) {
      if (!host || !el.tailor) return;
      el.tailor.disabled = !!on;
      el.tailor.textContent = label || (on ? "Tailoring…" : "Tailor resume");
    },

    setBackend(up, detail) {
      if (!host) return;
      el.dot.className = `dot ${up ? "up" : "down"}`;
      el.dot.title = detail || (up ? "backend connected" : "backend unreachable");
    },

    setJob(title, company, ats) {
      if (!host) return;
      const name = [title, company].filter(Boolean).join(" · ");
      el.job.innerHTML = name
        ? `<b>${escapeHtml(title || "")}</b>${company ? ` · ${escapeHtml(company)}` : ""}` +
          (ats && ats !== "unknown" ? `<br><span class="ats">${escapeHtml(ats)}</span>` : "")
        : "Ready on this page.";
    },

    /**
     * Portal gates (Workday's chooser, its sign-in step). Swaps the Autofill
     * button for the one thing that actually moves the user forward.
     */
    setStage(stage, handlers) {
      if (!host) return;
      el.step.hidden = !stage.step;
      el.step.textContent = stage.step || "";

      const actions = stage.actions || [];
      if (stage.stage === "gate" && actions.length) {
        el.fill.hidden = true;
        el.gate.hidden = false;
        el.gate.innerHTML = "";
        // First choice is the recommended one and gets the primary styling.
        actions.forEach((action, i) => {
          const btn = document.createElement("button");
          btn.className = `btn ${i === 0 ? "primary" : "ghost"}`;
          btn.textContent = action.label;
          btn.addEventListener("click", () => handlers.onAction(action.selector));
          el.gate.appendChild(btn);
        });
      } else {
        el.fill.hidden = false;
        el.gate.hidden = true;
        el.gate.innerHTML = "";
      }
    },

    setBusy(busy, label) {
      if (!host) return;
      el.fill.disabled = busy;
      el.refill.disabled = busy;
      el.fill.textContent = busy ? label || "Filling…" : "Autofill";
    },

    showResults({ filled, low, todo }) {
      if (!host) return;
      el.results.hidden = false;
      el.actions.hidden = false;
      el.nOk.textContent = filled;
      el.nLow.textContent = low;
      el.nTodo.textContent = todo;
      el.fill.textContent = "Autofill";
    },

    showTodo(labels) {
      if (!host) return;
      el.todo.innerHTML = "";
      el.todo.hidden = labels.length === 0;
      labels.forEach((label) => {
        const btn = document.createElement("button");
        btn.className = "todo-item";
        btn.textContent = label.length > 70 ? `${label.slice(0, 70)}…` : label;
        btn.addEventListener("click", () => this.onTodoClick && this.onTodoClick(label));
        el.todo.appendChild(btn);
      });
    },

    message(text, kind) {
      if (!host) return;
      el.msg.hidden = !text;
      el.msg.className = `msg ${kind || "info"}`;
      el.msg.innerHTML = text || "";
    },

    setAppliedLabel(text) {
      if (!host) return;
      el.applied.textContent = text;
    },
  };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
})();
