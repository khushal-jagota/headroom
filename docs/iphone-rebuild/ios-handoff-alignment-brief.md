# Panels iPhone App — Confirmed Design & Pre-Build Alignment Brief

This brief hands off the confirmed Panels iPhone app design to the Chief of Staff and the follow-up alignment agent. Khushal confirmed the visual and interaction design and requested an architectural discussion on current app problems, features to strip, and data-flow alignment before any build Tickets start.

## Confirmed Design Artifacts

- [Panels iPhone App Mockup](/files/tickets/t_ymqhjyzk/artifacts/iphone-app-mockup.html) — Full interactive prototype covering Review queue cards, Sidebar navigation, native SwiftUI sheets, 3-way Artifact bottom controls, Manager, Chief, Ticket, Conversation, and interactive data-flow/state simulations.
- [Final Review Page — Editorial](/files/tickets/t_ymqhjyzk/artifacts/review-final-page-options.html) — Four-state reference for the confirmed final Review screen, with a toggle for the optional active-agents status line.

---

## 1. Confirmed Design Decisions

### Review Queue (Primary Surface)
- **Separate cards for proposals and messages:** Proposals and unread conversation messages render as separate Review cards so every card has one clear focus and one bottom control dock.
- **Proposal cards:** Split top header (`Stage` left, `You ⌄` Reviewer button right — no dotted `· Approver` label), slightly lighter card surface, note composer (attach + voice), and one equal-width bottom row of three buttons (`Skip` secondary, `Return` secondary, `Approve` primary — only two visual button tiers).
- **Message cards:** `Message` header at top (matching Proposal and Question cards), `Reply…` composer with inline Send (`↑`), and two equal-width secondary buttons below (`Skip`, `Mark read`).
- **Question & permission cards:** Equal-width `Skip` + `Send answer` (primary) on worker questions; equal-width `Skip` + `Decline` + `Approve` on permission asks.
- **Final Review screen:** Two-tone left editorial display (`31px` bold white over `31px` semibold `#8a8a93` gray) anchored at a consistent upper-center optical baseline across all four states:
  - New + skipped (`3 to review.` / `1 new, 2 skipped.` + `Back to review`)
  - Skipped only (`2 skipped.` / `Nothing new.` + `Review skipped`)
  - New only (`N new.` / `Ready to review.` + `Back to review`)
  - Clear (`All clear.` / `Nothing to review.` — no button)
- **Quiet return button:** `Back to review` / `Review skipped` uses the quiet matte charcoal `Skip`/`Return` button dialect (`#272727` borderless fill, `46px` tall, `12px` radius, `15.5px` semibold `#f0f0f2`), never the bright accent fill.
- **Optional active-agents line:** Plain text (`4 agents working right now`, no status dot) flush-left above the anchored `Message Chief…` composer. Hiding or omitting it causes zero layout shift.
- **Zero noise toasts or network banners:** Remove `Sent` top confirmation toasts and top banners for `slow connection`, `offline`, `reviewer changed`, or `index down`. Handle degraded conditions directly in-place where needed.

### Native Sheets & 3-Way Artifact Bottom Controls
- **Native SwiftUI sheets:** Rebuild Approve / Ceiling, Reviewer, Attach, Ticket, and Actions sheets as standard iOS grouped inset forms (`#1c1c1e` modal sheet, `Cancel` / title / primary toolbar, `#2c2c2e` inset rows with hairline dividers, accent checkmarks, native segmented control for `Then reviews`).
- **3-way Artifact bottom controls (web parity):**
  1. Opened from a Review proposal → keep note composer + `Skip` / `Return` / `Approve` docked below.
  2. Opened from a Review message → keep inline-Send `Reply…` composer + `Skip` / `Mark read` docked below.
  3. Opened from Ticket Artifacts, Manager, or Conversation → keep the live Conversation composer docked below.
- **Collapsed artifacts on Ticket pages:** Keep artifacts hidden behind a collapsed hairline row with count on Ticket pages, matching desktop.

### Sidebar & Existing Workspace Pages
- **Slide-over Sidebar replaces the bottom tab bar:** Matte `#1b1b1b` rail cards with zero borders and zero recaps.
  - Chief of Staff sits at the top with identical card fill, an `All projects` eyebrow, and a right-hand status slot.
  - Active Managers follow with project eyebrow, goal status tag, and right-aligned signal marks.
  - Collapsible `Settled` group below; bottom bar holds `Review` (with badge count) left and Settings gear right.
- **Do not redesign existing pages yet:** Keep Manager (`OrchestratorView.swift`), Chief (`ChiefView.swift`), Ticket (`TicketView.swift`), and Conversation (`ConversationModel.swift` / `ConversationComposer.swift`) broadly faithful to today's app, with inline Send inside composers and no synthetic `Queued / Steer / Drop` or `Focus / Full` chat chrome.

---

## 2. What to Strip or Simplify from Today's iOS App

The follow-up agent should confirm this strip list with Khushal before writing build plans:

