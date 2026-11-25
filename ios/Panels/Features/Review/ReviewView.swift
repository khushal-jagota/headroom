import SwiftUI
import UIKit

/// Review: the summary screen, or one card with one purpose. The shell's ReviewSession
/// owns the queue; this screen shows it, reads the next cards ahead and acts on them.
struct ReviewView: View {
    let savedWork: ReviewSavedWork
    let session: ReviewSession
    var isActive = true
    @State private var appeared = false
    private var active: Bool { isActive && appeared && scenePhase == .active && !store.contentCovered }
    /// How many cards are read before they reach the screen, the shown one included.
    private static let readAhead = 3
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openRoute) private var openRoute
    @State private var cardHeight: CGFloat = 180
    private var server: String { LocalStore.server(store.baseURL) }
    private var snapshotCurrent: Bool { savedWork.key == ReviewSavedWork.Key(server: server, revision: LocalStore.shared.outgoingRevision(server)) }

    var body: some View {
        ScreenTextEntryScope { textEntry in
            VStack(spacing: 0) {
                topBar(textEntry)
                content
            }
        }
        .onAppear { appeared = true }
        .onDisappear { appeared = false }
        .onChange(of: savedWork.key) { _, _ in restoreSavedWork() }
        .onChange(of: active) { _, active in if active { restoreSavedWork() } }
        .screenLoadState("review", hasContent: store.review != nil, error: store.errors["Navigation"]?.note) { await store.refresh() }
        .navigationTitle("").navigationBarTitleDisplayMode(.inline)
        .background(PanelsTheme.Web.surface).panelsScreen()
        .task(id: "\(store.baseURL):\(active):\(store.connectionSession):\(savedWork.key?.revision ?? -1):\(session.upcoming(Self.readAhead).map(\.id).joined(separator: ","))") {
            guard active, snapshotCurrent else { return }
            await readAhead()
        }
    }

    @ViewBuilder private var content: some View {
        if savedWork.key?.server != server { Color.clear }
        else if let item = session.shown {
            ReviewCardScreen(item: item, presentation: session.exits, session: session, savedWork: savedWork, isActive: active, cardHeight: $cardHeight)
                .id("\(item.id)#\(session.exits)").transition(.identity)
        } else if session.loaded {
            ReviewSummaryView(summary: session.summary, workingCount: session.runningWorkerCount, isActive: active) {
                Haptics.selection()
                session.returnToCards(store: store)
            }
        } else {
            ScrollView { Color.clear.frame(height: 1) }.panelsRefreshable { await store.pull() }
        }
    }

    /// The bar's one action: the shown card's Ticket or conversation, or Chief from the summary.
    @ViewBuilder private func topBar(_ textEntry: ScreenTextEntryContext) -> some View {
        HStack {
            Spacer()
            if let route = barRoute {
                barButton(route.title) { textEntry.endEditingBeforeNavigation { openRoute(route.route) } }
                    .accessibilityIdentifier(route.identifier)
            }
        }
        .padding(.leading, 72).padding(.trailing, 14)
        .frame(minHeight: 52)
    }
    private var barRoute: (title: String, route: AppRoute, identifier: String)? {
        guard savedWork.key?.server == server else { return nil }
        guard let item = session.shown else {
            return session.loaded ? ("Open Chief", .chief, "review.openChief") : nil
        }
        if let ticket = item.owningTicketID { return ("Open ticket", .ticket(ticket), "review.openTicket") }
        guard let owner = item.owner, let id = item.conversationId else { return nil }
        return ("Open conversation", .conversation(owner: owner, id: id), "review.openConversation")
    }
    @ViewBuilder private func barButton(_ title: String, action: @escaping () -> Void) -> some View {
        let button = Button(action: action) { Text(title).panelsScaledFont(size: 13.5, weight: .medium) }
            .foregroundStyle(PanelsTheme.Web.text)
        if #available(iOS 26.0, *) { button.buttonStyle(.glass).buttonBorderShape(.capsule) }
        else { button.buttonStyle(.bordered).buttonBorderShape(.capsule) }
    }

    private func restoreSavedWork() {
        guard active, snapshotCurrent else { return }
        for decision in session.decisions.all { decision.refreshVoiceDraft() }
        session.messageDecisions.refreshAttempts()
    }

    /// Keep the next cards ready before the current one leaves: a proposal with its
    /// manifest, a message with its unread run, an ask with its wording. Each read is
    /// also saved on the phone, so a card read ahead still works offline.
    private func readAhead() async {
        let api = store.client
        var loads: [@MainActor @Sendable () async -> Void] = []
        for item in session.upcoming(Self.readAhead) {
            switch item.reviewItemType {
            case "message":
                let decision = session.messageDecisions.decision(for: item, savedWork: savedWork)
                if !decision.isCurrent(for: item) { loads.append { await decision.load(item: item, api: api) } }
            case "ask":
                let decision = session.askDecisions.decision(for: item)
                let viewFreshAt = store.viewFreshAt
                if !decision.loaded { loads.append { await decision.load(item: item, api: api, viewFreshAt: viewFreshAt) } }
            default:
                let decision = session.decisions.decision(for: item)
                let stamp = store.ticketStamp(item.ticketId)
                if decision.proposal == nil { loads.append { await decision.load(item: item, api: api); decision.loadedStamp = stamp } }
            }
        }
        await withTaskGroup(of: Void.self) { group in
            for (index, load) in loads.enumerated() {
                if index >= 3 { await group.next() }
                group.addTask { await load() }
            }
        }
        session.retainLoaded()
    }
}

