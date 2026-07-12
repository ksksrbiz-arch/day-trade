/* monitor.js — Market Monitor (MOST) panel.
 *
 * The "what's moving" board: a breadth gauge, top gainers/losers, most-active
 * (by volume spike), and a sector heat strip. Data from GET /api/monitor.
 * Self-registers via window.T; opened with the MOST function code. Row clicks
 * open that security. Keyless-safe: an empty scan shows a friendly message.
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
  function pct(v) {
    if (v == null || isNaN(v)) return "·";
    return (v >= 0 ? "+" : "") + (Number(v) * 100).toFixed(2) + "%";
  }
  function moveColor(v) {
    if (v == null || isNaN(v)) return "#8a94a6";
    return v > 0.0005 ? "#3ddc84" : v < -0.0005 ? "#ef4444" : "#8a94a6";
  }
  // green(up) -> grey(flat) -> red(down) cell background for the sector heat
  function heatBg(v) {
    if (v == null || isNaN(v)) return "rgba(255,255,255,.05)";
    var t = Math.max(-1, Math.min(1, Number(v) / 0.03)); // saturate at ±3%
    var h = t >= 0 ? 145 : 0;
    var a = 0.18 + Math.abs(t) * 0.5;
    return "hsla(" + h + ",70%,45%," + a.toFixed(2) + ")";
  }

  function rowList(rows) {
    if (!rows || !rows.length) return '<div class="muted" style="font-size:11px">—</div>';
    return rows.map(function (r) {
      return '<div class="mon-row" data-sym="' + esc(r.symbol) + '" ' +
        'style="display:flex;justify-content:space-between;gap:8px;padding:2px 0;' +
        'font-size:12px;cursor:pointer">' +
        '<span style="font-weight:600;width:56px">' + esc(r.symbol) + "</span>" +
        '<span class="muted" style="flex:1;text-align:right">' +
        (r.price != null ? Number(r.price).toFixed(2) : "·") + "</span>" +
        '<span style="width:64px;text-align:right;color:' + moveColor(r.move) + '">' +
        pct(r.move) + "</span></div>";
    }).join("");
  }

  function activeList(rows) {
    if (!rows || !rows.length) return '<div class="muted" style="font-size:11px">—</div>';
    return rows.map(function (r) {
      return '<div class="mon-row" data-sym="' + esc(r.symbol) + '" ' +
        'style="display:flex;justify-content:space-between;gap:8px;padding:2px 0;' +
        'font-size:12px;cursor:pointer">' +
        '<span style="font-weight:600;width:56px">' + esc(r.symbol) + "</span>" +
        '<span class="muted" style="flex:1;text-align:right">vol ×' +
        (r.vol_spike != null ? Number(r.vol_spike).toFixed(1) : "·") + "</span>" +
        '<span style="width:64px;text-align:right;color:' + moveColor(r.move) + '">' +
        pct(r.move) + "</span></div>";
    }).join("");
  }

  function breadthBar(b) {
    b = b || {};
    var adv = b.advancers || 0, dec = b.decliners || 0, tot = adv + dec;
    var advPct = tot ? Math.round((adv / tot) * 100) : 50;
    return (
      '<div style="margin-bottom:14px">' +
      '<div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px">' +
      '<span style="color:#3ddc84">▲ ' + adv + " adv</span>" +
      '<span class="muted">breadth ' + (b.adv_pct != null ? b.adv_pct : 0) + "% up</span>" +
      '<span style="color:#ef4444">' + dec + " dec ▼</span></div>" +
      '<div style="display:flex;height:12px;border-radius:3px;overflow:hidden;background:#222">' +
      '<div style="width:' + advPct + '%;background:#2e7d32"></div>' +
      '<div style="width:' + (100 - advPct) + '%;background:#a12d2d"></div></div></div>'
    );
  }

  function sectorHeat(sectors) {
    if (!sectors || !sectors.length) return "";
    var cells = sectors.map(function (s) {
      return '<div title="' + esc(s.sector) + " " + pct(s.avg_move) + '" ' +
        'style="flex:1;min-width:74px;background:' + heatBg(s.avg_move) +
        ';border:1px solid rgba(255,255,255,.06);border-radius:7px;padding:7px 8px">' +
        '<div style="font-size:10px" class="muted">' + esc(s.sector) + "</div>" +
        '<div style="font-size:13px;font-weight:700;color:' + moveColor(s.avg_move) + '">' +
        pct(s.avg_move) + "</div></div>";
    }).join("");
    return (
      '<div class="muted" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin:4px 0 6px">Sector heat</div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:6px">' + cells + "</div>"
    );
  }

  function col(title, inner) {
    return (
      '<div style="flex:1;min-width:150px">' +
      '<div class="muted" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">' +
      esc(title) + "</div>" + inner + "</div>"
    );
  }

  function render(el, d) {
    if (!d) { el.innerHTML = '<div class="muted">monitor unavailable</div>'; return; }
    var b = d.breadth || {};
    if (!b.total) {
      el.innerHTML = breadthBar(b) +
        '<div class="muted">No scan data — needs Alpaca market keys.</div>';
      wire(el);
      return;
    }
    el.innerHTML =
      breadthBar(b) +
      '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px">' +
      col("Top gainers", rowList((d.movers || {}).gainers)) +
      col("Top losers", rowList((d.movers || {}).losers)) +
      col("Most active", activeList(d.most_active)) +
      "</div>" +
      sectorHeat(d.sectors);
    wire(el);
  }

  function wire(el) {
    el.querySelectorAll(".mon-row[data-sym]").forEach(function (row) {
      row.onclick = function () {
        var s = row.getAttribute("data-sym");
        if (s && T.openSecurity) T.openSecurity(s);
      };
    });
  }

  async function load(el) {
    el.innerHTML = '<div class="muted">scanning market…</div>';
    var d = await T.J("/api/monitor");
    render(el, d);
  }

  function mount(el) { load(el); }

  T.registerPanel({ id: "monitor-panel", title: "Monitor (MOST)", tab: "terminal", mount: mount });
  T.registerCommand({
    code: "MOST", desc: "Most active / movers",
    run: function () { if (typeof window.showTab === "function") window.showTab("terminal"); },
  });
})();
