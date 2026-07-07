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
      ["backlog", "Backlog"],
      ["ideas", "Ideas"]
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
    nav.appendChild(make("span", "shell-brand", "Panels"));
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
    "agent-working": "● working",
    "blockers-cleared": "blockers cleared",
    "errored": "errored",
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

  // 5.8 Create form (D11 #13) — item / idea variants. Owns form mechanics ONLY
  // (disable, clear, error placement); the screen's onSubmit builds the request
  // body and returns the fetchJson promise. No optimistic UI — a successful create
  // is reflected only when the WS-invalidation refetch re-renders the list.
  var PROJECTS = ["Vylo", "Tribe", "Learning", "Other"];   // §3.2 Project enum
  var PRIORITIES = ["P0", "P1", "P2", "P3"];                // §3.2 priorities
  var FORM_SPECS = {
    item: {
      submitLabel: "Add item",
      fields: [
        { name: "title", kind: "text", label: "Title", required: true, placeholder: "Item title" },
        { name: "project", kind: "select", label: "Project", options: PROJECTS, default: "Vylo" },
        { name: "priority", kind: "select", label: "Priority", options: PRIORITIES, default: "P3" },
        { name: "deadline", kind: "date", label: "Deadline" }
      ]
    },
    idea: {
      submitLabel: "Add idea",
      fields: [
        { name: "title", kind: "text", label: "Title", required: true, placeholder: "Idea title" },
        { name: "project", kind: "select", label: "Project", options: PROJECTS,
          includeBlank: true, blankLabel: "— no project —", default: "" },
        { name: "body", kind: "textarea", label: "Body" }
      ]
    }
  };

  function createForm(variant, onSubmit) {
    var spec = FORM_SPECS[variant];
    var form = make("form", "create-form");
    form.setAttribute("data-create", variant);
    var controls = [];
    spec.fields.forEach(function (field) {
      var wrap = make("div", "create-form-field");
      wrap.appendChild(make("label", "create-form-label", field.label));
      var control;
      var reset = "";
      if (field.kind === "select") {
        control = make("select", "form-control");
        if (field.includeBlank) {
          var blank = make("option", null, field.blankLabel);
          blank.value = "";
          control.appendChild(blank);
        }
        field.options.forEach(function (opt) {
          var option = make("option", null, opt);
          option.value = opt;
          control.appendChild(option);
        });
        reset = field.default !== undefined
          ? field.default
          : (field.includeBlank ? "" : field.options[0]);
        control.value = reset;
      } else if (field.kind === "textarea") {
        control = make("textarea", "form-control");
        control.rows = 4;
      } else {
        control = make("input", "form-control");
        control.type = field.kind === "date" ? "date" : "text";
        if (field.placeholder) {
          control.setAttribute("placeholder", field.placeholder);
        }
      }
      control.setAttribute("data-input", field.name);
      if (field.required) {
        control.required = true;
      }
      wrap.appendChild(control);
      form.appendChild(wrap);
      controls.push({ name: field.name, control: control, reset: reset });
    });
    var actions = make("div", "field-editor-actions");
    var submit = make("button", "button button--primary", spec.submitLabel);
    submit.type = "submit";
    actions.appendChild(submit);
    form.appendChild(actions);

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var prior = form.querySelector(".error-line");
      if (prior) {
        prior.parentNode.removeChild(prior);
      }
      if (!form.checkValidity()) {
        form.reportValidity();
        return;
      }
      var values = {};
      controls.forEach(function (entry) {
        values[entry.name] = entry.control.value;
      });
      submit.disabled = true;
      Promise.resolve(onSubmit(values)).then(
        function () {
          controls.forEach(function (entry) {
            entry.control.value = entry.reset;
          });
        },
        function (err) {
          actions.prepend(errorLine(err));
        }
      ).then(function () {
        submit.disabled = false;
      });
    });
    return form;
  }

  // ------------------------------------------------------------------------
  // T15 additions (D11 items 7, 8, 9, 10, 16) — Day + Review composites, the
  // pluggable chat-input registry, and the small state-machine mirrors the two
  // screens share. Additive: nothing above this line is touched. The server
  // re-validates every grant (machine.py:75-100); these mirrors only pick which
  // grant options to OFFER, never gate a write.
  // ------------------------------------------------------------------------

  // §4.1 linear order + §4.2 tables, mirrored for grant-option math only.
  var STATE_ORDER = [
    "needs_success", "needs_approach", "needs_plan",
    "in_progress", "needs_review", "done"
  ];
  var GATING_FIELD = {
    needs_success: "success",
    needs_approach: "approach",
    needs_plan: "plan",
    in_progress: "result"
  };
  var ADVANCE = {
    needs_success: "needs_approach",
    needs_approach: "needs_plan",
    needs_plan: "in_progress",
    in_progress: "needs_review"
  };

  // Transient conversation-render state (SPEC §11 / §9 no-store doctrine). Chat
  // messages have NO server representation (chat/service.py:81-104 stores only the
  // session key), so this is not a cache of canonical data: losing it on reload is
  // correct. It survives WS-flush re-renders because scripts are not re-executed —
  // only route() re-runs, and each re-render rebuilds the panel from these.
  var chatTranscripts = {};   // entity_id -> [{who: "you"|"planner", text: string}]
  var chatDrafts = {};        // entity_id -> string (pending, unsent input text)
  var chatPending = {};       // entity_id -> bool (a send is in flight -> thinking dots)
  var _atcapSeq = 0;          // per-instance unique radio-group names

  function advanceTarget(state, ceiling) {
    // Mirror of machine.py:40-48 (+ the ceiling=done special case).
    if (state === "in_progress" && ceiling === "done") {
      return "done";
    }
    return ADVANCE[state] || null;
  }

  function gatingField(state) {
    return GATING_FIELD[state] || null;
  }

  // Centralized scope options: the ceiling states a selector may offer — the floor
  // state (the current state, or the state being advanced to) and every state after
  // it, never an earlier stage. One source for the approval grant picker and the
  // header scope control, so neither can offer "approve back past where we are".
  function ceilingOptions(floorState) {
    var start = STATE_ORDER.indexOf(floorState);
    if (start < 0) {
      start = 0;
    }
    return STATE_ORDER.slice(start).map(function (state) {
      return { value: state, label: stateLabel(state) };
    });
  }

  function quietLine(text) {
    return make("div", "quiet-line", text);
  }

  // Shared mutating-button discipline (amendment A3): on click clear any prior
  // error and disable; on rejection re-enable and surface the error; on SUCCESS
  // stay disabled — the WS-flush re-render replaces the DOM, and re-enabling would
  // only reopen the flush-gap double-fire window. Private helper (not exported).
  function bindMutating(button, container, run) {
    button.addEventListener("click", function () {
      var prior = container.querySelector(".error-line");
      if (prior) {
        container.removeChild(prior);
      }
      button.disabled = true;
      Promise.resolve(run()).then(
        function () {},
        function (err) {
          button.disabled = false;
          container.prepend(errorLine(err));
        }
      );
    });
  }

  // Fetch-once cache of the gateway command catalog (spike 02 §2): one GET per page
  // load, shared across every composer via the Planner namespace. A failed fetch
  // clears the cache so the next "/" retries; the menu just doesn't show until it
  // resolves. The catalog is a transient view — not a client state store.
  function commandCatalog() {
    if (!Planner._commandCatalog) {
      Planner._commandCatalog = Planner.api.fetchJson("/api/chat/commands").then(
        function (cat) { return cat; },
        function (err) {
          Planner._commandCatalog = null;
          return Promise.reject(err);
        }
      );
    }
    return Planner._commandCatalog;
  }

  // --- the pluggable chat-input source (SPEC §14 audio seam) -----------------
  // A source is factory(ctx) -> HTMLElement. ctx.submit(text) -> Promise is the
  // only way a source delivers input (the panel owns transport); ctx.initialText
  // is the preserved draft; ctx.onInput(text) reports pending text so the panel
  // can preserve the draft across re-renders. ctx.runCommand(command) -> Promise
  // runs a gateway /command on the ticket's own mind (skills; spike 02 §3).
  function makeTextInputSource(ctx) {
    // A single recessed field: the container IS the input. A tiny "/" trigger and a
    // send button (arrow, amber when there's text) sit in a footer. Enter sends,
    // Shift+Enter is a newline. When the text starts with "/" a popover of the
    // gateway's command catalog (categories then Skills) opens above the composer.
    var box = make("div", "chat-box");
    var textarea = make("textarea", "chat-ta");
    textarea.setAttribute("data-chat-input", "");
    textarea.rows = 1;
    textarea.placeholder = "Message the employee…";
    textarea.value = ctx.initialText || "";

    var menu = make("div", "chat-menu");
    menu.setAttribute("data-chat-menu", "");
    menu.hidden = true;

    var foot = make("div", "chat-foot");
    var slash = make("button", "chat-slash", "/");
    slash.type = "button";
    slash.title = "Commands";
    slash.setAttribute("data-chat-slash", "");
    var send = make("button", "chat-send", "↑");
    send.type = "button";
    send.title = "Send";
    send.setAttribute("data-chat-send", "");

    var catalog = null;       // the fetched CommandCatalog (or null until first "/")
    var loading = false;      // a catalog fetch is in flight
    var items = [];           // [{name, skill, el}] in menu order, for keyboard nav
    var highlight = 0;

    function grow() {
      textarea.style.height = "auto";
      textarea.style.height = Math.min(textarea.scrollHeight, 110) + "px";
    }
    function syncSend() {
      if (textarea.value.trim()) {
        send.classList.add("on");
      } else {
        send.classList.remove("on");
      }
    }
    function clearComposer() {
      textarea.value = "";
      grow();
      syncSend();
      ctx.onInput("");
    }
    function reenable() { send.disabled = false; }
    function canRun() { return typeof ctx.runCommand === "function"; }

    function isSkill(name) {
      if (!catalog || !catalog.skills) { return false; }
      for (var i = 0; i < catalog.skills.length; i += 1) {
        if (catalog.skills[i][0] === name) { return true; }
      }
      return false;
    }

    // --- the "/" menu ------------------------------------------------------
    function menuOpen() { return !menu.hidden; }
    function closeMenu() {
      menu.hidden = true;
      menu.replaceChildren();
      items = [];
      highlight = 0;
    }
    function setHighlight(i) {
      if (items.length === 0) { return; }
      if (i < 0) { i = 0; }
      if (i > items.length - 1) { i = items.length - 1; }
      items.forEach(function (rec, idx) {
        if (idx === i) { rec.el.classList.add("on"); } else { rec.el.classList.remove("on"); }
      });
      highlight = i;
      items[i].el.scrollIntoView({ block: "nearest" });
    }
    function move(delta) {
      if (items.length === 0) { return; }
      var n = items.length;
      setHighlight((highlight + delta + n) % n);
    }
    function selectItem(rec) {
      closeMenu();
      if (rec.skill && canRun()) {
        clearComposer();
        runSlash(rec.name);
      } else {
        // a command (or a skill with no runner): insert "/name " for args + Send
        textarea.value = rec.name + " ";
        grow();
        syncSend();
        ctx.onInput(textarea.value);
        textarea.focus();
      }
    }
    function selectHighlighted() {
      if (items.length > 0) { selectItem(items[highlight]); }
    }
    function matches(pair, query) {
      if (!query) { return true; }
      if (pair[0].toLowerCase().indexOf(query) !== -1) { return true; }
      if (pair[1].toLowerCase().indexOf(query) !== -1) { return true; }
      // an exact alias of this row (e.g. "/wp" -> "/writing-plans") stays visible
      return !!(catalog.canon && catalog.canon["/" + query] === pair[0]);
    }
    function renderMenu(query) {
      menu.replaceChildren();
      items = [];
      var frag = document.createDocumentFragment();
      function addSection(title, pairs, isSkillSec) {
        var shown = (pairs || []).filter(function (p) { return matches(p, query); });
        if (shown.length === 0) { return; }
        frag.appendChild(make("div", "chat-menu-hd", title));
        shown.forEach(function (p) {
          var item = make("button", "chat-menu-item");
          item.type = "button";
          item.setAttribute("data-chat-cmd", p[0]);
          if (isSkillSec) { item.setAttribute("data-chat-skill", ""); }
          item.appendChild(make("span", "chat-menu-name", p[0]));
          item.appendChild(make("span", "chat-menu-desc", p[1]));
          var rec = { name: p[0], skill: isSkillSec, el: item };
          var idx = items.length;
          items.push(rec);
          // preventDefault on mousedown keeps focus on the textarea through the click
          item.addEventListener("mousedown", function (e) { e.preventDefault(); });
          item.addEventListener("mouseenter", function () { setHighlight(idx); });
          item.addEventListener("click", function () { selectItem(rec); });
          frag.appendChild(item);
        });
      }
      (catalog.categories || []).forEach(function (cat) {
        addSection(cat.name, cat.pairs, false);
      });
      addSection("Skills", catalog.skills, true);
      menu.appendChild(frag);
      if (items.length === 0) {
        menu.hidden = true;
        return;
      }
      menu.hidden = false;
      setHighlight(0);
    }
    function updateMenu() {
      var v = textarea.value;
      if (!/^\/\S*$/.test(v)) { closeMenu(); return; }  // slash + no space yet
      var query = v.slice(1).toLowerCase();
      if (catalog) { renderMenu(query); return; }
      if (loading) { return; }
      loading = true;
      commandCatalog().then(
        function (cat) {
          loading = false;
          catalog = cat;
          var cur = textarea.value;
          if (/^\/\S*$/.test(cur)) { renderMenu(cur.slice(1).toLowerCase()); }
        },
        function () { loading = false; }  // silent: the menu just won't show
      );
    }

    // --- send / run --------------------------------------------------------
    function runSlash(command) {
      send.disabled = true;
      Promise.resolve(ctx.runCommand(command)).then(reenable, reenable);
    }
    function skillCommandFor(text) {
      // The canonical command string if `text` runs a known skill, else null. A
      // leading "/" is inert to a normal send, so only skills route to runCommand
      // in this slice; other "/" text sends as plain chat.
      if (text.charAt(0) !== "/" || !catalog || !canRun()) { return null; }
      var firstTok = text.split(/\s+/)[0];
      var name = (catalog.canon && catalog.canon[firstTok.toLowerCase()]) || firstTok;
      if (!isSkill(name)) { return null; }
      return name + text.slice(firstTok.length);  // canonical name + preserved args
    }
    function doSend() {
      var text = textarea.value.trim();
      if (!text) { return; }
      closeMenu();
      send.disabled = true;
      clearComposer();  // synchronous clear so a second Enter can't double-send
      // A leading "/" is inert to a normal send; if the catalog has not loaded yet we
      // must resolve it before deciding, or a fast type-and-send would lose a skill.
      if (text.charAt(0) === "/" && !catalog && canRun()) {
        commandCatalog().then(
          function (cat) { catalog = cat; routeSend(text); },
          function () { routeSend(text); }  // no catalog -> treat as plain chat
        );
        return;
      }
      routeSend(text);
    }
    function routeSend(text) {
      var skillCmd = skillCommandFor(text);
      if (skillCmd !== null) {
        Promise.resolve(ctx.runCommand(skillCmd)).then(reenable, reenable);
      } else {
        Promise.resolve(ctx.submit(text)).then(reenable, reenable);
      }
    }

    textarea.addEventListener("input", function () {
      ctx.onInput(textarea.value);
      grow();
      syncSend();
      updateMenu();
    });
    textarea.addEventListener("keydown", function (e) {
      if (menuOpen()) {
        if (e.key === "ArrowDown") { e.preventDefault(); move(1); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); move(-1); return; }
        if (e.key === "Escape") { e.preventDefault(); closeMenu(); return; }
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); selectHighlighted(); return; }
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        doSend();
      }
    });
    textarea.addEventListener("blur", function () {
      window.setTimeout(closeMenu, 100);  // let a menu click land before dismissing
    });
    send.addEventListener("click", doSend);
    // preventDefault keeps focus on the textarea so the click doesn't blur it (which
    // would schedule the close below and kill the menu this same click opens).
    slash.addEventListener("mousedown", function (e) { e.preventDefault(); });
    slash.addEventListener("click", function () {
      if (textarea.value.charAt(0) !== "/") {
        textarea.value = "/" + textarea.value;
      }
      textarea.focus();
      ctx.onInput(textarea.value);
      grow();
      syncSend();
      updateMenu();
    });

    foot.appendChild(slash);
    foot.appendChild(send);
    box.appendChild(textarea);
    box.appendChild(foot);
    box.appendChild(menu);
    grow();
    syncSend();
    updateMenu();  // a preserved "/draft" reopens the menu after a re-render
    return box;
  }

  // --- D11 item 8: grant-pair picker -----------------------------------------
  // getGrant() returns null until BOTH halves are explicitly chosen. The ceiling
  // options are exactly the states resolve_grant accepts (machine.py:94-99): the
  // NO_FURTHER sentinel plus every STATE_ORDER state at or beyond newState.
  function grantPairPicker(newState, onChange) {
    var wrap = make("div", "grant-picker");
    var label = make("label", "grant-label");
    label.appendChild(make("span", null, "how far"));
    var select = make("select", "grant-ceiling");
    select.setAttribute("data-grant-ceiling", "");
    var placeholder = make("option", null, "");
    placeholder.value = "";
    placeholder.disabled = true;
    placeholder.selected = true;
    placeholder.hidden = true;
    select.appendChild(placeholder);
    var noFurther = make("option", null, "No further");
    noFurther.value = "none";
    select.appendChild(noFurther);
    ceilingOptions(newState).forEach(function (opt) {
      var option = make("option", null, opt.label);
      option.value = opt.value;
      select.appendChild(option);
    });
    select.addEventListener("change", onChange);
    label.appendChild(select);
    wrap.appendChild(label);

    var atcap = make("span", "grant-atcap");
    atcap.setAttribute("data-grant-atcap", "");
    var groupName = "atcap-" + String((_atcapSeq += 1));
    var inputs = [];
    [["stop", "Stop"], ["propose", "Propose"]].forEach(function (pair) {
      var radioLabel = make("label", null);
      var input = make("input", null);
      input.type = "radio";
      input.name = groupName;
      input.value = pair[0];
      input.addEventListener("change", onChange);
      inputs.push(input);
      radioLabel.appendChild(input);
      radioLabel.appendChild(make("span", null, pair[1]));
      atcap.appendChild(radioLabel);
    });
    wrap.appendChild(atcap);

    wrap.getGrant = function () {
      if (select.value === "") {
        return null;
      }
      var checked = null;
      inputs.forEach(function (input) {
        if (input.checked) {
          checked = input;
        }
      });
      if (checked === null) {
        return null;
      }
      return { next_ceiling: select.value, at_cap: checked.value };
    };
    return wrap;
  }

  // --- D11 item 7: proposal card ---------------------------------------------
  // edited_body is included iff the textarea differs (strict string) from the
  // proposal body from this render's fetch (resolution.py:151-161). When
  // requireGrant, Accept is unfireable until the picker yields both halves.
  function proposalCard(opts) {
    var card = make("div", "proposal-card");
    card.appendChild(make("div", "proposal-meta", "proposed by " + opts.proposal.proposed_by));
    card.appendChild(markdownBlock(opts.proposal.body));
    var textarea = make("textarea", "field-editor-input proposal-edit");
    textarea.setAttribute("data-edit", "");
    textarea.rows = 8;
    textarea.value = opts.proposal.body;
    card.appendChild(textarea);

    var picker = null;
    var inFlight = false;
    var resolved = false;
    var actions = make("div", "proposal-card-actions");
    var accept = make("button", "button button--primary", "Accept");
    accept.type = "button";
    accept.setAttribute("data-accept", "");

    function sync() {
      accept.disabled = inFlight || resolved ||
        (opts.requireGrant && (picker === null || picker.getGrant() === null));
    }

    if (opts.requireGrant) {
      picker = grantPairPicker(opts.newState, sync);
      card.appendChild(picker);
    }

    accept.addEventListener("click", function () {
      var prior = actions.querySelector(".error-line");
      if (prior) {
        actions.removeChild(prior);
      }
      var payload = {};
      if (textarea.value !== opts.proposal.body) {
        payload.edited_body = textarea.value;
      }
      if (opts.requireGrant) {
        var grant = picker.getGrant();
        payload.next_ceiling = grant.next_ceiling;
        payload.at_cap = grant.at_cap;
      }
      inFlight = true;
      sync();
      Promise.resolve(opts.onAccept(payload)).then(
        function () {
          resolved = true;   // A3: stay disabled after a successful accept
          inFlight = false;
          sync();
        },
        function (err) {
          inFlight = false;
          actions.prepend(errorLine(err));
          sync();
        }
      );
    });

    actions.appendChild(accept);
    card.appendChild(actions);
    sync();   // Accept starts disabled when a grant is required (nothing picked)
    return card;
  }

  // --- D11 item 10: chat panel -----------------------------------------------
  // entityId is the SERVER-provided chat id (day.id / ticket.id), never built
  // client-side. Rebuilds its message list from the module-scope transcript, which
  // the WS flush does not touch — that is the survival mechanism.
  // The ticket's chat with its EMPLOYEE (the durable agent working the ticket).
  // Message history + input, nothing else: one user pill, bubble-less employee prose,
  // a thinking-dots row while a reply is in flight, a whisper header (presence + label),
  // no dividers. Offline -> a calm two-line notice, no composer.
  function chatPanel(entityId, opts) {
    var panelEl = make("div", "chat-panel");
    panelEl.setAttribute("data-chat-panel", "");

    var head = make("div", "chat-head");
    head.appendChild(
      make("span", "chat-dot " + (opts.available ? "chat-dot--on" : "chat-dot--off"))
    );
    head.appendChild(make("span", "chat-lbl", opts.available ? "employee" : "employee · offline"));
    panelEl.appendChild(head);

    var thread = make("div", "chat-thread");
    thread.setAttribute("data-chat-messages", "");
    panelEl.appendChild(thread);

    function paint() {
      thread.replaceChildren();
      if (!opts.available) {
        var off = make("div", "chat-off");
        off.setAttribute("data-chat-offline", "");
        off.appendChild(make("div", null, "The employee is offline."));
        off.appendChild(make("div", "chat-off-sub", "Your draft is saved."));
        thread.appendChild(off);
        return;
      }
      var msgs = chatTranscripts[entityId] || [];
      if (msgs.length === 0 && !chatPending[entityId]) {
        var empty = make("div", "chat-empty");
        empty.appendChild(make("h2", "chat-empty-h", "What do you need?"));
        thread.appendChild(empty);
        return;
      }
      msgs.forEach(function (msg) {
        if (msg.who === "you") {
          var you = make("div", "chat-u", msg.text);
          you.setAttribute("data-chat-msg", "you");
          thread.appendChild(you);
        } else if (msg.who === "system") {
          // display output from a slash command (status/exec) — bare monospace, no bubble
          var sys = make("div", "chat-sys", msg.text);
          sys.setAttribute("data-chat-msg", "system");
          thread.appendChild(sys);
        } else {
          var reply = make("div", "chat-a");
          reply.setAttribute("data-chat-msg", "planner");
          reply.appendChild(markdownBlock(msg.text));
          thread.appendChild(reply);
        }
      });
      if (chatPending[entityId]) {
        var dots = make("div", "chat-dots");
        dots.setAttribute("data-chat-pending", "");
        dots.appendChild(make("i"));
        dots.appendChild(make("i"));
        dots.appendChild(make("i"));
        thread.appendChild(dots);
      }
      thread.scrollTop = thread.scrollHeight;
    }
    paint();

    if (!opts.available) {
      return panelEl;   // §11: no composer when the employee is offline
    }

    var source;
    var ctx = {
      initialText: chatDrafts[entityId] || "",
      onInput: function (text) {
        chatDrafts[entityId] = text;
      },
      submit: function (text) {
        if (!chatTranscripts[entityId]) {
          chatTranscripts[entityId] = [];
        }
        chatTranscripts[entityId].push({ who: "you", text: text });
        delete chatDrafts[entityId];
        chatPending[entityId] = true;
        paint();
        return Planner.api.fetchJson("/api/chat/" + entityId + "/send", {
          method: "POST",
          body: { text: text }
        }).then(
          function (res) {
            chatPending[entityId] = false;
            chatTranscripts[entityId].push({ who: "planner", text: res.reply_text });
            if (panelEl.isConnected) {   // a flush may have replaced the panel mid-flight
              paint();
            }
            return res;
          },
          function (err) {
            chatPending[entityId] = false;
            if (panelEl.isConnected) {
              paint();
            }
            var prior = panelEl.querySelector(".error-line");
            if (prior) {
              prior.remove();
            }
            panelEl.insertBefore(errorLine(err), source);
            return Promise.reject(err);
          }
        );
      },
      runCommand: function (command) {
        // Run a gateway /command (a skill) on this ticket's mind. Same transport
        // discipline as submit: optimistic "you" echo, thinking dots, then either an
        // assistant turn or a system display line by res.kind.
        if (!chatTranscripts[entityId]) {
          chatTranscripts[entityId] = [];
        }
        chatTranscripts[entityId].push({ who: "you", text: command });
        delete chatDrafts[entityId];
        chatPending[entityId] = true;
        paint();
        return Planner.api.fetchJson("/api/chat/" + entityId + "/command", {
          method: "POST",
          body: { command: command }
        }).then(
          function (res) {
            chatPending[entityId] = false;
            chatTranscripts[entityId].push({
              who: res.kind === "system" ? "system" : "planner",
              text: res.reply_text
            });
            if (panelEl.isConnected) {
              paint();
            }
            return res;
          },
          function (err) {
            chatPending[entityId] = false;
            if (panelEl.isConnected) {
              paint();
            }
            var prior = panelEl.querySelector(".error-line");
            if (prior) {
              prior.remove();
            }
            panelEl.insertBefore(errorLine(err), source);
            return Promise.reject(err);
          }
        );
      }
    };
    source = Planner.chatInput.sources[Planner.chatInput.active](ctx);
    panelEl.appendChild(source);
    return panelEl;
  }

  // --- D11 item 16: review card ----------------------------------------------
  // The shell is panel() (D11 #2 — the only box primitive). The screen guards
  // staleness before constructing (proposal null / state moved on).
  function reviewCard(opts) {
    var entry = opts.entry;
    var detail = opts.detail;
    var kind = entry.kind;
    var gating = kind === "success" || kind === "approach" ||
      kind === "plan" || kind === "result";
    var content = [];
    var head = make("div", "review-card-head");
    var footer = make("div", "review-card-actions");
    var primary = null;

    if (gating) {
      head.appendChild(chip("state", detail.state));
      head.appendChild(chip("pending-proposal"));
      content.push(head);
      content.push(proposalCard({
        proposal: detail.fields[kind].proposal,
        requireGrant: true,
        newState: advanceTarget(detail.state, detail.ceiling),
        onAccept: opts.onAccept
      }));
    } else if (kind === "review") {
      head.appendChild(chip("state", detail.state));
      content.push(head);
      content.push(markdownBlock(detail.fields.result.value));
      if (detail.fields.result.notes) {
        var note = make("div", "review-note");
        note.appendChild(markdownBlock(detail.fields.result.notes));
        content.push(note);
      }
      primary = make("button", "button button--primary", "Approve");
      primary.type = "button";
      primary.setAttribute("data-approve", "");
      bindMutating(primary, footer, function () {
        return opts.onApprove();
      });
    } else if (kind === "status") {
      head.appendChild(chip(null, detail.status));
      head.appendChild(chip(null, "→ " + detail.status_proposal.to_status));
      content.push(head);
      if (detail.status_proposal.note) {
        content.push(make("div", "review-note", detail.status_proposal.note));
      }
      primary = make("button", "button button--primary", "Accept");
      primary.type = "button";
      primary.setAttribute("data-accept-status", "");
      bindMutating(primary, footer, function () {
        return opts.onAcceptStatus();
      });
    }

    if (primary) {
      footer.appendChild(primary);
    }
    var skip = make("button", "button", "Skip");
    skip.type = "button";
    skip.setAttribute("data-skip", "");
    skip.addEventListener("click", function () {
      opts.onSkip();
    });
    footer.appendChild(skip);
    if (entry.entity_type === "ticket") {
      var open = make("a", "review-open", "open ticket");
      open.setAttribute("data-open-ticket", "");
      open.setAttribute("href", "#/ticket/" + entry.entity_id);
      footer.appendChild(open);
    }
    content.push(footer);

    var section = panel(entry.title, content);
    section.setAttribute("data-review-card", "");
    section.setAttribute("data-entity-id", entry.entity_id);
    section.setAttribute("data-kind", kind);
    if (gating) {
      section.setAttribute("data-field", kind);
    }
    return section;
  }

  // ------------------------------------------------------------------------
  // T16 additions (D11 items 11, 12, 14, 15) — State control, Grant control,
  // Event log, Run history. Additive: nothing above this line is touched. These
  // reuse the T15 grant-pair picker and the shared bindMutating discipline; the
  // server re-validates every write, so the pickers only choose what to OFFER.
  // ------------------------------------------------------------------------

  function stateLabel(s) {
    return String(s).replace(/_/g, " ");
  }

  function formatUnix(seconds) {
    if (seconds === null || seconds === undefined) {
      return "";
    }
    return new Date(Number(seconds) * 1000).toLocaleString();
  }

  // The exact event-row summary (payload keys confirmed against resolution.py /
  // api.py writers). Unlisted kinds get an empty summary; kind + time still show.
  function eventSummary(kind, payload) {
    payload = payload || {};
    if (kind === "state_changed") {
      return String(payload.from) + " → " + String(payload.to);
    }
    // Run outcome — the errored case carries the reason (agent init / crash / no
    // proposal). Surfacing it here is what makes a failed run debuggable at all.
    if (kind === "ticket_status_changed") {
      if (payload.error) {
        return String(payload.status) + " — " + String(payload.error);
      }
      return String(payload.status) + (payload.worker ? " · " + String(payload.worker) : "");
    }
    if (kind === "grant_changed") {
      return "approved until " + String(payload.ceiling) + " · " + String(payload.at_cap);
    }
    if (kind === "chat_session_created") {
      return String(payload.session_key || "");
    }
    if (kind === "proposal_accepted") {
      return String(payload.field) + " · " + String(payload.resolved_by);
    }
    if (kind === "ticket_updated") {
      return String(payload.field);
    }
    if (kind === "field_value_edited") {   // Decision B: {field, body}
      return String(payload.field) + " · edited";
    }
    return "";
  }

  // D11 #11: state control — human jump / drop / (conditional) unblock. No
  // re-render on success; the WS flush handles it (bindMutating stays disabled).
  function stateControl(opts) {
    var wrap = make("div", "state-control");
    wrap.setAttribute("data-state-control", "");
    wrap.appendChild(make("span", "state-control-current", stateLabel(opts.state)));

    var select = make("select", "state-control-select form-control");
    select.setAttribute("data-state-select", "");
    STATE_ORDER.forEach(function (s) {
      var option = make("option", null, stateLabel(s));
      option.value = s;
      select.appendChild(option);
    });
    select.value = opts.state;
    wrap.appendChild(select);

    var errorHost = make("div", "state-control-error");

    var jump = make("button", "button", "Jump");
    jump.type = "button";
    jump.setAttribute("data-state-jump", "");
    bindMutating(jump, errorHost, function () {
      return opts.onJump(select.value);
    });
    wrap.appendChild(jump);

    var drop = make("button", "button", "Drop");
    drop.type = "button";
    drop.setAttribute("data-drop", "");
    bindMutating(drop, errorHost, function () {
      return opts.onDrop();
    });
    wrap.appendChild(drop);

    if (opts.autoBlocked) {
      var unblock = make("button", "button", "Unblock");
      unblock.type = "button";
      unblock.setAttribute("data-unblock", "");
      bindMutating(unblock, errorHost, function () {
        return opts.onUnblock();
      });
      wrap.appendChild(unblock);
    }

    wrap.appendChild(errorHost);
    return wrap;
  }

  // D11 #12: grant control — plain ceiling/at-cap pickers via the T15 grant-pair
  // picker (no dial). Save is disabled until BOTH picker halves are chosen. "No
  // further" (next_ceiling === "none") means the ceiling becomes exactly the
  // current state (resolve_grant, machine.py:87-89).
  function grantControl(opts) {
    var wrap = make("div", "grant-control");
    wrap.setAttribute("data-grant-control", "");

    // Raw values (not stateLabel'd) so the persisted grant is asserted verbatim.
    var current = make(
      "div", "grant-control-current",
      "ceiling: " + opts.ceiling + " · at-cap: " + opts.atCap
    );
    current.setAttribute("data-grant-current", "");
    wrap.appendChild(current);

    var errorHost = make("div", "grant-control-error");

    var save = make("button", "button button--primary", "Save");
    save.type = "button";
    save.setAttribute("data-grant-save", "");
    save.disabled = true;

    var picker = grantPairPicker(opts.state, function () {
      save.disabled = picker.getGrant() === null;
    });
    wrap.appendChild(picker);

    bindMutating(save, errorHost, function () {
      var grant = picker.getGrant();
      var ceiling = grant.next_ceiling === "none" ? opts.state : grant.next_ceiling;
      return opts.onSave(ceiling, grant.at_cap);
    });
    wrap.appendChild(save);
    wrap.appendChild(errorHost);
    return wrap;
  }

  // D11 #14: event log — events as served (ascending id), rendered in order.
  function eventLog(events) {
    var wrap = make("div", "event-log");
    wrap.setAttribute("data-event-log", "");
    if (!events || !events.length) {
      wrap.appendChild(quietLine("(no events)"));
      return wrap;
    }
    events.forEach(function (ev) {
      var payload = ev.payload || {};
      var isError = ev.kind === "ticket_status_changed" && payload.status === "errored";
      var row = make("div", "event-log-row" + (isError ? " event-log-row--error" : ""));
      row.setAttribute("data-event-row", "");
      row.setAttribute("data-event-kind", ev.kind);
      row.appendChild(make("span", "event-log-kind", ev.kind));
      row.appendChild(make("span", "event-log-summary", eventSummary(ev.kind, ev.payload)));
      row.appendChild(make("span", "event-log-time", formatUnix(ev.created_at)));
      wrap.appendChild(row);
    });
    return wrap;
  }

  // ------------------------------------------------------------------------
  // T3 additions (ticket redesign — SPEC §10.4). The shared interaction
  // primitives the T4 ticket screen consumes: one inline-edit hook, the two-mode
  // approval, collapsible field sections, and the enum pill. Additive: nothing
  // above this line is touched. Every write still flows through fetchJson -> the
  // WS-flush re-render; no optimistic UI, no client-side store (SPEC §9).
  // ------------------------------------------------------------------------

  // The ONE inline-edit hook (no affordance). Markdown fields render at rest via
  // markdownBlock and swap to a raw source editor on focus; plain scalars (title)
  // show/edit textContent. In BOTH the editor is seeded from getValue() — the raw
  // JSON string, NEVER reconstructed from the rendered DOM (rendered markdown can't
  // round-trip links/code/lists). Save on blur or Cmd/Ctrl+Enter -> onSave(raw);
  // Esc reverts; unchanged is a no-op; re-renders on save. A rejected save keeps the
  // raw surface open and surfaces errorLine(err) (the fieldEditor reject contract).
  function inlineEdit(el, opts) {
    opts = opts || {};
    var markdown = !!opts.markdown;
    var multiline = !!opts.multiline;
    var editing = false;
    var reverting = false;
    var inFlight = false;

    el.classList.add("ed");
    el.setAttribute("contenteditable", "true");
    if (opts.placeholder) {
      el.setAttribute("data-ph", opts.placeholder);
    }

    function rawValue() {
      var v = opts.getValue();
      return v === null || v === undefined ? "" : String(v);
    }

    function clearError() {
      var next = el.nextSibling;
      if (next && next.nodeType === 1 && next.classList.contains("error-line")) {
        next.parentNode.removeChild(next);
      }
    }

    // Paint the rested view: rendered markdown (or plain text), or truly empty so
    // the :empty placeholder shows.
    function paint(raw) {
      clearError();
      editing = false;
      var text = raw === null || raw === undefined ? "" : String(raw);
      if (markdown) {
        if (text.trim()) {
          el.replaceChildren(markdownBlock(text));
        } else {
          el.replaceChildren();
        }
      } else {
        el.textContent = text;
      }
    }

    function enterEdit() {
      if (editing || inFlight) {
        return;
      }
      editing = true;
      clearError();
      el.textContent = rawValue();   // seed raw from JSON, never from the DOM
    }

    function commit() {
      if (!editing || inFlight) {
        return;
      }
      var raw = el.textContent;
      if (raw === rawValue()) {
        paint(raw);   // unchanged: revert to the rested render, no request
        return;
      }
      inFlight = true;
      Promise.resolve(opts.onSave(raw)).then(
        function () {
          inFlight = false;
          paint(raw);   // re-render the rested view from the saved raw text
        },
        function (err) {
          inFlight = false;   // keep the raw surface open; show the structured error
          clearError();
          el.insertAdjacentElement("afterend", errorLine(err));
        }
      );
    }

    el.addEventListener("focus", enterEdit);
    el.addEventListener("blur", function () {
      if (reverting) {
        reverting = false;
        return;
      }
      commit();
    });
    el.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        el.blur();   // blur commits
        return;
      }
      if (!multiline && e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        el.blur();
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        reverting = true;
        paint(rawValue());
        el.blur();
      }
    });

    paint(opts.getValue());
    return el;
  }

  // A local markdown DRAFT surface (used by approvalBlock). Same preview<->raw swap
  // as inlineEdit, but it NEVER auto-persists: the raw text lives in this closure,
  // seeded raw from initialRaw (never DOM-derived), read only when Approve asks.
  function markdownDraft(initialRaw) {
    var node = make("div", "ed approval-draft");
    node.setAttribute("contenteditable", "true");
    node.setAttribute("data-edit", "");
    var raw = initialRaw === null || initialRaw === undefined ? "" : String(initialRaw);
    var editing = false;

    function preview() {
      editing = false;
      if (raw.trim()) {
        node.replaceChildren(markdownBlock(raw));
      } else {
        node.replaceChildren();
      }
    }
    node.addEventListener("focus", function () {
      if (editing) {
        return;
      }
      editing = true;
      node.textContent = raw;   // seed raw from the stored draft string
    });
    node.addEventListener("input", function () {
      if (editing) {
        raw = node.textContent;
      }
    });
    node.addEventListener("blur", function () {
      raw = node.textContent;   // sync before re-rendering the preview
      preview();
    });
    preview();
    return {
      node: node,
      getRaw: function () {
        return editing ? node.textContent : raw;
      }
    };
  }

  // The recessed Note surface: a header + an inline-editable markdown body that
  // persists on blur (a note, not a draft) via onNoteSave -> PUT /notes/{field}.
  function noteSurface(noteValue, onNoteSave, heading, placeholder) {
    var box = make("div", "note");
    box.appendChild(make("div", "note-head", heading));
    var body = make("div", "note-body");
    inlineEdit(body, {
      getValue: function () {
        return noteValue;
      },
      onSave: onNoteSave,
      markdown: true,
      multiline: true,
      placeholder: placeholder
    });
    box.appendChild(body);
    return box;
  }

  // The approval — a single instance, two modes (SPEC §10.4, §4.4).
  //  gating-pending: the body is a LOCAL draft (raw-seeded from proposalBody, markdown
  //    preview<->raw on focus); a recessed Note editable inline (persists on blur);
  //    Approve + the reused grant-pair picker. onApprove receives
  //    {next_ceiling, at_cap, edited_body?} — edited_body ONLY when the draft differs
  //    from the original (matching proposalCard so proposal_accepted.edited stays honest).
  //  needs_review: the settled result value (read-only markdown) + editable review
  //    notes; Approve (no grant, terminal) -> onApprove({}).
  function approvalBlock(opts) {
    opts = opts || {};
    var wrap = make("div", "approval");
    wrap.setAttribute("data-approval-block", "");
    wrap.setAttribute("data-mode", opts.mode);
    if (opts.field) {
      wrap.setAttribute("data-field", opts.field);
    }

    wrap.appendChild(make("div", "approval-what",
      opts.whatLabel || (opts.field ? stateLabel(opts.field) : "")));

    var actions = make("div", "approval-actions");

    if (opts.mode === "needs_review") {
      var result = make("div", "approval-result");
      if (opts.onValueSave) {
        // The settled result value edit lives HERE in needs_review (Decision B:
        // result is a passed field). inlineEdit renders it as markdown at rest.
        inlineEdit(result, {
          getValue: function () { return opts.proposalBody; },
          onSave: opts.onValueSave,
          markdown: true,
          multiline: true,
          placeholder: "Result…"
        });
      } else {
        result.appendChild(markdownBlock(opts.proposalBody));
      }
      wrap.appendChild(result);
      wrap.appendChild(noteSurface(
        opts.note, opts.onNoteSave, "Review notes",
        "Things to check before you approve the result…"
      ));

      var approve = make("button", "approval-approve", "Approve");
      approve.type = "button";
      approve.setAttribute("data-approve", "");
      bindMutating(approve, actions, function () {
        return opts.onApprove({});
      });
      actions.appendChild(approve);
      wrap.appendChild(actions);
      return wrap;
    }

    // gating-pending
    var draft = markdownDraft(opts.proposalBody);
    wrap.appendChild(draft.node);
    wrap.appendChild(noteSurface(opts.note, opts.onNoteSave, "Note", null));

    var origBody = opts.proposalBody === null || opts.proposalBody === undefined
      ? "" : String(opts.proposalBody);
    var accept = make("button", "approval-approve", "Approve");
    accept.type = "button";
    accept.setAttribute("data-accept", "");
    var inFlight = false;
    var resolved = false;

    function sync() {
      accept.disabled = inFlight || resolved || picker.getGrant() === null;
    }

    var picker = grantPairPicker(opts.newState, sync);

    accept.addEventListener("click", function () {
      var prior = actions.querySelector(".error-line");
      if (prior) {
        actions.removeChild(prior);
      }
      var grant = picker.getGrant();
      var payload = { next_ceiling: grant.next_ceiling, at_cap: grant.at_cap };
      var edited = draft.getRaw();
      if (edited !== origBody) {   // send raw edited_body only when it actually differs
        payload.edited_body = edited;
      }
      inFlight = true;
      sync();
      Promise.resolve(opts.onApprove(payload)).then(
        function () {
          resolved = true;   // A3: stay disabled after a successful approve
          inFlight = false;
          sync();
        },
        function (err) {
          inFlight = false;
          actions.prepend(errorLine(err));
          sync();
        }
      );
    });
    actions.appendChild(accept);
    actions.appendChild(picker);
    wrap.appendChild(actions);
    sync();   // disabled until BOTH grant halves are chosen
    return wrap;
  }

  // Collapsible field section — native <details>: summary = mark + name + chevron,
  // body directly below (not inset). The hairline seam lives ONLY between rows
  // (CSS: details.fsec + details.fsec), so the first row has no top border.
  var MARK_KIND = { "✓": "done", "●": "now", "○": "todo" };

  function collapsibleField(opts) {
    opts = opts || {};
    var details = make("details", "fsec");
    var summary = make("summary", null);
    var kind = opts.markKind || MARK_KIND[opts.mark] || "";
    summary.appendChild(make("span", "fsec-mark" + (kind ? " fsec-mark--" + kind : ""), opts.mark));
    summary.appendChild(make("span", "fsec-name", opts.name));
    summary.appendChild(make("span", "fsec-chev"));   // glyph is CSS ::before (swaps on [open])
    details.appendChild(summary);
    var body = make("div", "fsec-body");
    appendChildren(body, opts.body);
    details.appendChild(body);
    return details;
  }

  // Enum pill — background only, no border; a transparent native <select> overlay,
  // for the fixed-enum metadata (state / priority / project). onChange(newValue)
  // fires on change; no optimistic UI — the WS-flush re-render reflects the value.
  function enumPill(opts) {
    opts = opts || {};
    var pill = make("span", "pill" + (opts.variant ? " pill--" + opts.variant : ""));
    if (opts.key) {
      pill.appendChild(make("span", "pill-key", opts.key));
    }
    var options = opts.options || [];
    var currentLabel = null;
    options.forEach(function (o) {
      if (o.value === opts.value) {
        currentLabel = o.label;
      }
    });
    pill.appendChild(document.createTextNode(
      currentLabel === null ? String(opts.value) : currentLabel
    ));
    var select = make("select", null);
    options.forEach(function (o) {
      var option = make("option", null, o.label);
      option.value = o.value;
      select.appendChild(option);
    });
    if (opts.value !== undefined && opts.value !== null) {
      select.value = opts.value;
    }
    select.addEventListener("change", function () {
      opts.onChange(select.value);
    });
    pill.appendChild(select);
    return pill;
  }

  Planner.components = {
    appShell: appShell,
    panel: panel,
    markdownBlock: markdownBlock,
    fieldEditor: fieldEditor,
    chip: chip,
    entityRow: entityRow,
    errorLine: errorLine,
    createForm: createForm,
    quietLine: quietLine,
    advanceTarget: advanceTarget,
    gatingField: gatingField,
    ceilingOptions: ceilingOptions,
    STATE_ORDER: STATE_ORDER,
    proposalCard: proposalCard,
    grantPairPicker: grantPairPicker,
    chatPanel: chatPanel,
    reviewCard: reviewCard,
    stateControl: stateControl,
    grantControl: grantControl,
    eventLog: eventLog,
    inlineEdit: inlineEdit,
    approvalBlock: approvalBlock,
    collapsibleField: collapsibleField,
    enumPill: enumPill
  };

  // The pluggable chat-input registry (SPEC §14 audio seam). A later audio script
  // registers a second source with one <script> tag and no edits here: chatPanel
  // reads sources[active] at render time and couples only to the ctx contract.
  Planner.chatInput = {
    sources: { text: makeTextInputSource },
    active: "text",
    register: function (name, factory, makeActive) {
      Planner.chatInput.sources[name] = factory;
      if (makeActive) {
        Planner.chatInput.active = name;
      }
    }
  };
})();
