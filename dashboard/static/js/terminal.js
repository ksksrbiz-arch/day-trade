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

  function registerPanel(spec) {
    // spec: {id, title, tab, mount(bodyEl)}
    const wrap = document.querySelector(".wrap");
    if (!wrap || !spec || !spec.id) return null;
    if (document.getElementById(spec.id)) return document.getElementById(spec.id);
    const tab = spec.tab || "overview";
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
    // re-sync visibility with the current tab (setupTabs already ran)
    if (typeof window.showTab === "function") window.showTab(activeTab());
    return card;
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
