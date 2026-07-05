# T17 — Sprint (#/sprint) and Backlog & Ideas (#/backlog) — implementation plan

Stage 5, screens 5 and 6 (SPEC §10) on the T14 foundation. The implementer builds to this
plan verbatim; every shape decision is already made here. No contracts change, no server change.

---

## 0. Constraints that bind every file

- **Idiom (match T14 exactly).** Classic-script IIFE: `(function () { "use strict"; var Planner =
  (window.Planner = window.Planner || {}); ... })();`. Build DOM with
  `document.createElement` + `textContent` only. **No `innerHTML`** anywhere (markdown is the sole
  innerHTML in the codebase; reach it only via `Planner.components.markdownBlock` /
  `Planner.markdown.render`). No framework, no bundler, **no optimistic UI** — a successful write is
  reflected only when the WS-invalidation refetch re-renders the screen (SPEC §9; `assets/api.js`
  bus → `assets/app.js` `onFlush → route()`).
- **Reuse the T14 primitives, do not re-implement them.** `Planner.components`: `panel`,
  `markdownBlock`, `fieldEditor`, `chip`, `entityRow`, `errorLine`. `Planner.api.fetchJson(path,
  {method, body})` (rejects with `err.code` + `err.message`; PlannerError envelopes pass through).
  `Planner.config.ROUTES` (read-only: `.ticketPrefix` = `"#/ticket/"`, `.sprint` = `"#/sprint"`,
  `.backlog` = `"#/backlog"`).
- **API paths are hardcoded string literals inside the two screen files.** `assets/config.js` is
  **out of scope** and `config.ROUTES` holds no data-API paths, so each screen defines its own local
  path constants (listed below). Only hash-route hrefs come from `config.ROUTES`.
- **Registration.** Each screen file ends with exactly one `Planner.registerScreen("sprint",
  renderSprint)` / `Planner.registerScreen("backlog", renderBacklog)` (overwrite-wins over app.js's
  placeholder). `render(root, params)` receives the `.screen` div; set `data-screen` on it
  synchronously at the top, before the async fetch, so the root selector exists immediately.
- **Styling: tokens-only.** Every color/space/radius/duration/border/size/font in `app.css` resolves
  through a `var(--…)` from `tokens.css`. Permitted literals: `0`, unitless ratios, `%`/viewport
  units, keywords, font-weights. No hex, px, ms literals.
- **`node --check` must pass** on both new JS files (SPEC §18.2, D9).

---

## 1. `assets/components.js` — ADDITIVE change (D11 item 13: Create form)

Append **only** `createForm` and its `FORM_SPECS` table immediately before the
`Planner.components = { … }` export object, and add `createForm: createForm` to that object.
**Touch nothing existing** (no edits to `appShell`, `panel`, `fieldEditor`, `chip`, `entityRow`,
`errorLine`, `make`, `appendChildren`, `CHIP_MARKERS`).

### 1.1 `FORM_SPECS` (module-private, extensible — adding a `ticket` variant is one more entry)

```
var PROJECTS   = ["Vylo", "Tribe", "Learning", "Other"];   // §3.2 Project enum
var PRIORITIES = ["P0", "P1", "P2", "P3"];                  // §3.2 priorities
var FORM_SPECS = {
  item: {
    submitLabel: "Add item",
    fields: [
      { name: "title",    kind: "text",   label: "Title",    required: true, placeholder: "Item title" },
      { name: "project",  kind: "select", label: "Project",  options: PROJECTS,   default: "Vylo" }, // required, NO blank option
      { name: "priority", kind: "select", label: "Priority", options: PRIORITIES, default: "P3" },
      { name: "deadline", kind: "date",   label: "Deadline" }                                          // optional
    ]
  },
  idea: {
    submitLabel: "Add idea",
    fields: [
      { name: "title",   kind: "text",     label: "Title",   required: true, placeholder: "Idea title" },
      { name: "project", kind: "select",   label: "Project", options: PROJECTS, includeBlank: true, blankLabel: "— no project —", default: "" },
      { name: "body",    kind: "textarea", label: "Body" }
    ]
  }
};
```

**Project-select decision (per ticket "no blank default that can submit empty — decide and specify
exactly"):** the `item` variant's project select has **exactly four `<option>`s (Vylo/Tribe/
Learning/Other), no placeholder/blank option, first (`Vylo`) selected by default** — so a submit can
never carry an empty project. The `idea` variant's project select **does** carry a leading blank
option (`value=""`, label `— no project —`, selected by default) meaning "no project".

