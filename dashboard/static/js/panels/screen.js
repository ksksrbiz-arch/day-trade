/* screen.js — Screener (EQS) terminal panel.
 *
 * Interactive cross-sectional equity screener. Controls (min score, min rvol,
 * sector, limit) drive GET /api/screen; results render as a sortable table.
 * Clicking a row opens that security. Self-registers via window.T — no edits to
 * index.html / terminal.js. Keyless-safe: an empty result set shows a friendly
 * "no matches" line rather than erroring.
 */
(function () {
  "use strict";
  if (!window.T) return;

  var SECTORS = ["", "Index", "Technology", "Communication", "Consumer Disc.",
    "Consumer Staples", "Financials", "Energy", "Healthcare", "Credit", "Other"];

  // column -> header label
  var LABELS = {
    symbol: "Sym", score: "Score", mom: "Mom", reversal: "Rev", lowvol: "LoVol",
    trend: "Trend", rvol: "RVol", price: "Price", thesis: "Thesis",
    confidence: "Conf", sector: "Sector",
  };

  var sortState = { col: "score", dir: -1 }; // default: score desc

  function fmt(col, v) {
    if (v === null || v === undefined || v === "") return "·";
    if (typeof v === "number") {
      if (col === "price") return v.toFixed(2);
      if (col === "rvol") return v.toFixed(2) + "×";
      return v.toFixed(col === "confidence" ? 2 : 3).replace(/\.?0+$/, "") || "0";
    }
    return String(v);
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function buildControls(el) {
    var opts = SECTORS.map(function (s) {
      return '<option value="' + esc(s) + '">' + (s || "All sectors") + "</option>";
    }).join("");
    el.querySelector(".controls").innerHTML =
      '<label class="muted">Min score ' +
      '<input class="f-score" type="number" step="0.05" value="-1" style="width:5em"></label> ' +
      '<label class="muted">Min RVol ' +
      '<input class="f-rvol" type="number" step="0.1" value="0" style="width:5em"></label> ' +
      '<label class="muted">Sector <select class="f-sector">' + opts + "</select></label> " +
      '<label class="muted">Limit ' +
      '<input class="f-limit" type="number" step="5" value="50" style="width:5em"></label> ' +
      '<button class="f-run">Run</button>';
  }

  function paramsFrom(el) {
    function val(sel) { var n = el.querySelector(sel); return n ? n.value : ""; }
    var p = [];
    var sc = val(".f-score"); if (sc !== "") p.push("min_score=" + encodeURIComponent(sc));
    var rv = val(".f-rvol"); if (rv !== "") p.push("min_rvol=" + encodeURIComponent(rv));
    var se = val(".f-sector"); if (se) p.push("sector=" + encodeURIComponent(se));
    var lm = val(".f-limit"); if (lm !== "") p.push("limit=" + encodeURIComponent(lm));
    return p.join("&");
  }

  function sortRows(rows, col, dir) {
    return rows.slice().sort(function (a, b) {
      var x = a[col], y = b[col];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
      return String(x).localeCompare(String(y)) * dir;
    });
  }

  function renderTable(el, data) {
    var box = el.querySelector(".results");
    var cols = (data && data.columns) || [];
    var rows = (data && data.results) || [];
    if (!cols.length || !rows.length) {
      box.className = "results muted";
      box.textContent = rows.length ? "no columns" :
        "no matches (no data — Alpaca keys may be unset)";
      return;
    }
    box.className = "results";
    var sorted = sortRows(rows, sortState.col, sortState.dir);
    var arrow = function (c) {
      return c === sortState.col ? (sortState.dir < 0 ? " ▼" : " ▲") : "";
    };
    var thead = "<tr>" + cols.map(function (c) {
      return '<th data-col="' + esc(c) + '" style="cursor:pointer;text-align:right;' +
        'padding:2px 6px">' + esc(LABELS[c] || c) + arrow(c) + "</th>";
    }).join("") + "</tr>";
    var body = sorted.map(function (r) {
      var tds = cols.map(function (c) {
        var align = c === "symbol" || c === "thesis" || c === "sector" ? "left" : "right";
        var strong = c === "symbol" ? "font-weight:600" : "";
        return '<td style="text-align:' + align + ";padding:2px 6px;" + strong + '">' +
          esc(fmt(c, r[c])) + "</td>";
      }).join("");
      return '<tr data-sym="' + esc(r.symbol) + '" style="cursor:pointer">' + tds + "</tr>";
    }).join("");
    box.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
      "<thead>" + thead + "</thead><tbody>" + body + "</tbody></table>";

    // header sort
    box.querySelectorAll("th[data-col]").forEach(function (th) {
      th.onclick = function () {
        var c = th.getAttribute("data-col");
        if (sortState.col === c) sortState.dir = -sortState.dir;
        else { sortState.col = c; sortState.dir = -1; }
        renderTable(el, data);
      };
    });
    // row -> open security
    box.querySelectorAll("tr[data-sym]").forEach(function (tr) {
      tr.onclick = function () {
        var s = tr.getAttribute("data-sym");
        if (s) window.T.openSecurity(s);
      };
    });
  }

  async function load(el, params) {
    var box = el.querySelector(".results");
    box.className = "results muted";
    box.textContent = "loading…";
    var d = await window.T.J("/api/screen" + (params ? "?" + params : ""));
    if (!d) { box.textContent = "screen unavailable"; return; }
    renderTable(el, d);
  }

  function mount(el) {
    el.innerHTML =
      '<div class="controls" style="margin-bottom:8px;display:flex;gap:10px;' +
      'flex-wrap:wrap;align-items:center"></div>' +
      '<div class="results muted">loading…</div>';
    buildControls(el);
    var run = el.querySelector(".f-run");
    if (run) run.onclick = function () { load(el, paramsFrom(el)); };
    load(el, paramsFrom(el));
  }

  window.T.registerPanel({
    id: "screen-panel",
    title: "Screener (EQS)",
    tab: "terminal",
    mount: mount,
  });

  window.T.registerCommand({
    code: "EQS",
    desc: "Equity screener",
    run: function () {
      if (typeof window.showTab === "function") window.showTab("terminal");
    },
  });
})();
