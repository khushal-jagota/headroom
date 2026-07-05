/* Screen 4: Ticket (#/ticket/<id>) — T16 (SPEC §10.4). Stateless render over five
 * parallel fetches (detail + events + runs + sprints + chat status). Field
 * sections show the value as markdown; a pending proposal composes T15's
 * proposalCard (grant-pair picker gated on the gating field only); notes/recap are
 * inline field editors. State/Grant controls, Copy, Links, Day/Sprint assignment,
 * Run history, Event log, and the T15 chat panel round it out. Every mutation calls
 * fetchJson and relies on the WS flush -> route() re-render; no optimistic UI. The
 * lone direct DOM tweak is the copy button's "Copied" flash (a read, not a
 * WS-mutating action, so no flush follows). Classic script: IIFE + "use strict",
 * createElement only. */
(function () {
  "use strict";
  var Planner = window.Planner;
  var config = Planner.config;
  var C = Planner.components;
  var api = Planner.api;

  var LINK_KINDS = ["belongs_to", "parent_child", "blocks", "relates"];
  var FIELD_NAMES = ["success", "approach", "plan", "result"];
  var COPY_FLASH_MS = 1500;

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

  // Marker chip: value === variant, so "blocked" (no known chip variant) shows its
  // own text via the default branch while known markers render their canned label.
  function marker(v) {
    var chip = C.chip(v, v);
    chip.setAttribute("data-marker", v);
    return chip;
  }

  // Plain-text fetch for copy-text: fetchJson would JSON.parse the text body and
  // reject. The reject shape only needs .code/.message for errorLine.
  function fetchText(path) {
    return fetch(path).then(function (r) {
      if (!r.ok) {
        var err = new Error("HTTP " + r.status);
        err.code = "http_error";
        return Promise.reject(err);
      }
      return r.text();
    });
  }

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = el("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (e) {
        copied = false;
      }
      document.body.removeChild(ta);
      if (copied) {
        resolve();
      } else {
        reject(new Error("copy failed"));
      }
    });
  }

  // In-flight discipline for the screen's own buttons (links/day/sprint/copy): clear
  // any prior error, disable, run. On rejection re-enable and surface the error;
  // returns the promise so copy can flash after a successful clipboard write. For a
  // WS-mutating action the caller does NOT re-enable — the flush re-render replaces
  // the DOM.
  function submit(button, errorHost, fn) {
    var prior = errorHost.querySelector(".error-line");
    if (prior) {
      errorHost.removeChild(prior);
    }
    button.disabled = true;
    return Promise.resolve(fn()).then(
      function (res) {
        return res;
      },
      function (err) {
        button.disabled = false;
        errorHost.prepend(C.errorLine(err));
        return Promise.reject(err);
      }
    );
  }

  function selectEl(options, selectedValue) {
    var select = el("select", "form-control");
    options.forEach(function (opt) {
      var option = el("option", null, opt.label);
      option.value = opt.value;
      select.appendChild(option);
    });
    if (selectedValue !== undefined && selectedValue !== null) {
      select.value = selectedValue;
    }
    return select;
  }

  // --- section builders ------------------------------------------------------

  function headerPanel(detail) {
    var chips = el("div", "ticket-header-chips");
    chips.appendChild(C.chip("state", detail.state));
    chips.appendChild(C.chip("priority", detail.priority));
    if (detail.project) {
      chips.appendChild(C.chip("project", detail.project));
    }
    if (detail.deadline) {
      chips.appendChild(C.chip("deadline", detail.deadline));
    }
    if (detail.claim_active) {
      chips.appendChild(marker("running-claim"));
    }
    if (detail.auto_blocked) {
      chips.appendChild(marker("auto-blocked"));
    }
    if (detail.blocked) {
      chips.appendChild(marker("blocked"));
    }
    return C.panel(detail.title, [chips]);
  }

  function fieldPanel(id, detail, name) {
    var slot = detail.fields[name];
    var body = el("div", "ticket-field");
    body.setAttribute("data-field", name);
    body.appendChild(C.markdownBlock(slot.value));

    if (slot.proposal) {
      body.appendChild(C.proposalCard({
        proposal: slot.proposal,
        requireGrant: name === C.gatingField(detail.state),
        newState: C.advanceTarget(detail.state, detail.ceiling),
        onAccept: function (payload) {
          return api.fetchJson("/api/tickets/" + id + "/accept/" + name, {
            method: "POST",
            body: payload
          });
        }
      }));
    }

    body.appendChild(C.fieldEditor(slot.notes, function (note) {
      return api.fetchJson("/api/tickets/" + id + "/notes/" + name, {
        method: "PUT",
        body: { note: note }
      });
    }));

    return C.panel(name, [body]);
  }

  function recapPanel(id, detail) {
    var body = el("div", "ticket-recap");
    body.setAttribute("data-recap", "");
    body.appendChild(C.markdownBlock(detail.recap));
    body.appendChild(C.fieldEditor(detail.recap, function (text) {
      return api.fetchJson("/api/tickets/" + id + "/recap", {
        method: "PUT",
        body: { body: text }
      });
    }));
    return C.panel("Recap", [body]);
  }

  function statePanel(id, detail) {
    return C.panel("State", [
      C.stateControl({
        state: detail.state,
        autoBlocked: detail.auto_blocked,
        onJump: function (to) {
          return api.fetchJson("/api/tickets/" + id + "/state", {
            method: "POST",
            body: { to: to }
          });
        },
        onDrop: function () {
          return api.fetchJson("/api/tickets/" + id + "/drop", { method: "POST" });
        },
        onUnblock: function () {
          return api.fetchJson("/api/tickets/" + id + "/unblock", { method: "POST" });
        }
      })
    ]);
  }

  function grantPanel(id, detail) {
    return C.panel("Grant", [
      C.grantControl({
        state: detail.state,
        ceiling: detail.ceiling,
        atCap: detail.at_cap,
        onSave: function (ceiling, atCap) {
          return api.fetchJson("/api/tickets/" + id + "/grant", {
            method: "POST",
            body: { ceiling: ceiling, at_cap: atCap }
          });
        }
      })
    ]);
  }

  function copyPanel(id) {
    var errorHost = el("div", "ticket-copy-error");
    var button = el("button", "button", "Copy ticket");
    button.type = "button";
    button.setAttribute("data-copy", "");
    button.addEventListener("click", function () {
      submit(button, errorHost, function () {
        return fetchText("/api/tickets/" + id + "/copy-text").then(copyToClipboard);
      }).then(
        function () {
          // A read, not a WS-mutating action: no flush re-render follows, so the
          // flash is the only feedback and is set directly (the sole sanctioned
          // direct DOM tweak in this screen).
          button.textContent = "Copied";
          button.disabled = false;
          setTimeout(function () {
            button.textContent = "Copy ticket";
          }, COPY_FLASH_MS);
        },
        function () {}
      );
    });
    var wrap = el("div", "ticket-copy");
    wrap.appendChild(button);
    wrap.appendChild(errorHost);
    return C.panel("Copy", [wrap]);
  }

  function endpointNode(endpointId, currentId) {
    if (endpointId.indexOf("t_") === 0 && endpointId !== currentId) {
      var anchor = el("a", "ticket-link-target", endpointId);
      anchor.setAttribute("href", config.ROUTES.ticketPrefix + endpointId);
      return anchor;
    }
    return el("span", "ticket-link-endpoint", endpointId);
  }

  function linkRow(id, link, errorHost) {
    var row = el("div", "ticket-link-row");
    row.setAttribute("data-link-row", "");
    row.setAttribute("data-link-kind", link.kind);
    row.appendChild(el("span", "ticket-link-kind", link.kind + ":"));
    row.appendChild(endpointNode(link.from_id, id));
    row.appendChild(el("span", "ticket-link-arrow", "→"));
    row.appendChild(endpointNode(link.to_id, id));
    var remove = el("button", "button", "Remove");
    remove.type = "button";
    remove.setAttribute("data-link-remove", "");
    remove.addEventListener("click", function () {
      submit(remove, errorHost, function () {
        var qs = "from_id=" + encodeURIComponent(link.from_id) +
          "&to_id=" + encodeURIComponent(link.to_id) +
          "&kind=" + encodeURIComponent(link.kind);
        return api.fetchJson("/api/links?" + qs, { method: "DELETE" });
      });
    });
    row.appendChild(remove);
    return row;
  }

  function linkAddForm(id, errorHost) {
    var form = el("div", "ticket-link-add");
    form.setAttribute("data-link-add", "");

    var fromInput = el("input", "form-control");
    fromInput.type = "text";
    fromInput.setAttribute("data-link-from", "");
    fromInput.value = id;

    var toInput = el("input", "form-control");
    toInput.type = "text";
    toInput.setAttribute("data-link-to", "");
    toInput.setAttribute("placeholder", "to id");

    var kindSelect = selectEl(
      LINK_KINDS.map(function (k) { return { value: k, label: k }; }),
      LINK_KINDS[0]
    );
    kindSelect.setAttribute("data-link-kind-select", "");

    var add = el("button", "button", "Add");
    add.type = "button";
    add.setAttribute("data-link-add-btn", "");
    add.addEventListener("click", function () {
      submit(add, errorHost, function () {
        return api.fetchJson("/api/links", {
          method: "POST",
          body: {
            from_id: fromInput.value,
            to_id: toInput.value,
            kind: kindSelect.value
          }
        });
      });
    });

    form.appendChild(fromInput);
    form.appendChild(toInput);
    form.appendChild(kindSelect);
    form.appendChild(add);
    return form;
  }

  function linksPanel(id, detail) {
    var errorHost = el("div", "ticket-links-error");
    var list = el("div", "ticket-links");
    list.setAttribute("data-links", "");
    var links = detail.links || [];
    if (links.length) {
      links.forEach(function (link) {
        list.appendChild(linkRow(id, link, errorHost));
      });
    } else {
      list.appendChild(C.quietLine("(no links)"));
    }
    return C.panel("Links", [list, linkAddForm(id, errorHost), errorHost]);
  }

  function dayPanel(id, detail) {
    var errorHost = el("div", "ticket-day-error");
    var wrap = el("div", "ticket-day");
    wrap.setAttribute("data-day-assign", "");

    (detail.day_ids || []).forEach(function (dayId) {
      var row = el("div", "ticket-day-row");
      row.setAttribute("data-day-row", "");
      row.appendChild(el("span", "ticket-day-id", dayId));
      var remove = el("button", "button", "Remove");
      remove.type = "button";
      remove.setAttribute("data-day-remove", "");
      remove.addEventListener("click", function () {
        submit(remove, errorHost, function () {
          // day_<isoDate> -> isoDate; the mutating route wants the bare date segment.
          return api.fetchJson(
            "/api/day/" + dayId.slice(4) + "/tickets/" + id,
            { method: "DELETE" }
          );
        });
      });
      row.appendChild(remove);
      wrap.appendChild(row);
    });

    var addRow = el("div", "ticket-day-add");
    var dateInput = el("input", "form-control");
    dateInput.type = "date";
    dateInput.setAttribute("data-day-date", "");
    dateInput.value = new Date().toISOString().slice(0, 10);
    var add = el("button", "button", "Add to day");
    add.type = "button";
    add.setAttribute("data-day-add-btn", "");
    add.addEventListener("click", function () {
      submit(add, errorHost, function () {
        return api.fetchJson("/api/day/" + dateInput.value + "/tickets", {
          method: "POST",
          body: { ticket_id: id }
        });
      });
    });
    addRow.appendChild(dateInput);
    addRow.appendChild(add);
    wrap.appendChild(addRow);
    wrap.appendChild(errorHost);
    return C.panel("Day", [wrap]);
  }

  function sprintPanel(id, detail, sprints) {
    var errorHost = el("div", "ticket-sprint-error");
    var wrap = el("div", "ticket-sprint");
    wrap.setAttribute("data-sprint-assign", "");

    if (detail.sprint_item_id !== null && detail.sprint_item_id !== undefined) {
      // Parented: sprint is derived through the item; the server rejects set_sprint.
      wrap.appendChild(el("div", "ticket-sprint-derived",
        "sprint: " + (detail.effective_sprint_id || "(none)")));
      return C.panel("Sprint", [wrap]);
    }

    var options = [{ value: "", label: "(none)" }];
    sprints.forEach(function (s) {
      options.push({ value: s.id, label: s.name });
    });
    var select = selectEl(options, detail.sprint_id || "");
    select.setAttribute("data-sprint-select", "");

    var save = el("button", "button", "Save");
    save.type = "button";
    save.setAttribute("data-sprint-save", "");
    save.addEventListener("click", function () {
      submit(save, errorHost, function () {
        return api.fetchJson("/api/tickets/" + id, {
          method: "PATCH",
          body: { sprint_id: select.value || null }
        });
      });
    });

    wrap.appendChild(select);
    wrap.appendChild(save);
    wrap.appendChild(errorHost);
    return C.panel("Sprint", [wrap]);
  }

  // --- assembly --------------------------------------------------------------

  function build(root, id, detail, evs, runs, sprintsRes, statusRes) {
    var section = el("section", "ticket-screen");
    section.setAttribute("data-screen", "ticket");
    section.setAttribute("data-ticket-id", id);
    section.setAttribute("data-state", detail.state);

    section.appendChild(headerPanel(detail));
    FIELD_NAMES.forEach(function (name) {
      section.appendChild(fieldPanel(id, detail, name));
    });
    section.appendChild(recapPanel(id, detail));
    section.appendChild(statePanel(id, detail));
    section.appendChild(grantPanel(id, detail));
    section.appendChild(copyPanel(id));
    section.appendChild(linksPanel(id, detail));
    section.appendChild(dayPanel(id, detail));
    section.appendChild(sprintPanel(id, detail, sprintsRes.sprints || []));
    section.appendChild(C.panel("Runs", [C.runHistory(runs.runs)]));
    section.appendChild(C.panel("Events", [C.eventLog(evs.events)]));

    var chatWrap = el("div", "ticket-chat");
    chatWrap.setAttribute("data-chat", "");
    chatWrap.appendChild(C.chatPanel(id, { available: statusRes.available }));
    section.appendChild(C.panel("Chat", [chatWrap]));

    root.appendChild(section);
  }

  function render(root, params) {
    var id = params.id;
    Promise.all([
      api.fetchJson("/api/tickets/" + id),
      api.fetchJson("/api/tickets/" + id + "/events"),
      api.fetchJson("/api/tickets/" + id + "/runs"),
      api.fetchJson("/api/sprints"),
      api.fetchJson("/api/chat/" + id + "/status")
    ]).then(
      function (results) {
        build(root, id, results[0], results[1], results[2], results[3], results[4]);
      },
      function (err) {
        root.appendChild(C.errorLine(err));
      }
    );
  }

  Planner.registerScreen("ticket", render);
})();
