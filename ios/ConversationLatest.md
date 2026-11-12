# Returning to the latest message

The control should appear only after a meaningful move away from the newest
message. It should remain easy to reach above the conversation line and input,
fade without moving the thread, and return to the newest line without changing
the keyboard or draft. New output must not move someone reading older messages.

## Diagnosis

Baseline: `39a8786973c44c6fb679a730bbae98eb1b5a4ae5`, also the freshly fetched
`origin/ios-app` at investigation time. Recorded on already-booted PanelsLaneB,
under `lockf /tmp/panels-PanelsLaneB.lock`, using
`app.panels.ios.pios-latest-button` and a loopback-only HTTP stub. The thread has
18 history messages and a reply that streams, then completes with seven long
paragraphs. No live writes or simulator lifecycle changes were used.

The baseline clips and extracted frames show these concrete issues:

- Latest can pop up during a small drag while the latest message is still near
  the bottom. Touch-down immediately changes the same flag used for visibility.
- The centered pill covers the central reading line and sits only 10 points
  above the conversation area's lower border. It does not overlap the composer,
  but competes visually with the separate latest-message line beneath it.
- Appearance and removal have no explicit fade. Tapping removes the pill before
  the animated return has landed.
- Output arriving while scrolled up leaves the pill unchanged. Nothing on the
  control distinguishes new output from simply having scrolled into history.
- A large streamed completion can detach following and bring the button back.
  The old geometry callback treats programmatic animation like reader scrolling;
  a size update during the return can overwrite the intent to follow.

The baseline ordinary tap did return to the latest message. The recorded tap
with an open keyboard retained both the keyboard and draft. No overshoot was
observed. Those were acceptance requirements to preserve, not reproduced bugs.

## Comparison and decisions

The web implementation in
`web/src/components/conversation/viewport/ConversationViewport.svelte` separates
jump progress from visibility and keeps the control until the newest line is in
sight. Its geometry uses a 48-pixel near-bottom tolerance and a 24-pixel bottom
gap. The recent `23a68f9f` fix restores keyboard focus to the thread after Latest
leaves the DOM. Native text input already keeps its focus; the iOS fix preserves
that behavior rather than focusing the composer or dismissing the keyboard.

[Apple Messages](https://support.apple.com/en-ie/guide/iphone/iphe9b48b89e/ios)
describes catch-up as a move to the first unread message. That differs from a
Latest destination. Panels retains first-unread opening and keeps the Latest
label explicit. [Slack's documented catch-up view](https://slack.com/help/articles/226410907-View-all-your-unread-messages)
uses unread counts and a deliberate refresh for new messages. The useful lesson
is to preserve reading position and distinguish incoming activity. Panels uses
a dot, because streamed chunks and projected rows are not a reliable count of
unread messages. A [public WhatsApp user report](https://www.reddit.com/r/whatsapp/comments/1jyghxc)
describes its downward arrows above the recording control. That is a limited,
secondary reference for keeping the navigation control clear of input. These
peer comparisons are documentation/report based, not measured current-app
animation or device evidence.

The native policy now separates reader gestures, distance-based visibility,
return progress, and new output. Latest appears beyond 120 points and hides
within 32 points, preventing threshold flicker. It sits at the lower right with
a 44-point target and 16-point edge clearance. The pill can still cover content
at the right edge while reading; it no longer sits across the center of each
line. It uses the shared 150ms fade and 320ms entrance motion, with no bounce.
Reduce Motion removes both animations.

The return targets the native scroll view's bottom edge, including the content's
12-point bottom padding, instead of a zero-height sentinel above that padding.
Content-size and viewport-size changes keep the reader at the edge only while
following. A user gesture interrupts a return. Growth during an animated return
is aligned when it finishes, without losing the intent to follow.

## Verification and test admission

Four Foundation host cases add coverage absent from the delivery/model tests:
small gestures and threshold reversals; growth during a pending return; a reader
interrupting that return; and first-unread opening/reset. They reuse
`ConversationTests` and the existing host runner. No mock, simulator, server, or
new test host is needed for this policy.

One focused connected scenario reuses the existing conversation UI-test host
and loopback stub. Its distinct risk is the SwiftUI boundary between lazy row
layout, native scroll animation, content growth, and keyboard safe-area changes.
Pure policy tests cannot prove the actual final line is visible or that the
keyboard remains open. The test checks small-drag visibility, button clearance,
reader-position preservation during streaming, the new-output cue, return and
completion, and keyboard/draft preservation. Screenshot text recognition checks
the final visible line above the conversation line with and without a keyboard.
The final connected case passed in 40.941 seconds. All 40 host checks passed.
Ruff and Python syntax checks passed for the stub harness. Strict mypy is not
clean for this pre-existing untyped script: the base reports 46 errors and this
revision reports 49 (the three extra reports are calls into existing untyped
`respond`/`tick` helpers). This is not a passing Python type-check claim.
The whole multiline SwiftUI accessibility frame is unsuitable: it extends below
the visible glyphs on this simulator. No assertion is based only on button
absence or visual inspection.

Run the host checks with `ios/scripts/test-conversation.sh`. Run the native proof:

```sh
PANELS_CONVERSATION_OUTPUT=/path/outside/worktree \
PANELS_CONVERSATION_DEVICE=566D051B-B6BB-4046-9038-66C05CCC2B96 \
PANELS_CONVERSATION_LANE=PanelsLaneB \
PANELS_CONVERSATION_BUNDLE=app.panels.ios.pios-latest-button \
PANELS_CONVERSATION_TEST=ConversationLatestTests \
python3 ios/scripts/test-conversation-simulator.py
```

The script selects an unused loopback port and holds the lane lock through build,
test and recording. It stops its server and task app when finished. It does not
boot, shut down, erase or delete a simulator. `PANELS_LATEST_BASELINE=1` is only
for recording the old behavior; it skips the new regression assertions.

Evidence is retained outside the repository in
`/Users/khushaljagota/Coding/firstmate/data/pios-latest-button/`: `before/` contains
the baseline clips; `final/` contains the final distance, streaming, and keyboard
clips and the Xcode result bundle. Host output is in `host.log`. This is stub and
simulator evidence. Physical-device smoothness, VoiceOver operation, and runtime
Reduce Motion remain unverified. The change does not alter server read markers.
