/* Screen 1: Day (#/day, home) — the OVERVIEW, and nothing else. A calm strategic
 * daily READ (orchestration/today-redesign/): a date, a one-line focus hero, then
 * three sections — Brief Take, Watchout, If Today Lands. Board owns "what I'm doing",
 * Review owns approvals, so the plan-tree, today-ticket-list, review-count and chat
 * are all DROPPED here (their backend endpoints stay; they just have no Day home).
 *
 * The overview is four structured fields the boundary agent (§6.2) fills — focus,
 * brief_take, watchout, if_today_lands — not one markdown blob this screen parses.
 * The screen reads each field straight off /api/day/today and renders it in its slot.
 * An unfilled field (today's default, before the boundary or a human writes it) is
 * empty and shows the slot's quiet placeholder — no crash, no degrade branch.
 *
 * Editing: each field is human-amendable in place, on its own. Focus is a plain
 * scalar; the three bodies are markdown. The shared inlineEdit hook commits on blur →
 * PATCH /api/day/{date} with just that one field → the human-only per-field writer
 * (days/api.py, reject_agents). No optimistic UI: a saved edit is reflected only by
 * the WS-flush re-render, which re-reads all four fields.
 *
 * Stateless render over /api/day/today. Classic script: IIFE + "use strict", var/
 * function style, createElement only. Registers the real "day" screen over app.js's
 * placeholder (overwrite-wins). */
(function () {
  "use strict";
  var Planner = window.Planner;
  var c = Planner.components;

  var MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  var WEEKDAYS = [
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
  ];

  // The three markdown sections, in page order. `field` is the day JSON key the slot
  // reads/writes; `key` names the DOM hooks ([data-day-<key>], [data-day-<key>-body])
  // and the CSS class; `label` is the shown caption.
  var SECTIONS = [
    { field: "brief_take", key: "take", label: "Brief take" },
    { field: "watchout", key: "watch", label: "Watchout" },
    { field: "if_today_lands", key: "lands", label: "If today lands" }
  ];

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  // A day_YYYY-MM-DD date segment → {weekday, monthday}. Built from explicit parts
  // (local midnight, never a UTC-parsed string) so the weekday never shifts a day.
  // Falls back to the raw segment if it is not a plain ISO date.
  function formatDate(iso) {
    var parts = String(iso).split("-");
    if (parts.length !== 3) {
      return { weekday: "", monthday: String(iso) };
    }
    var y = Number(parts[0]);
    var m = Number(parts[1]);
    var d = Number(parts[2]);
    var dt = new Date(y, m - 1, d);
    if (isNaN(dt.getTime()) || m < 1 || m > 12) {
      return { weekday: "", monthday: String(iso) };
    }
    return { weekday: WEEKDAYS[dt.getDay()], monthday: MONTHS[m - 1] + " " + d };
  }

  function render(root) {
    root.setAttribute("data-screen", "day");
    Planner.api.fetchJson("/api/day/today").then(
      function (day) {
        build(root, day);
      },
      function (err) {
        root.replaceChildren(c.errorLine(err));
      }
    );
  }

  function build(root, day) {
    // Mutating routes use the server's canonical date, never the browser clock.
    var date = day.id.slice(4);

    // PATCH exactly one field; the WS flush re-renders from the saved value.
    function saveField(field, raw) {
      var body = {};
      body[field] = raw;
      return Planner.api.fetchJson("/api/day/" + date, { method: "PATCH", body: body });
    }

    var doc = el("div", "doc");
    doc.setAttribute("data-day-overview", "");
    var col = el("div", "col");

    // Date — a quiet orienting label ("today" is carried by the planning date itself).
    var info = formatDate(date);
    var dateEl = el("div", "date");
    dateEl.setAttribute("data-day-date", "");
    if (info.weekday) {
      dateEl.appendChild(document.createTextNode(info.weekday + " "));
      dateEl.appendChild(el("span", "dim", "·"));
      dateEl.appendChild(document.createTextNode(" " + info.monthday));
    } else {
      dateEl.textContent = info.monthday;
    }
    col.appendChild(dateEl);

    // Focus — the hero line. A plain scalar edit (single line), human-only PATCH.
    var focusEl = el("div", "focus");
    focusEl.setAttribute("data-day-focus", "");
    c.inlineEdit(focusEl, {
      getValue: function () { return day.focus; },
      onSave: function (raw) { return saveField("focus", raw); },
      placeholder: "(no focus set)"
    });
    col.appendChild(focusEl);

    // The three framing sections — one primitive (label + inline-editable markdown
    // body), flexed only by the label. Separated by space and rhythm alone.
    SECTIONS.forEach(function (s) {
      var cls = s.key === "take" ? "take" : "block " + s.key;
      var section = el("section", cls);
      section.setAttribute("data-day-" + s.key, "");
      section.appendChild(el("div", "label", s.label));
      var body = el("div", "body");
      body.setAttribute("data-day-" + s.key + "-body", "");
      c.inlineEdit(body, {
        getValue: function () { return day[s.field]; },
        onSave: function (raw) { return saveField(s.field, raw); },
        markdown: true,
        multiline: true,
        placeholder: "(none)"
      });
      section.appendChild(body);
      col.appendChild(section);
    });

    doc.appendChild(col);
    root.replaceChildren(doc);
  }

  Planner.registerScreen("day", render);
})();
