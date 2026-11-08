import SwiftUI

struct ReviewView: View {
    var openTicket: ((String) -> Void)? = nil
    @Environment(AppStore.self) private var store
    @State private var queue = ReviewQueue()
    /// The card on screen, held while its parts finish their exits.
    @State private var displayedCard: ReviewCard?
    @State private var decisions: [String: ReviewDecision] = [:]
    @State private var messageDecisions = ReviewMessageDecisions()
    @State private var reviewPage: String?
    @State private var cardHeight: CGFloat = 180
    @State private var emptyVisible = false
    private var savedDecisions: [PendingWork] {
        Outbox.shared.entries(LocalStore.server(store.baseURL)).filter { $0.kind == .approval || $0.kind == .sendBack }
    }
    /// Live cards without the proposals a saved decision will clear.
    private var queueCards: [ReviewCard] {
        (store.review?.cards ?? []).compactMap { card in
            let parts = card.parts.filter { item in
                !savedDecisions.contains { $0.ticketID == item.ticketId && $0.proposal?.field == item.field && $0.proposal?.createdAt == item.waitingSince && [.waiting, .sending, .uncertain, .sent].contains($0.state) }
            }
            return parts.isEmpty ? nil : ReviewCard(id: card.id, ticketId: card.ticketId, parts: parts)
        }
    }
    /// On a later page the skipped cards of earlier pages are not loaded; they still count.
    private var skippedCount: Int { reviewPage == nil ? queue.skippedCount(in: queueCards) : queue.skippedCardCount }
    /// As on the web, only a page whose every loaded card was skipped moves on;
    /// resolved cards leave the server queue and the page refills by itself.
    private var pageSkipped: Bool { !queueCards.isEmpty && queueCards.allSatisfy(queue.isSkipped) }
    private var pending: [ReviewItem] { queueCards.filter { !queue.isSkipped($0) }.flatMap(queue.pending) }
    private var shownCard: ReviewCard? {
        if let card = displayedCard, !parts(of: card).isEmpty { return card }
        return queue.current(in: queueCards)
    }

    /// The shown card's parts, proposal first: its live parts, a message that left the
    /// live queue while on screen, and a proposal decided here whose exit has not finished.
    private func parts(of card: ReviewCard) -> [ReviewItem] {
        let held = card.parts.filter { part in
            !queue.resolved.contains(part.id) && (part.isMessage || decisions[part.id]?.resolved == true)
        }
        let live = (queueCards.first { $0.id == card.id }?.parts ?? []).filter { part in
            !queue.resolved.contains(part.id) && !held.contains { $0.id == part.id || ($0.isMessage && $0.conversationId == part.conversationId) }
        }
        let all = live + held
        return all.filter { !$0.isMessage } + all.filter(\.isMessage)
    }

