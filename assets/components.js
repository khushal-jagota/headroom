/* The D11 primitive components (items 1–6, 17). Every function returns DOM nodes
 * built with createElement / textContent — no innerHTML here (the sole innerHTML
 * in the codebase is markdown.js). No framework, no build. Classic script:
 * attaches Planner.components. */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});

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

  function appendChildren(parent, children) {
    if (children === null || children === undefined) {
      return;
    }
    if (Array.isArray(children)) {
      children.forEach(function (child) {
        if (child) {
          parent.appendChild(child);
        }
      });
    } else {
      parent.appendChild(children);
    }
  }

  // 5.1 App shell — the one component returning a handle, not a bare node.
  function appShell() {
    var config = Planner.config;
    var shell = make("div", "shell");
    var nav = make("header", "shell-nav");
    var links = make("nav", "shell-links");
    var badge = make("span", "nav-badge hidden");
    var linkEls = {};
    var items = [
      ["day", "Day"],
      ["review", "Review"],
      ["board", "Board"],
      ["sprint", "Sprint"],
      ["backlog", "Backlog"]
    ];
    items.forEach(function (pair) {
      var name = pair[0];
      var anchor = make("a", "nav-link");
      anchor.setAttribute("data-screen", name);
      anchor.setAttribute("href", config.ROUTES[name]);
      anchor.appendChild(document.createTextNode(pair[1]));
      if (name === "review") {
        anchor.appendChild(badge);
      }
      linkEls[name] = anchor;
      links.appendChild(anchor);
    });
    nav.appendChild(make("span", "shell-brand", "planner"));
    nav.appendChild(links);
    var content = make("main", "shell-content");
    shell.appendChild(nav);
    shell.appendChild(content);

    function setReviewBadge(n) {
      if (n > 0) {
        badge.textContent = String(n);
        badge.classList.remove("hidden");
      } else {
        badge.classList.add("hidden");
      }
    }
    function setActiveNav(name) {
      items.forEach(function (pair) {
        linkEls[pair[0]].classList.toggle("active", pair[0] === name);
      });
    }
    // The shell IS the returned DOM node (ticket: components return DOM nodes);
    // the router's handles ride on it as properties.
    shell.content = content;
    shell.setReviewBadge = setReviewBadge;
    shell.setActiveNav = setActiveNav;
    return shell;
  }

  // 5.2 Panel — the ONLY box primitive.
  function panel(title, children) {
    var section = make("section", "panel");
    section.appendChild(make("h2", "panel-title", title));
    var body = make("div", "panel-body");
    appendChildren(body, children);
    section.appendChild(body);
    return section;
  }

  // 5.3 Markdown block.
  function markdownBlock(text) {
    if (!text || !String(text).trim()) {
      return make("div", "quiet-line", "(none)");
    }
    var block = Planner.markdown.render(text);
    block.classList.add("markdown-block");
    return block;
  }

  // 5.4 Field editor — no optimistic UI; a successful save is reflected by the
  // WS-invalidation refetch.
  function fieldEditor(value, onSave) {
    var wrap = make("div", "field-editor");
    var textarea = make("textarea", "field-editor-input");
    textarea.rows = 8;
    textarea.value = value || "";
    var actions = make("div", "field-editor-actions");
    var button = make("button", "button button--primary", "Save");
    button.type = "button";
    button.addEventListener("click", function () {
      var prior = actions.querySelector(".error-line");
      if (prior) {
        actions.removeChild(prior);
      }
      button.disabled = true;
      Promise.resolve(onSave(textarea.value)).then(
        function () {},
        function (err) {
          actions.prepend(errorLine(err));
        }
      ).then(function () {
        button.disabled = false;
      });
    });
    actions.appendChild(button);
    wrap.appendChild(textarea);
    wrap.appendChild(actions);
    return wrap;
  }

  // 5.5 Meta chips — ONE component, variants. The accent appears exactly where a
  // human is needed; everything else stays achromatic.
  var CHIP_MARKERS = {
    "pending-proposal": "proposal pending",
    "running-claim": "● running",
    "blockers-cleared": "blockers cleared",
    "auto-blocked": "auto-blocked",
    "frozen": "frozen"
  };

  function chip(variant, value, opts) {
    opts = opts || {};
    var span = make("span", "chip");
    if (variant === "priority") {
      span.classList.add("chip--priority");
      span.classList.add("chip--" + String(value).toLowerCase());
      span.textContent = String(value);
    } else if (variant === "state") {
      span.classList.add("chip--state");
      span.setAttribute("data-value", String(value));
      span.textContent = String(value).replace(/_/g, " ");
    } else if (variant === "project") {
      span.classList.add("chip--project");
      span.textContent = String(value);
    } else if (variant === "deadline") {
      span.classList.add("chip--deadline");
      if (opts.overdue) {
        span.classList.add("chip--overdue");
      }
      span.textContent = String(value);
    } else if (Object.prototype.hasOwnProperty.call(CHIP_MARKERS, variant)) {
      span.classList.add("chip--" + variant);
      span.textContent = CHIP_MARKERS[variant];
    } else {
      span.textContent = value === undefined || value === null ? "" : String(value);
    }
    return span;
  }

  // 5.6 Entity row — a native anchor; state lives in the URL only.
  function entityRow(opts) {
    opts = opts || {};
    var anchor = make("a", "entity-row");
    anchor.setAttribute("href", opts.href || "#");
    anchor.appendChild(make("span", "entity-row-title", opts.title || ""));
    var chips = make("span", "entity-row-chips");
    appendChildren(chips, opts.chips || null);
    anchor.appendChild(chips);
    return anchor;
  }

  // 5.7 Error line — consumes exactly what fetchJson rejections carry.
  function errorLine(err) {
    var line = make("div", "error-line");
    line.appendChild(make("span", "error-code", (err && err.code) || "error"));
    line.appendChild(make("span", "error-message", (err && err.message) || "request failed"));
    return line;
  }

  Planner.components = {
    appShell: appShell,
    panel: panel,
    markdownBlock: markdownBlock,
    fieldEditor: fieldEditor,
    chip: chip,
    entityRow: entityRow,
    errorLine: errorLine
  };
})();
