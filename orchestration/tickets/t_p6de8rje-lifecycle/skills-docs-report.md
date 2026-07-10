# Skills/docs implementation report

Ticket: `t_p6de8rje` — skills/docs slice.

## RED

The focused acceptance test already existed in the working tree
(`tests/unit/test_minds.py::test_provisioned_skills_encode_implementation_and_closeout_lifecycle`,
uncommitted from a prior partial run) and matched what the dispatch asked for: it
reads the provisioned skill symlinks and pins the new stage names/fields while
rejecting `in_progress`/`needs_review`. No new assertions were needed, so this run
proved it RED before touching skills/docs.

```sh
source .venv/bin/activate
python -m pytest tests/unit/test_minds.py -k "provisioned_skills_encode" -q
```

Decisive RED line:

```
>           assert "in_progress" not in text
E           AssertionError: assert 'in_progress' not in '---\nname: ... evidence.\n'
E             'in_progress' is contained here:
E               teps.
E               - **in_progress** — the work happens here, producing the **result**.
E               - **needs_review** — the result stands for review.
```

## GREEN

```sh
source .venv/bin/activate
python -m pytest tests/unit/test_minds.py -k "provisioned_skills_encode or provision_planner_home_skills" -q
# ..                                                                       [100%]
python -m pytest tests/unit/test_minds.py -q
# ......................................................................   [100%]
git add -A skills/ docs/ tests/unit/test_minds.py && git diff --check --cached
# (no output — clean)
git reset   # unstaged again; no commit made
```

## Changed files

- `skills/panels-worker/SKILL.md` — replaced the `in_progress`/`needs_review`/`result`
  stage pair with `needs_implementation` and `needs_closeout`; added the visible
  `Success → Approach → Plan → Implementation → Closeout → Done` sequence; stated
  Implementation produces a reviewable package and Closeout performs only applicable
  merge/deploy/follow-up/bookkeeping then proposes a verified report; added an
  explicit "never approve your own proposal" line; updated the proposal-shape,
  field-note, artifact-linking, and per-stage-guidance bullets from `result` to
  `implementation`/`closeout` without changing what any of that guidance means.
- `skills/panels/SKILL.md` — added the six-stage sequence and the five canonical
  field names to the ticket bullet; changed the artifact paragraph from
  `Success, approach, plan, result, ...` to the five current field names.
- `skills/panels-chief-of-staff/SKILL.md` — the external-work settled-prefix step now
  names the six valid target states explicitly; the ticket-worker-boundary paragraph
  now lists `implementation`/`closeout` instead of `result` and says "filed worker
  proposal" instead of "filed worker result".
- `skills/planning-worker.md`, `skills/planning-executor.md`, `skills/planner-main.md`
  — **retired (deleted)**. See judgment call below.
- `docs/tickets-and-gates.md` — redrew the ASCII stage diagram for six stages;
  rewrote "The stages" prose for `implementation`/`closeout`; removed the old
  "special ending" paragraph (accepted result → needs_review unless ceiling was
  done) since the new model has no such asymmetry — every stage, including
  Closeout, advances the same way; generalized the Review-screen return-for-revision
  paragraph to "whatever field is currently gated" instead of hard-coding `result`/
  `needs review`.
- `docs/systems.md` — updated the `state` enum and the field count/list (four → five
  fields, `result` → `implementation`, `closeout`).
- `docs/frontend.md` — "the four blanks" → "the five blanks" on the Ticket screen line.
- `docs/systems.html` — this is a live doc (the "designed, collapsible reading
  artifact" version of `systems.md`, linked from `docs/README.md`), and it
  independently duplicated the stale `state` enum and field list, so it was updated
  to match.
- `tests/unit/test_minds.py` — no new assertions added; the existing
  `test_provisioned_skills_encode_implementation_and_closeout_lifecycle` test already
  covered the required RED/GREEN proof (see RED section above).

## Wording judgment

- **Retiring the three loose skill files instead of rewriting them.** I checked
  `provision_planner_home_skills` (`src/planner/minds/config.py`) and
  `PLANNER_SKILL_NAMES`: only `panels`, `panels-worker`, and `panels-chief-of-staff`
  are symlinked into the Hermes home and therefore ever loaded by a live agent.
  `planning-worker.md`, `planning-executor.md`, and `planner-main.md` are loose files
  outside any provisioned skill directory; I grepped the whole repo (`src/`, `docs/`,
  tests) and found no code path or doc that reads them by name — the `"planning-worker"`
  string used in some runtime tests is an arbitrary fixture role name, unrelated to
  this file. Since they are not "active role prompts" in any consumed sense and exist
  only to describe the retired four-field model, deleting them (rather than editing
  stale prose no one reads) is the "everything earns its existence" call — a dead
  file kept "technically accurate" earns nothing. `skills/planning-boundary.md` was
  left untouched: it is the day-planning-boundary judgment prompt, describes a
  `plan_tree` node status vocabulary unrelated to ticket lifecycle, and was not named
  in the dispatch.
- **`docs/systems.html` counted as "actually stale."** It isn't named in the
  dispatch, but `docs/README.md` documents it as a live doc ("the same map as a
  designed, collapsible reading artifact"), and it contained the identical stale
  `state` enum and `four fields: ... result` text as `systems.md`. Fixed it in place
  rather than leaving a doc that still described the retired model.
- **Removed the "special ending" paragraph in `tickets-and-gates.md` rather than
  updating its wording.** Under the old model, `result` was the only field mapped to
  a state (`in_progress`) that did not gate the next stage by name — accepting it
  jumped to the fieldless `needs_review` wait-state, which is why the doc called it
  out as a special case. Under D55 / `contract.md`, every non-terminal linear state
  gates its own same-named field, including `needs_closeout`, so Closeout is no
  longer asymmetric with the earlier stages. Keeping a "special ending" callout for a
  behavior that no longer exists would misdescribe the model, so I replaced it with a
  sentence stating the now-uniform behavior instead of patching the old exception's
  wording.
- **Generalized "Review screen returns a result while at needs_review" to "whatever
  field is currently gated."** Return-for-revision is a general control mechanic
  (per D55 and `contract.md`, unchanged in meaning) that applies to any pending gated
  field, not only the old terminal `result`/`needs_review` pair; the old wording
  conflated the general Review-screen approval queue with the specific retired state
  name.
- **Left `docs/days.md` ("the day's four fields"), `docs/sprints.md` ("in progress
  when any child ticket is active"), and `docs/chat.md` ("Hermes's native ... result")
  unchanged.** These are general-language or different-entity uses (the Day record's
  own four fields; sprint-item-derived status prose; Hermes's own status vocabulary)
  that cannot be mistaken for the ticket lifecycle contract, matching the dispatch's
  "preserve valid general-language uses" instruction.
- **`panels-chief-of-staff` gained an explicit list of the six valid external-work
  target states** in the settled-prefix step. The prior wording was already
  state-name-agnostic (so not technically wrong), but the dispatch asked to "align...
  strict external-work settled-prefix guidance," and the CLI now presumably only
  accepts the new state names — spelling them out gives the Chief operator concrete,
  checkable state names instead of a vague "the target state."
- **No implementer-assignment language was added anywhere.** Per `contract.md`
  ("Implementer assignment... is out of scope") and D54, none of the edits above
  introduce or imply who performs Implementation vs. Closeout — only what each stage
  produces and requires.