### 1.2 `createForm(variant, onSubmit)` → returns a `<form>` DOM node

- `form` = `<form class="create-form" data-create="<variant>">`.
- For each field descriptor build `<div class="create-form-field">` containing
  `<label class="create-form-label">{label}</label>` then the control:
  - `text` / `date` → `<input class="form-control" type="text|date" data-input="{name}">`
    (set `placeholder` when present).
  - `select` → `<select class="form-control" data-input="{name}">`; if `includeBlank`, first append
    `<option value="">{blankLabel}</option>`; then one `<option value="{o}">{o}</option>` per option;
    set `select.value = default` (falls back to `""` when `includeBlank`, else `options[0]`).
  - `textarea` → `<textarea class="form-control" rows="4" data-input="{name}">`.
  - If `required`, set the control's `.required = true`.
- Actions row: reuse `<div class="field-editor-actions">` holding
  `<button class="button button--primary" type="submit">{submitLabel}</button>`.
- `form.addEventListener("submit", handler)` where `handler(e)`:
  1. `e.preventDefault()`.
  2. Remove any existing `.error-line` inside the form.
  3. `if (!form.checkValidity()) { form.reportValidity(); return; }` (native required-field guard;
     no network call).
  4. Collect `values = { <name>: control.value, … }` for every field.
  5. `submit.disabled = true`.
  6. `Promise.resolve(onSubmit(values)).then(onOk, onErr).then(reEnable)`:
     - **onOk** — reset each control back to its default (`""` for text/date/textarea; the spec
       `default` for selects). (The whole screen also re-renders on the WS refetch; this clear is the
       belt-and-braces per ticket.)
     - **onErr(err)** — `actions.prepend(Planner.components.errorLine(err))` (same placement idiom as
       `fieldEditor`).
     - **reEnable** — `submit.disabled = false`.
- The component owns **form mechanics only** (disable/clear/error). `onSubmit(values)` — supplied by
  the screen — builds the request body and **returns the `fetchJson` promise**.

---

## 2. `assets/screens-sprint.js` (new) — Sprint screen, `#/sprint`

### 2.1 Local constants
```
var C = Planner.config;
var CURRENT = "/api/sprint/current";
function sprintPath(id)     { return "/api/sprints/" + id; }
function freezePath(id, k)  { return "/api/sprints/" + id + "/freeze-" + k; }   // k = "kickoff"|"review"
function addendaPath(id)    { return "/api/sprints/" + id + "/addenda"; }
var KICKOFF = [                          // §3.1 kickoff fields, in this fixed order
  ["limiting_factor",     "Limiting factor"],
  ["primary_bet",         "Primary bet"],
  ["supports",            "Supports"],
  ["premortem",           "Premortem"]
];
var REVIEW = [                           // §3.1 review fields, in this fixed order
  ["outcomes",            "Outcomes"],
  ["solo_reflection",     "Solo reflection"],
  ["joint_discussion",    "Joint discussion"],
  ["updates_to_thinking", "Updates to thinking"],
  ["carry_forward",       "Carry forward"]
];
var STATUS_ORDER = ["todo", "active", "done", "blocked", "deferred_next_sprint"];   // §3.2, fixed
var STATE_ORDER  = ["needs_success", "needs_approach", "needs_plan",
                    "in_progress", "needs_review", "done", "dropped"];              // §4.1 for rollup order
```
Field labels above are pinned so the implementer makes no wording choice; `data-field` uses the raw
key, the label uses the pretty string.

### 2.2 `renderSprint(root, params)`
1. `root.setAttribute("data-screen", "sprint")` (synchronous).
2. `Planner.api.fetchJson(CURRENT).then(onData, onErr)`.
   - **onErr(err)** → `root.appendChild(Planner.components.errorLine(err))`.
   - **onData(res)**:
     - `if (res.sprint === null)` → `root.appendChild(quiet("No current sprint."))`; return. (`quiet`
       = local `make("div","quiet-line",text)`.)
     - else `var s = res.sprint; var sid = s.id;` then append, in order:
       - `sprintHeader(s)`
       - `kickoffPanel(s, sid)`
       - `itemsPanel(res.groups)`
       - `loosePanel(res.loose_tickets)`
       - `reviewPanel(s, sid)`
       - `addendaPanel(s, sid)`