    var body: some View {
        Group {
            if let card = shownCard {
                let parts = parts(of: card)
                ReviewCardView(card: card, parts: parts, decision: parts.first { !$0.isMessage }.map { decisions[$0.id] ?? ReviewDecision() },
                               messageDecision: messageDecisions.decision(for:), cardHeight: $cardHeight,
                               openTicket: openTicket, onSkip: { skip(card) }, onResolved: { resolve($0, in: card) })
                    .id(card.id).transition(.identity)
            } else if store.review != nil {
                GeometryReader { geometry in
                    ScrollView {
                        VStack(spacing: PanelsTheme.Space.sm) {
                            Text("You’re caught up.").font(ReviewStyle.empty).foregroundStyle(PanelsTheme.strong)
                            Text("All done for now.").font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.faint)
                        }
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity, minHeight: geometry.size.height)
                        .padding(.horizontal, ReviewStyle.gutter)
                    }.panelsRefreshable { await store.refresh() }
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if skippedCount > 0 {
                        Button("Revisit \(skippedCount) skipped \(skippedCount == 1 ? "item" : "items")") {
                            Haptics.selection()
                            withAnimation(ReviewStyle.entrance) { queue.revisit(); reviewPage = nil }
                        }
                        .buttonStyle(ReviewActionStyle())
                        .accessibilityIdentifier("review.revisitSkipped")
                        .padding(.horizontal, ReviewStyle.gutter)
                        .padding(.vertical, PanelsTheme.Space.xl)
                    }
                }
                .opacity(emptyVisible ? 1 : 0)
                .task { withAnimation(.easeOut(duration: ReviewStyle.fadeOut)) { emptyVisible = true } }
                .onDisappear { emptyVisible = false }
            } else { ScrollView { Color.clear.frame(height: 1) }.panelsRefreshable { await store.refresh() } }
        }
        .onChange(of: LocalStore.shared.workRevision) { _, _ in
            for decision in decisions.values { decision.refreshVoiceDraft() }
            for work in savedDecisions where work.state == .changed {
                if let card = queueCards.first(where: { $0.ticketId == work.ticketID }), let item = card.parts.first(where: { !$0.isMessage }) {
                    queue.restore(item)
                    if decisions[item.id]?.resolved == true { decisions[item.id] = nil }
                    if displayedCard == nil { displayedCard = card }
                }
            }
            holdShownCard()
        }
        .screenLoadState("review", hasContent: store.review != nil, error: store.errors["Navigation"]?.message) { await store.refresh() }
        .navigationTitle("").navigationBarTitleDisplayMode(.inline).panelsScreen()
        .onChange(of: store.baseURL) { _, _ in queue = ReviewQueue(); displayedCard = nil; decisions = [:]; messageDecisions = ReviewMessageDecisions(); reviewPage = nil }
        // The review window holds 30 cards. Once every loaded one was skipped here,
        // the next page replaces it; Revisit returns to the first.
        .onChange(of: reviewPage, initial: true) { _, page in demandReview(page) }
        .onChange(of: pageSkipped) { _, skipped in if skipped { advancePage() } }
        .task(id: "\(store.baseURL):\(pending.prefix(30).map(\.id).joined(separator: ","))") {
            holdShownCard()
            await prefetch()
        }
    }

    /// Keep the shown card on screen, with its current parts, until every part is
    /// handled or removed; then the next card replaces it.
    private func holdShownCard() {
        if let card = displayedCard, !parts(of: card).isEmpty {
            displayedCard = ReviewCard(id: card.id, ticketId: card.ticketId, parts: parts(of: card))
        } else { displayedCard = queue.current(in: queueCards) }
    }

    private func demandReview(_ page: String?) {
        store.setNavigationDemand("review", [ViewWindowRequest(section: "review", anchor: page, limit: NavigationScope.reviewWindowLimit)])
    }
    private func advancePage() {
        if let next = store.view?.window("review")?.next { reviewPage = next }
    }

    /// Keep the next complete asks ready before the current one leaves: proposals
    /// with their manifest, messages with their unread run. A few at a time.
    private func prefetch() async {
        let api = store.client
        var loads: [@MainActor @Sendable () async -> Void] = []
        for item in pending.prefix(30) {
            if item.isMessage {
                let decision = messageDecisions.decision(for: item)
                if !decision.isCurrent(for: item) { loads.append { await decision.load(item: item, api: api) } }
            } else {
                let decision = decisions[item.id] ?? ReviewDecision()
                decisions[item.id] = decision
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
        let ids = Set((store.review?.items ?? []).map(\.id)).union((displayedCard?.parts ?? []).map(\.id))
        decisions = decisions.filter { ids.contains($0.key) }
        messageDecisions.retain(ids)
    }

    private func skip(_ card: ReviewCard) {
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            let parts = parts(of: card)
            queue.skip(ReviewCard(id: card.id, ticketId: card.ticketId, parts: parts))
            // A revisited message reads its run again, as a freshly opened card does.
            for part in parts where part.isMessage { messageDecisions.drop(part.id) }
            displayedCard = queue.current(in: queueCards)
        }
    }

    /// One part is handled; the card stays on screen while any other part waits.
    private func resolve(_ part: ReviewItem, in card: ReviewCard) {
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            queue.resolve(part)
            if part.isMessage { messageDecisions.drop(part.id) }
            displayedCard = card
            holdShownCard()
        }
        Task { await store.refresh() }
    }
}

