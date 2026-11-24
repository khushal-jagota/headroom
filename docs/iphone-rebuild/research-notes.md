# Research notes for Question Answers (22 November 2026)

Three read-only code surveys of commit dbb3991c (staging). Nothing was built or run.
Summaries of their findings are in question-tree.html. The ranked evidence is kept here.

## Review staleness (verified in code unless marked inferred)
1. ReviewView keeps shown, skipped and resolved cards in its own @State (ReviewView.swift:11-15). It resets only on a server URL change (:97). The tab content stays mounted across background (AppShell.swift:17), so the state survives every foreground entry. A held message stays while its owner is eligible (ReviewDecision.swift:285-317), and its conversation IDs widen the stream scope (ReviewView.swift:30,101,140). Result: a message read days ago on the web stays the phone's card until skip, Ticket done, or restart.
2. Pull calls store.refresh() (ReviewView.swift:83,88). refresh() reads the view only when no stream runs (AppStore.swift:395). reconnectRequested is only checked between reconnects (:345), so pull never replaces an open stream and never clears held cards.
3. `connection` is never reset when a stream ends or a run starts (AppStore.swift:46,341,368). Web resets at once (web/src/lib/navigation/views/owner.ts:85-91).
4. Inferred: a hung connection after wake is bounded by the 45 s URLSession timeout. The phone ignores the 15 s heartbeat lines (NavigationViewContracts.swift:430).
5. Saved displays at launch have no age limit (AppStore.swift:121-130), replaced by the first snapshot.
Load: two foreground streams (/api/navigation/view/events, /api/changes). A full snapshot on every foreground entry including brief .inactive (AppShell.swift:96), on every scope change including each card change (ReviewView.swift:101), and on each server change-log gap. Read-ahead runs up to 30 pending items, 3 at a time.

## Sending and offline
Outbox kinds: message, approval, sendBack, voice (PendingWork.swift:4). Queued: approve, send back, review reply, Chief message, conversation messages, notification replies, photos, voice (into draft only).
Direct, fail offline: mark read (ReviewMessageDecision.swift:199-204, ConversationModel.swift:589-604), reviewer-only or ceiling-only change (ReviewDecision.swift:67-70), backlog/resume, recap request and edit, blocker removal, worker setup (TicketAPI.swift:13-54).
Approve and send back leave at once. Review reply waits for a send attempt (ReviewMessageDecision.swift:193-196). Current app shows a Tailscale "Try again" cover and notes such as "Waiting for connection", "Sent", "Delivery uncertain" (Outbox.swift:257-289).
Server de-duplicates messages by sender message ID (server/conversation/runtime/send.ts:223-262), so plain replay is safe for existing conversations (inferred).
Asks: answered by POST .../permission-answers and .../user-input-answers (server/conversation/routes.ts:494,513). Not idempotent: a repeat returns refused, with no receipt (server/conversation/runtime/answers.ts:43-48,66). The review window carries asks (server/navigation/views/projection.ts:34-51,181-213). The phone builds only proposal and message parts (NavigationScope.swift:150-167). Ask wording comes from the conversation's pending_permission_ask / pending_user_input, as on web (web/src/components/ReviewAskCard.svelte:65-66).
Decision operations are idempotent with receipts in one transaction (server/tickets/decision_operations.ts:199-261).

## Codebase
Keep: Core networking, persistence, state, models, navigation-view client, ConversationModel, Ticket, Manager, Artifacts, Notifications.
Reshape: Review views (keep logic), Workspace into the Sidebar, ConversationNavigation dual mount (ConversationNavigation.swift:38-47).
Replace: the shell's TabView/MountedTabContent (AppShell.swift:91-115,161-210), AgentPageBackGesture (AgentPageActions.swift:103).
Strip: Chief Diagnostics (576 lines), WorkspaceContextMenu.swift (300 lines), Feedback (no server route, not on web), Steer/Queue/Send-now menu (ConversationComposer.swift:199).
Sheets: DecisionApprovalSheet (ReviewStyle.swift:227-310) is the hand-drawn one, used by Review and the Ticket proposal card. TicketApprovalLimitSheet is already a Form close to the mockup. panelsScreen() (Theme.swift:178-186) repaints every Form flat.
Long-press: Remove blocker (TicketView.swift:140) and Edit recap on Manager pages (AgentRecap.swift:49) have no other entry. Chart scrubbing (GoalPlot.swift:44) stays.
Goals are not Managers. Chief and Manager pages embed their goals (GoalPlacements.swift:201). Web keeps Usage, Ideas, Goals, VPS and Settings.
Tests: about 7,900 lines of host tests through ios/scripts/test-offline.sh, which copies named files. 426 minimum-build gate exists (server/ios_compatibility/policy.ts, NativeBuildGate).