/// One card: its title and recap, its kind's content and controls, the swipe that skips
/// it, its entrance and its exit.
private struct ReviewCardScreen: View {
    let item: ReviewItem
    let presentation: Int
    let session: ReviewSession
    let savedWork: ReviewSavedWork
    let isActive: Bool
    @Binding var cardHeight: CGFloat
    @Environment(AppStore.self) private var store
    @State private var motion = ReviewCardMotion()

    var body: some View {
        switch item.reviewItemType {
        case "message":
            ReviewMessageCard(item: item, isActive: isActive, decision: session.messageDecisions.decision(for: item, savedWork: savedWork),
                              refusal: session.refusalNotes[item.id], motion: motion, onSkip: skip, onResolve: resolve)
        case "ask":
            ReviewAskCard(item: item, isActive: isActive, decision: session.askDecisions.decision(for: item),
                          motion: motion, onSkip: skip, onResolve: resolve)
        default:
            ReviewProposalCard(item: item, isActive: isActive, savedWork: savedWork, decision: session.decisions.decision(for: item),
                               refusal: session.refusalNotes[item.id], notice: session.notice?.item.id == item.id ? session.notice : nil,
                               cardHeight: $cardHeight, motion: motion, onSkip: skip, onHold: { if session.presents(item, at: presentation) { session.resolve(item) } }, onResolve: resolve)
        }
    }

    private func skip(_ swipeWidth: CGFloat?) {
        motion.leave(swipeWidth: swipeWidth) {
            guard session.presents(item, at: presentation) else { return }
            var transaction = Transaction(animation: nil)
            transaction.disablesAnimations = true
            withTransaction(transaction) { session.skip(item, store: store) }
        }
    }

    /// Handled here: the card leaves at once, and the next one follows its exit.
    private func resolve(_ afterExit: (() -> Void)?) {
        // A card already leaving by Skip finishes that exit instead.
        guard session.presents(item, at: presentation), !motion.leaving else { afterExit?(); return }
        session.resolve(item)
        motion.leave(swipeWidth: nil) {
            afterExit?()
            guard session.presents(item, at: presentation) else { return }
            var transaction = Transaction(animation: nil)
            transaction.disablesAnimations = true
            withTransaction(transaction) { session.finishExit(item) }
            Task { await store.refresh() }
        }
    }
}

/// A card's entrance, its swipe and its exit.
@MainActor @Observable final class ReviewCardMotion {
    var drag: CGFloat = 0
    var crossedThreshold = false
    private(set) var leaving = false
    var visible = false
    var reduceMotion = false