### 2.3 DOM sketches + helpers (element · class · data-*)

**`sprintHeader(s)`** →
```
<div class="sprint-header">
  <h1 class="screen-title">{s.name}</h1>
  <div class="sprint-dates quiet-line">{s.date_start} – {s.date_end}</div>
</div>
```

**`freezePanelFields(pairs, s, sid, kind)`** — shared builder for kickoff and review.
`kind` ∈ `"kickoff"|"review"`; `frozenAt = kind==="kickoff" ? s.kickoff_frozen_at :
s.review_frozen_at`; `frozen = frozenAt !== null`.
```
body children (in order):
  if (frozen)  → Planner.components.chip("frozen")        // → <span class="chip chip--frozen">frozen</span>
  for each [key,label] in pairs → fieldWrap(key,label, s[key], frozen, sid)
  if (!frozen) → freezeButton(kind, sid)
panel = Planner.components.panel(kind==="kickoff" ? "Kickoff" : "Review", body)
panel.setAttribute("data-" + kind, "")                    // data-kickoff | data-review
if (frozen) panel.setAttribute("data-frozen", "1")
return panel
```
`kickoffPanel(s,sid)` = `freezePanelFields(KICKOFF, s, sid, "kickoff")`;
`reviewPanel(s,sid)`  = `freezePanelFields(REVIEW,  s, sid, "review")`.

**`fieldWrap(key, label, value, frozen, sid)`** →
```
<div class="sprint-field" data-field="{key}">
  <div class="sprint-field-label">{label}</div>
  {Planner.components.markdownBlock(value)}          // .markdown.markdown-block, or "(none)" quiet-line if empty
  {frozen ? nothing : Planner.components.fieldEditor(value, onSave)}
</div>
```
`onSave(newValue)` = `return Planner.api.fetchJson(sprintPath(sid), {method:"PATCH",
body: makeObj(key, newValue)})` where `makeObj(k,v)` returns `{}` then sets `[k]=v` (no computed
literals needed — `var b={}; b[key]=newValue; return b`). The existing `fieldEditor` handles
disable + reject→`errorLine` (this is the s4 frozen-write path).

**`freezeButton(kind, sid)`** →
```
<button class="button" type="button" data-freeze="{kind}">Freeze {kind}</button>
```
click → clear any sibling `.error-line`, `btn.disabled=true`,
`Planner.api.fetchJson(freezePath(sid,kind), {method:"POST"}).then(noop, err→ insert
errorLine after the button).then(btn.disabled=false)`. Success reflects via WS refetch (panel
re-renders frozen, editors gone, this button gone).

**`itemsPanel(groups)`** → `panel("Items", STATUS_ORDER.map(statusGroup(status, groups[status])))`.
**`statusGroup(status, items)`** (always emitted, even when `items` is empty — stable selector) →
```
<div class="status-group" data-status-group="{status}">
  <div class="status-group-title">{prettyStatus(status)} · {items.length}</div>
  {items.length ? <div class="list-stack">{ items.map(itemRow) }</div>
                : <div class="quiet-line">(none)</div>}
</div>
```
`prettyStatus(s)` = `s.replace(/_/g," ")` with first letter upper (e.g. `deferred_next_sprint` →
`"Deferred next sprint"`).

**`itemRow(item)`** →
```
<div class="sprint-item" data-item-id="{item.id}">
  {Planner.components.entityRow({ href: C.ROUTES.sprint, title: item.title, chips: itemChips(item) })}
  <span class="rollup" data-rollup>{rollupText(item.rollup)}</span>
</div>
```
`href = C.ROUTES.sprint` ("#/sprint") → same-hash click fires no hashchange → no-op (item rows have
no detail screen in v1).
`itemChips(item)` in fixed order:
  `chip("priority", item.priority)`, `chip("project", item.project)`,
  `item.deadline ? chip("deadline", item.deadline) : —`,
  `item.blockers_cleared ? chip("blockers-cleared") : —`,
  `item.status_proposal ? chip("pending-proposal") : —`.
