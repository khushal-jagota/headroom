# Rest-band final-verify diagnosis

## Outcome

The composer run-controls extraction does not own this failure.

The test samples viewport coordinates while the route's entrance animation is still moving
the whole screen. `.screen-enter` runs for `var(--motion-slow)`, which is 240 ms, and begins
at `translateY(var(--space-2))`, which is 8 px. The test's first geometry read happens partway
through that transform. Releasing the held conversation read and waiting for the rest line
uses enough time for the transform to move nearer to, or reach, its final position. Therefore
`getBoundingClientRect().top` is different even though hydration did not change the rest
band's layout.

Instrumentation distinguished transformed viewport position from layout:

- the band, composer, pane, and conversation-layer heights stayed identical;
- the band stayed 41 px high;
- the band stayed above the composer;
- the rest-band DOM node stayed the same node;
- the band and composer moved upward together by 1–3 px;
- the whole `.screen-enter` element's viewport top moved as its animation settled.

This is a test synchronisation defect, not a product geometry regression.

## Tight feedback loop and reproduction rate

The smallest existing scenario is already the correct product seam. It:

1. opens a Ticket conversation at rest;
2. holds the first conversation read;
3. measures the empty, already-mounted rest band and composer;
4. releases that one read;
5. waits for the hydrated rest line;
6. measures the same band and composer again.

Command:

```sh
.venv/bin/python -m pytest -q \
  'tests/e2e/test_conversation_three_states.py::test_the_rest_band_is_present_before_activity_and_keeps_its_geometry_through_loading[chromium]'
```

On merged Ticket revision `0d3041d9`, ten isolated repetitions failed: **10/10 red**.
Representative result:

```text
before: {'bandHeight': 41, 'bandTop': 569, 'composerTop': 609,
         'bandAboveTheComposer': True}
after:  {'bandHeight': 41, 'bandTop': 566, 'composerTop': 607,
         'bandAboveTheComposer': True}
```

The exact symptom was consistent: stable height and ordering, with both viewport-top
coordinates moving upward by 1–3 px.

## Ranked falsifiable hypotheses

These were recorded before the differential and animation probes.

1. **The new `origin/staging` status-band work exposed an existing asynchronous movement.**
   If so, `origin/staging` with the old composer will fail the same test.
2. **The run-controls extraction changes the footer during conversation hydration.**
   If so, `origin/staging` will pass and only the merged Ticket revision will fail.
3. **An ancestor or page frame moves while descendant layout remains constant.**
   If so, pane/layer/composer heights will stay fixed while their viewport tops move
   together.
4. **The composer itself grows when hydrated model/backend/effort values arrive.**
   If so, the composer's height or the footer/control rectangles will change while the
   surrounding frame remains fixed.
5. **The test measures during a transient animation/layout frame.**
   If so, waiting only for the route entrance animation before the first sample will make
   the same hydration comparison pass without changing product code.

## One-variable probes

### 1. Old composer on `origin/staging`

A detached worktree at `118d8f56` was used. That revision contains the status-band test and
always-mounted rest bar, but retains the pre-extraction inline composer run controls.

Command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q \
  'tests/e2e/test_conversation_three_states.py::test_the_rest_band_is_present_before_activity_and_keeps_its_geometry_through_loading[chromium]'
```

Five isolated repetitions failed: **5/5 red**. The failures were the same 1–3 px common
upward movement, for example:

```text
before: bandTop=568 composerTop=609 bandHeight=41
after:  bandTop=566 composerTop=606 bandHeight=41
```

This falsifies hypothesis 2 and establishes that the composer extraction does not own the
failure. It supports hypothesis 1's ownership prediction, while later instrumentation
refines its mechanism from a possible composer-height change to an ancestor transform.

### 2. Geometry instrumentation

Temporary browser instrumentation measured the screen, conversation layer, pane, band,
composer, and document before and after hydration. Only viewport position changed; the
conversation descendants' heights were stable. The active animation was the route's
`.screen-enter` animation from `assets/app.css`, with the 240 ms and 8 px values resolved
from `assets/tokens.css`.

This confirms hypothesis 3 and falsifies hypothesis 4.

### 3. Wait for only the screen entrance

A throwaway copy of the minimal test added exactly one synchronisation operation before
the first geometry sample:

```js
const screen = document.querySelector(".screen-enter");
await Promise.all(
  screen.getAnimations().map((animation) => animation.finished)
);
```

Nothing about the held read, hydration, rest bar, composer, or assertion changed. Five
isolated repetitions passed: **5/5 green**. The throwaway probe was then removed.

This confirms hypothesis 5 and the diagnosed mechanism.

## Root cause

`page.wait_for_selector(...)` and the SSE-open gate establish that the Ticket screen is
present and live; they do not establish that its decorative entrance transform has ended.
`getBoundingClientRect()` includes transforms. The test therefore compared two different
animation times and called that difference hydration movement.

Conversation hydration is correlated with the coordinate change only because waiting for
the hydrated line advances wall-clock time. It is not causal.

## Smallest repair

Keep the product unchanged. In the browser test, wait for the route screen's own active
animations before taking `before`:

```python
page.locator(".screen-enter").evaluate(
    """async (screen) => {
      await Promise.all(
        screen.getAnimations().map((animation) => animation.finished)
      );
    }"""
)
```

Then retain the current exact `after == before` assertion and same-node assertion. This
keeps the contract strong: after unrelated route decoration has settled, loading activity
must not move or replace the rest band.

A fixed sleep would also mask the failure but would encode machine timing rather than the
actual prerequisite. Relaxing the coordinate assertion would weaken the stated contract,
and changing composer or rest-band CSS would address the wrong system.

## Cleanup

- No production source was edited for diagnosis.
- No committed test was edited by this diagnosis.
- The throwaway animation-wait probe was removed.
- No debug logging or debug prefixes remain.
