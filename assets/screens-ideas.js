/* Ideas screen (#/ideas) — §3.5 raw capture, redesigned per
 * orchestration/backlog-redesign/ideas.html. The one job: CAPTURE a thought with
 * the least friction, then browse the pile. So — unlike Backlog, where scanning is
 * the job and create is dormant — the compose is the HERO: always open at the top,
 * the largest type on the page, borrowing the Today focus-line idiom (transparent,
 * amber caret, separated by rhythm). Type a title, press Enter, it's captured;
 * detail and project are quiet and optional.
 *
 * The pile below, newest first (GET /api/ideas is created_at DESC). An idea's
 * substance is its body, but a wall of open bodies isn't calm — so bodies DISCLOSE:
 * each idea shows title + project at rest, expand to read (markdown via the shared
 * comp.markdownBlock). Title-only ideas — the quick dumps — are FLAT rows with no
 * chevron: an affordance that reveals nothing isn't drawn. A light relative date
 * from created_at orients the browse.
 *
 * No amber beyond the caret (capture is user-initiated, not a system ask). No
 * optimistic UI: a capture surfaces only when the WS-invalidation refetch re-renders
 * the screen. Classic script: registers the "ideas" screen over app.js's
 * placeholder (overwrite-wins). */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  var comp = Planner.components;
  var api = Planner.api;

  var IDEAS = "/api/ideas";                           // server-sorted: created_at DESC, id
  var POST_IDEA = "/api/ideas";

  // Project is nullable on an idea (§3.5) — "None" is the resting default and posts
  // no project (→ NULL).
  var PROJECT_OPTS = [
    { value: null, label: "None" },
    { value: "Vylo", label: "Vylo" },
    { value: "Tribe", label: "Tribe" },
    { value: "Learning", label: "Learning" },
    { value: "Other", label: "Other" }
  ];

  var MONTHS_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
  ];

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

  // created_at (unix seconds) → a light relative label: "today", "Nd" within a week,
  // else "Mon D". Uses the browser clock only for this cosmetic orientation (mutating
  // writes never touch the browser clock).
  function relDate(seconds) {
    var secs = Number(seconds);
    if (!isFinite(secs)) {
      return "";
    }
    var days = Math.floor((Date.now() / 1000 - secs) / 86400);
    if (days <= 0) {
      return "today";
    }
    if (days < 7) {
      return days + "d";
    }
    var d = new Date(secs * 1000);
    return MONTHS_ABBR[d.getMonth()] + " " + d.getDate();
  }

  // --- the hero capture --------------------------------------------------------

  function capture() {
    var section = make("section", "capture");
    section.setAttribute("data-create", "idea");

    var titleIn = make("input", "in title-in");
    titleIn.type = "text";
    titleIn.setAttribute("placeholder", "Capture an idea…");
    titleIn.setAttribute("data-input", "title");
    section.appendChild(titleIn);

    var detailIn = make("textarea", "in detail-in");
    detailIn.rows = 2;
    detailIn.setAttribute("placeholder", "Add detail — optional");
    detailIn.setAttribute("data-input", "body");
    section.appendChild(detailIn);

    var foot = make("div", "foot");
    var projSeg = segToggle("project", PROJECT_OPTS, null);
    foot.appendChild(projSeg);
    foot.appendChild(make("div", "spacer"));
    var hint = make("span", "hint");
    hint.appendChild(make("kbd", null, "↩"));
    hint.appendChild(document.createTextNode(" to capture"));
    foot.appendChild(hint);
    var commit = make("button", "commit", "Capture");
    commit.type = "button";
    commit.setAttribute("data-commit", "");
    commit.disabled = true;
    foot.appendChild(commit);
    section.appendChild(foot);

    function submit() {
      if (titleIn.value.trim() === "") {
        return;
      }
      var prior = section.querySelector(".error-line");
      if (prior) {
        prior.parentNode.removeChild(prior);
      }
      commit.disabled = true;
      var body = { title: titleIn.value.trim() };
      if (detailIn.value.trim()) {
        body.body = detailIn.value;
      }
      var project = projSeg.getValue();
      if (project) {
        body.project = project;     // "None" → null → omitted → NULL project
      }
      api.fetchJson(POST_IDEA, { method: "POST", body: body }).then(
        function () {
          // A3: stay disabled — the WS flush re-render replaces the screen.
        },
        function (err) {
          commit.disabled = false;
          section.insertBefore(comp.errorLine(err), section.firstChild);
        }
      );
    }

    titleIn.addEventListener("input", function () {
      commit.disabled = titleIn.value.trim() === "";
    });
    // The focus-line idiom: Enter on the title captures (the detail textarea keeps
    // Enter for newlines).
    titleIn.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submit();
      }
    });
    commit.addEventListener("click", submit);

    return section;
  }

  // --- the pile ----------------------------------------------------------------

  function ideaSummaryTail(summary, idea) {
    summary.appendChild(make("span", "it entity-row-title", idea.title));
    if (idea.project) {
      summary.appendChild(comp.chip("project", idea.project));
    }
    summary.appendChild(make("span", "when", relDate(idea.created_at)));
  }

  function ideaRow(idea) {
    var hasBody = idea.body !== null && idea.body !== undefined
      && String(idea.body).trim() !== "";
    if (hasBody) {
      var details = make("details", "idea");
      details.setAttribute("data-idea-id", idea.id);
      var summary = make("summary");
      summary.appendChild(make("span", "chev", "›"));
      ideaSummaryTail(summary, idea);
      details.appendChild(summary);
      var body = make("div", "body");
      body.appendChild(comp.markdownBlock(idea.body));
      details.appendChild(body);
      return details;
    }
    // Title-only: a flat row, no chevron (the empty .chev keeps the title aligned).
    var flat = make("div", "flat");
    flat.setAttribute("data-idea-id", idea.id);
    flat.appendChild(make("span", "chev"));
    ideaSummaryTail(flat, idea);
    return flat;
  }

  function renderList(host, ideas) {
    host.replaceChildren();
    if (!ideas.length) {
      host.appendChild(quiet("No ideas yet."));
      return;
    }
    host.appendChild(make("div", "glabel", "Captured"));
    ideas.forEach(function (idea) {
      host.appendChild(ideaRow(idea));
    });
  }

  // --- render ------------------------------------------------------------------

  function renderIdeas(root, params) {
    root.setAttribute("data-screen", "ideas");

    var doc = make("div", "doc");

    var head = make("header", "head");
    var headRow = make("div", "row");
    headRow.appendChild(make("div", "title", "Ideas"));
    var meta = make("div", "meta");
    var pill = make("span", "pill");
    var countText = document.createTextNode("0");
    pill.appendChild(countText);
    meta.appendChild(pill);
    headRow.appendChild(meta);
    head.appendChild(headRow);
    doc.appendChild(head);

    var col = make("div", "col");
    col.appendChild(capture());
    var listHost = make("div", "list");
    listHost.setAttribute("data-ideas", "");
    col.appendChild(listHost);
    doc.appendChild(col);

    root.replaceChildren(doc);

    api.fetchJson(IDEAS).then(
      function (res) {
        var ideas = res.ideas || [];
        countText.textContent = String(ideas.length);
        renderList(listHost, ideas);
      },
      function (err) {
        listHost.appendChild(comp.errorLine(err));
      }
    );
  }

  Planner.registerScreen("ideas", renderIdeas);
})();