`rollupText(rollup)`:
```
total = sum of rollup values
parts = STATE_ORDER.filter(st => rollup[st] > 0).map(st => st.replace(/_/g," ") + " " + rollup[st])
text  = (total === 1 ? "1 ticket" : total + " tickets")
text += parts.length ? " · " + parts.join(" · ") : ""
```
e.g. demo active item → `"2 tickets · needs plan 1 · in progress 1"`.

**`loosePanel(tickets)`** → `panel("Loose tickets", looseSection(tickets))`.
**`looseSection(tickets)`** →
```
<section class="list-stack" data-loose>
  { tickets.length ? tickets.map(looseRow) : <div class="quiet-line">(none)</div> }
</section>
```
**`looseRow(t)`** = `row = entityRow({ href: C.ROUTES.ticketPrefix + t.id, title: t.title,
chips: [ chip("state", t.state), chip("priority", t.priority) ] }); row.setAttribute(
"data-ticket-id", t.id); return row;` (href = `"#/ticket/" + id`).

**`addendaPanel(s, sid)`** → `panel("Weekly addenda", [ addendaList(s.weekly_addenda),
addendaForm(sid) ])`. Rendered **regardless of freeze state** (append is allowed post-freeze, §3.1).
```
addendaList(list):
  <div class="list-stack" data-addenda>
    { list.length ? list.map(a =>
        <div class="addendum" data-addendum>
          <span class="addendum-date">{a.date}</span>
          {markdownBlock(a.text)}
        </div>)
      : <div class="quiet-line">(none)</div> }
  </div>
```
`addendaForm(sid)` — bespoke `<form>` (NOT the createForm component):
```
<form class="create-form" data-addenda-form>
  <div class="create-form-field"><label class="create-form-label">Date</label>
       <input class="form-control" type="date" data-input="date"></div>
  <div class="create-form-field"><label class="create-form-label">Note</label>
       <textarea class="form-control" rows="3" data-input="text"></textarea></div>
  <div class="field-editor-actions"><button class="button button--primary" type="submit">Add addendum</button></div>
</form>
```
submit handler mirrors createForm mechanics: `preventDefault`; remove prior `.error-line`;
`submit.disabled=true`; `Planner.api.fetchJson(addendaPath(sid), {method:"POST", body:{date:
dateInput.value, text: textArea.value}}).then(clear inputs, err→ prepend errorLine to actions).then(
re-enable)`. Server rejects empty date/text with `validation` (surfaced via the error line); success
→ WS refetch renders the new `[data-addendum]` row.

---

## 3. `assets/screens-backlog.js` (new) — Backlog & Ideas, `#/backlog`

### 3.1 Local constants
```
var C = Planner.config;
var ITEMS_BACKLOG = "/api/items?sprint_id=null";   // server-sorted: priority rank, created_at, id
var IDEAS         = "/api/ideas";                   // server-sorted: created_at DESC
var POST_ITEM     = "/api/items";
var POST_IDEA     = "/api/ideas";
```

### 3.2 `renderBacklog(root, params)`
1. `root.setAttribute("data-screen", "backlog")`.
2. Build the two sections **synchronously** (empty for now) so create forms are present immediately:
   - `itemsSection` = `<section class="list-stack" data-backlog-items>` (empty).
   - `ideasSection`  = `<section class="list-stack" data-ideas>` (empty).
   - `root.appendChild(panel("Backlog", [ createForm("item", onSubmitItem), itemsSection ]))`.
   - `root.appendChild(panel("Ideas",   [ createForm("idea", onSubmitIdea), ideasSection ]))`.
   (Create form first, list below.)
3. Populate each list async and independently:
   - `fetchJson(ITEMS_BACKLOG).then(res => fill(itemsSection, res.items, itemRow, "No deferred items."),
      err => itemsSection.appendChild(errorLine(err)))`.
   - `fetchJson(IDEAS).then(res => fill(ideasSection, res.ideas, ideaRow, "No ideas."),
      err => ideasSection.appendChild(errorLine(err)))`.
   `fill(section, rows, rowFn, emptyText)`: if `rows.length` append `rows.map(rowFn)` else append
   `quiet(emptyText)`.

### 3.3 Rows and submit handlers
**`itemRow(item)`** = `row = entityRow({ href: C.ROUTES.backlog, title: item.title, chips:
itemChips(item) }); row.setAttribute("data-item-id", item.id); return row;`
`itemChips`: `chip("priority", item.priority)`, `chip("project", item.project)`,
`item.deadline ? chip("deadline", item.deadline) : —`. Server order preserved (no client re-sort).

