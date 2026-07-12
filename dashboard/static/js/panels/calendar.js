/* calendar.js — terminal "Calendar (ECO)" panel.
 *
 * Lists upcoming economic events + earnings grouped by date. Data from
 * /api/calendar (rule-computed macro estimates + scheduled FOMC). Self-registers
 * via window.T; opened with the ECO function code.
 */
(function () {
  "use strict";
  if (!window.T) return;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function fmtDay(iso) {
    // iso "YYYY-MM-DD" -> "Mon Jul 13" without timezone drift.
    var p = String(iso || "").split("-");
    if (p.length !== 3) return esc(iso);
    var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
    var wd = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getUTCDay()];
    var mo = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
              "Oct", "Nov", "Dec"][d.getUTCMonth()];
    return wd + " " + mo + " " + d.getUTCDate();
  }

  function tag(source) {
    var color = source === "scheduled" ? "#4ea1ff"
      : source === "recurring-estimate" ? "#8a8f98" : "#c08a4e";
    return '<span style="font-size:10px;color:' + color +
      ';border:1px solid ' + color + '55;border-radius:3px;padding:0 4px;' +
      'margin-left:6px">' + esc(source || "?") + "</span>";
  }

  function row(ev, kind) {
    var badge = kind === "earnings" ? "EARN" : "ECO";
    var when = ev.time ? ' <span class="muted">' + esc(ev.time) + "</span>" : "";
    return '<div style="display:flex;justify-content:space-between;gap:8px;' +
      'padding:3px 0;border-bottom:1px solid #ffffff10">' +
      '<div><span class="muted" style="font-size:10px">' + badge + "</span> " +
      esc(ev.name || ev.symbol || "?") + when + "</div>" +
      "<div>" + tag(ev.source) + "</div></div>";
  }

  function render(el, data) {
    if (!data) {
      el.innerHTML = '<div class="muted">calendar unavailable</div>';
      return;
    }
    var events = [];
    (data.econ || []).forEach(function (e) { events.push([e, "econ"]); });
    (data.earnings || []).forEach(function (e) {
      events.push([{ name: e.symbol || e.name, date: e.date, time: e.time,
                     source: e.source }, "earnings"]);
    });
    events.sort(function (a, b) {
      return String(a[0].date).localeCompare(String(b[0].date));
    });

    if (!events.length) {
      el.innerHTML = '<div class="muted">no upcoming events</div>';
      return;
    }

    var groups = {}, order = [];
    events.forEach(function (pair) {
      var d = pair[0].date || "—";
      if (!groups[d]) { groups[d] = []; order.push(d); }
      groups[d].push(pair);
    });

    var html = "";
    order.forEach(function (d) {
      html += '<div style="margin:8px 0 2px;font-weight:600;color:#cbd0d8">' +
        fmtDay(d) + "</div>";
      groups[d].forEach(function (pair) { html += row(pair[0], pair[1]); });
    });
    if (data.note) {
      html += '<div class="muted" style="font-size:10px;margin-top:10px">' +
        esc(data.note) + "</div>";
    }
    el.innerHTML = html;
  }

  async function load(el) {
    var data = await window.T.J("/api/calendar");
    render(el, data);
  }

  function mount(el) {
    el.innerHTML = '<div class="muted">loading…</div>';
    load(el);
  }

  window.T.registerPanel({
    id: "calendar-panel", title: "Calendar (ECO)", tab: "terminal", mount: mount,
  });

  window.T.registerCommand({
    code: "ECO", desc: "Economic calendar",
    run: function () {
      if (typeof window.showTab === "function") window.showTab("terminal");
      var p = document.getElementById("calendar-panel");
      if (p && p.scrollIntoView) p.scrollIntoView({ behavior: "smooth" });
    },
  });
})();