/// Message decisions keyed by item, shared by the visible card and the read-ahead.
/// Plain storage: creating one while the view renders publishes nothing.
@MainActor private final class ReviewMessageDecisions {
    private var decisions: [String: ReviewMessageDecision] = [:]
    func decision(for item: ReviewItem) -> ReviewMessageDecision {
        if let existing = decisions[item.id] { return existing }
        let decision = ReviewMessageDecision()
        decisions[item.id] = decision
        return decision
    }
    func retain(_ ids: Set<String>) { decisions = decisions.filter { ids.contains($0.key) } }
    func drop(_ id: String) { decisions[id] = nil }
}

private extension ReviewDecision {
    func fieldLabel(_ field: String) -> String { manifest?.fields.first(where: { $0.id == field })?.label ?? field.capitalized }
}

/// One card on one page, as desktop's review card: the title and recap once, the
/// proposal first, then each unread conversation. Skip and the swipe act on the card.
private struct ReviewCardView: View {
    let card: ReviewCard
    let parts: [ReviewItem]
    /// The proposal's decision, when the card holds a proposal.
    let decision: ReviewDecision?
    let messageDecision: (ReviewItem) -> ReviewMessageDecision
    @Binding var cardHeight: CGFloat
    let openTicket: ((String) -> Void)?
    let onSkip: () -> Void
    let onResolved: (ReviewItem) -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var sheet: ReviewSheet?
    @State private var editing = false
    @FocusState private var editorFocused: Bool
    @State private var recapExpanded = false
    @State private var drag: CGFloat = 0
    @State private var crossedThreshold = false
    @State private var leaving = false
    @State private var visible = false
    @State private var returning = false
    @State private var actionsVisible = true
    @State private var changingActions = false
    @State private var resolvedAfterDismiss = false
    @State private var renaming = false
    @State private var titleDraft = ""
    /// Parts fading out while the rest of the card stays.
    @State private var exiting: Set<String> = []
    @State private var messageStates: [String: ReviewMessageSection.Activity] = [:]
    private enum ReviewSheet: String, Identifiable { case approve; var id: String { rawValue } }
    private var proposal: ReviewItem? { parts.first { !$0.isMessage } }
    private var messages: [ReviewItem] { parts.filter(\.isMessage) }
    private var messagesLocked: Bool { messages.contains { messageStates[$0.id]?.locked == true } }
    private var skipLocked: Bool { leaving || returning || decision?.busy == true || messagesLocked }
    private var recap: String? { decision?.ticket?.recap ?? messages.first?.recap }

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                    header
                    if let proposal, let decision {
                        ReviewProposalSection(item: proposal, decision: decision, cardHeight: $cardHeight, editing: $editing, editorFocused: $editorFocused,
                                              returning: returning, actionsVisible: actionsVisible, actionsLocked: leaving || changingActions,
                                              canReload: sheet == nil && !returning && drag == 0,
                                              onApprove: openApproval, onSendBack: { changeActions(returning: true) }, onSkip: { skip(width: 12, button: true) })
                            .opacity(exiting.contains(proposal.id) ? 0 : 1)
                    }
                    ForEach(messages) { message in
                        ReviewMessageSection(item: message, decision: messageDecision(message), locked: leaving || exiting.contains(message.id),
                                             onActivity: { messageStates[message.id] = $0 },
                                             onResolved: { afterExit in resolve(message, afterExit: afterExit) })
                            .opacity(exiting.contains(message.id) ? 0 : 1)
                            .padding(.top, message.id == messages.first?.id && proposal != nil ? PanelsTheme.Space.md : 0)
                    }
                }.padding(.horizontal, ReviewStyle.gutter).padding(.top, PanelsTheme.Space.md).padding(.bottom, PanelsTheme.Space.xl)
                    .offset(x: drag)
                    .opacity(visible && !leaving ? 1 : 0)
                    .allowsHitTesting(!leaving)
                    .simultaneousGesture(DragGesture(minimumDistance: 24)
                        .onChanged { value in
                            guard !editing, !skipLocked, abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                            drag = min(0, value.translation.width)
                            let crossed = -drag > geometry.size.width * ReviewStyle.swipeFraction
                            if crossed && !crossedThreshold { Haptics.light() }
                            crossedThreshold = crossed
                        }
                        .onEnded { value in
                            guard !editing, !skipLocked else { return }
                            if -drag > geometry.size.width * ReviewStyle.swipeFraction, abs(value.translation.width) > abs(value.translation.height) * 1.5 {
                                skip(width: geometry.size.width, button: false)
                            } else { withAnimation(.smooth(duration: ReviewStyle.enter, extraBounce: 0)) { drag = 0 } }
                            crossedThreshold = false
                        })
            }
            .scrollDismissesKeyboard(.interactively)
            .scrollDisabled(leaving || messages.contains { messageStates[$0.id]?.sending == true })
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if returning, let proposal, let decision {
                    ReviewReturnComposer(decision: decision, fieldLabel: decision.fieldLabel(proposal.field), api: store.client, onCancel: { changeActions(returning: false) }) {
                        if await decision.sendBack(api: store.client, message: decision.revisionDraft) { complete() }
                        else { ReviewStyle.error() }
                    }
                    .opacity(actionsVisible ? 1 : 0)
                    .padding(.horizontal, ReviewStyle.gutter)
                    .padding(.top, PanelsTheme.Space.md)
                    .background(PanelsTheme.surface)
                }
            }
            .panelsRefreshable { await refresh() }
            .toolbar { reviewToolbar }
        }
        .onAppear { withAnimation(.timingCurve(0.2, 0.7, 0.3, 1, duration: reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.fadeIn)) { visible = true } }
        // A part that left and comes back, such as a proposal whose saved decision
        // found it changed, shows again.
        .onChange(of: parts.map(\.id)) { _, ids in exiting.formIntersection(ids) }
        .alert("Rename ticket", isPresented: $renaming) {
            TextField("Title", text: $titleDraft)
            Button("Cancel", role: .cancel) {}
            Button("Save") { Haptics.light(); Task { if await decision?.rename(api: store.client, title: titleDraft) == true { Haptics.success() } else { ReviewStyle.error() } } }
        }
        .sheet(item: $sheet, onDismiss: {
            if resolvedAfterDismiss { resolvedAfterDismiss = false; resolveProposal() }
        }) { selected in
            switch selected {
            case .approve:
                if let decision {
                    ReviewApprovalSheet(decision: decision) { ceiling, holder in
                        if await decision.approve(api: store.client, ceiling: ceiling, holder: holder) { complete() }
                        else { ReviewStyle.error() }
                    }
                }
            }
        }
    }

    @MainActor private func refresh() async {
        await store.refresh()
        if let proposal, let decision { await decision.load(item: proposal, api: store.client) }
    }

    @ViewBuilder private var header: some View {
        if card.ticketId.isEmpty {
            ticketTitle
        } else {
            Group {
                if let openTicket { Button { openTicket(card.ticketId) } label: { ticketTitle } }
                else { NavigationLink(value: AppRoute.ticket(card.ticketId)) { ticketTitle } }
            }.buttonStyle(.plain).accessibilityIdentifier("review.openTicket")
                .accessibilityHint("Opens ticket for review")
                .contextMenu {
                    if let decision, decision.ticket != nil {
                        Button("Rename", systemImage: "pencil") { titleDraft = decision.ticket?.title ?? card.title; renaming = true }
                    }
                }
        }
        if let recap, !recap.isEmpty {
            Button { withAnimation(.easeOut(duration: PanelsTheme.Motion.base)) { recapExpanded.toggle() } } label: {
                Text((try? AttributedString(markdown: recap)) ?? AttributedString(recap))
                    .font(.subheadline).foregroundStyle(PanelsTheme.muted).lineLimit(recapExpanded || typeSize.isAccessibilitySize ? nil : 4)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain).accessibilityLabel("Recap")
                .accessibilityValue(recap)
                .accessibilityHint(recapExpanded ? "Collapses recap" : "Expands recap")
        }
    }

    private var ticketTitle: some View {
        Text(decision?.ticket?.title ?? card.title).font(ReviewStyle.title)
            .fixedSize(horizontal: false, vertical: true).foregroundStyle(PanelsTheme.strong)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ToolbarContentBuilder private var reviewToolbar: some ToolbarContent {
        if #available(iOS 26.0, *) {
            ToolbarItem(placement: .topBarTrailing) { toolbarAction }
                .sharedBackgroundVisibility(.hidden)
        } else {
            ToolbarItem(placement: .topBarTrailing) { toolbarAction }
        }
    }

    @ViewBuilder private var toolbarAction: some View {
        if editing {
            Button("Done") { editorFocused = false; editing = false }.buttonStyle(.plain)
        } else {
            Button("Skip") { skip(width: 12, button: true) }
                .buttonStyle(.plain).foregroundStyle(PanelsTheme.muted)
                .disabled(skipLocked)
                .accessibilityIdentifier("review.skip")
        }
    }

    private func changeActions(returning next: Bool) {
        guard !changingActions else { return }
        changingActions = true
        editorFocused = false; editing = false
        withAnimation(.easeOut(duration: ReviewStyle.fadeOut)) { actionsVisible = false }
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(ReviewStyle.fadeOut))
            guard !Task.isCancelled else { return }
            returning = next
            withAnimation(.easeOut(duration: reduceMotion ? ReviewStyle.fadeOut : PanelsTheme.Motion.base)) { actionsVisible = true }
            changingActions = false
        }
    }
    private func openApproval() { guard decision?.ready == true else { return }; Haptics.light(); editorFocused = false; editing = false; sheet = .approve }
    private func complete() {
        Haptics.light()
        if sheet != nil { resolvedAfterDismiss = true; sheet = nil }
        else { resolveProposal() }
    }
    private func resolveProposal() {
        guard let proposal else { return }
        resolve(proposal) {
            returning = false; actionsVisible = true
        }
    }
    private func skip(width: CGFloat, button: Bool) {
        guard !skipLocked else { return }
        if button { Haptics.selection() }
        leave(swipeWidth: button ? nil : width, then: onSkip)
    }
    /// The last waiting part takes the whole card with it; another part fades alone.
    private func resolve(_ part: ReviewItem, afterExit: (() -> Void)? = nil) {
        let last = parts.allSatisfy { $0.id == part.id || exiting.contains($0.id) }
        if last {
            leave { afterExit?(); onResolved(part) }
        } else {
            withAnimation(.easeOut(duration: ReviewStyle.fadeOut)) { _ = exiting.insert(part.id) }
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(ReviewStyle.fadeOut))
                guard !Task.isCancelled else { return }
                withAnimation(.easeOut(duration: PanelsTheme.Motion.base)) { afterExit?(); onResolved(part) }
            }
        }
    }
    private func leave(swipeWidth: CGFloat? = nil, then action: @escaping () -> Void) {
        guard !leaving else { return }
        let duration = swipeWidth == nil || reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.swipeOut
        withAnimation(.easeOut(duration: duration)) {
            leaving = true
            if let swipeWidth, !reduceMotion { drag = -swipeWidth }
        }
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(duration))
            guard !Task.isCancelled else { return }
            action()
        }
    }
}