**`ideaRow(idea)`** = `row = entityRow({ href: C.ROUTES.backlog, title: idea.title, chips:
idea.project ? [ chip("project", idea.project) ] : [] }); row.setAttribute("data-idea-id", idea.id);
return row;` (Ideas have no priority/status — §3.5.)

Self-hrefs (`C.ROUTES.backlog`) make row clicks no-ops (no detail screen).

**`onSubmitItem(values)`** →
```
var body = { title: values.title, project: values.project, priority: values.priority };
if (values.deadline) body.deadline = values.deadline;   // omit empty
return Planner.api.fetchJson(POST_ITEM, { method: "POST", body: body });
// NO sprint_id → lands NULL → backlog/deferred (§3.2)
```
**`onSubmitIdea(values)`** →
```
var body = { title: values.title };
if (values.body)    body.body = values.body;
if (values.project) body.project = values.project;      // "" blank → omitted → NULL project
return Planner.api.fetchJson(POST_IDEA, { method: "POST", body: body });
```
On success the component clears inputs and the WS refetch re-renders both lists; a rejection (e.g.
idea with empty title → `validation`) renders an `.error-line` inside the form.

---

## 4. Exact API calls (per render / per action)

| Trigger | Method + path | Body |
|---|---|---|
| Sprint render | `GET /api/sprint/current` | — |
| Kickoff/review field Save | `PATCH /api/sprints/{id}` | `{ <field_key>: <textarea value> }` |
| Freeze kickoff button | `POST /api/sprints/{id}/freeze-kickoff` | — |
| Freeze review button | `POST /api/sprints/{id}/freeze-review` | — |
| Addenda form submit | `POST /api/sprints/{id}/addenda` | `{ date, text }` |
| Backlog render | `GET /api/items?sprint_id=null` and `GET /api/ideas` | — |
| Item create submit | `POST /api/items` | `{ title, project, priority[, deadline] }` (no `sprint_id`) |
| Idea create submit | `POST /api/ideas` | `{ title[, body][, project] }` |

Loose-ticket / item / idea row clicks issue **no** request (hash-only hrefs). Browser (human)
requests carry no `X-Plan-*` headers, so `reject_agents` (freeze routes) passes; PATCH on a frozen
field returns the structured `frozen_write` envelope which `fetchJson` rejects with
`err.code === "frozen_write"`.

---

## 5. Pinned `data-*` contract (e2e item 32 selects on these — nothing unlisted)

| attribute | on element | notes |
|---|---|---|
| `data-screen` = `sprint`\|`backlog` | screen root | set synchronously per render |
| `data-kickoff` | kickoff panel `<section>` | `+ data-frozen="1"` when `kickoff_frozen_at` set |
| `data-review` | review panel `<section>` | `+ data-frozen="1"` when `review_frozen_at` set |
| `data-field` = `<field_key>` | each kickoff/review field wrapper | raw key (`limiting_factor`, …) |
| `data-freeze` = `kickoff`\|`review` | freeze buttons | present **pre-freeze only** |
| `data-addenda` | addenda list container | |
| `data-addendum` | each addendum row | |
| `data-addenda-form` | the append form | present pre- and post-freeze |
| `data-status-group` = `<status>` | each of the **5** group containers | all five always rendered, even empty |
| `data-item-id` = `<id>` | sprint item wrapper `<div>` **and** backlog item row `<a>` | every item row |
| `data-rollup` | rollup summary `<span>` | sprint items only |
| `data-loose` | loose-tickets `<section>` | |
| `data-ticket-id` = `<id>` | loose ticket row `<a>` | href = `#/ticket/<id>` |
| `data-backlog-items` | backlog items `<section>` | |
| `data-ideas` | ideas `<section>` | |
| `data-idea-id` = `<id>` | idea row `<a>` | |
| `data-create` = `item`\|`idea` | create `<form>` | set by the component from the variant |
| **`data-input` = `<field_name>`** | each create-form control + the addenda form's date/text controls | **one declared addition** — lets the smoke/e2e target individual controls unambiguously (`title`/`project`/`priority`/`deadline`/`body`/`date`/`text`) |

---

## 6. Frozen / unfrozen branch behavior mapped to spec

