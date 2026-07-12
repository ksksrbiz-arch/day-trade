/*
 * Options (OMON) panel — option chain grouped by expiry + IV heatmap.
 *
 * Renders the chain served by GET /api/options/{symbol} as a set of per-expiry
 * tables (strike / call+put bid-ask / greeks) plus a color-graded IV heatmap so
 * you can eyeball the vol surface. Function code OMON loads a typed ticker.
 *
 * Keyless-safe: the endpoint returns {symbol, chain:[]} without credentials, so
 * this panel simply shows an empty-state message rather than erroring.
 */
(function () {
  "use strict";
  if (!window.T || !window.T.registerPanel) return;
  const T = window.T;
  let SYM = "AAPL";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function fmt(v, d) {
    return v == null || isNaN(v) ? "·" : Number(v).toFixed(d == null ? 2 : d);
  }

  // Map an IV value to a heatmap color. Grades green(low) -> amber -> red(high)
  // across [min,max]; falls back to a neutral cell when IV is missing.
  function ivColor(iv, min, max) {
    if (iv == null || isNaN(iv)) return "transparent";
    const span = max - min;
    const t = span > 1e-9 ? Math.max(0, Math.min(1, (iv - min) / span)) : 0.5;
    const hue = 140 - 140 * t; // 140=green -> 0=red
    return `hsl(${hue.toFixed(0)}, 70%, ${(28 + 14 * t).toFixed(0)}%)`;
  }

  function collectIV(chain) {
    const vals = [];
    chain.forEach((g) => g.strikes.forEach((s) => {
      [s.call, s.put].forEach((leg) => {
        if (leg && leg.iv != null && !isNaN(leg.iv)) vals.push(leg.iv);
      });
    }));
    return vals;
  }

  function leg(l, key, d) {
    return l ? fmt(l[key], d) : "·";
  }

  function renderExpiryTable(g, min, max) {
    let rows = "";
    g.strikes.forEach((s) => {
      const c = s.call, p = s.put;
      const cIV = c && c.iv != null ? c.iv : null;
      const pIV = p && p.iv != null ? p.iv : null;
      rows +=
        '<tr>' +
        `<td style="background:${ivColor(cIV, min, max)}">${leg(c, "iv", 3)}</td>` +
        `<td>${leg(c, "delta")}</td>` +
        `<td>${leg(c, "bid")}/${leg(c, "ask")}</td>` +
        `<td class="omon-strike">${fmt(s.strike, 2)}</td>` +
        `<td>${leg(p, "bid")}/${leg(p, "ask")}</td>` +
        `<td>${leg(p, "delta")}</td>` +
        `<td style="background:${ivColor(pIV, min, max)}">${leg(p, "iv", 3)}</td>` +
        '</tr>';
    });
    return (
      `<div class="muted" style="margin:8px 0 2px">Expiry ${esc(g.expiry)}</div>` +
      '<table class="omon-tbl" style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr class="muted">' +
      '<th>IV</th><th>Δ</th><th>C bid/ask</th><th>Strike</th>' +
      '<th>P bid/ask</th><th>Δ</th><th>IV</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>'
    );
  }

  // Compact IV heatmap: one row per expiry, one cell per strike, colored by IV
  // (max of call/put IV at that strike).
  function renderHeatmap(chain, min, max) {
    let body = "";
    chain.forEach((g) => {
      let cells = "";
      g.strikes.forEach((s) => {
        const ivs = [s.call && s.call.iv, s.put && s.put.iv].filter(
          (v) => v != null && !isNaN(v));
        const iv = ivs.length ? Math.max.apply(null, ivs) : null;
        cells +=
          `<td title="${fmt(s.strike, 1)} IV ${fmt(iv, 3)}" ` +
          `style="width:14px;height:14px;padding:0;background:${ivColor(iv, min, max)}"></td>`;
      });
      body += `<tr><td class="muted" style="padding-right:6px;font-size:11px">${esc(g.expiry)}</td>${cells}</tr>`;
    });
    return (
      '<div class="muted" style="margin:10px 0 2px">IV heatmap (green=low · red=high)</div>' +
      '<div style="overflow-x:auto"><table style="border-collapse:collapse">' +
      body + '</table></div>'
    );
  }

  function render(el, data) {
    const chain = (data && data.chain) || [];
    if (!chain.length) {
      el.innerHTML =
        `<div class="muted">No option chain for <b>${esc((data && data.symbol) || SYM)}</b> ` +
        '(needs Alpaca options data / market keys).</div>';
      return;
    }
    const ivs = collectIV(chain);
    const min = ivs.length ? Math.min.apply(null, ivs) : 0;
    const max = ivs.length ? Math.max.apply(null, ivs) : 1;
    let html = `<div style="margin-bottom:4px"><b>${esc(data.symbol)}</b> option chain — `
      + `${chain.length} expiries</div>`;
    html += renderHeatmap(chain, min, max);
    chain.forEach((g) => { html += renderExpiryTable(g, min, max); });
    el.innerHTML = html;
  }

  async function load(el) {
    el.innerHTML = '<div class="muted">loading option chain…</div>';
    let data = null;
    try {
      data = await T.J("/api/options/" + encodeURIComponent(SYM));
    } catch (e) { data = null; }
    if (!data) {
      el.innerHTML = '<div class="muted">option chain unavailable.</div>';
      return;
    }
    render(el, data);
  }

  function mount(el) {
    load(el);
    T.bus.on("security:open", (s) => { if (s) { SYM = s; load(el); } });
  }

  T.registerPanel({ id: "options-panel", title: "Options (OMON)", tab: "terminal", mount });
  T.registerCommand({ code: "OMON", desc: "Option monitor", run: (s) => { T.openSecurity(s); } });
})();
