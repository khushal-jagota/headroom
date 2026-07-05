/* Screen 1: Day (#/day, home). Stateless render over /api/day/today +
 * /api/queues + /api/chat/<day>/status. Every action calls fetchJson and relies
 * on the WS flush -> route() -> full re-render with fresh fetches; no optimistic
 * UI. Registers the real "day" screen over app.js's placeholder (overwrite-wins).
 * Classic script: IIFE + "use strict", var/function style, createElement only. */
(function () {
  "use strict";
  var Planner = window.Planner;
  var c = Planner.components;

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

  function render(root) {
    root.setAttribute("data-screen", "day");
    Promise.all([
      Planner.api.fetchJson("/api/day/today"),
      Planner.api.fetchJson("/api/queues")
    ]).then(function (results) {
      var day = results[0];
      var queues = results[1];
      // The chat entity id is day.id (day_YYYY-MM-DD) verbatim; a status-fetch
      // failure renders the offline notice rather than blanking the screen.
      return Planner.api.fetchJson("/api/chat/" + day.id + "/status").then(
        function (st) {
          build(root, day, queues, st.available);
        },
        function () {
          build(root, day, queues, false);
        }
      );
    }).then(null, function (err) {
      root.replaceChildren(c.errorLine(err));
    });
  }

  function planHandlers(date) {
    return {
      accept: function (node) {
        return Planner.api.fetchJson("/api/day/" + date + "/plan/accept", {
          method: "POST",
          body: { node: node }
        });
      },
      invalidate: function (node) {
        return Planner.api.fetchJson("/api/day/" + date + "/plan/invalidate", {
          method: "POST",
          body: { node: node }
        });
      },
      acceptAll: function () {
        return Planner.api.fetchJson("/api/day/" + date + "/plan/accept-all", {
          method: "POST"
        });
      },
      rejectAll: function () {
        return Planner.api.fetchJson("/api/day/" + date + "/plan/reject-all", {
          method: "POST"
        });
      }
    };
  }

  function dayRow(t, date, container) {
    var row = el("div", "day-ticket-row");
    row.setAttribute("data-ticket-id", t.id);
    var chips = [c.chip("priority", t.priority), c.chip("state", t.state)];
    if (t.claim_active) {
      chips.push(c.chip("running-claim"));
    }
    var gf = c.gatingField(t.state);
    if (gf && t.fields[gf] && t.fields[gf].proposal) {
      chips.push(c.chip("pending-proposal"));
    }
    row.appendChild(c.entityRow({ title: t.title, href: "#/ticket/" + t.id, chips: chips }));
    // The remove button is a SIBLING of the entity-row anchor (never nested).
    // Remove = defer (association-only delete, days/data.py:139-162).
    var remove = el("button", "button", "Remove");
    remove.type = "button";
    remove.setAttribute("data-remove", "");
    remove.addEventListener("click", function () {
      var prior = container.querySelector(".error-line");
      if (prior) {
        container.removeChild(prior);
      }
      remove.disabled = true;
      Planner.api.fetchJson("/api/day/" + date + "/tickets/" + t.id, { method: "DELETE" }).then(
        function () {},   // A3: stay disabled on success; the flush re-render replaces the row
        function (err) {
          remove.disabled = false;
          container.appendChild(c.errorLine(err));
        }
      );
    });
    row.appendChild(remove);
    return row;
  }

  function build(root, day, queues, available) {
    // Mutating day routes use the server's canonical date, not the browser clock.
    var date = day.id.slice(4);
    var grid = el("div", "day-grid");

    var main = el("div", "day-main");
    main.appendChild(c.panel("Brief", c.markdownBlock(day.brief)));
    main.appendChild(c.panel("Plan", day.plan ? c.planTree(day.plan, planHandlers(date)) : c.quietLine("(no plan)")));

    var tickets = day.tickets || [];
    var todayPanel = c.panel("Today", tickets.length ? [] : c.quietLine("(none)"));
    var todayBody = todayPanel.querySelector(".panel-body");
    tickets.forEach(function (t) {
      todayBody.appendChild(dayRow(t, date, todayBody));
    });
    main.appendChild(todayPanel);
    grid.appendChild(main);

    var side = el("div", "day-side");
    var reviewLink = el("a", "review-entry");
    reviewLink.setAttribute("data-review-entry", "");
    reviewLink.setAttribute("href", "#/review");
    reviewLink.appendChild(el("span", null, "Review"));
    var n = (queues.approvals || []).length;
    var count = el("span", n === 0 ? "review-entry-count zero" : "review-entry-count", String(n));
    count.setAttribute("data-pending-count", String(n));
    reviewLink.appendChild(count);
    side.appendChild(reviewLink);
    side.appendChild(c.panel("Chat", c.chatPanel(day.id, { available: available })));
    grid.appendChild(side);

    root.replaceChildren(grid);
  }

  Planner.registerScreen("day", render);
})();