- **§5 / §3.1 (kickoff):** pre-freeze — each of the four kickoff fields shows its value as a markdown
  block **with a `fieldEditor` beneath it**, and a `Freeze kickoff` button (`data-freeze="kickoff"`)
  is present. On `POST /freeze-kickoff` the server sets `kickoff_frozen_at` and emits `kickoff_frozen`
  → WS refetch. Post-freeze — panel carries `data-frozen="1"`, a `frozen` chip is shown, **no field
  editors**, **no freeze button**. A direct `PATCH` on a kickoff field after freeze is rejected with
  `frozen_write` (`sprints/data.py` `update_sprint_field` → `field_write_admissible`), surfaced by the
  field editor's `errorLine`.
- **§3.1 (review):** identical pattern keyed on `review_frozen_at` with the five review fields and
  `data-freeze="review"`.
- **§3.1 (addenda):** `weekly_addenda` is append-only and **allowed after freeze** — the addenda list
  and `data-addenda-form` render in both states; the form never disappears.
- **§3.2 (items):** grouped by `status` in the fixed order `todo, active, done, blocked,
  deferred_next_sprint`; each item carries its ticket rollup (`item.rollup`, counts keyed by every
  ticket state) and the `blockers_cleared` flag (chip when true) and a pending-proposal chip when
  `status_proposal` is non-null.
- **§5 (loose tickets):** `res.loose_tickets` (server: `sprint_id = this sprint AND sprint_item_id IS
  NULL`, no state filter — includes `done`/`dropped`) rendered as ticket links.
- **§3.2 (backlog):** `GET /api/items?sprint_id=null` → items with `sprint_id IS NULL`, server-sorted
  by priority rank then `created_at` then `id`; item creates omit `sprint_id` so they land NULL.
- **§3.5 (ideas):** `title`, `body`, nullable `project` only; `GET /api/ideas` newest-first.

---

## 7. `assets/app.css` — ADDITIVE styles (append at end; tokens-only)

**Reused existing classes (no new CSS):** `.panel` `.panel-title` `.panel-body`, `.markdown`
`.markdown-block`, `.field-editor` `.field-editor-input` `.field-editor-actions`, `.button`
`.button--primary`, all `.chip*` variants, `.entity-row` `.entity-row-title` `.entity-row-chips`,
`.error-line` `.error-code` `.error-message`, `.quiet-line`.

**New classes to add** (each color/space/radius/size via `var(--…)`):

| class | role / token intent |
|---|---|
| `.screen-title` | sprint name; `--type-xl`, `--text-strong`, bottom margin `--space-2` |
| `.sprint-header` | wraps title + dates; margin-bottom `--space-4` |
| `.sprint-dates` | date range line (also carries `.quiet-line`); `--type-sm` |
| `.sprint-field` | field wrapper; margin-bottom `--space-4` |
| `.sprint-field-label` | field label; `--type-xs`, `--text-muted`, weight 600, margin-bottom `--space-1` |
| `.status-group` | group container; margin-bottom `--space-4` |
| `.status-group-title` | group heading; `--type-sm`, `--text-muted`, weight 600, margin-bottom `--space-2` |
| `.list-stack` | vertical row list (groups, loose, backlog items, ideas, addenda); `display:flex; flex-direction:column; gap:var(--space-1)` |
| `.sprint-item` | item wrapper (row + rollup); `display:flex; flex-direction:column; gap:var(--space-1)`; padding-bottom `--space-1` |
| `.rollup` | rollup summary; `--type-xs`, `--text-faint`, left padding `--space-3` to align under the row title |
| `.addendum` | addendum row; `display:flex`, `gap:var(--space-3)`, `align-items:baseline` |
| `.addendum-date` | date; `--font-mono`, `--type-xs`, `--text-muted`, no-wrap |
| `.create-form` | form; `display:flex; flex-direction:column; gap:var(--space-3)` |
| `.create-form-field` | label+control pair; `display:flex; flex-direction:column; gap:var(--space-1)` |
| `.create-form-label` | control label; `--type-xs`, `--text-muted` |
| `.form-control` | inputs/selects/textareas in create + addenda forms; mirrors `.field-editor-input` bg/border/radius/padding but `--font-ui` (titles read as prose, not code), `--type-sm`, `width:100%`; `:focus` border `--accent-bright`, `outline:none` |

---

## 8. `orchestration/tickets/T17-sprint-backlog/smoke.py` (new)