/// A proposal inside its card: the field, the editable text, its links and its actions.
private struct ReviewProposalSection: View {
    let item: ReviewItem
    @Bindable var decision: ReviewDecision
    @Binding var cardHeight: CGFloat
    @Binding var editing: Bool
    var editorFocused: FocusState<Bool>.Binding
    let returning: Bool
    let actionsVisible: Bool
    let actionsLocked: Bool
    /// False while the approval sheet, the return composer or a swipe is active.
    let canReload: Bool
    let onApprove: () -> Void
    let onSendBack: () -> Void
    let onSkip: () -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var appeared = false
    private var fieldLabel: String { decision.fieldLabel(item.field) }
    private var reviewer: String {
        switch decision.ticket?.ceilingHolder.kind { case "chief": "Chief"; case "manager": "Manager"; case "ticket": "Worker"; default: "User" }
    }
    private func loadProposal() async {
        let stamp = store.ticketStamp(item.ticketId)
        await decision.load(item: item, api: store.client)
        decision.loadedStamp = stamp
    }

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
            if decision.proposal != nil {
                proposalCard
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { cardHeight = $0 }
                    .transition(.opacity)
            } else if decision.error == nil { ScreenLoadingView(title: "review").frame(height: cardHeight) }
            if decision.proposal?.field != "brief", decision.proposal != nil, !decision.canReturn {
                Text("No existing Worker conversation to send a revision to.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            }
            if let error = decision.error {
                Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
                Button(decision.changed ? "Reload proposal" : "Try again") {
                    Task { await store.refresh(); await decision.load(item: item, api: store.client, discardDraft: decision.changed) }
                }.disabled(decision.busy)
            }
            if !returning { actionBar }
        }
        .task(id: "\(store.baseURL)|\(store.connectionSession)") {
            let firstAppearance = !appeared
            appeared = true
            // A prefetched ask is ready to reveal; do not immediately dim its actions
            // with a redundant load, unless its Ticket changed after the read-ahead. A later
            // change, a return to the foreground or a recovered connection reloads it.
            if firstAppearance, decision.proposal != nil, decision.loadedStamp == store.ticketStamp(item.ticketId) { return }
            guard canReload, !editing else { return }
            await loadProposal()
        }
        .reload(onChangeOf: store.ticketStamp(item.ticketId)) {
            guard canReload, !editing else { return }
            await loadProposal()
        }
    }