    func leave(swipeWidth: CGFloat?, then action: @escaping () -> Void) {
        guard !leaving else { return }
        let duration = swipeWidth == nil || reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.swipeOut
        withAnimation(.easeOut(duration: duration)) {
            leaving = true
            if let swipeWidth, !reduceMotion { drag = -swipeWidth }
        }
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(duration))
            action()
        }
    }
}

/// The frame every card shares: the scrolling title, recap and content; the controls
/// docked below; Skip by a leftward swipe past 30% of the width.
struct ReviewCardFrame<Content: View, Dock: View>: View {
    let title: String
    let recap: String?
    let motion: ReviewCardMotion
    /// True while an action is under way, so the swipe waits.
    let locked: Bool
    let onSkip: (CGFloat?) -> Void
    var refresh: (@Sendable () async -> Void)? = nil
    @ViewBuilder let content: Content
    @ViewBuilder let dock: Dock
    @Environment(\.screenTextEntry) private var textEntry
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(AppStore.self) private var store

    @State private var selectingText = false
    @ScaledMetric(relativeTo: .title) private var titleSize: CGFloat = 23
    @ScaledMetric private var recapSize: CGFloat = 14

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    PanelsSelectableText(title, font: .systemFont(ofSize: titleSize, weight: .semibold),
                        color: UIColor(PanelsTheme.Web.strong), tracking: -0.37)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityAddTraits(.isHeader)
                        .modifier(DecisionForegroundFade())
                    if let recap, !recap.isEmpty {
                        PanelsSelectableText((try? AttributedString(markdown: recap, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(recap),
                            font: .systemFont(ofSize: recapSize), color: UIColor(PanelsTheme.Web.secondary), lineSpacing: 5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.top, 8)
                            .accessibilityLabel("Recap").accessibilityValue(recap)
                            .modifier(DecisionForegroundFade())
                    }
                    content.padding(.top, 18)
                }
                .environment(\.panelsTextSelectionChanged, { selected in
                    selectingText = selected
                    if selected { motion.drag = 0; motion.crossedThreshold = false }
                })
                .padding(.horizontal, 18).padding(.top, 10).padding(.bottom, 28)
                .offset(x: motion.drag)
                .opacity(motion.visible && !motion.leaving ? 1 : 0)
                .allowsHitTesting(!motion.leaving)
                .simultaneousGesture(DragGesture(minimumDistance: 24)
                    .onChanged { value in
                        guard !locked, !selectingText, !motion.leaving, abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                        motion.drag = min(0, value.translation.width)
                        let crossed = -motion.drag > geometry.size.width * ReviewStyle.swipeFraction
                        if crossed && !motion.crossedThreshold { Haptics.light() }
                        motion.crossedThreshold = crossed
                    }
                    .onEnded { value in
                        guard !locked, !selectingText, !motion.leaving else { return }
                        if -motion.drag > geometry.size.width * ReviewStyle.swipeFraction, abs(value.translation.width) > abs(value.translation.height) * 1.5 {
                            textEntry?.endEditing()
                            onSkip(geometry.size.width)
                        } else { withAnimation(.smooth(duration: ReviewStyle.enter, extraBounce: 0)) { motion.drag = 0 } }
                        motion.crossedThreshold = false
                    })
            }
            .accessibilityIdentifier("review-content")
            .gesture(TapGesture().onEnded { textEntry?.endEditing() })
            .scrollDismissesKeyboard(.interactively)
            .scrollDisabled(motion.leaving)
            .modifier(DecisionScrollViewport())
            .safeAreaInset(edge: .bottom, spacing: 0) { dock.opacity(motion.leaving ? 0 : 1).allowsHitTesting(!motion.leaving) }
            .panelsRefreshable { [store, refresh] in await store.refresh(); await refresh?() }
        }
        .onAppear {
            motion.reduceMotion = reduceMotion
            withAnimation(.timingCurve(0.2, 0.7, 0.3, 1, duration: reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.fadeIn)) { motion.visible = true }
        }
    }
}
