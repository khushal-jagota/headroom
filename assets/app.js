/* Hash router, screen registry, and boot. Owns the shell instance. The router
 * exposes exactly Planner.registerScreen(name, render) — screens navigate via
 * anchors and read params; no navigate(), no route events, no current-route
 * getter. Every render derives from location.hash plus fresh fetches, so refresh
 * restores state by construction. Classic script: attaches Planner.registerScreen. */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  var config = Planner.config;

  var registry = {};
  var shell = null;
  var lastHash = null;

  Planner.registerScreen = function (name, render) {
    registry[name] = { render: render };
  };

  function quietLine(text) {
    var div = document.createElement("div");
    div.className = "quiet-line";
    div.textContent = text;
    return div;
  }

  // Placeholders — T15–T17 scripts, appended after this file, re-register the real
  // screens over these before DOMContentLoaded fires (overwrite-wins).
  ["day", "review", "board", "ticket", "sprint", "backlog"].forEach(function (name) {
    Planner.registerScreen(name, function (root) {
      root.appendChild(quietLine("not built yet"));
    });
  });

  function route() {
    var hash = location.hash;
    if (hash === "" || hash === "#") {
      location.replace(config.ROUTES.day);
      return;
    }
    var segments = hash.slice(1).split("/").filter(Boolean);
    var name = segments[0];
    var params = {};
    if (name === "ticket") {
      params.id = segments[1];
    }
    var known = Object.prototype.hasOwnProperty.call(registry, name);
    // Exact route shapes only: #/<screen> or #/ticket/<id> — no trailing extras.
    var badShape = name === "ticket"
      ? segments.length !== 2 || !params.id
      : segments.length !== 1;
    if (!known || badShape) {
      shell.setActiveNav(null);
      shell.content.replaceChildren(quietLine("no such screen"));
      return;
    }
    shell.setActiveNav(name);
    var entrance = hash !== lastHash;
    lastHash = hash;
    var screenDiv = document.createElement("div");
    screenDiv.className = entrance ? "screen screen-enter" : "screen";
    shell.content.replaceChildren(screenDiv);
    registry[name].render(screenDiv, params);
  }

  function refreshBadge() {
    Planner.api.fetchJson(config.API.QUEUES).then(
      function (res) {
        shell.setReviewBadge((res.approvals || []).length);
      },
      function () {
        console.debug("[planner] queues fetch failed; badge unchanged");
      }
    );
  }

  function onFlush() {
    refreshBadge();
    route();
  }

  function boot() {
    shell = Planner.components.appShell();
    document.getElementById("app").appendChild(shell);

    Planner.api.fetchJson(config.API.META).then(
      function (meta) {
        return meta.ui_debounce_ms;
      },
      function () {
        console.debug("[planner] meta fetch failed; using default debounce");
        return config.DEBOUNCE_MS_DEFAULT;
      }
    ).then(function (debounceMs) {
      Planner.bus.onInvalidate(onFlush);
      Planner.bus.start(debounceMs);
      window.addEventListener("hashchange", route);
      refreshBadge();
      route();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