    private var proposalCard: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            Button { onApprove() } label: {
                (Text(fieldLabel).font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong) + Text(" — for \(reviewer) to approve").foregroundStyle(PanelsTheme.muted))
                    .font(.title3).frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain).accessibilityLabel("\(fieldLabel), for \(reviewer) to approve")
                .accessibilityHint("Opens approval options").disabled(!decision.ready)
            if editing {
                TextField("Proposal", text: $decision.draft, axis: .vertical).lineLimit(5...30)
                    .font(.body).lineSpacing(6).focused(editorFocused).accessibilityIdentifier("review.editor")
            } else {
                Button {
                    editing = true; editorFocused.wrappedValue = true
                } label: { ReviewProse(text: decision.draft, baseURL: store.baseURL) }
                    .buttonStyle(.plain).accessibilityLabel("Proposal").accessibilityValue(decision.draft)
                    .accessibilityHint("Edits proposal").accessibilityIdentifier("review.edit")
                    .disabled(decision.busy || decision.changed)
                ForEach(ReviewArtifactLinks.targets(in: decision.draft, baseURL: store.baseURL)) { target in ReviewInlineArtifact(target: target) }
            }
        }
        .reviewArtifactLinks()
        .padding(ReviewStyle.gutter)
        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
        .overlay(alignment: .top) { RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(Color.white.opacity(0.05), lineWidth: 1) }
        .shadow(color: .black.opacity(0.12), radius: 20, y: 8)
        .accessibilityAction(named: "Skip") { onSkip() }
    }

    private var actionBar: some View {
        (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: PanelsTheme.Space.sm)) : AnyLayout(HStackLayout(spacing: PanelsTheme.Space.sm))) {
            if decision.canReturn {
                Button("Send back") { Haptics.light(); onSendBack() }.accessibilityIdentifier("review.sendBack")
            }
            Button("Approve") { onApprove() }
                .buttonStyle(ReviewActionStyle(approval: true))
                .accessibilityIdentifier("review.approve")
        }
        .buttonStyle(ReviewActionStyle())
        .disabled(!decision.ready || actionsLocked)
        .opacity(decision.ready ? 1 : 0.45)
        .opacity(actionsVisible ? 1 : 0)
    }
}

