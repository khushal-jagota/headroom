/* Backlog screen (#/backlog) — the pool of UNSCHEDULED sprint items (sprint_id
 * NULL), redesigned per orchestration/backlog-redesign/backlog.html. The one job:
 * SCAN unscheduled work by priority, and ADD to it. So the list is the primary
 * content, grouped by priority (P0–P3 whisper labels, exactly the Sprint's
 * status-group treatment); the create surface is a DORMANT <details> at the top.
 *
 * Deliberate calls (justified in orchestration/backlog-redesign/notes.md):
 *   · Rows are FLAT + navigable — a backlog item has no sub-tickets, so no tree to
 *     reveal; its depth lives on the item page one click away. (This app has no item
 *     detail screen, so a row anchors to #/backlog — see the itemRow note.)
 *   · Priority IS the group, so it is NOT repeated as a row chip. Rows carry only
 *     project + an optional deadline (the shared comp.chip primitive).
 *   · The compose is dormant: a quiet "+ New backlog item" that opens on demand so
 *     the list reads uninterrupted. Its inputs are the inline-edit surface
 *     (transparent, amber caret); project/priority are chip toggles; the commit is
 *     grayscale (no amber beyond the caret — amber stays "needs the human").
 *
 * No optimistic UI: a create surfaces only when the WS-invalidation refetch
 * re-renders the screen (each route() call renders into a fresh screen div, so the
 * new item appears on the next flush). Classic script: registers the "backlog"
 * screen over app.js's placeholder (overwrite-wins). */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  var C = Planner.config;
  var comp = Planner.components;
  var api = Planner.api;

  var ITEMS_BACKLOG = "/api/items?sprint_id=null";   // server-sorted: priority, created_at, id
  var POST_ITEM = "/api/items";

  var PROJECT_OPTS = [
    { value: "Vylo", label: "Vylo" },
    { value: "Tribe", label: "Tribe" },
    { value: "Learning", label: "Learning" },
    { value: "Other", label: "Other" }
  ];
  var PRIORITY_OPTS = [
    { value: "P0", label: "P0" },
    { value: "P1", label: "P1" },
    { value: "P2", label: "P2" },
    { value: "P3", label: "P3" }
  ];
  var PRIORITY_ORDER = ["P0", "P1", "P2", "P3"];

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

  // A segmented chip toggle: exactly one option pressed. getValue() returns the
  // current value (a project/priority string). The pressed state is the aria-pressed
  // attribute the CSS styles; no optimistic anything — it is a local input control.
  function segToggle(name, options, initialValue) {
    var seg = make("div", "seg");
    seg.setAttribute("role", "group");
    seg.setAttribute("data-seg", name);
    var current = initialValue;
    var buttons = [];
    options.forEach(function (opt) {
      var btn = make("button", "opt", opt.label);
      btn.type = "button";
      btn.setAttribute("data-value", opt.value === null ? "" : opt.value);
      btn.setAttribute("aria-pressed", opt.value === current ? "true" : "false");
      btn.addEventListener("click", function () {
        current = opt.value;
        buttons.forEach(function (b) {
          b.el.setAttribute("aria-pressed", b.value === current ? "true" : "false");
        });
      });
      buttons.push({ el: btn, value: opt.value });
      seg.appendChild(btn);
    });
    seg.getValue = function () { return current; };
    return seg;
  }

  function labelWithOptional(text) {
    var label = make("div", "fl", text + " ");
    label.appendChild(make("span", "fl-opt", "— optional"));
    return label;
  }

  // --- the dormant compose -----------------------------------------------------

  function composeItem() {
    var details = make("details", "make");
    details.setAttribute("data-create", "item");
    var summary = make("summary");
    summary.appendChild(make("span", "plus", "+"));
    summary.appendChild(document.createTextNode(" New backlog item"));
    details.appendChild(summary);

    var form = make("div", "form");

    var titleField = make("div", null);
    titleField.appendChild(make("div", "fl", "Title"));
    var titleIn = make("input", "in title-in");
    titleIn.type = "text";
    titleIn.setAttribute("placeholder", "What needs doing?");
    titleIn.setAttribute("data-input", "title");
    titleField.appendChild(titleIn);
    form.appendChild(titleField);

    var two = make("div", "two");
    var projWrap = make("div", null);
    projWrap.appendChild(make("div", "fl", "Project"));
    var projSeg = segToggle("project", PROJECT_OPTS, "Vylo");
    projWrap.appendChild(projSeg);
    two.appendChild(projWrap);
    var prioWrap = make("div", null);
    prioWrap.appendChild(make("div", "fl", "Priority"));
    var prioSeg = segToggle("priority", PRIORITY_OPTS, "P3");
    prioWrap.appendChild(prioSeg);
    two.appendChild(prioWrap);
    form.appendChild(two);

    var dlField = make("div", null);
    dlField.appendChild(labelWithOptional("Deadline"));
    var dlIn = make("input", "in detail-in");
    dlIn.type = "text";
    dlIn.setAttribute("placeholder", "YYYY-MM-DD");
    dlIn.setAttribute("data-input", "deadline");
    dlField.appendChild(dlIn);
    form.appendChild(dlField);

    var descField = make("div", null);
    descField.appendChild(labelWithOptional("Description"));
    var descIn = make("textarea", "in detail-in");
    descIn.rows = 2;
    descIn.setAttribute("placeholder", "Why it matters, any context. Lives on the item page.");
    descIn.setAttribute("data-input", "body");
    descField.appendChild(descIn);
    form.appendChild(descField);

    var commit = make("button", "commit", "Add to backlog");
    commit.type = "button";
    commit.setAttribute("data-commit", "");
    commit.disabled = true;
    form.appendChild(commit);

    titleIn.addEventListener("input", function () {
      commit.disabled = titleIn.value.trim() === "";
    });

    commit.addEventListener("click", function () {
      if (titleIn.value.trim() === "") {
        return;
      }
      var prior = form.querySelector(".error-line");
      if (prior) {
        prior.parentNode.removeChild(prior);
      }
      commit.disabled = true;
      var body = {
        title: titleIn.value.trim(),
        project: projSeg.getValue(),
        priority: prioSeg.getValue(),
        body: descIn.value          // the redesign's Description — create_item accepts it
      };
      var deadline = dlIn.value.trim();
      if (deadline) {
        body.deadline = deadline;   // omit empty → NULL
      }
      api.fetchJson(POST_ITEM, { method: "POST", body: body }).then(
        function () {
          // A3: stay disabled — the WS flush re-render replaces the screen.
        },
        function (err) {
          commit.disabled = false;
          form.insertBefore(comp.errorLine(err), form.firstChild);
        }
      );
    });

    details.appendChild(form);
    return details;
  }

  // --- rows + priority groups --------------------------------------------------

  function itemRow(item) {
    // No item detail screen exists in this app; the row anchors to #/backlog so it
    // stays a real (harmless) link rather than a dead #/item/{id} route. The title
    // span carries entity-row-title (the cross-screen title selector); chips are
    // siblings, so the title textContent stays exact.
    var row = make("a", "brow");
    row.setAttribute("href", C.ROUTES.backlog);
    row.setAttribute("data-item-id", item.id);
    row.appendChild(make("span", "bt entity-row-title", item.title));
    var chips = make("span", "chips");
    chips.appendChild(comp.chip("project", item.project));
    if (item.deadline) {
      chips.appendChild(comp.chip("deadline", item.deadline));
    }
    row.appendChild(chips);
    return row;
  }

  function priorityGroup(priority, items) {
    var group = make("div", "grp");
    group.setAttribute("data-priority-group", priority);
    var label = make("div", "glabel");
    label.appendChild(document.createTextNode(priority + " "));
    label.appendChild(make("span", "n", "· " + items.length));
    group.appendChild(label);
    items.forEach(function (item) {
      group.appendChild(itemRow(item));
    });
    return group;
  }

  function renderGroups(host, items) {
    host.replaceChildren();
    if (!items.length) {
      host.appendChild(quiet("No unscheduled items."));
      return;
    }
    var byPriority = {};
    PRIORITY_ORDER.forEach(function (p) { byPriority[p] = []; });
    items.forEach(function (item) {
      // Server order (priority → created_at → id) is preserved within each group.
      (byPriority[item.priority] || (byPriority[item.priority] = [])).push(item);
    });
    PRIORITY_ORDER.forEach(function (p) {
      var group = byPriority[p];
      if (group && group.length) {
        host.appendChild(priorityGroup(p, group));   // empty priorities are not rendered
      }
    });
  }

  // --- render ------------------------------------------------------------------

  function renderBacklog(root, params) {
    root.setAttribute("data-screen", "backlog");

    var doc = make("div", "doc");

    var head = make("header", "head");
    var headRow = make("div", "row");
    headRow.appendChild(make("div", "title", "Backlog"));
    var meta = make("div", "meta");
    var pill = make("span", "pill");
    pill.appendChild(make("span", "pill-key", "unscheduled"));
    var countText = document.createTextNode("0");
    pill.appendChild(countText);
    meta.appendChild(pill);
    headRow.appendChild(meta);
    head.appendChild(headRow);
    doc.appendChild(head);

    var col = make("div", "col");
    col.appendChild(composeItem());
    var groupsHost = make("div", "groups");
    groupsHost.setAttribute("data-backlog-items", "");
    col.appendChild(groupsHost);
    doc.appendChild(col);

    root.replaceChildren(doc);

    api.fetchJson(ITEMS_BACKLOG).then(
      function (res) {
        var items = res.items || [];
        countText.textContent = String(items.length);
        renderGroups(groupsHost, items);
      },
      function (err) {
        groupsHost.appendChild(comp.errorLine(err));
      }
    );
  }

  Planner.registerScreen("backlog", renderBacklog);
})();
