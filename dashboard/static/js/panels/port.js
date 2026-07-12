/*
 * Portfolio (PORT) — terminal panel.
 * Exposure bars + attribution table + a strip of risk KPIs, fed by /api/port.
 * Self-registers on window.T; keyless-safe (renders the zeroed shape).
 */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function money(n) {
    var v = Number(n) || 0;
    return (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  function pct(n) {
    var v = Number(n) || 0;
    return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
  }
  function signColor(n) {
    return (Number(n) || 0) >= 0 ? "#3cf0e4" : "#ef4444";
  }

  function kpiCard(label, value, color) {
    return (
      '<div style="flex:1;min-width:96px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);' +
      'border-radius:9px;padding:9px 11px">' +
      '<div class="muted" style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase">' + esc(label) + "</div>" +
      '<div style="font-size:17px;font-weight:700;margin-top:3px;color:' + (color || "#e8fbf9") + '">' + esc(value) + "</div>" +
      "</div>"
    );
  }

  function renderKPIs(risk) {
    risk = risk || {};
    var cards = [
      kpiCard("Equity", money(risk.equity)),
      kpiCard("Day P&L", pct(risk.day_pl_pct), signColor(risk.day_pl_pct)),
      kpiCard("Gross Exp", (Number(risk.gross_pct) || 0).toFixed(0) + "%"),
      kpiCard("Net Exp", (Number(risk.net_pct) || 0).toFixed(0) + "%", signColor(risk.net_pct)),
      kpiCard("Positions", String(risk.positions || 0)),
      kpiCard("Max Weight", (Number(risk.max_weight) || 0).toFixed(1) + "%"),
      kpiCard("Cash", (Number(risk.cash_pct) || 0).toFixed(0) + "%"),
      kpiCard("Max DD", pct(-Math.abs(Number(risk.max_drawdown_pct) || 0)), "#ef4444"),
    ];
    return '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px">' + cards.join("") + "</div>";
  }

  function renderExposures(exposures) {
    if (!exposures || !exposures.length) {
      return '<div class="muted" style="margin:6px 0 14px">No open positions.</div>';
    }
    var maxW = exposures.reduce(function (m, e) { return Math.max(m, Number(e.weight) || 0); }, 0) || 1;
    var rows = exposures.slice(0, 12).map(function (e) {
      var w = Number(e.weight) || 0;
      var barW = Math.max(2, (w / maxW) * 100);
      var isShort = String(e.side).toLowerCase().indexOf("short") >= 0;
      var col = isShort ? "#f59e0b" : "#3cf0e4";
      return (
        '<div style="display:flex;align-items:center;gap:8px;margin:4px 0">' +
        '<div style="width:58px;font-weight:600;font-size:12px;cursor:pointer" ' +
        'onclick="window.T&&window.T.openSecurity(\'' + esc(e.symbol) + "')\">" + esc(e.symbol) + "</div>" +
        '<div style="flex:1;background:rgba(255,255,255,.05);border-radius:4px;height:14px;overflow:hidden">' +
        '<div style="height:100%;width:' + barW + '%;background:' + col + ';opacity:.75"></div></div>' +
        '<div style="width:48px;text-align:right;font-size:11px" class="muted">' + w.toFixed(1) + "%</div>" +
        '<div style="width:58px;text-align:right;font-size:11px;color:' + signColor(e.unrealized_plpc) + '">' +
        pct(e.unrealized_plpc) + "</div>" +
        "</div>"
      );
    });
    return (
      '<div style="margin-bottom:16px">' +
      '<div class="muted" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Exposures (weight of gross)</div>' +
      rows.join("") +
      "</div>"
    );
  }

  function renderAttribution(attr) {
    if (!attr || !attr.length) {
      return '<div class="muted">Attribution still maturing — no resolved decisions yet.</div>';
    }
    var body = attr.slice(0, 10).map(function (v) {
      return (
        "<tr>" +
        '<td style="padding:3px 6px">' + esc(v.voice) + "</td>" +
        '<td style="padding:3px 6px;text-align:right" class="muted">' + (Number(v.weight) || 0).toFixed(2) + "</td>" +
        '<td style="padding:3px 6px;text-align:right;color:' + signColor(v.attributed_return_pct) + '">' +
        pct(v.attributed_return_pct) + "</td>" +
        '<td style="padding:3px 6px;text-align:right" class="muted">' + (v.opinions || 0) + "</td>" +
        '<td style="padding:3px 6px" class="muted">' + esc(v.verdict) + "</td>" +
        "</tr>"
      );
    });
    return (
      '<div class="muted" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Voice attribution (realized P&L decomposition)</div>' +
      '<table style="width:100%;border-collapse:collapse;font-size:11.5px">' +
      '<thead><tr style="text-align:left;border-bottom:1px solid rgba(255,255,255,.1)" class="muted">' +
      '<th style="padding:3px 6px">Voice</th><th style="padding:3px 6px;text-align:right">Wt</th>' +
      '<th style="padding:3px 6px;text-align:right">Attr P&L</th><th style="padding:3px 6px;text-align:right">Opin</th>' +
      '<th style="padding:3px 6px">Verdict</th></tr></thead>' +
      "<tbody>" + body.join("") + "</tbody></table>"
    );
  }

  async function load(el) {
    var d = await window.T.J("/api/port");
    if (!d) {
      el.innerHTML = '<div class="muted">Portfolio data unavailable.</div>';
      return;
    }
    var note = d.note
      ? '<div class="muted" style="font-size:10.5px;margin-bottom:10px;opacity:.75">' + esc(d.note) + "</div>"
      : "";
    el.innerHTML =
      note +
      renderKPIs(d.risk) +
      renderExposures(d.exposures) +
      renderAttribution(d.attribution);
  }

  function mount(el) {
    el.innerHTML = '<div class="muted">loading…</div>';
    load(el);
  }

  window.T.registerPanel({ id: "port-panel", title: "Portfolio (PORT)", tab: "terminal", mount: mount });
  window.T.registerCommand({
    code: "PORT",
    desc: "Portfolio analytics",
    run: function () {
      if (typeof window.showTab === "function") window.showTab("terminal");
    },
  });
})();
