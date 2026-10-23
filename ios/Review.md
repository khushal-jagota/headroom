# Review on iOS

Review shows proposals and unread conversations in one queue, ordered by waiting
time. Each conversation has one card containing its complete unread message run.
The queue and badge follow the shared change stream. Skip keeps an item out of this visit's queue without
changing it on the server. When every remaining item was skipped, Revisit skipped
items brings them back. A new proposal on the same ticket appears again.

The finished screen says “You’re caught up.” and “All done for now.” in the
center. It has no agent progress indicator. A quiet rounded button at the bottom
shows how many skipped items are still available and brings them back into the
queue. Removed or resolved asks do not contribute to that count. When none remain,
the button is absent. Pull down to refresh still works.

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
field takes focus. The complete return composer and its actions sit in a bottom
safe-area inset above the keyboard, even on long proposals. Cancel dismisses the keyboard and restores the buttons while
keeping the draft for that ask. Swipe-to-skip is disabled while it is open. The
mic records AAC locally after permission, then calls the existing conversation
voice-transcription endpoint. Words append to typed guidance. Failed transcription
retains audio on disk for retry after reconnect or relaunch. Closing the composer
preserves the recording; only explicit discard removes it. Sending requires nonempty
text and locks the field until the response. Failure keeps the words available.

A decision is saved before its ask leaves the screen. A pending row shows waiting,
sending, completion, or a need to review again. Both queued writes first check that
the proposal is still current. Both send the displayed revision to the server,
which checks it in the same transaction as the decision. If it changed after the
last read, the pending row says “This changed, review again” and keeps the saved words.

A proposal title opens its ticket in Workspace. Cross-tab navigation sets the destination without an
intermediate Workspace-root animation. Every pushed route hides the tab bar;
returning to a tab root restores it. A message card shows markdown, attached images and files, and inline previews for
linked artifacts. Reply uses the same composer, attachments and voice controls as
Conversation. Its draft is separate from the full conversation's draft and survives
Skip and relaunch. A reply is saved in the outbox with its conversation ID, message
identity and the last displayed message sequence. Accepted and server-queued replies
acknowledge only that sequence. A later message stays unread and returns to Review.
A refused or uncertain reply keeps its words and attachments. Receipt reconciliation
and held-prompt checks avoid sending an accepted reply twice. An explicit retry after
a terminal refusal receives a new identity while retaining the original read boundary.

Mark read acknowledges only the displayed run and does not send a reply. Open
conversation opens the exact conversation named by the card. An older conversation
whose owner has moved on is read-only. Message cards use the existing Review fade,
swipe threshold and feedback. Mark read uses the filled action styling; Open
conversation uses the raised action styling.

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

Saved approval and send-back operations retain the ticket title and proposal step
label alongside delivery state and saved words. These names are saved with the
operation, so advancing the queue or relaunching does not lose its identity. Older
saved operations fall back to the loaded ticket title or ticket ID and field name.

Unread-message verification uses `ReviewMessageTests` with the existing URLProtocol
host: mixed queue decoding, a complete 65-message unread run, bounded Mark read,
refusal/relaunch/retry with attachments, historical send prevention, and recovery
sender attribution. The existing offline simulator host also supports
`PANELS_REVIEW_MESSAGES=1`. Its one connected test exercises SwiftUI card controls,
reply refusal and relaunch, queued acceptance, a message arriving after display,
Mark read, exact historical navigation, Skip and revisit. Run it on an already
booted PanelsLaneA under its lock; `PANELS_OFFLINE_BUNDLE` selects the task bundle.
This proof uses loopback writes. The optional live test requires
`TEST_RUNNER_PANELS_LIVE_READ_ONLY=1` and only reads Workspace, Review and Conversation.

Finished-state verification extends the existing local-skip unit test to cover
removed, replaced, and resolved asks in the revisit count. One connected case,
`ReviewMessagesTests/testCompletionAndRevisit`, reuses the offline HTTP stub and
UI test host to check the real bottom button, restore the skipped queue, and
remove the button after fixture approvals. Model tests cannot prove that SwiftUI
wires the button to the queue or keeps it above the tab bar. This case took 27.8
seconds on PanelsLaneB; the stub recorded no writes for Skip and three approvals
only after Revisit. The fixture supplies seven running agents to check that Review
shows no agent status. No live server writes or physical-device checks were made.
Run with `PANELS_REVIEW_COMPLETION=1`,
`PANELS_OFFLINE_BUNDLE=app.panels.ios.pios-review-done`, and a task-owned
`PANELS_OFFLINE_OUTPUT` directory using `python3 ios/scripts/test-offline-simulator.py`.
The completion mode takes `/tmp/panels-PanelsLaneB.lock` and requires that lane to
be already booted; it does not operate simulator lifecycle.
