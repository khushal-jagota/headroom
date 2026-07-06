/* Screen 1: Day (#/day, home) — the OVERVIEW, and nothing else. A calm strategic
 * daily READ (orchestration/today-redesign/): a date, a one-line focus hero, then
 * three sections — Brief Take, Watchout, If Today Lands. Board owns "what I'm doing",
 * Review owns approvals, so the plan-tree, today-ticket-list, review-count and chat
 * are all DROPPED here (their backend endpoints stay; they just have no Day home).
 *
 * Content lives in day.brief, one markdown field. The structure is a convention the
 * boundary agent (§6.2) is meant to emit: a leading focus line, then the fixed H2s
 * "## Brief Take" / "## Watchout" / "## If Today Lands". This screen parses those to
 * slot the sections and lift the focus. Graceful degrade: an unstructured or empty
 * brief (today's default) never crashes — the whole brief renders in Brief Take, the
 * other slots fall back to quiet placeholders, focus empty.
 *
 * Editing: the brief is boundary-authored but human-amendable in place. Focus and the
 * three bodies are inline-editable (the shared inlineEdit hook, no affordance). Any
 * edit re-serializes the whole model back to canonical structured markdown and PATCHes
 * /api/day/{date} {brief} — the human-only brief writer (days/api.py, reject_agents).
 * No optimistic UI: a saved edit is reflected only by the WS-flush re-render.
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

  // The three fixed section headings (case-insensitive, whitespace-tolerant around
  // the ## marker), in page order. `re` matches a heading line; `heading` is what we
  // write back on serialize.
  var SECTIONS = [
    { key: "take", heading: "Brief Take", label: "Brief take", re: /^##\s+brief take\s*$/i },
    { key: "watch", heading: "Watchout", label: "Watchout", re: /^##\s+watchout\s*$/i },
    { key: "lands", heading: "If Today Lands", label: "If today lands",
      re: /^##\s+if today lands\s*$/i }
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

  // Parse day.brief → {structured, focus, take, watch, lands}. The leading text
  // (before the first recognized H2) is the focus; a leading "# " marker is stripped
  // so a focus authored as a heading still reads as a plain hero line. If no section
  // heading is present the brief is legacy/unstructured: the whole blob becomes the
  // Brief Take (the read stays visible) and focus/watch/lands are empty.
  function parseBrief(brief) {
    var text = brief === null || brief === undefined ? "" : String(brief);
    var lines = text.split("\n");
    var buckets = { focus: [], take: [], watch: [], lands: [] };
    var current = "focus";
    var sawHeading = false;

    lines.forEach(function (line) {
      var matched = null;
      var trimmed = line.trim();
      SECTIONS.forEach(function (s) {
        if (s.re.test(trimmed)) {
          matched = s.key;
        }
      });
      if (matched) {
        current = matched;
        sawHeading = true;
      } else {
        buckets[current].push(line);
      }
    });

    function joined(key) {
      return buckets[key].join("\n").trim();
    }

    if (!sawHeading) {
      return { structured: false, focus: "", take: text.trim(), watch: "", lands: "" };
    }
    var focus = joined("focus").replace(/^#{1,6}\s+/, "").trim();
    return {
      structured: true,
      focus: focus,
      take: joined("take"),
      watch: joined("watch"),
      lands: joined("lands")
    };
  }

  // model → canonical structured markdown. Focus (if any) leads; all three headings
  // are always emitted so the shape round-trips (an empty section keeps its heading,
  // parsing back to structured with an empty body → quiet placeholder).
  function serializeBrief(model) {
    var out = [];
    var focus = (model.focus || "").trim();
    if (focus) {
      out.push(focus);
    }
    SECTIONS.forEach(function (s) {
      var body = (model[s.key] || "").trim();
      out.push(body ? "## " + s.heading + "\n\n" + body : "## " + s.heading);
    });
    return out.join("\n\n") + "\n";
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
    var model = parseBrief(day.brief);

    function saveBrief() {
      return Planner.api.fetchJson("/api/day/" + date, {
        method: "PATCH",
        body: { brief: serializeBrief(model) }
      });
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
      getValue: function () { return model.focus; },
      onSave: function (raw) { model.focus = raw; return saveBrief(); },
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
        getValue: function () { return model[s.key]; },
        onSave: function (raw) { model[s.key] = raw; return saveBrief(); },
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
