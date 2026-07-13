/* xasset.js — Cross-Asset (XA) panel.
 *
 * A macro board: crypto, FX, rates, and commodities in four compact sections,
 * each row showing level + change (colored). Data from GET /api/xasset.
 * Self-registers via window.T; opened with the XA function code.
 */
(function () {
  "use strict";
  if (!window.T) return;
  var T = window.T;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmtVal(v, unit) {
    if (v == null || isNaN(v)) return "·";
    var n = Number(v);
    var s = n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : n.toFixed(n >= 1 ? 2 : 4);
    return s + (unit && unit !== "%" && unit !== "" ? " " + esc(unit) : "");
  }
  function fmtChg(c, unit) {
    if (c == null || isNaN(c)) return '<span class="muted">·</span>';
    var n = Number(c);
    var col = n > 0 ? "#3ddc84" : n < 0 ? "#ef4444" : "#8a94a6";
    var suffix = unit === "%" ? "%" : (unit === "" ? "" : "");
    // rates/crypto changes are already in their unit; show sign + value
    var txt = (n >= 0 ? "+" : "") + n.toFixed(2) + (unit === "%" ? "%" : "");
    return '<span style="color:' + col + '">' + txt + "</span>";
  }

  function section(title, rows, kind) {
    var body;
    if (!rows || !rows.length) {
      body = '<div class="muted" style="font-size:11px">' +
        (kind === "av" ? "needs ALPHAVANTAGE_API_KEY / rate-limited" : "unavailable") +
        "</div>";
    } else {
      body = rows.map(function (r) {
        return '<div style="display:flex;justify-content:space-between;gap:8px;' +
          'padding:2px 0;font-size:12px">' +
          '<span style="flex:1">' + esc(r.name) + "</span>" +
          '<span style="width:96px;text-align:right;font-variant-numeric:tabular-nums">' +
          fmtVal(r.value, r.unit) + "</span>" +
          '<span style="width:66px;text-align:right;font-variant-numeric:tabular-nums">' +
          fmtChg(r.change, r.unit) + "</span></div>";
      }).join("");
    }
    return (
      '<div style="flex:1;min-width:200px;margin-bottom:10px">' +
      '<div class="muted" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">' +
      esc(title) + "</div>" + body + "</div>"
    );
  }

  function render(el, d) {
    if (!d) { el.innerHTML = '<div class="muted">cross-asset data unavailable</div>'; return; }
    el.innerHTML =
      '<div style="display:flex;flex-wrap:wrap;gap:18px">' +
      section("Crypto", d.crypto, "cg") +
      section("FX", d.fx, "av") +
      section("Rates", d.rates, "av") +
      section("Commodities", d.commodities, "av") +
      "</div>" +
      (d.note ? '<div class="muted" style="font-size:10px;margin-top:8px;opacity:.75">' +
        esc(d.note) + "</div>" : "");
  }

  async function load(el) {
    el.innerHTML = '<div class="muted">loading cross-asset…</div>';
    var d = await T.J("/api/xasset");
    render(el, d);
  }

  function mount(el) { load(el); }

  T.registerPanel({ id: "xasset-panel", title: "Cross-Asset (XA)", tab: "terminal", mount: mount });
  T.registerCommand({
    code: "XA", desc: "Cross-asset board",
    run: function () { if (typeof window.showTab === "function") window.showTab("terminal"); },
  });
})();
