/* tape.js — Tape & Depth (TAS) panel.
 *
 * A time & sales list (recent prints) plus a bid/ask depth + imbalance bar.
 * Book updates live from T.quotes.subscribe(SYM); prints refresh on load/open.
 * Registers function code TAS (type "TICKER TAS" in the palette) to load a
 * typed ticker via T.openSecurity.
 */
(function () {
  "use strict";
  var T = window.T;
  if (!T) return;

  var SYM = "AAPL";
  var unsub = null;

  function fmt(n, d) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toFixed(d == null ? 2 : d);
  }

  function renderBook(el, q) {
    var host = el.querySelector(".tas-book");
    if (!host) return;
    q = q || {};
    var bs = Number(q.bid_size || 0);
    var as = Number(q.ask_size || 0);
    var tot = bs + as;
    var imb = q.imbalance != null ? Number(q.imbalance)
      : (tot > 0 ? (bs - as) / tot : 0);
    var bidPct = tot > 0 ? Math.round((bs / tot) * 100) : 50;
    var askPct = 100 - bidPct;
    var side = imb > 0.05 ? "bid" : (imb < -0.05 ? "ask" : "flat");
    if (!q.bid && !q.ask && !bs && !as) {
      host.innerHTML = '<div class="muted">no book (needs live quotes)</div>';
      return;
    }
    host.innerHTML =
      '<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">' +
        '<span>bid <b>' + fmt(q.bid) + '</b> &times;' + fmt(bs, 0) + '</span>' +
        '<span style="color:' + (side === "bid" ? "#4caf50" : side === "ask" ? "#e05555" : "#999") + '">' +
          'imb ' + fmt(imb, 3) + '</span>' +
        '<span>ask <b>' + fmt(q.ask) + '</b> &times;' + fmt(as, 0) + '</span>' +
      '</div>' +
      '<div style="display:flex;height:14px;border-radius:3px;overflow:hidden;background:#222">' +
        '<div title="bid depth" style="width:' + bidPct + '%;background:#2e7d32"></div>' +
        '<div title="ask depth" style="width:' + askPct + '%;background:#a12d2d"></div>' +
      '</div>';
  }

  function renderPrints(el, prints) {
    var host = el.querySelector(".tas-prints");
    if (!host) return;
    if (!prints || !prints.length) {
      host.innerHTML = '<div class="muted">no prints (needs Alpaca keys)</div>';
      return;
    }
    var rows = prints.map(function (p) {
      var t = p.t ? String(p.t).slice(11, 19) : "";
      return '<div style="display:flex;justify-content:space-between;font-family:monospace;font-size:12px;padding:1px 0">' +
        '<span class="muted">' + t + '</span>' +
        '<span>' + fmt(p.price) + '</span>' +
        '<span class="muted">' + fmt(p.size, 0) + '</span>' +
      '</div>';
    }).join("");
    host.innerHTML =
      '<div style="display:flex;justify-content:space-between;font-size:11px;opacity:.6;border-bottom:1px solid #333;padding-bottom:2px;margin-bottom:2px">' +
        '<span>time</span><span>price</span><span>size</span></div>' + rows;
  }

  async function load(el) {
    var host = el.querySelector(".tas-prints");
    if (host) host.innerHTML = '<div class="muted">loading…</div>';
    if (T.setPanelSymbol) T.setPanelSymbol("tape-panel", SYM);
    var d = await T.J("/api/tape/" + encodeURIComponent(SYM));
    var head = el.querySelector(".tas-head");
    if (head) head.textContent = SYM;
    if (!d) {
      renderBook(el, {});
      renderPrints(el, []);
      return;
    }
    renderBook(el, d.book || {});
    renderPrints(el, d.prints || []);
    // prefer a live quote if the store already has one
    var live = T.quotes.get(SYM);
    if (live) renderBook(el, live);
  }

  function mount(el) {
    el.innerHTML =
      '<div style="font:600 13px monospace;margin-bottom:6px" class="tas-head">' + SYM + '</div>' +
      '<div class="tas-book" style="margin-bottom:10px"></div>' +
      '<div class="tas-prints muted">loading…</div>';
    load(el);
    if (unsub) unsub();
    unsub = T.quotes.subscribe(SYM, function (q) { renderBook(el, q); });
    T.bus.on("security:open", function (s) {
      if (!s) return;
      SYM = String(s).toUpperCase();
      if (unsub) unsub();
      unsub = T.quotes.subscribe(SYM, function (q) { renderBook(el, q); });
      load(el);
    });
  }

  T.registerPanel({ id: "tape-panel", title: "Tape (TAS)", tab: "terminal", mount: mount });
  T.registerCommand({ code: "TAS", desc: "Time & sales", run: function (s) { T.openSecurity(s); } });
})();
