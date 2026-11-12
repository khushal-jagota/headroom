# Review on iOS

Review shows proposals and unread conversations in one queue, ordered by waiting
time. Like desktop, a Ticket is one card on one page: its title and recap once, its
proposal first, then each of its conversations with its complete unread message run.
A Chief or Manager conversation is a card of its own. There is no picker between parts.
The queue and badge come from the navigation view's review window, which the phone
follows over the view's events stream; desktop builds its queue from the same window. Skip keeps a card out of this visit's queue without
changing it on the server. A decision on one part leaves the others on the page;
the page leaves when every part is handled. When every remaining card was skipped,
Revisit skipped items brings them back, counted per card. A new proposal or message on the same ticket brings its card back.

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
Tapping the proposal edits it in place, with Done in the navigation bar. A tap on a
link instead follows it. Proposal and message cards share one link route
(`ReviewArtifacts.swift`): links resolve against the server address, a managed file
opens the in-app viewer, and other links open in the system. Each linked file also
shows an inline preview with Open artifact, hidden while editing. Approval
sends the edited text together with the exact revision that was read. If the
proposal changes during editing, Review keeps the draft and requires a reload.

The approval sheet shows How far as a list of gates and Who reviews as a native
segmented picker. The suggested next gate and current holder are selected first.
Nothing is written until Approve until <Stage> is pressed. The Ticket's saved workflow supplies the available gates; Review does not assume
coding stages or read controls from the current Worker preset.

Swipe left past 30% of the screen to skip, or use the Skip button. The swipe never
approves. The entire ask follows a leftward finger drag; releasing short of the
threshold returns it without bounce. Skip fades the whole ask out over 120ms,
resets its scroll position while hidden, then fades the next ask in over 200ms.
The next asks are read ahead, three at a time: a proposal with its worker manifest,
a message card with its unread run. The view already carries each conversation's
read boundary and whether its address is current, so a message card needs one read. A message card
read ahead before its conversation's latest event moved in the view reads its run
again as it is shown, with the read-ahead run on screen meanwhile; a message that
arrives while the card is on screen stays outside the displayed run.
The review window holds 30 cards; once every loaded card was skipped, the next
page replaces it, and Revisit returns to the first with every skipped card. While a proposal is
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

Review and its tab badge share one server-specific snapshot of outgoing work.
The snapshot reads the store once after an outgoing payload changes or a row is
removed. Queue evaluation and message-attempt presentation use memory. Draft edits,
cache writes, and unchanged outgoing saves do not refresh the snapshot. A different
server replaces it. Review pauses store reads while its root is hidden or the app
is not active. On return, it restores changed proposals and saved voice drafts.
This preserves the existing decision matching and outgoing retention policy.

A decision is saved before its ask leaves the screen. Like desktop, Review shows no
receipt or status row for a saved decision. Both queued writes first check that
the proposal is still current. Both send the displayed revision to the server,
which checks it in the same transaction as the decision. If it changed after the
last read, the ask returns to the queue.

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

Mark read acknowledges only the displayed run and does not send a reply. Both
Mark read and Open conversation use the existing secondary action style. Open
conversation follows a link through the owning Ticket, Orchestrator, Chief or Goal page
and opens that page's normal conversation. Back or a left-edge swipe returns to
that owner page. An older conversation whose owner has moved on remains read-only
inside the same conversation page. Message cards retain their Review fade, swipe
threshold and feedback.

Reply uses the normal composer's one-line resting shape and expands with text or
attachments. Clearing the draft collapses it without dropping keyboard focus.
Recording dismisses the keyboard. Error messages, attachments and reply delivery
notes appear inside the input's border, including their retry controls. Review's
reply draft and send/read boundary remain separate from an ordinary conversation
message.

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

Unread-message verification uses `ReviewMessageTests` with the existing URLProtocol
host: a complete 65-message unread run, bounded Mark read,
refusal/relaunch/retry with attachments, historical send prevention, and recovery
sender attribution. Its items carry the view's read boundary and current flag;
only the events read reaches the stub. `NavigationViewTests` checks the queue
itself against desktop's synthetic fixtures: canonical addresses with no fallback
to a former owner, the oldest wait per Ticket card, exclusion of backlogged, done,
settled-owner and agent-held asks, window order, and the native item identities.
The offline simulator stub serves the same view snapshot. The existing offline simulator host also supports
`PANELS_REVIEW_MESSAGES=1`. Its one connected test exercises SwiftUI card controls,
reply refusal and relaunch, queued acceptance, a message arriving after display,
Mark read, shared historical conversation navigation, Skip and revisit. Run it on an already
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

Review conversation-link verification reuses that same stub and UI host in
`ReviewMessagesTests/testReviewConversationLinkAndComposer`. It exercises Ticket
and Orchestrator links, cancelled and completed left-edge swipes, chevron return,
normal draft retention and separate Review drafts. It also measures the shared
48-point resting height, expansion with text, collapse after clearing text without
losing keyboard focus, keyboard dismissal on recording, and feedback containment
in both Review and Conversation. These are SwiftUI layout and navigation risks
that the model tests cannot prove. The one added connected case took 135.9 seconds
on the temporary iPhone 17 Pro simulator; it reuses the existing server and test
host. Speech silence is a debug fixture, not microphone or provider evidence.
Before and after captures remain outside the source worktree.

Stacked Ticket card verification. `ReviewDecisionTests/testTicketCardSkipsAndResolvesAsOneEntry`
checks the queue model: Skip takes the whole card and counts once, deciding one part
keeps the card current, and a new part brings a skipped card back. The existing
`NavigationViewTests` case now checks that a Ticket card stays one entry with the
proposal first. One connected case, `ReviewMessagesTests/testStackedTicketCard`,
proves what the model tests cannot: on the real SwiftUI host, the proposal shows above
the message under one title, Skip and Revisit act on the card, the message stays on
screen after approval, and Mark read takes the page away. Its writes change the shared
fixture, so it runs alone on a fresh stub with `PANELS_REVIEW_MESSAGES=1`,
`PANELS_REVIEW_STACKED=1` and
`PANELS_OFFLINE_TEST=ReviewMessagesTests/testStackedTicketCard`. It reuses the
offline stub, whose `ticket-stacked` owner keeps the proposal on the message's Ticket,
and it took 22.5 seconds on a task simulator.
