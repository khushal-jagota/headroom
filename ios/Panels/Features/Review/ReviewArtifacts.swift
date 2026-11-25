import SwiftUI

/// Proposals, messages and conversations share one route for managed file links:
/// a tap opens the in-app viewer with the caller's dock, and other links go to the system.
struct ReviewArtifactLinks: ViewModifier {
    /// What the viewer docks below the artifact, from the place that shows the link.
    let dock: ArtifactDock
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @Environment(\.openRoute) private var openRoute
    @Environment(\.screenTextEntry) private var textEntry

    func body(content: Content) -> some View {
        content
            .environment(\.openURL, OpenURLAction { url in follow(url); return .handled })
    }
    private func follow(_ url: URL) {
        if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return }
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) {
            let route = AppRoute.artifact(target, dock: dock)
            if let textEntry { textEntry.endEditingBeforeNavigation { openRoute(route) } }
            else { openRoute(route) }
        }
        else { openURL(url) }
    }
}

extension View {
    func reviewArtifactLinks(dock: ArtifactDock) -> some View { modifier(ReviewArtifactLinks(dock: dock)) }
}

/// A Review card's own dock under an artifact opened from it: the proposal's note and
/// decision row, or the message's reply and Mark read, on the card's shared decision. Only
/// the viewer on top mounts it. A decision, Return, Skip, reply or Mark read here closes
/// every artifact viewer back to Review, and the queue moves on as if the card did it. The
/// viewers also close when the card stops being the one Review shows.
struct ReviewArtifactDock: ViewModifier {
    let itemID: String
    let isMessage: Bool
    @Environment(AppStore.self) private var store
    @Environment(ReviewSession.self) private var session: ReviewSession?
    @Environment(ReviewSavedWork.self) private var savedWork: ReviewSavedWork?
    @Environment(\.closeArtifacts) private var closeArtifacts
    @Environment(\.scenePhase) private var scenePhase
    @State private var visible = false
    @State private var sheet: DecisionSheet?
    @State private var busyAction: String?
    @State private var messageActions = ReviewMessageActions()
    /// The card whose decision is saved, held by the queue until it leaves.
    @State private var held: ReviewItem?
    @State private var left = false
    /// Set when this dock read the run itself: no screen showed those messages, so
    /// Mark read stays off here. Back shows the run on the card, where it works.
    @State private var readUnshownRun = false
    private var item: ReviewItem? { session?.shown.flatMap { $0.id == itemID ? $0 : nil } }

    func body(content: Content) -> some View {
        content
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if visible, let session, let savedWork, let item { dock(item, session: session, savedWork: savedWork) }
            }
            .onAppear { visible = true }
            .onDisappear {
                visible = false
                // Removed under an open sheet after the save: the card still leaves.
                if let held, let session { resolve(held, session: session, afterExit: nil) }
            }
            // Also on appear: a page path restored on return can hold a card that has gone.
            .onChange(of: session?.shown?.id, initial: true) { _, shown in
                if session != nil, shown != itemID { closeArtifacts() }
            }
            // Opened before the card's first read finished, which the push cancelled: the dock
            // reads the run itself, so Reply works. Mark read waits for Back, where the card
            // shows the run.
            .task(id: "\(visible)|\(store.connectionSession)") {
                guard visible, isMessage, let session, let savedWork, let item else { return }
                let decision = session.messageDecisions.decision(for: item, savedWork: savedWork)
                if !decision.loaded {
                    readUnshownRun = true
                    await decision.load(item: store.reviewMessageFacts(item), api: store.client)
                }
            }
    }

    @ViewBuilder private func dock(_ item: ReviewItem, session: ReviewSession, savedWork: ReviewSavedWork) -> some View {
        let refusal = session.refusalNotes[item.id]
        if isMessage {
            let decision = session.messageDecisions.decision(for: item, savedWork: savedWork)
            // A refused reply shows its reason in its composer; a refused Mark read here.
            if let refusal, decision.attempt?.state != .failed { notes { ReviewInlineNote(tone: .refused, title: "Refused", text: refusal) } }
            ReviewMessageDock(item: item, isActive: scenePhase == .active, decision: decision, actions: messageActions,
                              markReadAllowed: !readUnshownRun,
                              onSkip: { skip(item, session: session) },
                              onResolve: { resolve(item, session: session, afterExit: $0) })
        } else {
            let decision = session.decisions.decision(for: item)
            let notice = session.notice?.item.id == item.id ? session.notice : nil
            if notice != nil || refusal != nil || decision.changed {
                notes {
                    if let notice { ReviewInlineNote(tone: .info, title: "Reviewer changed", text: "\(notice.reviewer) reviews this now.") }
                    if let refusal { ReviewInlineNote(tone: .refused, title: "Refused", text: refusal) }
                    else if decision.changed, let error = decision.error {
                        Text(error).textSelection(.enabled).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Web.error).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            ReviewProposalDock(isActive: scenePhase == .active, savedWork: savedWork, decision: decision, refusal: refusal, notice: notice,
                               sheet: $sheet, busyAction: $busyAction, onSkip: { skip(item, session: session) },
                               onHold: { held = item; session.resolve(item) },
                               onResolve: { resolve(item, session: session, afterExit: $0) })
        }
    }

    private func notes<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(spacing: 10) { content() }
            .padding(.horizontal, 18).padding(.top, 12)
            .background(PanelsTheme.Web.surface)
    }

    private func skip(_ item: ReviewItem, session: ReviewSession) {
        guard !left else { return }
        left = true
        closeArtifacts()
        withoutAnimation { session.skip(item, store: store) }
    }

    /// Handled here, in the card's order within one turn: the queue holds the card, the
    /// viewers close, then its exit finishes, so the card below never acts on it as well.
    private func resolve(_ item: ReviewItem, session: ReviewSession, afterExit: (() -> Void)?) {
        guard !left else { return }
        left = true
        session.resolve(item)
        closeArtifacts()
        afterExit?()
        withoutAnimation { session.finishExit(item) }
        Task { await store.refresh() }
    }

    private func withoutAnimation(_ change: () -> Void) {
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction, change)
    }
}
