/* Screen 2: Review (#/review). One-at-a-time approval walk, oldest-first from
 * /api/queues approvals. Stateless render: each action calls fetchJson and relies
 * on the WS flush -> route() re-render (the fresh queue drops resolved entries).
 * The only client state is transient skip memory (module scope, per-session).
 * Registers the real "review" screen over app.js's placeholder (overwrite-wins).
 * Classic script: IIFE + "use strict", var/function style, createElement only. */
(function () {
  "use strict";
  var Planner = window.Planner;
  var c = Planner.components;

  // The one transient set (amendment A2): human skips + auto-skips. Reload resets
  // it; the wrap rule clears it when every entry is skipped.
  var skipped = {};

  function key(entry) {
    return entry.entity_id + ":" + entry.kind;
  }

  function render(root) {
    root.setAttribute("data-screen", "review");
    root.classList.add("review-screen");
    Planner.api.fetchJson("/api/queues").then(function (q) {
      var entries = q.approvals || [];   // oldest-first from the server (views.py:310)
      if (!entries.length) {
        showEmpty(root);
        return;
      }
      var live = entries.filter(function (e) {
        return !skipped[key(e)];
      });
      if (!live.length) {
        // Wrap rule: skipping the last entry starts over at the oldest.
        skipped = {};
        live = entries;
      }
      show(root, live);
    }, function (err) {
      root.replaceChildren(c.errorLine(err));
    });
  }

  function showEmpty(root) {
    var empty = c.quietLine("nothing waiting");
    empty.setAttribute("data-review-empty", "");
    root.replaceChildren(empty);
  }

  function isStale(entry, detail) {
    var kind = entry.kind;
    if (kind === "review") {
      return detail.state !== "needs_review";
    }
    if (kind === "status") {
      return !detail.status_proposal;
    }
    // gating kinds: proposal must still be pending on the current gating field.
    if (!detail.fields || !detail.fields[kind] || !detail.fields[kind].proposal) {
      return true;
    }
    return c.gatingField(detail.state) !== kind;
  }

  function detailError(root, entry, err) {
    // A2: detail-fetch rejection. Do NOT hide the entry and do NOT auto-refetch.
    // Surface an inline error; the human decides to move on via Skip.
    var wrap = document.createElement("div");
    wrap.appendChild(c.errorLine(err));
    wrap.appendChild(c.quietLine(entry.title));
    var footer = document.createElement("div");
    footer.className = "review-card-actions";
    var skip = document.createElement("button");
    skip.type = "button";
    skip.className = "button";
    skip.setAttribute("data-skip", "");
    skip.textContent = "Skip";
    skip.addEventListener("click", function () {
      skipped[key(entry)] = true;
      render(root);
    });
    footer.appendChild(skip);
    if (entry.entity_type === "ticket") {
      var open = document.createElement("a");
      open.setAttribute("data-open-ticket", "");
      open.setAttribute("href", "#/ticket/" + entry.entity_id);
      open.textContent = "open ticket";
      footer.appendChild(open);
    }
    wrap.appendChild(footer);
    root.replaceChildren(wrap);
  }

  function show(root, live) {
    if (!live.length) {
      showEmpty(root);
      return;
    }
    var entry = live[0];
    var path = entry.entity_type === "ticket" ? "/api/tickets/" : "/api/items/";
    Planner.api.fetchJson(path + entry.entity_id).then(function (detail) {
      if (isStale(entry, detail)) {
        // A2 staleness guard: mark it skipped and advance WITHIN this fetched
        // list — never a dead end while later entries may be live, never a
        // render loop (the list strictly shrinks; the next fresh queue fetch
        // drops truly-resolved entries anyway).
        skipped[key(entry)] = true;
        show(root, live.slice(1));
        return;
      }
      root.replaceChildren(c.reviewCard({
        entry: entry,
        detail: detail,
        onSkip: function () {
          skipped[key(entry)] = true;
          render(root);
        },
        onAccept: function (payload) {
          return Planner.api.fetchJson(
            "/api/tickets/" + entry.entity_id + "/accept/" + entry.kind,
            { method: "POST", body: payload }
          );
        },
        onApprove: function () {
          return Planner.api.fetchJson("/api/tickets/" + entry.entity_id + "/approve", {
            method: "POST",
            body: {}
          });
        },
        onAcceptStatus: function () {
          return Planner.api.fetchJson("/api/items/" + entry.entity_id + "/accept-status", {
            method: "POST",
            body: {}
          });
        }
      }));
    }, function (err) {
      detailError(root, entry, err);
    });
  }

  Planner.registerScreen("review", render);
})();