Adapt the T14 smoke (`orchestration/tickets/T14-ui-foundation/smoke.py`) verbatim for the boot
scaffold: `os.chdir(REPO)`, temp dir, `free_or`, in-process test-mode uvicorn on a daemon thread,
fake clock, `wait_ready`, `httpx.Client`, Playwright chromium, structured `ok NN`, `SMOKE PASS`,
`try/finally` teardown, exit `0/1`. Differences from T14:

- **Seed the demo DB** after `create_schema`: open a `connect(...)` and call
  `planner.seed.demo.seed_demo(conn)` (one unfrozen "Demo Sprint", 3 items, 6 loose tickets, 0
  deferred items, 0 ideas). `seed_demo` requires an empty DB, so seed before the server starts.
- **`PLAN_WS_POLL_MS = "1000"`** (widen the pre-refetch window; `ui_debounce_ms` stays 250). This
  gives roughly a `[250ms, 1250ms]` gap between a write's event and its re-render — ample for the s4
  frozen-write assertion (error line appears ~tens of ms after the click, the earliest re-render is
  ≥250ms later and typically ~750ms+).
- **Screen scripts.** `server.py`'s `_SHELL` is **shared and out of scope** — it does not reference
  `screens-sprint.js` / `screens-backlog.js`. The smoke therefore:
  1. `page.goto(base + "/")` → app boots, routes to `#/day` (placeholder). `wait_for_function`
     `window.__plannerDebug.wsOpens >= 1`.
  2. `page.add_script_tag(url=base + "/assets/screens-sprint.js")` and
     `page.add_script_tag(url=base + "/assets/screens-backlog.js")` — served by StaticFiles; each
     classic IIFE re-registers its real screen over the placeholder (overwrite-wins). `add_script_tag`
     resolves after the script executes, so the registry is updated by return.
  3. **Navigate only by setting `location.hash` via `page.evaluate` — never `page.goto` again**
     (a reload would discard the injected scripts). Helper: `nav(page, "#/sprint")` =
     `page.evaluate("h => { location.hash = h; }", h)`. Because we start at `#/day`, each nav to a
     new hash fires a real `hashchange` → `route()` → the injected render. After each nav,
     `wait_for_selector` on the screen root.

### Assertions (each numbered `ok NN`; exact selectors)

- **(s1)** `nav("#/sprint")`; `wait_for_selector('[data-screen="sprint"] [data-kickoff]')`.
  `page.query_selector_all('[data-kickoff] [data-field]')` → **4** wrappers; the four
  `.sprint-field-label` texts equal `["Limiting factor","Primary bet","Supports","Premortem"]`; each
  wrapper contains a `.markdown-block` and a `.field-editor` (pre-freeze editors present).
- **(s2)** `[data-status-group="active"] [data-item-id]` count == 1 and its
  `.entity-row-title` == `"Ship the demo feature end to end."`; its
  `[data-status-group="active"] [data-item-id] [data-rollup]` text contains `"2 tickets"`,
  `"needs plan 1"`, and `"in progress 1"`. `[data-status-group="todo"]` contains
  `"Research the search index options."`; `[data-status-group="done"]` contains
  `"Retire the legacy export job."`; `[data-status-group="blocked"]` and
  `[data-status-group="deferred_next_sprint"]` containers exist (empty).
- **(s3)** `[data-loose] [data-ticket-id]` count == **6**; every such `<a>`'s `href` starts with
  `"#/ticket/"`; one row's title == `"Fix the flaky login test."`.
- **(s4)** frozen-write surfaced. `sid = httpx GET /api/sprint/current → json["sprint"]["id"]`.
  `page.fill('[data-field="limiting_factor"] .field-editor-input', "edited while about to freeze")`.
  `httpx POST /api/sprints/{sid}/freeze-kickoff` (no agent headers → human → passes). **Immediately**
  `page.click('[data-field="limiting_factor"] .field-editor-actions .button')`.
  `wait_for_selector('[data-field="limiting_factor"] .error-line', timeout≈2000)`; assert its
  `.error-code` text == `"frozen_write"`.
