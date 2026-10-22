# Review on iOS

Review shows one waiting proposal at a time. The queue and badge follow the
shared change stream. Skip keeps a proposal out of this visit's queue without
changing it on the server. When every remaining item was skipped, Revisit skipped
items brings them back. A new proposal on the same ticket appears again.

The proposal can be edited in a native sheet. Done keeps that draft on the device;
approval sends the edited text together with the exact revision that was read.
If the proposal changes while an edit is underway, Review keeps the draft and
asks the reader to reload. Approving first chooses how far the worker may proceed
and who should be asked next. These choices come from that worker type's current
manifest, with the next gate selected initially.

Swipe right opens the approval choices. Swipe left skips. Buttons and VoiceOver
custom actions provide the same operations. A successful approval or send-back
produces a success haptic; Skip produces a light haptic. The brief cannot be sent
back, and other proposals require an existing worker conversation. Send-back
requires a written instruction. Opening a ticket pushes its existing route.

A decision remains on screen if its request fails. Both writes first check that
the proposal is still current. Approval also sends the revision to the server,
which atomically refuses an outdated revision. The existing send-back endpoint
has no conditional revision parameter, so another writer can still change the
proposal between that check and the send-back request; this matches the existing
backend limitation and is not an atomic guarantee.

Motion uses the existing theme durations and native sheets/navigation. Queue
changes use a short fade and small offset; Reduce Motion removes the offset.
The app does not implement the pending conversation redesign.

## Focused verification

`ReviewDecisionTests` uses the existing ticket fixture and an in-memory URLProtocol
transport. It adds four distinct checks absent from the foundation tests:

- Edited approval includes the visible revision, edited text, next gate and holder,
  and cannot be submitted twice after success.
- A changed proposal preserves an unsaved draft and blocks either decision until
  the reader reloads it.
- Send-back trims guidance; a refused write leaves the decision available to retry.
- Skip changes only local selection, and a newer proposal can appear again.

These are native unit tests with no connected server or browser host. The separate
headless capture driver is task evidence outside the repository; it uses live GETs
for the initial screenshot and a loopback fixture server for mutation recordings.
Simulator evidence does not establish physical-device haptic feel or frame rate.
