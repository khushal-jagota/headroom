/* Screen 3: Board (#/board) — T16 (SPEC §10.3). Stateless render over /api/board:
 * one column per ticket state (server order; `dropped` never present), cards are
 * entity-row anchors decorated with data-* for e2e. The whole card is the
 * click-through; no drag-and-drop, no client-side date math (the server sends no
 * overdue flag). Registers the real "board" screen over app.js's placeholder
 * (overwrite-wins). Classic script: IIFE + "use strict", createElement only. */
(function () {
  "use strict";
  var Planner = window.Planner;
  var config = Planner.config;
  var C = Planner.components;
  var api = Planner.api;

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

  // Marker chip reused from the chip primitive; value === variant so the "blocked"
  // default branch (no known variant) still shows its own text, while the known
  // marker variants ignore the value and render their canned label.
  function marker(v) {
    var chip = C.chip(v, v);
    chip.setAttribute("data-marker", v);
    return chip;
  }

  function cardAnchor(card) {
    var chips = [
      C.chip("priority", card.priority),
      card.deadline ? C.chip("deadline", card.deadline) : null,
      card.project ? C.chip("project", card.project) : null,
      card.has_pending_proposal ? marker("pending-proposal") : null,
      card.has_running_claim ? marker("running-claim") : null
    ];
    var anchor = C.entityRow({
      href: config.ROUTES.ticketPrefix + card.id,
      title: card.title,
      chips: chips
    });
    anchor.setAttribute("data-card", "");
    anchor.setAttribute("data-ticket-id", card.id);
    return anchor;
  }

  function render(root) {
    api.fetchJson("/api/board").then(
      function (board) {
        var section = el("section", "board-screen");
        section.setAttribute("data-screen", "board");
        var lane = el("div", "board");
        (board.columns || []).forEach(function (col) {
          var column = el("div", "board-column");
          column.setAttribute("data-column", col.state);

          var head = el("div", "board-column-head");
          head.appendChild(el("span", "board-column-name", col.state.replace(/_/g, " ")));
          head.appendChild(el("span", "board-column-count", String(col.cards.length)));
          column.appendChild(head);

          var cards = el("div", "board-column-cards");
          if (col.cards.length) {
            col.cards.forEach(function (card) {
              cards.appendChild(cardAnchor(card));
            });
          } else {
            cards.appendChild(C.quietLine("(empty)"));
          }
          column.appendChild(cards);
          lane.appendChild(column);
        });
        section.appendChild(lane);
        root.appendChild(section);
      },
      function (err) {
        root.appendChild(C.errorLine(err));
      }
    );
  }

  Planner.registerScreen("board", render);
})();
