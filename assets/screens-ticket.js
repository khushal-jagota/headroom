/* Screen 4: Ticket (#/ticket/<id>) — T4 ticket redesign (SPEC §10.4, the
 * ticket-redesign plan + mockup). Stateless render over four parallel fetches
 * (detail + sprints + chat status + current sprint). Top → bottom:
 *   header (inlineEdit title, then a pill row: priority / due / project / sprint —
 *   sprint reads "current" when it is the current sprint — a copy affordance, and the
 *   agent-working/errored/blocked run-status markers) → Recap (inlineEdit, offered only
 *   past needs_success) → THE Approval (a single approvalBlock, gating-pending or
 *   needs_review by state; it carries the scope pair) → collapsible field sections
 *   (success/approach/plan/result, driven by field_is_passed). Chat is a side rail.
 * Every mutation calls fetchJson and relies on the WS flush -> route() re-render;
 * no optimistic UI. The lone direct DOM tweak is the copy button's "Copied" flash
 * (a read, not a WS-mutating action, so no flush follows). Classic script: IIFE +
 * "use strict", createElement only, no innerHTML. The field-render primitives
 * (inlineEdit, approvalBlock, collapsibleField, enumPill) come from T3
 * (Planner.components) — consumed here, never redefined. */
(function () {
  "use strict";
  var Planner = window.Planner;
  var C = Planner.components;
  var api = Planner.api;

  var FIELD_NAMES = ["success", "approach", "plan", "result"];
  var PROJECTS = ["Vylo", "Tribe", "Learning", "Other"];   // §3.2 Project enum
  var PRIORITIES = ["P0", "P1", "P2", "P3"];                // §3.2 priorities
  // Mirror of the backend gate map (machine.GATED_STATE): a field is "passed"
  // once the ticket's state is strictly beyond the state that field gates.
  var GATED_STATE = {
    success: "needs_success",
    approach: "needs_approach",
    plan: "needs_plan",
    result: "in_progress"
  };
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

  function pretty(s) {
    return String(s).replace(/_/g, " ");
  }

  // field_is_passed(field, state): the exact mirror of Decision B's rule, so the
  // UI never offers a value-edit the server would reject. dropped is absent from
  // STATE_ORDER (index -1), so no field is "passed" on a dropped ticket.
  function fieldIsPassed(field, state) {
    return C.STATE_ORDER.indexOf(state) > C.STATE_ORDER.indexOf(GATED_STATE[field]);
  }

  // Marker chip: value === variant, so "blocked" (no known chip variant) shows its
  // own text via the default branch while known markers render their canned label.
  function marker(v) {
    var chip = C.chip(v, v);
    chip.setAttribute("data-marker", v);
    return chip;
  }

  function patch(id, body) {
    return api.fetchJson("/api/tickets/" + id, { method: "PATCH", body: body });
  }

  function saveScope(id, body) {
    return api.fetchJson("/api/tickets/" + id + "/scope", { method: "POST", body: body });
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

  // Editable markdown note home (PUT /notes/{field}). Mirrors T3's internal
  // noteSurface (which is not exported): a recessed "Note" surface persisting on
  // blur. Every field section carries exactly one of these except where the
  // approval block above owns the note (current gating field with a pending
  // proposal, and result in needs_review).
  function noteEditor(id, name, slot) {
    var box = el("div", "note");
    box.appendChild(el("div", "note-head", "Note"));
    var body = el("div", "note-body");
    C.inlineEdit(body, {
      getValue: function () { return slot.notes; },
      onSave: function (note) {
        return api.fetchJson("/api/tickets/" + id + "/notes/" + name, {
          method: "PUT",
          body: { note: note }
        });
      },
      markdown: true,
      multiline: true,
      placeholder: "Note…"
    });
    box.appendChild(body);
    return box;
  }

  // A settled value shown read-only as rendered markdown ("(none)" when empty).
  function valueReadonly(slot) {
    return C.markdownBlock(slot.value);
  }

  // A passed, proposal-less value: editable in place via PUT /value/{field}
  // (Decision B). At rest inlineEdit renders the value as a .markdown-block.
  function valueEditable(id, name, slot) {
    var body = el("div", "ticket-field-value");
    C.inlineEdit(body, {
      getValue: function () { return slot.value; },
      onSave: function (raw) {
        return api.fetchJson("/api/tickets/" + id + "/value/" + name, {
          method: "PUT",
          body: { body: raw }
        });
      },
      markdown: true,
      multiline: true,
      placeholder: "Value…"
    });
    return body;
  }

  // A resolvable proposal card for a NON-gating pending proposal (§4.4.4): accept /
  // edit with no scope pair (only the gating field requires the scope pair).
  function resolvableProposal(id, detail, name, slot) {
    return C.proposalCard({
      proposal: slot.proposal,
      requireScope: false,
      newState: C.advanceTarget(detail.state, detail.ceiling),
      onAccept: function (payload) {
        return api.fetchJson("/api/tickets/" + id + "/accept/" + name, {
          method: "POST",
          body: payload
        });
      }
    });
  }

  // Copy — a small header affordance (pill-styled button); errors surface in the
  // shared header error host. A read (not a WS-mutating action), so the "Copied"
  // flash is the only feedback and is set directly.
  function headerCopy(id, errorHost) {
    var button = el("button", "pill pill-button", "Copy");
    button.type = "button";
    button.setAttribute("data-copy", "");
    button.addEventListener("click", function () {
      submit(button, errorHost, function () {
        return fetchText("/api/tickets/" + id + "/copy-text").then(copyToClipboard);
      }).then(
        function () {
          button.textContent = "Copied";
          button.disabled = false;
          setTimeout(function () {
            button.textContent = "Copy";
          }, COPY_FLASH_MS);
        },
        function () {}
      );
    });
    return button;
  }

  // --- header ----------------------------------------------------------------

  function headerNode(id, detail, sprints, currentSprintId) {
    var head = el("header", "ticket-head");
    var headErr = el("div", "ticket-head-error");

    // On success the WS flush re-renders; on rejection surface the error inline.
    function headSave(factory) {
      var prior = headErr.querySelector(".error-line");
      if (prior) {
        headErr.removeChild(prior);
      }
      Promise.resolve(factory()).then(null, function (err) {
        headErr.prepend(C.errorLine(err));
      });
    }

    // Title — inline plain-scalar edit (human-only PATCH {title}).
    var title = el("div", "ticket-title");
    C.inlineEdit(title, {
      getValue: function () { return detail.title; },
      onSave: function (raw) { return patch(id, { title: raw }); },
      markdown: false,
      multiline: false,
      placeholder: "Untitled"
    });
    head.appendChild(title);

    var meta = el("div", "ticket-meta");

    // Priority pill (agent-permitted; PATCH {priority}).
    meta.appendChild(C.enumPill({
      value: detail.priority,
      options: PRIORITIES.map(function (p) { return { value: p, label: p }; }),
      onChange: function (v) {
        if (v === detail.priority) {
          return;
        }
        headSave(function () { return patch(id, { priority: v }); });
      }
    }));

    // Due — a nullable date; empty shows a bare "due" placeholder (not a dash).
    // A transparent native date input overlays the pill; a click opens the picker.
    (function () {
      var wrap = el("span", "pill");
      wrap.appendChild(el("span", "pill-key", "due"));
      if (detail.deadline) {
        wrap.appendChild(document.createTextNode(detail.deadline));
      }
      var input = el("input", "ticket-deadline-input");
      input.type = "date";
      input.setAttribute("data-deadline", "");
      input.value = detail.deadline || "";
      input.addEventListener("change", function () {
        headSave(function () { return patch(id, { deadline: input.value || null }); });
      });
      input.addEventListener("click", function () {
        if (typeof input.showPicker === "function") {
          try {
            input.showPicker();
          } catch (e) {
            /* not user-activated / unsupported — the field is still focusable */
          }
        }
      });
      wrap.appendChild(input);
      meta.appendChild(wrap);
    })();

    // Project pill — editable on unparented tickets even when null (offer a clear);
    // OMITTED entirely when parented (project is then derived + unsettable).
    if (detail.sprint_item_id === null || detail.sprint_item_id === undefined) {
      var projOptions = [{ value: "", label: "(no project)" }].concat(
        PROJECTS.map(function (p) { return { value: p, label: p }; })
      );
      meta.appendChild(C.enumPill({
        value: detail.project || "",
        options: projOptions,
        onChange: function (v) {
          var next = v || null;
          if (next === (detail.project || null)) {
            return;
          }
          headSave(function () { return patch(id, { project: next }); });
        }
      }));
    }

    // Sprint — the effective sprint reads "current" when it is the current sprint,
    // else the sprint's name. Read-only when parented (effective_sprint_id);
    // otherwise a select populated from /sprints (PATCH {sprint_id}).
    function sprintLabel(sprintId) {
      if (sprintId && sprintId === currentSprintId) {
        return "current";
      }
      var match = sprints.filter(function (s) { return s.id === sprintId; })[0];
      return match ? match.name : sprintId;
    }
    if (detail.sprint_item_id !== null && detail.sprint_item_id !== undefined) {
      var ro = el("span", "pill");
      ro.appendChild(el("span", "pill-key", "sprint"));
      ro.appendChild(document.createTextNode(
        detail.effective_sprint_id ? sprintLabel(detail.effective_sprint_id) : "(none)"
      ));
      meta.appendChild(ro);
    } else {
      var sprintOptions = [{ value: "", label: "(no sprint)" }].concat(
        sprints.map(function (s) { return { value: s.id, label: sprintLabel(s.id) }; })
      );
      meta.appendChild(C.enumPill({
        key: "sprint",
        value: detail.sprint_id || "",
        options: sprintOptions,
        onChange: function (v) {
          var next = v || null;
          if (next === (detail.sprint_id || null)) {
            return;
          }
          headSave(function () { return patch(id, { sprint_id: next }); });
        }
      }));
    }

    // Markers — in the header, visible (never tucked). Run status is the code-owned
    // "lock": agent_working while a mind runs, errored when a run left nothing to approve.
    if (detail.status === "agent_working") {
      meta.appendChild(marker("agent-working"));
    }
    if (detail.status === "errored") {
      meta.appendChild(marker("errored"));
    }
    if (detail.blocked) {
      meta.appendChild(marker("blocked"));
    }

    // Copy — a small header affordance (not a disclosure row).
    meta.appendChild(headerCopy(id, headErr));

    head.appendChild(meta);

    // Scope (the employee's authority ceiling): how far it may advance without approval, and
    // what it does at the cap. Human-editable anytime (POST /scope); the WS flush re-renders — no
    // optimistic UI. Rendered with the SAME enumPill as the meta row so it reads as one of
    // the header's pills ("approved until X" · "then stop/propose"). Ceiling options are
    // the current state and beyond, never earlier (C.ceilingOptions). Hidden on done.
    if (C.STATE_ORDER.indexOf(detail.state) >= 0 && detail.state !== "done") {
      var scope = el("div", "ticket-scope");
      var scopeBody = { ceiling: detail.ceiling, at_cap: detail.at_cap };
      var scopePush = function () {
        headSave(function () { return saveScope(id, scopeBody); });
      };
      var ceilingPill = C.enumPill({
        key: "approved until",
        value: detail.ceiling,
        options: C.ceilingOptions(detail.state),
        onChange: function (v) { scopeBody.ceiling = v; scopePush(); }
      });
      ceilingPill.setAttribute("data-scope-ceiling", "");
      scope.appendChild(ceilingPill);
      var atcapPill = C.enumPill({
        key: "then",
        value: detail.at_cap,
        options: [{ value: "stop", label: "stop" }, { value: "propose", label: "propose" }],
        onChange: function (v) { scopeBody.at_cap = v; scopePush(); }
      });
      atcapPill.setAttribute("data-scope-atcap", "");
      scope.appendChild(atcapPill);
      head.appendChild(scope);
    }

    head.appendChild(headErr);
    return head;
  }

  // --- recap -----------------------------------------------------------------

  function recapNode(id, detail) {
    var wrap = el("div", "ticket-recap");
    wrap.setAttribute("data-recap", "");
    wrap.appendChild(el("div", "ticket-block-label", "Recap"));
    // Offered only past needs_success (the server rejects recap in
    // needs_success/dropped); shown read-only otherwise.
    var editable = C.STATE_ORDER.indexOf(detail.state) >
      C.STATE_ORDER.indexOf("needs_success");
    if (editable) {
      var body = el("div", "ticket-recap-body");
      C.inlineEdit(body, {
        getValue: function () { return detail.recap; },
        onSave: function (raw) {
          return api.fetchJson("/api/tickets/" + id + "/recap", {
            method: "PUT",
            body: { body: raw }
          });
        },
        markdown: true,
        multiline: true,
        placeholder: "Recap the state of play…"
      });
      wrap.appendChild(body);
    } else {
      wrap.appendChild(C.markdownBlock(detail.recap));
    }
    return wrap;
  }

  // --- the approval (single instance) ----------------------------------------

  function approvalNode(id, detail) {
    var gating = C.gatingField(detail.state);
    if (gating) {
      var slot = detail.fields[gating];
      if (slot && slot.proposal) {
        var block = C.approvalBlock({
          mode: "gating-pending",
          field: gating,
          whatLabel: pretty(gating),
          proposalBody: slot.proposal.body,
          note: slot.notes,
          newState: C.advanceTarget(detail.state, detail.ceiling),
          onApprove: function (payload) {
            return api.fetchJson("/api/tickets/" + id + "/accept/" + gating, {
              method: "POST",
              body: payload
            });
          },
          onNoteSave: function (note) {
            return api.fetchJson("/api/tickets/" + id + "/notes/" + gating, {
              method: "PUT",
              body: { note: note }
            });
          }
        });
        // The approvalBlock omits proposal provenance; item 31/23 need a VISIBLE
        // "proposed by <who>" .proposal-meta inside the approval region (the body
        // is already a visible .markdown-block via the draft preview).
        var meta = el("div", "proposal-meta",
          "proposed by " + slot.proposal.proposed_by);
        var draftNode = block.querySelector(".approval-draft");
        if (draftNode) {
          block.insertBefore(meta, draftNode);
        } else {
          block.appendChild(meta);
        }
        return block;
      }
    }
    if (detail.state === "needs_review") {
      var rslot = detail.fields.result;
      return C.approvalBlock({
        mode: "needs_review",
        field: "result",
        whatLabel: "Result",
        proposalBody: rslot.value,
        note: rslot.notes,
        onApprove: function () {
          return api.fetchJson("/api/tickets/" + id + "/approve", { method: "POST" });
        },
        // The settled result value edit lives in the approval block (Decision B:
        // result is passed in needs_review); the result section is the read-only mirror.
        onValueSave: function (raw) {
          return api.fetchJson("/api/tickets/" + id + "/value/result", {
            method: "PUT",
            body: { body: raw }
          });
        },
        onNoteSave: function (note) {
          return api.fetchJson("/api/tickets/" + id + "/notes/result", {
            method: "PUT",
            body: { note: note }
          });
        }
      });
    }
    return null;
  }

  // --- collapsible field sections --------------------------------------------

  function fieldSection(id, detail, name) {
    var slot = detail.fields[name];
    var state = detail.state;
    var isDropped = state === "dropped";
    var isGating = C.gatingField(state) === name;
    var passed = fieldIsPassed(name, state);
    var hasProposal = !!(slot && slot.proposal);
    var hasValue = !!(slot && slot.value !== null && slot.value !== undefined &&
      String(slot.value) !== "");
    var body = [];
    var mark;

    if (isDropped) {
      // Terminal: proposal/accept/value-edit render read-only; notes stay editable.
      body.push(valueReadonly(slot));
      if (hasProposal) {
        var dp = el("div", "ticket-field-proposal");
        dp.appendChild(el("div", "proposal-meta",
          "proposed by " + slot.proposal.proposed_by));
        dp.appendChild(C.markdownBlock(slot.proposal.body));
        body.push(dp);
      }
      body.push(noteEditor(id, name, slot));
      mark = hasValue ? "✓" : "○";
    } else if (isGating) {
      if (hasProposal) {
        // Read-only mirror of the single editable draft in the approval above; the
        // approval owns this field's note, so no note editor here.
        var mirror = el("div", "ticket-field-mirror");
        mirror.appendChild(C.markdownBlock(slot.proposal.body));
        body.push(mirror);
      } else {
        // No pending proposal -> no approval block, so this section owns the note.
        body.push(valueReadonly(slot));
        body.push(noteEditor(id, name, slot));
      }
      mark = "●";
    } else if (state === "needs_review" && name === "result") {
      // Approval owns the result value + review-notes; this section is the
      // read-only mirror. A live proposal (§4.4.4) still renders resolvable.
      body.push(valueReadonly(slot));
      if (hasProposal) {
        body.push(resolvableProposal(id, detail, name, slot));
      }
      mark = "✓";
    } else {
      // Non-gating, non-dropped, ordinary field.
      if (hasProposal) {
        body.push(resolvableProposal(id, detail, name, slot));
        if (hasValue) {
          body.push(valueReadonly(slot));   // value read-only while a proposal is live
        }
      } else if (passed && hasValue) {
        body.push(valueEditable(id, name, slot));   // PUT /value/{field}
      } else {
        body.push(valueReadonly(slot));   // future/unset, or future-held value: read-only
      }
      body.push(noteEditor(id, name, slot));
      mark = (passed || hasValue) ? "✓" : "○";
    }

    var section = C.collapsibleField({ mark: mark, name: name, body: body });
    section.setAttribute("data-field", name);
    return section;
  }

  // --- chat side rail --------------------------------------------------------

  function chatRail(id, statusRes) {
    var aside = el("aside", "chat-rail");
    aside.setAttribute("data-chat", "");
    aside.appendChild(C.chatPanel(id, { available: statusRes.available }));
    return aside;
  }

  // --- assembly --------------------------------------------------------------

  function build(root, id, detail, sprintsRes, statusRes, currentRes) {
    var sprints = sprintsRes.sprints || [];
    var currentSprintId = currentRes && currentRes.sprint ? currentRes.sprint.id : null;

    var section = el("section", "ticket-screen");
    section.setAttribute("data-screen", "ticket");
    section.setAttribute("data-ticket-id", id);
    section.setAttribute("data-state", detail.state);

    var page = el("div", "ticket-page");
    var doc = el("main", "ticket-doc");

    doc.appendChild(headerNode(id, detail, sprints, currentSprintId));

    var col = el("div", "ticket-col");
    col.appendChild(recapNode(id, detail));

    var approval = approvalNode(id, detail);
    if (approval) {
      col.appendChild(approval);
    }

    var fields = el("div", "fields");
    FIELD_NAMES.forEach(function (name) {
      fields.appendChild(fieldSection(id, detail, name));
    });
    col.appendChild(fields);

    doc.appendChild(col);
    page.appendChild(doc);
    page.appendChild(chatRail(id, statusRes));
    section.appendChild(page);
    root.appendChild(section);
  }

  function render(root, params) {
    var id = params.id;
    Promise.all([
      api.fetchJson("/api/tickets/" + id),
      api.fetchJson("/api/sprints"),
      api.fetchJson("/api/chat/" + id + "/status"),
      api.fetchJson("/api/sprint/current")
    ]).then(
      function (results) {
        build(root, id, results[0], results[1], results[2], results[3]);
      },
      function (err) {
        root.appendChild(C.errorLine(err));
      }
    );
  }

  Planner.registerScreen("ticket", render);
})();
