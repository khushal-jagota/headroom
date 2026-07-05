/* Backlog & Ideas screen (#/backlog) — SPEC §10 screen 6 on the T14 foundation.
 * Itemless sprint items (sprint_id NULL) by priority with a Create form (variant item);
 * ideas list with a Create form (variant idea). DOM via createElement / textContent only.
 * No optimistic UI: creates surface only via the WS-invalidation refetch.
 * Classic script: registers the "backlog" screen over app.js's placeholder. */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  var C = Planner.config;
  var comp = Planner.components;
  var api = Planner.api;

  // Local data-API path literals (config.js out of scope; ROUTES holds only hash hrefs).
  var ITEMS_BACKLOG = "/api/items?sprint_id=null";   // server-sorted: priority, created_at, id
  var IDEAS = "/api/ideas";                           // server-sorted: created_at DESC
  var POST_ITEM = "/api/items";
  var POST_IDEA = "/api/ideas";

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

  // --- rows --------------------------------------------------------------------

  function itemChips(item) {
    var chips = [
      comp.chip("priority", item.priority),
      comp.chip("project", item.project)
    ];
    if (item.deadline) {
      chips.push(comp.chip("deadline", item.deadline));
    }
    return chips;
  }

  function itemRow(item) {
    var row = comp.entityRow({
      href: C.ROUTES.backlog,
      title: item.title,
      chips: itemChips(item)
    });
    row.setAttribute("data-item-id", item.id);
    return row;
  }

  function ideaRow(idea) {
    var row = comp.entityRow({
      href: C.ROUTES.backlog,
      title: idea.title,
      chips: idea.project ? [comp.chip("project", idea.project)] : []
    });
    row.setAttribute("data-idea-id", idea.id);
    return row;
  }

  // --- submit handlers (build body; return the fetchJson promise) --------------

  function onSubmitItem(values) {
    var body = { title: values.title, project: values.project, priority: values.priority };
    if (values.deadline) {
      body.deadline = values.deadline;   // omit empty
    }
    // No sprint_id → lands NULL → backlog/deferred (§3.2).
    return api.fetchJson(POST_ITEM, { method: "POST", body: body });
  }

  function onSubmitIdea(values) {
    var body = { title: values.title };
    if (values.body) {
      body.body = values.body;
    }
    if (values.project) {
      body.project = values.project;   // "" blank → omitted → NULL project
    }
    return api.fetchJson(POST_IDEA, { method: "POST", body: body });
  }

  // --- render ------------------------------------------------------------------

  function fill(section, rows, rowFn, emptyText) {
    if (rows.length) {
      rows.forEach(function (r) {
        section.appendChild(rowFn(r));
      });
    } else {
      section.appendChild(quiet(emptyText));
    }
  }

  function renderBacklog(root, params) {
    root.setAttribute("data-screen", "backlog");

    // Build both sections synchronously (empty) so the create forms are present at once.
    var itemsSection = make("section", "list-stack");
    itemsSection.setAttribute("data-backlog-items", "");
    var ideasSection = make("section", "list-stack");
    ideasSection.setAttribute("data-ideas", "");

    root.appendChild(comp.panel("Backlog", [comp.createForm("item", onSubmitItem), itemsSection]));
    root.appendChild(comp.panel("Ideas", [comp.createForm("idea", onSubmitIdea), ideasSection]));

    // Populate each list independently.
    api.fetchJson(ITEMS_BACKLOG).then(
      function (res) { fill(itemsSection, res.items, itemRow, "No deferred items."); },
      function (err) { itemsSection.appendChild(comp.errorLine(err)); }
    );
    api.fetchJson(IDEAS).then(
      function (res) { fill(ideasSection, res.ideas, ideaRow, "No ideas."); },
      function (err) { ideasSection.appendChild(comp.errorLine(err)); }
    );
  }

  Planner.registerScreen("backlog", renderBacklog);
})();