private struct ReviewProse: View {
    let text: String
    let baseURL: URL
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            ForEach(Array(text.components(separatedBy: "\n\n").enumerated()), id: \.offset) { _, paragraph in
                Text((try? AttributedString(markdown: paragraph, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace), baseURL: baseURL)) ?? AttributedString(paragraph))
                    .font(.body).lineSpacing(6).foregroundStyle(PanelsTheme.text).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

private struct ReviewApprovalSheet: View {
    @Bindable var decision: ReviewDecision
    let onApprove: (String, Principal) async -> Void
    @State private var ceiling = ""
    @State private var holder = Principal(kind: "owner", id: "owner")
    private var holders: [(String, Principal)] {
        var values = [("User", Principal(kind: "owner", id: "owner"))]
        if let id = decision.ticket?.managerId { values.append(("Manager", Principal(kind: "manager", id: id))) }
        values.append(("Chief", Principal(kind: "chief", id: "chief")))
        return values
    }
    private var selectedLabel: String { decision.approvalStages.first(where: { $0.id == ceiling })?.label ?? "…" }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                Text("How far").reviewEyebrow()
                VStack(spacing: 0) {
                    ForEach(decision.approvalStages) { stage in
                        Button { ceiling = stage.id } label: {
                            HStack { Text(stage.label).foregroundStyle(PanelsTheme.text); Spacer(); if ceiling == stage.id { Image(systemName: "checkmark").foregroundStyle(PanelsTheme.accent) } }
                                .frame(minHeight: 46).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                            .accessibilityLabel(stage.label)
                            .accessibilityAddTraits(ceiling == stage.id ? .isSelected : [])
                        Divider().overlay(PanelsTheme.border)
                    }
                }
                Text("Who reviews").reviewEyebrow().padding(.top, PanelsTheme.Space.sm)
                Picker("Who reviews", selection: $holder) {
                    ForEach(holders, id: \.1) { Text($0.0).tag($0.1) }
                }.accessibleSegmentedPicker()
                Button {
                    Haptics.light(); Task { await onApprove(ceiling, holder) }
                } label: {
                    HStack { Spacer(); if decision.busy { RingSpinner(color: PanelsTheme.ink) } else { Text("Approve until \(selectedLabel)") }; Spacer() }.frame(minHeight: ReviewStyle.actionHeight)
                }.buttonStyle(ReviewActionStyle(approval: true))
                    .accessibilityLabel("Approve until \(selectedLabel)")
                    .accessibilityValue(decision.busy ? "Approving" : "")
                    .disabled(!decision.ready || ceiling.isEmpty).accessibilityIdentifier("review.confirmApproval")
                if let error = decision.error { Text(error).font(.footnote).foregroundStyle(PanelsTheme.error) }
            }.padding(ReviewStyle.gutter).padding(.top, PanelsTheme.Space.lg)
        }.background(PanelsTheme.raised)
        .onAppear { ceiling = decision.approvalStages.first?.id ?? ""; if let current = decision.ticket?.ceilingHolder, holders.contains(where: { $0.1 == current }) { holder = current } }
        .onChange(of: ceiling) { _, _ in Haptics.selection() }.onChange(of: holder) { _, _ in Haptics.selection() }
        .presentationDetents([.medium, .large]).presentationDragIndicator(.visible).interactiveDismissDisabled(decision.busy)
    }
}

