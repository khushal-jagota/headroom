# `./verify` is red on staging — five e2e tests, unconfirmed cause

Written 2026-07-27 from the command-menu ticket, which did not cause this and did
not fix it. Recorded so whoever picks it up starts where this left off.

## What fails

Five tests, always the same five:

- `tests/e2e/test_brand_colour.py::test_soft_steel_brand_tokens_reach_representative_states`
  — both the `chromium-desktop` and `chromium-mobile` parameters
- `tests/e2e/test_scrollbar_treatment.py::test_scroll_containers_share_stable_fine_pointer_treatment`
- `tests/e2e/test_scrollbar_treatment.py::test_scroll_containers_keep_visible_baseline_in_touch_context`
- `tests/e2e/test_scrollbar_treatment.py::test_scroll_containers_keep_native_baseline_in_forced_colors_fine_pointer`

## They are not caused by any branch

Established by running those files on the untouched main checkout on `staging`,
with nothing else running and no change applied. The same five failed there, in a
run of only thirteen tests, so it is not load either. `./verify` is red on
`staging` before anyone's work is added to it.

## What the failure looks like

Each one times out waiting for the page to reach **network-idle** — either
`page.set_content(..., wait_until="networkidle")` or a selector wait behind the
same condition. The pages load the real app first.

## The hypothesis, which is NOT confirmed

A page that never reaches network-idle is the signature of a request that never
finishes. Our live-update stream is exactly such a request: `GET /api/changes` is
a Server-Sent Events connection that is *designed* to stay open, and the browser
opens it as soon as the app mounts. If the browser counts that open connection as
outstanding network activity, the page can never go idle and the wait can only
ever time out.

That is reasoning from the symptom, not a finding. Nobody has confirmed it. It
was never traced, no fix was attempted, and the SSE stream has not been shown to
be the connection holding these pages open — a hung asset request or something
else entirely would produce the same symptom. Treat it as the first thing to
check, not as the answer.

If it does hold up, the question is whether these tests should wait on
network-idle at all, since a live app with a permanent stream may simply never
satisfy it.

## Two other failures that are not part of this

`tests/e2e/test_workers_frontend.py` and one scroll test in
`tests/e2e/test_dev_conversation_pane.py` fail only inside a full `./verify` run,
move between runs, and pass in isolation. One run also hit a server fixture that
could not bind a port. That is the e2e suite running out of room on this machine,
and is a different problem from the five above.
