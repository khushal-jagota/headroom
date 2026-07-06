/* Sprint screen — TWO pages behind one registered "sprint" screen (params.sub):
 *   #/sprint, #/sprint/tracking → the TRACKING page (redesign,
 *     orchestration/sprint-redesign/mockup.html): sprint → items → tickets,
 *     progressive disclosure, calm at rest. Items are native <details> collapsed
 *     by default; expanding one reveals its ticket rows. Read + navigate only —
 *     the sole write is the inline sprint-name edit.
 *   #/sprint/overview → the SPRINT OVERVIEW page (redesign, rev6,
 *     orchestration/sprint-redesign/kickoff-review-mockup.html): three headed
 *     `<details class="phase">` sections — Kickoff / Mid-sprint Review / Sprint
 *     Review — each a set of headed inline-editable fields (the Today/ticket idiom).
 *     Every field commits on blur → PATCH that ONE sprint field (shared inlineEdit
 *     hook, human-only). NO freeze, NO amber, NO sprint number, NO weekly-addenda
 *     UI (rev6 retired them; their backends stay dormant). The phase only decides
 *     which section is OPEN by default, derived from which sections already have
 *     content (P8 state-drives-surface).
 *
 * DOM is built with createElement / textContent only; rendered markdown HTML is
 * reached solely through Planner.components.markdownBlock. No optimistic UI: a
 * successful write surfaces only when the WS-invalidation refetch re-renders the
 * screen. Classic script: registers the "sprint" screen over app.js's placeholder
 * (overwrite-wins). */
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

  // The three Overview sections, each [field_key, label] in fixed order. KICKOFF +
  // REVIEW are the §3.1 fields; MID is the rev6 Mid-sprint Review sub-fields.
  var KICKOFF = [
    ["limiting_factor", "Limiting factor"],
    ["primary_bet", "Primary bet"],
    ["supports", "Supports"],
    ["premortem", "Premortem"]
  ];
  var MID = [
    ["mid_where_we_stand", "Where we stand"],
    ["mid_whats_changed", "What's changed"],
    ["mid_what_to_adjust", "What to adjust"]
  ];
  var REVIEW = [
    ["outcomes", "Outcomes"],
    ["solo_reflection", "Solo reflection"],
    ["joint_discussion", "Joint discussion"],
    ["updates_to_thinking", "Updates to thinking"],
    ["carry_forward", "Carry forward"]
  ];

  // Tracking-page grouping: live groups always render (visible at rest, even when
  // empty); settled groups tuck behind a group-level disclosure. §3.2 status enum.
  var LIVE_ORDER = ["active", "todo", "blocked"];
  var SETTLED_ORDER = ["done", "deferred_next_sprint"];
  var GROUP_LABEL = {
    active: "Active",
    todo: "Todo",
    blocked: "Blocked",
    done: "Done",
    deferred_next_sprint: "Deferred → next sprint"
  };

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

  function prettyState(state) {
    return String(state).replace(/_/g, " ");
  }

  // --- shared header + two-page tab pair ---------------------------------------

  // The sprint name is the one editable surface across both pages (P7 in-place edit;
  // PATCH /api/sprints/{id} {name}, human-only, never freeze-gated). The date range
  // is a read-only pill; its .sprint-dates span carries the range text ALONE (the
  // "dates" key is a sibling) so the value stays exact.
  function sprintHeader(s, sid) {
    var header = make("header", "sprint-header");
    var row = make("div", "sprint-header-row");
    var title = make("h1", "screen-title");
    comp.inlineEdit(title, {
      getValue: function () { return s.name; },
      onSave: function (raw) {
        return api.fetchJson(sprintPath(sid), { method: "PATCH", body: { name: raw } });
      },
      placeholder: "(unnamed sprint)"
    });
    row.appendChild(title);
    var meta = make("div", "sprint-meta");
    var pill = make("span", "pill sprint-dates-pill");
    pill.appendChild(make("span", "pill-key", "dates"));
    pill.appendChild(make("span", "sprint-dates", s.date_start + " – " + s.date_end));
    meta.appendChild(pill);
    row.appendChild(meta);
    header.appendChild(row);
    return header;
  }

  function tabPair(current) {
    var nav = make("nav", "tabs");
    [["overview", "Sprint Overview"], ["tracking", "Sprint Tracking"]].forEach(function (pair) {
      var tab = make("a", "tab" + (pair[0] === current ? " cur" : ""), pair[1]);
      tab.setAttribute("href", "#/sprint/" + pair[0]);
      nav.appendChild(tab);
    });
    return nav;
  }

  // --- TRACKING page (the redesign) --------------------------------------------

  // The bet, stated once — a read-only echo of primary_bet (the editable copy lives
  // on the Overview page's Primary bet field). Health = "X of M done": X = items with
  // status done, M = total items, derived from the groups on render (P8 state-driven).
  function betFrame(s, groups) {
    var frame = make("section", "frame");
    var lead = make("div", "lead");
    lead.appendChild(comp.markdownBlock(s.primary_bet));
    frame.appendChild(lead);
    var total = 0;
    Object.keys(groups).forEach(function (k) {
      total += (groups[k] || []).length;
    });
    var done = (groups.done || []).length;
    frame.appendChild(make("div", "health", done + " of " + total + " done"));
    return frame;
  }

  function countText(tickets) {
    var n = (tickets || []).length;
    if (n === 0) {
      return "no tickets yet";
    }
    return n === 1 ? "1 ticket" : n + " tickets";
  }

  function itemChips(item) {
    var chips = make("span", "chips");
    chips.appendChild(comp.chip("priority", item.priority));
    chips.appendChild(comp.chip("project", item.project));
    if (item.deadline) {
      chips.appendChild(comp.chip("deadline", item.deadline));
    }
    if (item.blockers_cleared) {
      chips.appendChild(comp.chip("blockers-cleared"));
    }
    (item.blocked_by_titles || []).forEach(function (blockerTitle) {
      var chip = make("span", "chip chip--blocked-by");
      chip.appendChild(make("span", "k", "blocked by"));
      chip.appendChild(document.createTextNode(blockerTitle));
      chips.appendChild(chip);
    });
    return chips;
  }

  // A ticket row: state · title · priority, navigating to the ticket page. Inside an
  // item disclosure the title is a plain .tt; on a LOOSE row it also carries
  // entity-row-title (the loose-ticket e2e contract) — item-ticket titles must NOT,
  // so the item-title selector [data-item-id] .entity-row-title stays exact.
  function ticketRow(t, looseTitle) {
    var row = make("a", "tk");
    row.setAttribute("href", C.ROUTES.ticketPrefix + t.id);
    row.setAttribute("data-ticket-id", t.id);
    row.appendChild(make("span", "st st--" + t.state, prettyState(t.state)));
    row.appendChild(make("span", looseTitle ? "tt entity-row-title" : "tt", t.title));
    row.appendChild(make("span", "pr", t.priority));
    return row;
  }

  // The item = the primary row, a native <details data-item-id> composed in-screen
  // (its summary is chevron + title + chips + count — a different composition from the
  // ticket build's collapsibleField, so it is NOT routed through it: components.js
  // stays untouched). The title span carries entity-row-title; the chevron, chips and
  // count are siblings, so its textContent equals the title exactly (e2e item 33).
  function itemDisclosure(item) {
    var details = make("details", "item");
    details.setAttribute("data-item-id", item.id);
    var summary = make("summary");
    summary.appendChild(make("span", "chev", "›"));
    summary.appendChild(make("span", "it entity-row-title", item.title));
    summary.appendChild(itemChips(item));
    summary.appendChild(make("span", "count", countText(item.tickets)));
    details.appendChild(summary);
    var body = make("div", "tkts");
    var tickets = item.tickets || [];
    if (tickets.length) {
      tickets.forEach(function (t) {
        body.appendChild(ticketRow(t, false));
      });
    } else {
      body.appendChild(make("div", "none", "No tickets on this item yet."));
    }
    details.appendChild(body);
    return details;
  }

  function groupLabel(status, count) {
    var label = make("div", "glabel");
    label.appendChild(document.createTextNode(GROUP_LABEL[status] + " "));
    label.appendChild(make("span", "n", "· " + count));
    return label;
  }

  // Live group — always rendered (visible even when empty) so [data-status-group=…]
  // resolves for the "ready when active is empty" e2e wait (item 32).
  function liveGroup(status, items) {
    var group = make("div", "grp");
    group.setAttribute("data-status-group", status);
    group.appendChild(groupLabel(status, items.length));
    items.forEach(function (item) {
      group.appendChild(itemDisclosure(item));
    });
    return group;
  }

  // Settled group — a collapsed group-level disclosure; the inner grp carries
  // data-status-group so [data-status-group="done"] [data-item-id] still resolves
  // (item 33 reads titles visibility-agnostically, so collapsed is fine).
  function settledGroup(status, items) {
    var details = make("details", "sett");
    var summary = make("summary");
    summary.appendChild(make(
      "span", "glabel2" + (status === "done" ? " glabel2--done" : ""),
      GROUP_LABEL[status] + " · " + items.length
    ));
    summary.appendChild(make("span", "gchev", "›"));
    details.appendChild(summary);
    var group = make("div", "grp settled");
    group.setAttribute("data-status-group", status);
    items.forEach(function (item) {
      group.appendChild(itemDisclosure(item));
    });
    details.appendChild(group);
    return details;
  }

  // Loose tickets (§5) — tickets on the sprint with no item parent, tucked behind a
  // group-level disclosure. Rows link direct to the ticket; the [data-loose] body and
  // per-row data-ticket-id are the e2e contract (items 32 + 33).
  function looseDisclosure(tickets) {
    var details = make("details", "sett");
    var summary = make("summary");
    summary.appendChild(make("span", "glabel2", "Loose tickets · " + tickets.length));
    summary.appendChild(make("span", "gchev", "›"));
    details.appendChild(summary);
    var body = make("div", "tkts");
    body.setAttribute("data-loose", "");
    tickets.forEach(function (t) {
      body.appendChild(ticketRow(t, true));
    });
    details.appendChild(body);
    return details;
  }

  function renderTracking(root, res) {
    var s = res.sprint;
    var sid = s.id;
    var doc = make("div", "doc");
    doc.appendChild(sprintHeader(s, sid));
    var col = make("div", "col");
    col.appendChild(tabPair("tracking"));
    col.appendChild(betFrame(s, res.groups));
    LIVE_ORDER.forEach(function (status) {
      col.appendChild(liveGroup(status, res.groups[status] || []));
    });
    SETTLED_ORDER.forEach(function (status) {
      var items = res.groups[status] || [];
      if (items.length) {
        col.appendChild(settledGroup(status, items));
      }
    });
    if (res.loose_tickets.length) {
      col.appendChild(looseDisclosure(res.loose_tickets));
    }
    doc.appendChild(col);
    root.replaceChildren(doc);
  }

  // --- OVERVIEW page (Kickoff / Mid-sprint Review / Sprint Review, redesign) ----

  // The bet, stated once atop the sections — a read-only echo of primary_bet (its
  // editable copy is the Kickoff "Primary bet" field below). Same frame as tracking,
  // minus the health line (that's a tracking-only rollup).
  function overviewBet(s) {
    var frame = make("section", "frame");
    var lead = make("div", "lead");
    lead.appendChild(comp.markdownBlock(s.primary_bet));
    frame.appendChild(lead);
    return frame;
  }

  // One field — the shared primitive across all three sections: a header label + an
  // inline-editable markdown body (the Today/ticket idiom). Editing commits on blur →
  // PATCH that ONE sprint field; the WS flush re-renders (no optimistic UI). Nothing
  // freezes, so every field is always editable.
  function overviewField(s, sid, pair) {
    var field = make("div", "field");
    field.setAttribute("data-field", pair[0]);
    field.appendChild(make("div", "flabel", pair[1]));
    var val = make("div", "fval");
    comp.inlineEdit(val, {
      getValue: function () { return s[pair[0]]; },
      onSave: function (raw) {
        var b = {};
        b[pair[0]] = raw;
        return api.fetchJson(sprintPath(sid), { method: "PATCH", body: b });
      },
      markdown: true,
      multiline: true,
      placeholder: "(none)"
    });
    field.appendChild(val);
    return field;
  }

  // A section — one native <details> primitive (name + meta + chevron), body = its
  // fields (an optional refline first). `open` is phase-driven, decided by the caller.
  function phaseSection(kind, name, meta, open, fields, s, sid, refline) {
    var details = make("details", "phase");
    details.setAttribute("data-phase", kind);
    if (open) {
      details.setAttribute("open", "");
    }
    var summary = make("summary");
    summary.appendChild(make("span", "pnm", name));
    summary.appendChild(make("span", "pmeta", meta));
    summary.appendChild(make("span", "chev", "›"));
    details.appendChild(summary);
    var body = make("div", "body");
    if (refline) {
      body.appendChild(make("div", "refline", refline));
    }
    fields.forEach(function (pair) {
      body.appendChild(overviewField(s, sid, pair));
    });
    details.appendChild(body);
    return details;
  }

  // Any of a section's fields already written? Drives which section opens (P8).
  function sectionHasContent(s, fields) {
    return fields.some(function (pair) {
      var v = s[pair[0]];
      return v !== null && v !== undefined && String(v).trim() !== "";
    });
  }

  function renderOverview(root, res) {
    var s = res.sprint;
    var sid = s.id;
    var doc = make("div", "doc");
    doc.appendChild(sprintHeader(s, sid));
    var col = make("div", "col");
    col.appendChild(tabPair("overview"));
    col.appendChild(overviewBet(s));

    // Phase → which section opens, derived from the sprint's own content (no backend
    // phase flag, no freeze): review-open once the Sprint Review has content; running
    // once the Mid-sprint Review does; kickoff-open at the start.
    var reviewHas = sectionHasContent(s, REVIEW);
    var midHas = sectionHasContent(s, MID);
    var openKickoff = !reviewHas && !midHas;
    var openMid = reviewHas || midHas;
    var openReview = reviewHas;

    col.appendChild(
      phaseSection("kickoff", "Kickoff", "set at the start", openKickoff, KICKOFF, s, sid, null)
    );
    col.appendChild(
      phaseSection("mid", "Mid-sprint Review", "mid-sprint", openMid, MID, s, sid, null)
    );
    col.appendChild(
      phaseSection(
        "review", "Sprint Review", "end of sprint", openReview, REVIEW, s, sid,
        "Written with the Mid-sprint Review above in view — it's the raw material " +
          "for this retrospective."
      )
    );
    doc.appendChild(col);
    root.replaceChildren(doc);
  }

  // --- render ------------------------------------------------------------------

  function renderSprint(root, params) {
    root.setAttribute("data-screen", "sprint");
    var overview = params && params.sub === "overview";
    api.fetchJson(CURRENT).then(
      function (res) {
        if (res.sprint === null) {
          root.replaceChildren(quiet("No current sprint."));
          return;
        }
        if (overview) {
          renderOverview(root, res);
        } else {
          renderTracking(root, res);
        }
      },
      function (err) {
        root.replaceChildren(comp.errorLine(err));
      }
    );
  }

  Planner.registerScreen("sprint", renderSprint);
})();