private struct ReviewReturnComposer: View {
    @Bindable var decision: ReviewDecision
    let fieldLabel: String
    let api: APIClient
    let onCancel: () -> Void
    let onReturn: () async -> Void
    @State private var voice = ReviewVoiceCapture()
    @FocusState private var focused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            Text("Send back · \(fieldLabel)".uppercased()).reviewEyebrow()
            HStack(alignment: .bottom) {
            if voice.phase == .idle { TextField("Tell the worker what to change…", text: $decision.revisionDraft, axis: .vertical)
                .lineLimit(1...6).font(.body).focused($focused).accessibilityLabel("Changes for the worker").accessibilityIdentifier("review.revision")
                .disabled(voice.phase != .idle || decision.busy)
            } else { voiceStatus }
            if voice.phase == .idle {
                Button { Haptics.light(); focused = false; Task { await voice.start(api: api, draftID: decision.revisionDraftID) } } label: { Image(systemName: "mic").frame(width: 44, height: 44) }
                    .buttonStyle(.plain).foregroundStyle(PanelsTheme.muted).accessibilityLabel("Record a revision").disabled(decision.busy)
            }
            }.padding(.leading, PanelsTheme.Space.md)
                .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
                .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(PanelsTheme.border))
            HStack(spacing: PanelsTheme.Space.md) {
                Button("Cancel") { Haptics.selection(); focused = false; voice.cancel(); onCancel() }
                    .buttonStyle(ReviewActionStyle()).disabled(decision.busy)
                    .accessibilityIdentifier("review.cancelReturn")
                Button {
                    Haptics.light(); focused = false; Task { await onReturn() }
                } label: {
                    HStack { Spacer(); if decision.busy { RingSpinner(color: PanelsTheme.ink) } else { Text("Send back") }; Spacer() }.frame(minHeight: 44)
                }.buttonStyle(ReviewActionStyle(approval: true))
                    .disabled(!decision.ready || decision.revisionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || voice.phase != .idle)
                    .opacity(decision.revisionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.45 : 1)
                    .accessibilityLabel("Send back")
                    .accessibilityValue(decision.busy ? "Sending revision to worker" : "")
                    .accessibilityIdentifier("review.confirmReturn")
            }
            if let error = decision.error ?? voice.error { Text(error).font(.footnote).foregroundStyle(PanelsTheme.error) }
        }.padding(.bottom, PanelsTheme.Space.md)
            .onAppear { decision.refreshVoiceDraft(); focused = true; voice.restore(api: api, draftID: decision.revisionDraftID) }
            .onChange(of: LocalStore.shared.workRevision) { _, _ in decision.refreshVoiceDraft(); voice.restore(api: api, draftID: decision.revisionDraftID) }
            .onDisappear { voice.preserve() }
    }
    @ViewBuilder private var voiceStatus: some View {
        HStack(spacing: PanelsTheme.Space.md) {
            Button { Haptics.selection(); voice.cancel(); focused = true } label: { Image(systemName: "xmark").frame(width: 44, height: 44) }.accessibilityLabel("Cancel recording")
            Spacer()
            switch voice.phase {
            case .recording:
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let elapsed = max(0, Int(context.date.timeIntervalSince(voice.startedAt)))
                    Text("recording \(elapsed / 60):\(String(format: "%02d", elapsed % 60))").monospacedDigit()
                }
                Spacer()
                Button { Haptics.light(); Task { append(await voice.stop(api: api)) } } label: { Image(systemName: "stop.fill").frame(width: 44, height: 44) }.background(PanelsTheme.accent, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg)).foregroundStyle(PanelsTheme.ink).accessibilityLabel("Stop and transcribe")
            case .transcribing: Text("transcribing"); Spacer(); RingSpinner()
            case .failed:
                Text("recording saved on this phone").font(.footnote)
                Spacer()
                Button("Retry") { Task { append(await voice.transcribe(api: api)) } }
            case .idle: EmptyView()
            }
        }.foregroundStyle(PanelsTheme.muted)
    }
    private func append(_ transcript: String?) {
        if transcript != nil { decision.refreshVoiceDraft() }
        focused = true
    }
}

private extension Text {
    func reviewEyebrow() -> some View { self.font(PanelsTheme.TypeScale.reviewEyebrow).foregroundStyle(PanelsTheme.faint).accessibilityAddTraits(.isHeader) }
}
