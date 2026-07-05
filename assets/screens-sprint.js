/* Sprint screen (#/sprint) — SPEC §10 screen 5 on the T14 foundation. Composes the
 * D11 primitives (panel, markdownBlock, fieldEditor, chip, entityRow, errorLine).
 * DOM is built with createElement / textContent only; rendered markdown HTML is reached
 * solely through Planner.components.markdownBlock. No optimistic UI: a successful write
 * surfaces only when the WS-invalidation refetch re-renders the screen. Classic
 * script: registers the "sprint" screen over app.js's placeholder (overwrite-wins). */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  var C = Planner.config;
  var comp = Planner.components;
  var api = Planner.api;

  // Data-API paths are local string literals (config.js is out of scope; ROUTES holds
  // only hash hrefs). Only hash-route hrefs come from C.ROUTES.
  var CURRENT = "/api/sprint/current";
  function sprintPath(id) { return "/api/sprints/" + id; }
  function freezePath(id, k) { return "/api/sprints/" + id + "/freeze-" + k; }
  function addendaPath(id) { return "/api/sprints/" + id + "/addenda"; }

  var KICKOFF = [                          // §3.1 kickoff fields, fixed order
    ["limiting_factor", "Limiting factor"],
    ["primary_bet", "Primary bet"],
    ["supports", "Supports"],
    ["premortem", "Premortem"]
  ];
  var REVIEW = [                           // §3.1 review fields, fixed order
    ["outcomes", "Outcomes"],
    ["solo_reflection", "Solo reflection"],
    ["joint_discussion", "Joint discussion"],
    ["updates_to_thinking", "Updates to thinking"],
    ["carry_forward", "Carry forward"]
  ];
  var STATUS_ORDER = ["todo", "active", "done", "blocked", "deferred_next_sprint"];   // §3.2
  var STATE_ORDER = ["needs_success", "needs_approach", "needs_plan",
                     "in_progress", "needs_review", "done", "dropped"];               // §4.1

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function quiet(text) {
    return make("div", "quiet-line", text);
  }

  // --- header ------------------------------------------------------------------

  function sprintHeader(s) {
    var header = make("div", "sprint-header");
    header.appendChild(make("h1", "screen-title", s.name));
    header.appendChild(make("div", "sprint-dates quiet-line", s.date_start + " – " + s.date_end));
    return header;
  }

  // --- kickoff / review (shared freeze pattern) --------------------------------

  function freezePanelFields(pairs, s, sid, kind) {
    var frozenAt = kind === "kickoff" ? s.kickoff_frozen_at : s.review_frozen_at;
    var frozen = frozenAt !== null;
    var body = [];
    if (frozen) {
      body.push(comp.chip("frozen"));
    }
    pairs.forEach(function (pair) {
      body.push(fieldWrap(pair[0], pair[1], s[pair[0]], frozen, sid));
    });
    if (!frozen) {
      body.push(freezeButton(kind, sid));
    }
    var section = comp.panel(kind === "kickoff" ? "Kickoff" : "Review", body);
    section.setAttribute("data-" + kind, "");
    if (frozen) {
      section.setAttribute("data-frozen", "1");
    }
    return section;
  }

  function kickoffPanel(s, sid) { return freezePanelFields(KICKOFF, s, sid, "kickoff"); }
  function reviewPanel(s, sid) { return freezePanelFields(REVIEW, s, sid, "review"); }

  function fieldWrap(key, label, value, frozen, sid) {
    var wrap = make("div", "sprint-field");
    wrap.setAttribute("data-field", key);
    wrap.appendChild(make("div", "sprint-field-label", label));
    wrap.appendChild(comp.markdownBlock(value));
    if (!frozen) {
      wrap.appendChild(comp.fieldEditor(value, function (newValue) {
        var b = {};
        b[key] = newValue;
        return api.fetchJson(sprintPath(sid), { method: "PATCH", body: b });
      }));
    }
    return wrap;
  }

  function freezeButton(kind, sid) {
    var btn = make("button", "button", "Freeze " + kind);
    btn.type = "button";
    btn.setAttribute("data-freeze", kind);
    btn.addEventListener("click", function () {
      var prior = btn.parentNode.querySelector(".error-line");
      if (prior) {
        prior.parentNode.removeChild(prior);
      }
      btn.disabled = true;
      api.fetchJson(freezePath(sid, kind), { method: "POST" }).then(
        function () {},
        function (err) {
          var line = comp.errorLine(err);
          if (btn.nextSibling) {
            btn.parentNode.insertBefore(line, btn.nextSibling);
          } else {
            btn.parentNode.appendChild(line);
          }
        }
      ).then(function () {
        btn.disabled = false;
      });
    });
    return btn;
  }

  // --- items -------------------------------------------------------------------

  function itemsPanel(groups) {
    return comp.panel("Items", STATUS_ORDER.map(function (status) {
      return statusGroup(status, groups[status] || []);
    }));
  }

  function prettyStatus(status) {
    var spaced = status.replace(/_/g, " ");
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
  }

  function statusGroup(status, items) {
    var group = make("div", "status-group");
    group.setAttribute("data-status-group", status);
    group.appendChild(make("div", "status-group-title", prettyStatus(status) + " · " + items.length));
    if (items.length) {
      var stack = make("div", "list-stack");
      items.forEach(function (item) {
        stack.appendChild(itemRow(item));
      });
      group.appendChild(stack);
    } else {
      group.appendChild(quiet("(none)"));
    }
    return group;
  }

  function itemChips(item) {
    var chips = [
      comp.chip("priority", item.priority),
      comp.chip("project", item.project)
    ];
    if (item.deadline) {
      chips.push(comp.chip("deadline", item.deadline));
    }
    if (item.blockers_cleared) {
      chips.push(comp.chip("blockers-cleared"));
    }
    if (item.status_proposal) {
      chips.push(comp.chip("pending-proposal"));
    }
    return chips;
  }

  function rollupText(rollup) {
    var total = 0;
    STATE_ORDER.forEach(function (st) {
      total += rollup[st] || 0;
    });
    var parts = STATE_ORDER.filter(function (st) {
      return rollup[st] > 0;
    }).map(function (st) {
      return st.replace(/_/g, " ") + " " + rollup[st];
    });
    var text = total === 1 ? "1 ticket" : total + " tickets";
    if (parts.length) {
      text += " · " + parts.join(" · ");
    }
    return text;
  }

  function itemRow(item) {
    var wrap = make("div", "sprint-item");
    wrap.setAttribute("data-item-id", item.id);
    wrap.appendChild(comp.entityRow({
      href: C.ROUTES.sprint,
      title: item.title,
      chips: itemChips(item)
    }));
    var rollup = make("span", "rollup", rollupText(item.rollup));
    rollup.setAttribute("data-rollup", "");
    wrap.appendChild(rollup);
    return wrap;
  }

  // --- loose tickets -----------------------------------------------------------

  function loosePanel(tickets) {
    return comp.panel("Loose tickets", looseSection(tickets));
  }

  function looseSection(tickets) {
    var section = make("section", "list-stack");
    section.setAttribute("data-loose", "");
    if (tickets.length) {
      tickets.forEach(function (t) {
        section.appendChild(looseRow(t));
      });
    } else {
      section.appendChild(quiet("(none)"));
    }
    return section;
  }

  function looseRow(t) {
    var row = comp.entityRow({
      href: C.ROUTES.ticketPrefix + t.id,
      title: t.title,
      chips: [comp.chip("state", t.state), comp.chip("priority", t.priority)]
    });
    row.setAttribute("data-ticket-id", t.id);
    return row;
  }

  // --- weekly addenda (append-only; rendered pre- and post-freeze) --------------

  function addendaPanel(s, sid) {
    return comp.panel("Weekly addenda", [addendaList(s.weekly_addenda), addendaForm(sid)]);
  }

  function addendaList(list) {
    var stack = make("div", "list-stack");
    stack.setAttribute("data-addenda", "");
    if (list.length) {
      list.forEach(function (a) {
        var row = make("div", "addendum");
        row.setAttribute("data-addendum", "");
        row.appendChild(make("span", "addendum-date", a.date));
        row.appendChild(comp.markdownBlock(a.text));
        stack.appendChild(row);
      });
    } else {
      stack.appendChild(quiet("(none)"));
    }
    return stack;
  }

  function addendaForm(sid) {
    var form = make("form", "create-form");
    form.setAttribute("data-addenda-form", "");

    var dateWrap = make("div", "create-form-field");
    dateWrap.appendChild(make("label", "create-form-label", "Date"));
    var dateInput = make("input", "form-control");
    dateInput.type = "date";
    dateInput.setAttribute("data-input", "date");
    dateWrap.appendChild(dateInput);
    form.appendChild(dateWrap);

    var textWrap = make("div", "create-form-field");
    textWrap.appendChild(make("label", "create-form-label", "Note"));
    var textArea = make("textarea", "form-control");
    textArea.rows = 3;
    textArea.setAttribute("data-input", "text");
    textWrap.appendChild(textArea);
    form.appendChild(textWrap);

    var actions = make("div", "field-editor-actions");
    var submit = make("button", "button button--primary", "Add addendum");
    submit.type = "submit";
    actions.appendChild(submit);
    form.appendChild(actions);

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var prior = form.querySelector(".error-line");
      if (prior) {
        prior.parentNode.removeChild(prior);
      }
      submit.disabled = true;
      api.fetchJson(addendaPath(sid), {
        method: "POST",
        body: { date: dateInput.value, text: textArea.value }
      }).then(
        function () {
          dateInput.value = "";
          textArea.value = "";
        },
        function (err) {
          actions.prepend(comp.errorLine(err));
        }
      ).then(function () {
        submit.disabled = false;
      });
    });
    return form;
  }

  // --- render ------------------------------------------------------------------

  function renderSprint(root, params) {
    root.setAttribute("data-screen", "sprint");
    api.fetchJson(CURRENT).then(
      function (res) {
        if (res.sprint === null) {
          root.appendChild(quiet("No current sprint."));
          return;
        }
        var s = res.sprint;
        var sid = s.id;
        root.appendChild(sprintHeader(s));
        root.appendChild(kickoffPanel(s, sid));
        root.appendChild(itemsPanel(res.groups));
        root.appendChild(loosePanel(res.loose_tickets));
        root.appendChild(reviewPanel(s, sid));
        root.appendChild(addendaPanel(s, sid));
      },
      function (err) {
        root.appendChild(comp.errorLine(err));
      }
    );
  }

  Planner.registerScreen("sprint", renderSprint);
})();
