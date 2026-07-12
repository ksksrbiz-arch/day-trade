/* terminal.js — client substrate for the intelligence terminal.
 *
 * Exposes a small global `window.T` that panel modules build on, so each panel
 * can ship as a NEW file under static/js/panels/ that self-registers, with no
 * edits to the monolithic index.html. Provides:
 *   T.quotes  — live quote store fed by an SSE delta stream (/api/quotes/stream)
 *   T.bus     — tiny pub/sub bus
 *   T.J       — fetch-json helper (null on error)
 *   T.registerPanel({id,title,tab,mount})  — inject a card + (maybe) a nav tab
 *   T.registerCommand({code,run,desc})     — function-code for the cmdk palette
 *   T.openSecurity(sym)                     — bus event 'security:open'
 *
 * This file is loaded via <script defer> so it runs after index.html's inline
 * bootstrap (setupTabs/refresh) has already executed.
 */
(function () {
  "use strict";
  if (window.T) return; // idempotent

  // ---- fetch helper -------------------------------------------------------
  async function J(u, opts) {
    try {
      const r = await fetch(u, opts);
      if (!r.ok) throw 0;
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  // ---- pub/sub bus --------------------------------------------------------
  const bus = (function () {
    const map = new Map();
    return {
      on(evt, cb) {
        if (!map.has(evt)) map.set(evt, new Set());
        map.get(evt).add(cb);
        return () => map.get(evt).delete(cb);
      },
      emit(evt, data) {
        (map.get(evt) || []).forEach((cb) => {
          try { cb(data); } catch (e) { /* isolate subscribers */ }
        });
      },
    };
  })();

  // ---- live quote store (SSE deltas) --------------------------------------
  const quotes = (function () {
    const store = new Map();       // symbol -> quote object
    const subs = new Map();        // symbol -> Set<cb>
    let es = null;

    function connect() {
      if (es || typeof window.EventSource === "undefined") return;
      try {
        es = new EventSource("/api/quotes/stream");
        es.onmessage = (ev) => {
          if (!ev.data) return;
          let q;
          try { q = JSON.parse(ev.data); } catch (e) { return; }
          if (!q || !q.symbol) return;
          const sym = q.symbol.toUpperCase();
          store.set(sym, Object.assign(store.get(sym) || {}, q));
          (subs.get(sym) || []).forEach((cb) => {
            try { cb(store.get(sym)); } catch (e) {}
          });
          bus.emit("quote", store.get(sym));
        };
        es.onerror = () => { /* EventSource auto-reconnects (retry: hint) */ };
      } catch (e) { es = null; }
    }

    return {
      get(sym) { return store.get((sym || "").toUpperCase()) || null; },
      all() { return Object.fromEntries(store); },
      subscribe(sym, cb) {
        connect();
        sym = (sym || "").toUpperCase();
        if (!subs.has(sym)) subs.set(sym, new Set());
        subs.get(sym).add(cb);
        const cur = store.get(sym);
        if (cur) { try { cb(cur); } catch (e) {} }
        return () => subs.get(sym) && subs.get(sym).delete(cb);
      },
      connect,
    };
  })();

  // ---- panel registry -----------------------------------------------------
  const panels = [];

  function activeTab() {
    const on = document.querySelector(".tabnav button.on");
    return (on && on.dataset && on.dataset.t) || "overview";
  }

  function ensureTab(tab, title) {
    if (!tab) return;
    const nav = document.querySelector(".tabnav");
    if (!nav) return;
    if (nav.querySelector(`button[data-t="${tab}"]`)) return;
    const b = document.createElement("button");
    b.dataset.t = tab;
    b.textContent = title || tab;
    b.setAttribute("onclick", `showTab('${tab}')`);
    nav.appendChild(b);
  }

  // ---- Launchpad: tiled / resizable / reorderable / savable workspace -----
  // Panels registered under the 'terminal' tab render as tiles in a 12-column
  // grid whose layout (order, width, collapsed, hidden) persists in
  // localStorage — a single-operator "Launchpad". Non-terminal tabs keep the
  // plain stacked-card behaviour.
  const LP_TAB = "terminal";
  const LP_KEY = "terminal.launchpad.v1";
  const LP_SPANS = [4, 6, 12]; // ⅓ · ½ · full
  let lpState = (function () {
    try { return JSON.parse(localStorage.getItem(LP_KEY)) || {}; } catch (e) { return {}; }
  })();
  function lpSave() {
    try { localStorage.setItem(LP_KEY, JSON.stringify(lpState)); } catch (e) {}
  }
  function lpField(name) { return (lpState[name] = lpState[name] || {}); }

  function injectLaunchpadCSS() {
    if (document.getElementById("lp-style")) return;
    const s = document.createElement("style");
    s.id = "lp-style";
    s.textContent =
      ".term-launchpad{padding:0}" +
      ".lp-toolbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 12px}" +
      ".lp-toolbar .lp-label{font-weight:700;font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:var(--muted,#8a94a6);margin-right:4px}" +
      ".lp-chip{cursor:pointer;border:1px solid var(--line,#2a3344);background:var(--panel2,#141922);color:#c8d0dc;border-radius:999px;padding:2px 10px;font-size:11px;user-select:none}" +
      ".lp-chip.off{opacity:.38}" +
      ".lp-reset{margin-left:auto;cursor:pointer;border:1px solid var(--line,#2a3344);background:transparent;color:#8a94a6;border-radius:6px;padding:2px 9px;font-size:11px}" +
      ".lp-grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;align-items:start}" +
      ".lp-tile{grid-column:span 6;background:var(--panel,#0f141c);border:1px solid var(--line,#222b38);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;min-width:0}" +
      ".lp-tile.collapsed .lp-body{display:none}" +
      ".lp-tile.dragging{opacity:.5}" +
      ".lp-head{display:flex;align-items:center;gap:6px;padding:8px 11px;cursor:grab;border-bottom:1px solid var(--line,#222b38);background:var(--panel2,#141922)}" +
      ".lp-head:active{cursor:grabbing}" +
      ".lp-name{font-weight:700;font-size:12px;letter-spacing:.4px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
      ".lp-btn{cursor:pointer;border:1px solid var(--line,#2a3344);background:transparent;color:#8a94a6;border-radius:5px;font-size:10px;padding:1px 6px;line-height:1.6}" +
      ".lp-btn.on{color:#e6ebf2;border-color:#3a4759}" +
      ".lp-body{padding:12px;overflow:auto;min-height:36px}" +
      ".lp-tile.drop-before{box-shadow:-3px 0 0 0 var(--cyan,#3cf0e4)}" +
      ".lp-tile.drop-after{box-shadow:3px 0 0 0 var(--cyan,#3cf0e4)}" +
      "@media(max-width:900px){.lp-tile{grid-column:span 12 !important}}";
    document.head.appendChild(s);
  }

  function ensureLaunchpad() {
    let lp = document.querySelector(".term-launchpad");
    if (lp) return lp;
    const wrap = document.querySelector(".wrap");
    if (!wrap) return null;
    injectLaunchpadCSS();
    ensureTab(LP_TAB, "Terminal");
    lp = document.createElement("section");
    lp.className = "term-launchpad";
    lp.dataset.sec = LP_TAB;
    lp.innerHTML = '<div class="lp-toolbar"></div><div class="lp-grid"></div>';
    wrap.appendChild(lp);
    return lp;
  }

  function lpGrid() {
    const lp = ensureLaunchpad();
    return lp ? lp.querySelector(".lp-grid") : null;
  }

  function lpApplyOrder(grid) {
    const order = (lpState.order || []).slice();
    if (!order.length) return;
    order.forEach((id) => {
      const t = grid.querySelector('.lp-tile[data-pid="' + CSS.escape(id) + '"]');
      if (t) grid.appendChild(t); // append in saved order; unknown tiles trail
    });
  }
  function lpPersistOrder(grid) {
    lpState.order = Array.from(grid.children).map((t) => t.dataset.pid);
    lpSave();
  }

  function lpBuildToolbar() {
    const lp = document.querySelector(".term-launchpad");
    if (!lp) return;
    const bar = lp.querySelector(".lp-toolbar");
    const hidden = lpField("hidden");
    let html = '<span class="lp-label">Launchpad</span>';
    panels.forEach((p) => {
      html += '<span class="lp-chip' + (hidden[p.id] ? " off" : "") +
        '" data-pid="' + p.id + '">' + esc(p.title || p.id) + "</span>";
    });
    html += '<button class="lp-reset">reset layout</button>';
    bar.innerHTML = html;
    bar.querySelectorAll(".lp-chip").forEach((chip) => {
      chip.onclick = () => lpToggleHidden(chip.dataset.pid);
    });
    const reset = bar.querySelector(".lp-reset");
    if (reset) reset.onclick = lpReset;
  }

  function lpToggleHidden(id) {
    const hidden = lpField("hidden");
    hidden[id] = !hidden[id];
    lpSave();
    const tile = document.querySelector('.lp-tile[data-pid="' + CSS.escape(id) + '"]');
    if (tile) tile.style.display = hidden[id] ? "none" : "";
    lpBuildToolbar();
  }

  function lpReset() {
    lpState = {};
    lpSave();
    const grid = lpGrid();
    if (!grid) return;
    Array.from(grid.children).forEach((tile) => {
      tile.style.display = "";
      tile.style.gridColumn = "span 6";
      tile.classList.remove("collapsed");
      tile.querySelectorAll(".lp-span").forEach((b) =>
        b.classList.toggle("on", +b.dataset.s === 6));
      const cb = tile.querySelector(".lp-collapse");
      if (cb) cb.textContent = "▾";
    });
    lpBuildToolbar();
  }

  function lpWireTile(tile, id) {
    const span = (lpField("span")[id]) || 6;
    tile.style.gridColumn = "span " + span;
    tile.querySelectorAll(".lp-span").forEach((b) => {
      b.classList.toggle("on", +b.dataset.s === span);
      b.onclick = () => {
        const s = +b.dataset.s;
        lpField("span")[id] = s; lpSave();
        tile.style.gridColumn = "span " + s;
        tile.querySelectorAll(".lp-span").forEach((x) =>
          x.classList.toggle("on", +x.dataset.s === s));
      };
    });
    const collapsed = !!lpField("collapsed")[id];
    tile.classList.toggle("collapsed", collapsed);
    const cbtn = tile.querySelector(".lp-collapse");
    if (cbtn) {
      cbtn.textContent = collapsed ? "▸" : "▾";
      cbtn.onclick = () => {
        const now = !tile.classList.contains("collapsed");
        tile.classList.toggle("collapsed", now);
        lpField("collapsed")[id] = now; lpSave();
        cbtn.textContent = now ? "▸" : "▾";
      };
    }
    if (lpField("hidden")[id]) tile.style.display = "none";

    // drag-to-reorder via the header
    const head = tile.querySelector(".lp-head");
    if (head) {
      head.setAttribute("draggable", "true");
      head.addEventListener("dragstart", (e) => {
        tile.classList.add("dragging");
        try { e.dataTransfer.setData("text/plain", id); e.dataTransfer.effectAllowed = "move"; } catch (_) {}
      });
      head.addEventListener("dragend", () => {
        tile.classList.remove("dragging");
        document.querySelectorAll(".lp-tile").forEach((t) =>
          t.classList.remove("drop-before", "drop-after"));
      });
    }
    tile.addEventListener("dragover", (e) => {
      e.preventDefault();
      const dragging = document.querySelector(".lp-tile.dragging");
      if (!dragging || dragging === tile) return;
      const r = tile.getBoundingClientRect();
      const after = e.clientX > r.left + r.width / 2;
      tile.classList.toggle("drop-after", after);
      tile.classList.toggle("drop-before", !after);
    });
    tile.addEventListener("dragleave", () =>
      tile.classList.remove("drop-before", "drop-after"));
    tile.addEventListener("drop", (e) => {
      e.preventDefault();
      const dragging = document.querySelector(".lp-tile.dragging");
      const grid = tile.parentElement;
      if (!dragging || dragging === tile || !grid) return;
      const after = tile.classList.contains("drop-after");
      tile.classList.remove("drop-before", "drop-after");
      grid.insertBefore(dragging, after ? tile.nextSibling : tile);
      lpPersistOrder(grid);
    });
  }

  function registerPanel(spec) {
    // spec: {id, title, tab, mount(bodyEl)}
    if (!spec || !spec.id || document.getElementById(spec.id)) {
      return spec && document.getElementById(spec.id);
    }
    const tab = spec.tab || "overview";

    // Non-terminal tabs: plain stacked card (legacy behaviour).
    if (tab !== LP_TAB) {
      const wrap = document.querySelector(".wrap");
      if (!wrap) return null;
      ensureTab(tab, tab.charAt(0).toUpperCase() + tab.slice(1));
      const card = document.createElement("section");
      card.className = "card term-panel";
      card.id = spec.id;
      card.dataset.sec = tab;
      if (spec.title) {
        const h = document.createElement("h2");
        h.className = "term-panel-title";
        h.textContent = spec.title;
        card.appendChild(h);
      }
      const body = document.createElement("div");
      body.className = "term-panel-body";
      card.appendChild(body);
      wrap.appendChild(card);
      panels.push(spec);
      try { if (typeof spec.mount === "function") spec.mount(body); } catch (e) {
        body.innerHTML = '<div class="muted">panel failed to mount</div>';
      }
      if (typeof window.showTab === "function") window.showTab(activeTab());
      return card;
    }

    // Terminal tab: Launchpad tile.
    const grid = lpGrid();
    if (!grid) return null;
    const tile = document.createElement("div");
    tile.className = "lp-tile";
    tile.id = spec.id;
    tile.dataset.pid = spec.id;
    tile.innerHTML =
      '<div class="lp-head">' +
      '<span class="lp-name">' + esc(spec.title || spec.id) + "</span>" +
      '<button class="lp-btn lp-span" data-s="4" title="third width">⅓</button>' +
      '<button class="lp-btn lp-span" data-s="6" title="half width">½</button>' +
      '<button class="lp-btn lp-span" data-s="12" title="full width">1</button>' +
      '<button class="lp-btn lp-collapse" title="collapse">▾</button>' +
      "</div>";
    const body = document.createElement("div");
    body.className = "lp-body";
    tile.appendChild(body);
    grid.appendChild(tile);

    panels.push(spec);
    try { if (typeof spec.mount === "function") spec.mount(body); } catch (e) {
      body.innerHTML = '<div class="muted">panel failed to mount</div>';
    }
    lpWireTile(tile, spec.id);
    lpApplyOrder(grid);
    lpBuildToolbar();
    if (typeof window.showTab === "function") window.showTab(activeTab());
    return tile;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // ---- command registry (feeds the cmdk palette) --------------------------
  const commands = [];            // {code, run(sym), desc}

  function registerCommand(spec) {
    if (!spec || !spec.code || typeof spec.run !== "function") return;
    commands.push({ code: spec.code.toUpperCase(), run: spec.run, desc: spec.desc || spec.code });
  }

  // index.html's cmdkCommands() merges whatever this returns (one additive line).
  // Each command becomes a palette entry; when the query starts with a ticker we
  // surface "TICKER CODE" runnable entries (Bloomberg-style mnemonics).
  window.terminalCommands = function (query) {
    const out = [];
    const q = (query || "").trim().toUpperCase();
    const m = q.match(/^([A-Z][A-Z0-9.\/]{0,6})(?:\s+([A-Z]+))?$/);
    const sym = m ? m[1] : null;
    commands.forEach((c) => {
      if (sym) {
        out.push({
          cat: "Terminal",
          label: `${sym} ${c.code} — ${c.desc}`,
          run: () => c.run(sym),
        });
      } else {
        out.push({
          cat: "Terminal",
          label: `${c.code} — ${c.desc} (type a ticker first)`,
          run: () => {},
        });
      }
    });
    return out;
  };

  function openSecurity(sym) {
    bus.emit("security:open", (sym || "").toUpperCase());
  }

  // ---- expose -------------------------------------------------------------
  window.T = {
    J, bus, quotes, registerPanel, registerCommand, openSecurity,
    get panels() { return panels.slice(); },
    get commands() { return commands.slice(); },
  };

  // ---- panel autoloader ---------------------------------------------------
  (async function loadPanels() {
    const data = await J("/api/panels");
    const list = (data && data.panels) || [];
    for (const src of list) {
      await new Promise((resolve) => {
        const s = document.createElement("script");
        s.src = src;
        s.onload = resolve;
        s.onerror = resolve; // one bad panel must not block the rest
        document.head.appendChild(s);
      });
    }
    bus.emit("panels:loaded", list.length);
  })();
})();
