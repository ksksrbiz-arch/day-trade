/* chart.js — Advanced Chart (GP) panel for the intelligence terminal.
 *
 * Registers under the 'terminal' tab. Renders a multi-pane Chart.js view:
 *   main pane — close price + overlays (SMA20, EMA12/26, Bollinger bands, VWAP)
 *   sub pane  — RSI(14) and MACD histogram
 * Data comes from GET /api/chart/{symbol}?days=120 (keyless-safe: empty ok).
 * Command code GP loads a typed ticker (Bloomberg-style mnemonic).
 */
(function () {
  "use strict";
  if (!window.T) return;
  const T = window.T;
  let SYM = "SPY";
  let mainChart = null;
  let subChart = null;
  let statusEl = null;

  function destroy() {
    if (mainChart) { try { mainChart.destroy(); } catch (e) {} mainChart = null; }
    if (subChart) { try { subChart.destroy(); } catch (e) {} subChart = null; }
  }

  function line(label, data, color, opts) {
    return Object.assign({
      label: label,
      data: data,
      borderColor: color,
      backgroundColor: color,
      borderWidth: 1.2,
      pointRadius: 0,
      tension: 0.1,
      spanGaps: true,
    }, opts || {});
  }

  function draw(d) {
    destroy();
    if (!window.Chart) { if (statusEl) statusEl.textContent = "Chart.js unavailable"; return; }
    const candles = (d && d.candles) || [];
    const studies = (d && d.studies) || {};
    if (!candles.length) {
      if (statusEl) statusEl.textContent = SYM + " — no data (market keys absent or unknown symbol)";
      return;
    }
    if (statusEl) statusEl.textContent = SYM + " — " + candles.length + " bars";

    const labels = candles.map(function (b) { return b.t; });
    const close = candles.map(function (b) { return b.c; });

    // ---- main pane: price + overlays ----
    const mainDs = [
      line("Close", close, "#4da3ff", { borderWidth: 1.6 }),
    ];
    if (studies.sma20) mainDs.push(line("SMA20", studies.sma20, "#f5a623"));
    if (studies.ema12) mainDs.push(line("EMA12", studies.ema12, "#7ed321"));
    if (studies.ema26) mainDs.push(line("EMA26", studies.ema26, "#d0021b"));
    if (studies.vwap) mainDs.push(line("VWAP", studies.vwap, "#bd10e0", { borderDash: [4, 3] }));
    if (studies.bb_upper) mainDs.push(line("BB Upper", studies.bb_upper, "rgba(120,144,168,0.55)", { borderDash: [2, 2] }));
    if (studies.bb_lower) mainDs.push(line("BB Lower", studies.bb_lower, "rgba(120,144,168,0.55)", { borderDash: [2, 2] }));

    const commonScales = {
      x: { ticks: { color: "#8a94a6", maxTicksLimit: 8, autoSkip: true }, grid: { color: "rgba(255,255,255,0.05)" } },
      y: { ticks: { color: "#8a94a6" }, grid: { color: "rgba(255,255,255,0.05)" } },
    };
    const legend = { labels: { color: "#c8d0dc", boxWidth: 10, font: { size: 10 } } };

    const mainCtx = document.getElementById("gp-main");
    if (mainCtx) {
      mainChart = new window.Chart(mainCtx, {
        type: "line",
        data: { labels: labels, datasets: mainDs },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          interaction: { intersect: false, mode: "index" },
          plugins: { legend: legend, tooltip: { enabled: true } },
          scales: commonScales,
        },
      });
    }

    // ---- sub pane: RSI + MACD histogram ----
    const subDs = [];
    if (studies.rsi14) subDs.push(line("RSI14", studies.rsi14, "#50e3c2", { yAxisID: "y" }));
    if (studies.macd_hist) {
      subDs.push({
        type: "bar", label: "MACD hist", data: studies.macd_hist,
        yAxisID: "y1",
        backgroundColor: studies.macd_hist.map(function (v) {
          return (v || 0) >= 0 ? "rgba(126,211,33,0.6)" : "rgba(208,2,27,0.6)";
        }),
      });
    }
    const subCtx = document.getElementById("gp-sub");
    if (subCtx && subDs.length) {
      subChart = new window.Chart(subCtx, {
        type: "line",
        data: { labels: labels, datasets: subDs },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          interaction: { intersect: false, mode: "index" },
          plugins: { legend: legend },
          scales: {
            x: commonScales.x,
            y: { position: "left", min: 0, max: 100, ticks: { color: "#50e3c2", maxTicksLimit: 5 }, grid: { color: "rgba(255,255,255,0.05)" } },
            y1: { position: "right", ticks: { color: "#8a94a6", maxTicksLimit: 5 }, grid: { drawOnChartArea: false } },
          },
        },
      });
    }
  }

  async function load() {
    if (statusEl) statusEl.textContent = "Loading " + SYM + "…";
    const d = await T.J("/api/chart/" + encodeURIComponent(SYM) + "?days=120");
    if (!d) { if (statusEl) statusEl.textContent = SYM + " — request failed"; return; }
    SYM = (d.symbol || SYM);
    draw(d);
  }

  function mount(el) {
    el.innerHTML =
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">' +
      '  <input id="gp-sym" class="muted" value="' + SYM + '" spellcheck="false" ' +
      '     style="background:#141922;border:1px solid #2a3344;color:#e6ebf2;padding:4px 8px;border-radius:4px;width:110px;text-transform:uppercase" />' +
      '  <button id="gp-go" style="background:#1f2a3a;border:1px solid #2a3344;color:#e6ebf2;padding:4px 10px;border-radius:4px;cursor:pointer">Load</button>' +
      '  <span id="gp-status" class="muted" style="font-size:12px"></span>' +
      "</div>" +
      '<div style="position:relative;height:280px"><canvas id="gp-main"></canvas></div>' +
      '<div style="position:relative;height:130px;margin-top:6px"><canvas id="gp-sub"></canvas></div>';

    statusEl = el.querySelector("#gp-status");
    const input = el.querySelector("#gp-sym");
    const go = el.querySelector("#gp-go");
    function submit() {
      const v = (input.value || "").trim().toUpperCase();
      if (v) { SYM = v; input.value = v; load(); }
    }
    if (go) go.addEventListener("click", submit);
    if (input) input.addEventListener("keydown", function (e) { if (e.key === "Enter") submit(); });

    T.bus.on("security:open", function (s) {
      if (!s) return;
      SYM = String(s).toUpperCase();
      if (input) input.value = SYM;
      load();
    });

    load();
  }

  T.registerPanel({ id: "chart-panel", title: "Chart (GP)", tab: "terminal", mount: mount });
  T.registerCommand({ code: "GP", desc: "Price graph", run: function (s) { T.openSecurity(s); } });
})();
