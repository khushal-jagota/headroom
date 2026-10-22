# Review on iOS

Review shows one waiting proposal at a time. The queue and badge follow the
shared change stream. Skip keeps a proposal out of this visit's queue without
changing it on the server. When every remaining item was skipped, Revisit skipped
items brings them back. A new proposal on the same ticket appears again.

Review uses the system font throughout. Skip stays in the top bar. Send back and
Approve have equal weight at the end of the scrollable proposal, with Approve on
the right. Both use rounded rectangles. Approve uses the filled accent background with dark
ink text; Send back uses the accent surface and light accent text. The title opens the
ticket in Workspace; its long-press menu offers Rename. Recaps expand in place.
Tapping the proposal edits it in place, with Done in the navigation bar. Approval
sends the edited text together with the exact revision that was read. If the
proposal changes during editing, Review keeps the draft and requires a reload.

The approval sheet shows How far as a list of gates and Who reviews as a native
segmented picker. The suggested next gate and current holder are selected first.
Nothing is written until Approve until <Stage> is pressed. The worker type's
served manifest supplies the available gates; Review does not assume coding stages.

Swipe left past 30% of the screen to skip, or use the Skip button. The swipe never
approves. The title and actions do not move sideways with the proposal. Reduced Motion removes the
positional part of the transition. Writes give a light haptic on submission and
success or error feedback when the server responds. Skip uses selection feedback;
crossing the swipe threshold uses a light impact.

Send back is absent for briefs and proposals with no existing worker conversation.
Its sheet opens with the keyboard ready. The typed draft belongs to the ask and
survives sheet dismissal. The mic records AAC locally after microphone permission,
then calls the existing conversation voice-transcription endpoint. Transcribed
words append to typed guidance. A failed transcription retains the audio for Retry
while the sheet remains open. Closing the sheet cancels recording/transcription
and removes temporary audio; typed words remain. Sending requires nonempty text.

A decision remains on screen if its request fails. Both writes first check that
the proposal is still current. Approval also sends the revision to the server,
which atomically refuses an outdated revision. The existing send-back endpoint
has no conditional revision parameter, so another writer can change the proposal
between that check and the send-back request; this is not an atomic guarantee.

This screen does not implement the pending conversation redesign. The title opens
its ticket in Workspace. Cross-tab navigation sets the destination without an
intermediate Workspace-root animation. Queue replacement does not cross-fade
the outgoing and incoming titles or action bars. Group labels retain normal title case. Every pushed
route hides the tab bar; returning to a tab root restores it.

The shell uses a small deployment capsule inside each root and detail page's safe
area. Healthy idle status takes no space. Deploying, Reconnecting and Problem
remain visible while relevant; Deployed expires at the server's valid-until time.
Expired deployment progress shows Reconnecting, matching the web. A failed or
rolled-back deployment stays Problem. This replaces the permanent deployment strip.
The Send back sheet uses a large native detent with scrollable content so its
header, editor and actions remain reachable above the keyboard.

## Focused verification

`ReviewDecisionTests` reuses the foundation ticket fixture and an in-memory
URLProtocol transport. Five Review checks cover gaps in the foundation tests:

- Edited approval includes the visible revision, edited text, next gate and holder,
  and cannot be submitted twice after success.
- A changed proposal preserves an unsaved draft and blocks either decision until
  reload, including a change discovered only during the submission's preflight.
- Send-back trims guidance; a refused write leaves the decision available to retry.
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