1. **Bottom tab bar and `MountedTabContent` tab hosting** (`AppShell.swift`, `Navigation.swift`) — replaced by Review as root plus the slide-over Sidebar.
2. **Long-press preview sheets and awkward context menus** (`WorkspaceContextMenu.swift`, workspace cards) — Khushal explicitly approved stripping these to simplify the codebase.
3. **Mixed proposal + conversation stacking inside one Review card** (`ReviewView.swift`) — split into separate single-purpose Review cards.
4. **Top network status banners and `Sent` top toasts** — remove noisy status banners; keep writes and retries quiet.
5. **Hidden `UINavigationBar` delegate hijack & Chief swipe diagnostic scaffolding** (`Features/Chief/Diagnostics/*`) — confirm removal once navigation moves to one clean SwiftUI stack.
6. **Secondary `More` and `Goals` screens** (`Features/More/Feedback`, `Ideas`, `VPS`, `Usage`, `Features/Goals/*`) — confirm which screens stay reachable from Settings/Sidebar unchanged vs. which can be stripped or deferred so the first build focuses cleanly on Review + core workspace.

---

## 3. Key Technical & Data-Flow Problems to Discuss and Align On

These issues surfaced from inspecting `ios/`, `web/src/`, server routes, and settled Manager *"Make the Panels iPhone app's data work like the web app"* (`si_duer6288`). The alignment agent should resolve each with Khushal before slicing build Tickets:

1. **Review Queue Contract & Separating Messages from Proposals:**
   - Decide whether the iOS app splits ticket proposals and unread conversations client-side from `/api/navigation/view/events` (and `/snapshot`) or whether the server Review projection should expose separate queue entries directly.
   - Confirm how `review_priority_keys` behaves when returning from the final screen (`Back to review` / `Review skipped`) while both newly arrived and skipped items exist.
2. **Main-Thread Hangs & Draft Persistence (`ReviewView.swift`, `LocalStore.swift`):**
   - Previous profiles showed ~1,451 ms main-thread hangs from synchronous SwiftData queries during `ReviewView` renders and per-keystroke MainActor SQLite writes in `saveDraft`.
   - Align on replacing per-render queries with an in-memory `ReviewSavedWork` snapshot (invalidated only on Outbox writes) plus debounced off-main draft persistence.
3. **Idempotent Decisions, Silent Offline Outbox & Reviewer Conflicts (`Outbox.swift`, `ReviewDecision.swift`):**
   - Proposal writes must send `POST /api/tickets/:id/decision-operations/:op_id` with `expected_proposal_revision` and freeze the command into SwiftData Outbox before network dispatch.
   - On timeout (`.uncertain`) or reconnect, check `GET /api/tickets/:id/decision-operations/:op_id` before re-POSTing so decisions never double-apply.
   - On HTTP 200 `receipt.outcome="refused"` (for example, reviewer moved mid-review), surface an inline refusal state on the card while preserving the user's draft note and attachments.
4. **Navigation Architecture & Dual-Mounted Composer Leaks (`AppShell.swift`, `ChiefView.swift`, `ConversationModel.swift`):**
   - Replacing the nested `MountedTabContent` hosting controller and hidden `UINavigationBar` delegate with a standard SwiftUI navigation stack + slide-over Sidebar should eliminate the Chief sideways swipe shift and dual-mounted `ConversationSurface` composer leaks. Confirm exact state retention rules when switching Sidebar destinations.
5. **Unified Media Capture & Markdown Parity (`ConversationImagePreparation.swift`, `ReviewVoiceCapture.swift`, `ConversationSpeech.swift`):**
   - Move HEIC→JPEG compression off MainActor (preventing >3 MiB HTTP 422 rejections and UI freezes).
   - Unify Review voice capture and Conversation speech into one durable `.m4a` recording pipeline backed by Outbox so offline voice notes never vanish.
   - Confirm shared block Markdown rendering (tables, code blocks, `panels-chart`) across Review proposals, Artifacts, and Conversations.
6. **3-Way Artifact Layer & Native Sheets (`ArtifactViewerView.swift`, `TicketApprovalLimitSheet.swift`):**
   - Align on how `ArtifactViewerView` inherits its bottom dock (proposal decision bar, review message reply bar, or live conversation composer) without duplicating state or breaking back-navigation.
   - Confirm exact SwiftUI sheet presentation (`presentationDetents`, grouped `Form` styling) for Approve / Ceiling, Reviewer, Attach, and Ticket Actions, plus the 15-minute `worker_setup` restart guard (HTTP 400 refusal).
7. **Server / Build Coordination & Phasing:**
   - Separate pure-iOS refactors from any server API/projection changes and confirm whether an HTTP 426 minimum-build gate is needed.
   - Agree on build slicing — for example: (1) Shell, Sidebar, and clean Navigation + stripping dead chrome, (2) Review Queue rewrite + Final Screen + Native Sheets + Outbox/Performance fixes, (3) 3-Way Artifact layer + Media/Markdown parity.
