/* Security Master (DES/FA + House View) — flagship single-security deep-dive.
 *
 * Renders the fundamentals (DES/FA) scorecard and a PROMINENT "House View"
 * block (confluence conviction, mesh consensus, council take, RL voice) for one
 * ticker. Loads from GET /api/security/{symbol}; re-loads on the 'security:open'
 * bus event. Registers Bloomberg-style function codes DES and FA.
 *
 * Self-contained IIFE over window.T. No external scripts.
 */
(function () {
  "use strict";
  var SYM = "AAPL";

  // ---- tiny render helpers ------------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function num(x, d) {
    if (x === null || x === undefined || x === "" || isNaN(x)) return "—";
    return Number(x).toFixed(d == null ? 2 : d);
  }
  function pct(x, d) {
    if (x === null || x === undefined || x === "" || isNaN(x)) return "—";
    return (Number(x) * 100).toFixed(d == null ? 1 : d) + "%";
  }
  function sideColor(v) {
    if (v == null || isNaN(v)) return "var(--muted)";
    if (v > 0.03) return "var(--green)";
    if (v < -0.03) return "var(--red)";
    return "var(--muted)";
  }
  function signed(x, d) {
    if (x === null || x === undefined || x === "" || isNaN(x)) return "—";
    var n = Number(x);
    return (n >= 0 ? "+" : "") + n.toFixed(d == null ? 2 : d);
  }
  // colored score bar in [-1,1]
  function scoreBar(v) {
    var w = Math.max(0, Math.min(1, Math.abs(Number(v) || 0))) * 50;
    var left = (Number(v) || 0) >= 0 ? 50 : 50 - w;
    return (
      '<span style="position:relative;display:inline-block;width:120px;height:8px;' +
      "border-radius:4px;background:rgba(120,170,200,.12);vertical-align:middle;" +
      'border-left:1px solid var(--line2)">' +
      '<span style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line2)"></span>' +
      '<span style="position:absolute;top:0;height:8px;border-radius:4px;left:' +
      left + "%;width:" + w + "%;background:" + sideColor(v) + '"></span>' +
      "</span>"
    );
  }

  function mount(el) {
    el.innerHTML = '<div class="muted">loading…</div>';
    load(el);
    T.bus.on("security:open", function (s) {
      SYM = (s || "AAPL").toUpperCase();
      load(el);
    });
  }

  async function load(el) {
    el.innerHTML = '<div class="muted">loading ' + esc(SYM) + "…</div>";
    if (window.T.setPanelSymbol) window.T.setPanelSymbol("security-panel", SYM);
    var d = await window.T.J("/api/security/" + encodeURIComponent(SYM));
    if (!d) {
      el.innerHTML =
        '<div class="muted">no data for ' + esc(SYM) +
        " (endpoint unreachable)</div>";
      return;
    }
    el.innerHTML = render(d);
    wire(el);
  }

  function render(d) {
    var hv = d.house_view || {};
    return (
      searchRow(d) +
      houseView(hv) +
      fundamentals(d.fundamentals || {}) +
      signals(d.signals || {}) +
      '<div class="mut" style="font-size:10px;margin-top:10px">updated ' +
      esc(d.updated || "") + " · " + esc(d.bars || 0) + " bars</div>"
    );
  }

  // ---- symbol search row --------------------------------------------------
  function searchRow(d) {
    return (
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">' +
      '<input class="symin js-sym" value="' + esc(d.symbol || SYM) +
      '" style="width:110px;text-transform:uppercase" ' +
      'placeholder="TICKER" spellcheck="false">' +
      '<button class="btn js-go">Load</button>' +
      '<span class="mut" style="font-size:11px;margin-left:auto">DES · FA</span>' +
      "</div>"
    );
  }

  // ---- PROMINENT House View block ----------------------------------------
  function houseView(hv) {
    var c = hv.confluence || {};
    var mesh = hv.mesh || {};
    var council = hv.council || {};
    var comp = c.available ? c.composite : null;
    var side = c.available ? c.side : "n/a";
    var col = sideColor(comp);

    var head =
      '<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">' +
      '<div style="font-size:34px;font-weight:800;line-height:1;color:' + col + '">' +
      (comp == null ? "—" : signed(comp, 2)) + "</div>" +
      '<div style="display:flex;flex-direction:column;gap:2px">' +
      '<span class="tag ' + tagClass(side) + '" style="text-transform:uppercase;' +
      'align-self:flex-start">' + esc(side) + "</span>" +
      '<span class="mut" style="font-size:11px">confluence · ' +
      (c.available ? esc(c.agree) + "/" + esc(c.n_methods) + " agree" : "unavailable") +
      "</span></div>";

    // gate + size chips
    var chips = "";
    if (c.available) {
      chips =
        '<div style="margin-left:auto;text-align:right">' +
        '<div style="font-size:11px" class="mut">' +
        (c.gate_pass
          ? '<span style="color:var(--green)">● GATE PASS</span>'
          : '<span style="color:var(--red)">○ blocked</span>') +
        "</div>" +
        '<div style="font-size:13px;font-weight:700">size ×' +
        num(c.size_mult, 2) + "</div></div>";
    }
    head += chips + "</div>";

    // voice breakdown (the confluence "scores" map)
    var voices = "";
    if (c.available && c.scores && Object.keys(c.scores).length) {
      var keys = Object.keys(c.scores);
      voices =
        '<div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr;' +
        'gap:4px 18px">' +
        keys
          .map(function (k) {
            var v = c.scores[k];
            return (
              '<div style="display:flex;align-items:center;gap:8px;font-size:11px">' +
              '<span class="mut" style="width:78px;text-transform:uppercase">' +
              esc(k) + "</span>" + scoreBar(v) +
              '<span style="color:' + sideColor(v) + ';font-weight:700;width:44px;' +
              'text-align:right">' + signed(v, 2) + "</span></div>"
            );
          })
          .join("") +
        "</div>";
    }

    // sub-cards: mesh + council + rl
    var meshCard = subCard(
      "Mesh consensus",
      mesh.available
        ? '<span style="color:' + sideColor(mesh.net) + ';font-weight:700">' +
          esc((mesh.direction || "flat").toUpperCase()) + "</span> net " +
          signed(mesh.net, 2) +
          '<div class="mut" style="font-size:10px;margin-top:2px">' +
          esc(mesh.agree || 0) + " layers agree · " + esc(mesh.mentions || 0) +
          " mentions</div>"
        : '<span class="mut">' + esc(mesh.note || "unavailable") + "</span>"
    );

    var councilCard = subCard(
      "Council take",
      council.available
        ? esc(council.take || "")
        : '<span class="mut">cached-only · regime ' +
          esc(council.regime || "n/a") + "</span>"
    );

    var rl = hv.rl;
    var rlCard = subCard(
      "RL voice",
      rl == null
        ? '<span class="mut">no model</span>'
        : '<span style="color:' + sideColor(rl) + ';font-weight:700">' +
          signed(rl, 2) + "</span>"
    );

    var reason = c.available && c.reason
      ? '<div class="mut" style="font-size:10px;margin-top:10px;line-height:1.5">' +
        esc(c.reason) + "</div>"
      : "";

    return (
      panelBox(
        "House View",
        "var(--cyan)",
        head + voices +
          '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;' +
          'margin-top:12px">' + meshCard + councilCard + rlCard + "</div>" +
          reason
      )
    );
  }

  // ---- Fundamentals (DES / FA) -------------------------------------------
  function fundamentals(f) {
    if (!f.available) {
      return panelBox(
        "Fundamentals · DES/FA",
        "var(--muted)",
        '<div class="mut">' + esc(f.note || f.error || "unavailable") + "</div>"
      );
    }
    var pillars =
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;' +
      'margin-bottom:12px">' +
      pillar("Value", f.value_score) +
      pillar("Quality", f.quality_score) +
      pillar("Growth", f.growth_score) +
      "</div>";

    var comp =
      '<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:12px">' +
      '<span style="font-size:24px;font-weight:800;color:' +
      sideColor(f.fundamental_score) + '">' + signed(f.fundamental_score, 2) +
      "</span>" +
      '<span class="tag ' + labelClass(f.label) +
      '" style="text-transform:uppercase">' + esc(f.label || "") + "</span>" +
      '<span class="mut" style="font-size:11px">composite score</span></div>';

    var metrics = [
      ["P/E", num(f.pe, 1)],
      ["PEG", num(f.peg, 2)],
      ["P/B", num(f.pb, 2)],
      ["ROE", pct(f.roe)],
      ["Margin", pct(f.profit_margin)],
      ["Rev YoY", pct(f.rev_growth_yoy)],
      ["EPS YoY", pct(f.eps_growth_yoy)],
    ];
    var grid =
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px 8px">' +
      metrics
        .map(function (m) {
          return (
            '<div class="tcell"><div class="k">' + esc(m[0]) +
            '</div><div class="v">' + esc(m[1]) + "</div></div>"
          );
        })
        .join("") +
      "</div>";

    return panelBox("Fundamentals · DES/FA", "var(--amber)", comp + pillars + grid);
  }

  function pillar(name, v) {
    return (
      '<div style="background:var(--panel2);border-radius:9px;padding:9px 11px">' +
      '<div class="mut" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px">' +
      esc(name) + "</div>" +
      '<div style="font-size:16px;font-weight:700;color:' + sideColor(v) +
      ';margin:3px 0 5px">' + signed(v, 2) + "</div>" +
      scoreBar(v) + "</div>"
    );
  }

  // ---- recent signal scorecard -------------------------------------------
  function signals(s) {
    if (!s.available || !s.count) {
      return panelBox(
        "Signal scorecard",
        "var(--muted)",
        '<div class="mut">' +
          esc(s.error || "no recorded signals for " + SYM) + "</div>"
      );
    }
    var rows = (s.recent || [])
      .map(function (r) {
        var hit =
          r.hit === 1 ? '<span style="color:var(--green)">✓</span>'
            : r.hit === 0 ? '<span style="color:var(--red)">✗</span>'
            : '<span class="mut">·</span>';
        return (
          '<div class="row" style="display:flex;gap:10px;align-items:center;' +
          'padding:4px 0;font-size:11px;border-bottom:1px solid var(--line)">' +
          '<span class="tag ' + tagClass(r.side) + '">' + esc(r.side) + "</span>" +
          '<span style="flex:1">' + esc(r.source) + "</span>" +
          '<span class="mut">' + esc((r.ts || "").slice(0, 10)) + "</span>" +
          '<span style="width:60px;text-align:right">' +
          (r.fwd_ret == null ? '<span class="mut">open</span>' : signed(r.fwd_ret * 100, 2) + "%") +
          "</span>" +
          '<span style="width:16px;text-align:center">' + hit + "</span>" +
          "</div>"
        );
      })
      .join("");
    return panelBox(
      "Signal scorecard · " + s.count,
      "var(--purple)",
      rows
    );
  }

  // ---- shared chrome ------------------------------------------------------
  function panelBox(title, accent, inner) {
    return (
      '<div style="border:1px solid var(--line);border-radius:11px;padding:13px 14px;' +
      "margin-bottom:12px;background:var(--panel2);border-left:3px solid " +
      accent + '">' +
      '<div class="mut" style="font-size:10px;font-weight:700;letter-spacing:.7px;' +
      "text-transform:uppercase;margin-bottom:10px;color:" + accent + '">' +
      esc(title) + "</div>" + inner + "</div>"
    );
  }
  function subCard(title, inner) {
    return (
      '<div style="background:var(--panel);border-radius:8px;padding:9px 10px">' +
      '<div class="mut" style="font-size:9px;text-transform:uppercase;' +
      'letter-spacing:.5px;margin-bottom:4px">' + esc(title) + "</div>" +
      '<div style="font-size:12px">' + inner + "</div></div>"
    );
  }
  function tagClass(side) {
    side = (side || "").toLowerCase();
    if (side === "buy" || side === "long" || side === "up") return "buy";
    if (side === "sell" || side === "short" || side === "down") return "sell";
    return "neutral";
  }
  function labelClass(label) {
    label = (label || "").toLowerCase();
    if (label === "strong" || label === "solid") return "buy";
    if (label === "weak" || label === "soft") return "sell";
    return "neutral";
  }

  function wire(el) {
    var input = el.querySelector(".js-sym");
    var go = el.querySelector(".js-go");
    function fire() {
      var v = (input.value || "").trim().toUpperCase();
      if (v) T.openSecurity(v);
    }
    if (go) go.addEventListener("click", fire);
    if (input)
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") fire();
      });
  }

  // ---- registration -------------------------------------------------------
  window.T.registerPanel({
    id: "security-panel",
    title: "Security Master",
    tab: "terminal",
    mount: mount,
  });
  window.T.registerCommand({
    code: "DES",
    desc: "Security description / deep-dive",
    run: function (s) { T.openSecurity(s); },
  });
  window.T.registerCommand({
    code: "FA",
    desc: "Financial analysis (DES/FA)",
    run: function (s) { T.openSecurity(s); },
  });
})();
