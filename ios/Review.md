# Review on iOS

Review shows one waiting proposal at a time. The queue and badge follow the
shared change stream. Skip keeps a proposal out of this visit's queue without
changing it on the server. When every remaining item was skipped, Revisit skipped
items brings them back. A new proposal on the same ticket appears again.

Review uses the system font throughout. Skip stays in the top bar. Send back and
Approve have equal weight at the end of the scrollable proposal, with Approve on
the right. Both use rounded rectangles. Approve uses the filled accent background with dark
ink text; Send back uses the raised surface, strong text and a hairline border.
Both have 12pt corners. The title opens the
ticket in Workspace; its long-press menu offers Rename. Recaps expand in place.
Tapping the proposal edits it in place, with Done in the navigation bar. Approval
sends the edited text together with the exact revision that was read. If the
proposal changes during editing, Review keeps the draft and requires a reload.

The approval sheet shows How far as a list of gates and Who reviews as a native
segmented picker. The suggested next gate and current holder are selected first.
Nothing is written until Approve until <Stage> is pressed. The worker type's
served manifest supplies the available gates; Review does not assume coding stages.

Swipe left past 30% of the screen to skip, or use the Skip button. The swipe never
approves. The entire ask follows a leftward finger drag; releasing short of the
threshold returns it without bounce. Skip fades the whole ask out over 120ms,
resets its scroll position while hidden, then fades the next ask in over 200ms.
The next proposal and its worker manifest are prefetched. While a proposal is
loading, its card reserves the previous card height. After approval succeeds,
the same transition waits for the approval sheet's dismissal callback. Reduce
Motion keeps direct finger tracking and uses 120ms fades for the swap. Writes
give light feedback on submission and success or error feedback on response.
Skip uses selection feedback; crossing the swipe threshold uses a light impact.

Send back is absent for briefs and proposals with no existing worker conversation.
It replaces the buttons with an inline field and Cancel/Send back controls. The
field takes focus. Cancel dismisses the keyboard and restores the buttons while
keeping the draft for that ask. Swipe-to-skip is disabled while it is open. The
mic records AAC locally after permission, then calls the existing conversation
voice-transcription endpoint. Words append to typed guidance. Failed transcription
retains audio on disk for retry after reconnect or relaunch. Closing the composer
preserves the recording; only explicit discard removes it. Sending requires nonempty
text and locks the field until the response. Failure keeps the words available.

A decision is saved before its ask leaves the screen. A pending row shows waiting,
sending, completion, or a need to review again. Both queued writes first check that
the proposal is still current. Approval also sends the revision to the server,
which atomically refuses an outdated revision. The existing send-back endpoint
has no conditional revision parameter, so another writer can change the proposal
between that check and the send-back request; this is not an atomic guarantee.

This screen does not implement the pending conversation redesign. The title opens
its ticket in Workspace. Cross-tab navigation sets the destination without an
intermediate Workspace-root animation. Every pushed route hides the tab bar;
returning to a tab root restores it. The conversation surface is unchanged.

Deployment status lives in More's main list, in a Panels server row that opens
VPS. There is no floating deployment capsule. A small dot on More appears while
status is active, and is red for a problem. Healthy idle has no dot. Deployed
expires at the server's valid-until time; expired progress becomes Reconnecting.
A failed or rolled-back deployment stays Problem.

## Focused verification

`ReviewDecisionTests` reuses the foundation ticket fixture and an in-memory
URLProtocol transport. Five Review checks cover gaps in the foundation tests:

- Edited approval includes the visible revision, edited text, next gate and holder,
  and cannot be submitted twice after success.
- A changed proposal preserves an unsaved draft and blocks either decision until
  reload, including a change discovered only during the submission's preflight.
- Send-back trims guidance; a changed proposal holds the saved decision for review.
- Skip changes only local selection, and a newer proposal can appear again.
- Voice uses the existing base64/audio-mp4 request contract and appends returned
  words without losing typed guidance; an empty transcript does not change a draft.

These are native unit tests with no connected server or browser host. The temporary
headless capture driver is task evidence outside the repository. It uses live GETs
for the initial screenshot and a loopback fixture server for mutation recordings.
Simulator evidence does not establish physical-device haptic feel or frame rate.
The microphone recording path and live transcription provider were not exercised;
the transcription wire contract was tested against the in-memory transport.

A sixth focused check covers deployment expiry and failure precedence, which the
foundation tests did not cover. It requires no server or simulator interaction.

Round 5 verification used one locked PanelsLaneA fixture pass with bundle ID
`app.panels.ios.pios-round5`: six Review tests, four Workspace tests, and a temporary
37-second UI check of Settled open/close, Skip, Send back draft retention, approval
dismissal, and More status. The UI check covers SwiftUI focus, sheet dismissal and
scroll behavior that the model tests cannot prove; it reuses the existing UI test
host and a loopback stub, with no live server writes. The driver and 29-second clip
are task evidence outside the repository. Motion frames were compared with the
selected A/C mockups. This does not verify device haptics, live transcription,
Reduce Motion on a device, or pagination with hundreds of settled managers.

After the one capture pass, source review removed a redundant prefetched read,
pinned the current ask against a concurrent queue refresh during sheet dismissal,
used sentence-case group labels, and hid Skip’s system toolbar capsule. Those
small follow-ups received a fresh jobs-4 compile, without a second capture.

See [offline recovery](Offline.md) for persistence, response-loss handling and focused verification.