- **(s5)** after refetch: `wait_for_function` that `[data-kickoff] .field-editor` count == 0 (editors
  gone). Assert `[data-kickoff]` has `data-frozen == "1"`; `[data-kickoff] .chip--frozen` present;
  `[data-freeze="kickoff"]` count == 0 (button gone); `[data-addenda-form]` present. Then append via
  UI: `page.fill('[data-addenda-form] [data-input="date"]', "2026-07-05")`,
  `page.fill('[data-addenda-form] [data-input="text"]', "Mid-sprint addendum note.")`,
  `page.click('[data-addenda-form] .button')`; `wait_for_selector('[data-addenda] [data-addendum]')`;
  assert exactly one `[data-addendum]` row and its text contains `"Mid-sprint addendum note."`.
- **(b1)** `nav("#/backlog")`; `wait_for_selector('[data-screen="backlog"] [data-create="item"]')`.
  Create item 1 (P3 default): `fill('[data-create="item"] [data-input="title"]', "Item Low")`,
  `click('[data-create="item"] button[type="submit"]')`; wait for one `[data-backlog-items]
  [data-item-id]`. Create item 2 (P1): `fill(... title, "Item High")`,
  `select_option('[data-create="item"] [data-input="priority"]', "P1")`, submit; wait for two rows.
  `rows = query_selector_all('[data-backlog-items] [data-item-id]')` → first row title `"Item High"`
  and has `.chip--p1`; second row title `"Item Low"` and has `.chip--p3` (proves server priority
  ordering, independent of creation order).
- **(b2)** `fill('[data-create="idea"] [data-input="title"]', "Search relevance idea")`,
  `click('[data-create="idea"] button[type="submit"]')`; `wait_for_selector('[data-ideas]
  [data-idea-id]')`; assert a `[data-idea-id]` row with title `"Search relevance idea"`.

Print `SMOKE PASS (N checks)` and `return 0`; any failed assertion raises → traceback + non-zero
exit; `finally` closes client/page/browser/playwright, stops the server, removes the temp dir.

---

## 9. Explicitly OUT OF SCOPE (do not create or modify)

- `src/planner/core/server.py` and its `_SHELL` (shared; the smoke injects screen scripts via
  `add_script_tag`).
- `assets/app.js`, `assets/api.js`, `assets/config.js`, `assets/markdown.js`, `assets/tokens.css`.
- Every other screen file (`screens-day.js`, `screens-review.js`, `screens-board.js`,
  `screens-ticket.js` — T15/T16, not this ticket).
- Any **non-additive** change to `assets/components.js` (only the `createForm` append + one export
  line are permitted; the seven existing functions and helpers are untouched).
- All server-side Python except the new `smoke.py`; no contract/type files change.

---

## 10. BINDING AMENDMENTS (from plan review — these override §8 where they conflict)

**A1 — real clock, no fake now.** The smoke env sets `PLAN_TEST_MODE=1` but does **not** set
`PLAN_FAKE_NOW`. `build_clock` then returns the real clock, matching `seed_demo`'s wall-clock
anchoring (`datetime.now().astimezone().date()`); the demo sprint spans today−3 … today+10, so
`/api/sprint/current` always resolves it regardless of run time or the 05:00 boundary shift.
Remove every reference to `FAKE_NOW` from the §8 scaffold.

**A2 — deterministic s4 window.** Smoke env: `PLAN_UI_DEBOUNCE_MS = "1500"` and
`PLAN_WS_POLL_MS = "250"`. The 1500ms client debounce is a hard floor between any event append and
the earliest re-render, so the s4 Save click (issued immediately after the freeze POST returns,
~tens of ms) can never lose to the refetch; the re-render ceiling stays ≈1750ms so waits remain
fast. At boot the smoke asserts `GET /api/meta` returns `ui_debounce_ms == 1500` (guards the env
plumbing). All `wait_for_*` timeouts ≥ 8000ms.

**A3 — conditional script injection.** Before injecting, the smoke GETs `/` and checks the HTML for
`/assets/screens-sprint.js` and `/assets/screens-backlog.js`. If both are referenced (shell already
integrated), it skips `add_script_tag` and prints `shell mode: integrated`; otherwise it injects
both and prints `shell mode: injected (shell integration pending)`. Same assertions either way, so
this exact smoke re-validates the real shell after the integrator adds the two script tags to
`_SHELL`.

**A4 — createForm variants stay item + idea** (dispatch scope). D11 item 13's `ticket` variant has
no consuming screen in v1; `FORM_SPECS` is table-driven so it lands later as one entry. Do not add
it in this ticket.
