import SwiftUI

struct ReviewView: View {
    let savedWork: ReviewSavedWork
    var isActive = true
    @State private var appeared = false
    private var active: Bool { isActive && appeared && scenePhase == .active && !store.serverUnreachable }
    var openTicket: ((String) -> Void)? = nil
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var queue = ReviewQueue()
    /// The card on screen, held while its parts finish their exits.
    @State private var displayedCard: ReviewCard?
    @State private var decisions: [String: ReviewDecision] = [:]
    @State private var messageDecisions = ReviewMessageDecisions()
    @State private var reviewPage: String?
    @State private var cardHeight: CGFloat = 180
    @State private var emptyVisible = false
    private var server: String { LocalStore.server(store.baseURL) }
    private var snapshotCurrent: Bool { savedWork.key == ReviewSavedWork.Key(server: server, revision: LocalStore.shared.outgoingRevision(server)) }
    private var savedDecisions: [OutgoingStore.Summary] { savedWork.decisions(server) }
    /// Live cards without the proposals a saved decision will clear.
    private var queueCards: [ReviewCard] { ReviewSavedWork.cards(store.review?.cards ?? [], decisions: savedDecisions) }
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
            !queue.resolved.contains(part.id) && (part.isMessage || decisions[part.id]?.resolved == true || decisions[part.id]?.scopeChange.holdingProposal == true)
        }
        let live = (queueCards.first { $0.id == card.id }?.parts ?? []).filter { part in
            !queue.resolved.contains(part.id) && !held.contains { $0.id == part.id || ($0.isMessage && $0.conversationId == part.conversationId) }
        }
        let all = live + held
        return all.filter { !$0.isMessage } + all.filter(\.isMessage)
    }

    var body: some View {
        Group {
            if savedWork.key?.server != server { Color.clear }
            else if let card = shownCard {
                let parts = parts(of: card)
                ReviewCardView(card: card, parts: parts, isActive: active, savedWork: savedWork, decision: parts.first { !$0.isMessage }.map { decisions[$0.id] ?? ReviewDecision() },
                               messageDecision: { messageDecisions.decision(for: $0, savedWork: savedWork) }, cardHeight: $cardHeight,
                               openTicket: openTicket, onSkip: { skip(card) }, onResolved: { resolve($0, in: card) })
                    .id(card.id).transition(.identity)
            } else if store.review != nil {
                GeometryReader { geometry in
                    ScrollView {
                        VStack(spacing: PanelsTheme.Space.xl) {
                            VStack(spacing: PanelsTheme.Space.sm) {
                                Text("You’re caught up.").font(ReviewStyle.empty).foregroundStyle(PanelsTheme.strong)
                                Text("All done for now.").font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.faint)
                            }
                            .multilineTextAlignment(.center)
                            ReviewChiefMessage(isActive: active)
                            if skippedCount > 0 {
                                Button("Revisit \(skippedCount) skipped \(skippedCount == 1 ? "item" : "items")") {
                                    Haptics.selection()
                                    withAnimation(ReviewStyle.entrance) { queue.revisit(); reviewPage = nil }
                                }
                                .buttonStyle(ReviewActionStyle())
                                .accessibilityIdentifier("review.revisitSkipped")
                            }
                        }
                        .padding(.horizontal, ReviewStyle.gutter)
                        .padding(.vertical, PanelsTheme.Space.xl)
                        .frame(maxWidth: .infinity, minHeight: geometry.size.height)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .panelsRefreshable { await store.refresh() }
                }
                .opacity(emptyVisible ? 1 : 0)
                .task { withAnimation(.easeOut(duration: ReviewStyle.fadeOut)) { emptyVisible = true } }
                .onDisappear { emptyVisible = false }
            } else { ScrollView { Color.clear.frame(height: 1) }.panelsRefreshable { await store.refresh() } }
        }
        .onAppear { appeared = true }
        .onDisappear { appeared = false }
        .onChange(of: savedWork.key) { _, _ in restoreSavedWork() }
        .onChange(of: active) { _, active in if active { restoreSavedWork() } }
        .screenLoadState("review", hasContent: store.review != nil, error: store.errors["Navigation"]?.message) { await store.refresh() }
        .navigationTitle("").navigationBarTitleDisplayMode(.inline).toolbar(.hidden, for: .navigationBar)
        .background(PanelsTheme.Web.surface).panelsScreen()
        .onChange(of: store.baseURL) { _, _ in queue = ReviewQueue(); displayedCard = nil; decisions = [:]; messageDecisions = ReviewMessageDecisions(); reviewPage = nil }
        // The review window holds 30 cards. Once every loaded one was skipped here,
        // the next page replaces it; Revisit returns to the first.
        .onChange(of: reviewPage, initial: true) { _, page in demandReview(page) }
        .onChange(of: pageSkipped) { _, skipped in if skipped { advancePage() } }
        .task(id: "\(store.baseURL):\(active):\(savedWork.key?.revision ?? -1):\(pending.prefix(30).map(\.id).joined(separator: ","))") {
            guard active, snapshotCurrent else { return }
            holdShownCard()
            await prefetch()
        }
    }

    private func restoreSavedWork() {
        guard active, snapshotCurrent else { return }
        for decision in decisions.values { decision.refreshVoiceDraft() }
        messageDecisions.refreshAttempts()
        for work in savedDecisions where work.state == "changed" {
            if let card = queueCards.first(where: { $0.ticketId == work.ticketID }),
               let item = card.parts.first(where: { !$0.isMessage && $0.field == work.proposalField && $0.waitingSince == work.proposalCreatedAt && $0.proposalRevision != nil && $0.proposalRevision == work.proposalRevision }),
               let proposal = decisions[item.id]?.proposal,
               proposal.field == work.proposalField, proposal.revision == work.proposalRevision, proposal.createdAt == work.proposalCreatedAt {
                queue.restore(item)
                if decisions[item.id]?.resolved == true { decisions[item.id] = nil }
                if displayedCard == nil { displayedCard = card }
            }
        }
        holdShownCard()
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
                let decision = messageDecisions.decision(for: item, savedWork: savedWork)
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
    func decision(for item: ReviewItem, savedWork: ReviewSavedWork) -> ReviewMessageDecision {
        if let existing = decisions[item.id] { return existing }
        let decision = ReviewMessageDecision(refreshSavedWork: savedWork.refresh)
        decisions[item.id] = decision
        return decision
    }
    func refreshAttempts() { for decision in decisions.values { decision.refreshAttempt() } }
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
    let isActive: Bool
    let savedWork: ReviewSavedWork
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
    @State private var drag: CGFloat = 0
    @State private var crossedThreshold = false
    @State private var leaving = false
    @State private var visible = false
    @State private var busyAction: String?
    @State private var resolvedAfterDismiss = false
    @State private var approvalGuidance: DecisionGuidance?
    /// Parts fading out while the rest of the card stays.
    @State private var exiting: Set<String> = []
    @State private var messageStates: [String: ReviewMessageSection.Activity] = [:]
    private enum ReviewSheet: String, Identifiable { case approve, reviewer; var id: String { rawValue } }
    private var proposal: ReviewItem? { parts.first { !$0.isMessage } }
    private var messages: [ReviewItem] { parts.filter(\.isMessage) }
    private var messagesLocked: Bool { messages.contains { messageStates[$0.id]?.locked == true } }
    private var skipLocked: Bool { leaving || busyAction != nil || decision?.busy == true || messagesLocked }
    private var recap: String? { decision?.ticket?.recap ?? messages.first?.recap }

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                    header
                    if let proposal, let decision {
                        ReviewProposalSection(isActive: isActive, item: proposal, decision: decision, cardHeight: $cardHeight,
                                              canReload: sheet == nil && busyAction == nil && drag == 0,
                                              onReviewer: { if decision.ready && !leaving && busyAction == nil { decision.approvalFlow = DecisionApprovalFlow(); sheet = .reviewer } })
                            .opacity(exiting.contains(proposal.id) ? 0 : busyAction != nil ? 0.4 : 1)
                    }
                    ForEach(messages) { message in
                        ReviewMessageSection(item: message, isActive: isActive, decision: messageDecision(message), locked: leaving || exiting.contains(message.id),
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
                            guard !skipLocked, abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                            drag = min(0, value.translation.width)
                            let crossed = -drag > geometry.size.width * ReviewStyle.swipeFraction
                            if crossed && !crossedThreshold { Haptics.light() }
                            crossedThreshold = crossed
                        }
                        .onEnded { value in
                            guard !skipLocked else { return }
                            if -drag > geometry.size.width * ReviewStyle.swipeFraction, abs(value.translation.width) > abs(value.translation.height) * 1.5 {
                                skip(width: geometry.size.width, button: false)
                            } else { withAnimation(.smooth(duration: ReviewStyle.enter, extraBounce: 0)) { drag = 0 } }
                            crossedThreshold = false
                        })
            }
            .scrollDismissesKeyboard(.interactively)
            .scrollDisabled(leaving || messages.contains { messageStates[$0.id]?.sending == true })
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if let proposal, let decision {
                    ReviewDecisionDock(isActive: isActive, savedWork: savedWork, decision: decision,
                                       api: store.client, busyAction: busyAction, ready: !leaving && sheet == nil,
                                       onApprove: openApproval) {
                        busyAction = "back"
                        if await decision.sendBack(api: store.client, message: decision.revisionDraft) { complete() }
                        else { busyAction = nil; ReviewStyle.error() }
                    }
                    .opacity(exiting.contains(proposal.id) ? 0 : 1)
                }
            }
            .panelsRefreshable { await refresh() }
        }
        .onAppear { withAnimation(.timingCurve(0.2, 0.7, 0.3, 1, duration: reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.fadeIn)) { visible = true } }
        // A part that left and comes back, such as a proposal whose saved decision
        // found it changed, shows again.
        .onChange(of: parts.map(\.id)) { _, ids in exiting.formIntersection(ids) }
        .sheet(item: $sheet, onDismiss: {
            decision?.scopeChange.releasePresentation()
            if resolvedAfterDismiss { resolvedAfterDismiss = false; resolveProposal() }
        }) { selected in
            if let decision, let ticket = decision.ticket {
                DecisionApprovalSheet(stages: decision.approvalStages, nextStage: decision.nextStage ?? "",
                                      holder: ticket.ceilingHolder, managerID: ticket.managerId,
                                      reviewerOnly: selected == .reviewer,
                                      checkCurrent: decision.scopeChange.needsCheck ? {
                                          let current = try await decision.scopeChange.check(online: Outbox.shared.networkAvailable && !store.serverUnreachable)
                                          decision.acceptScopeRead(current)
                                          await store.refresh()
                                      } : nil,
                                      confirmation: { decision.scopeChange.holdingProposal ? decision.scopeChange.message : nil }, flow: decision.approvalFlow) { ceiling, holder in
                    if selected == .reviewer || ceiling.map({ decision.manifest?.selectionApprovesPending(ceiling: $0, waitingStage: ticket.stage) == false }) == true {
                        let current = try await decision.scopeChange.save(api: store.client, ticket: ticket,
                            ceiling: selected == .reviewer ? nil : ceiling, holder: holder,
                            online: Outbox.shared.networkAvailable && !store.serverUnreachable)
                        decision.acceptScopeRead(current)
                        await store.refresh()
                    } else if let ceiling {
                        busyAction = "approve"
                        if await decision.approve(api: store.client, ceiling: ceiling, holder: holder, guidance: approvalGuidance) { complete() }
                        else {
                            busyAction = nil
                            throw APIError(status: 0, message: decision.error ?? "The decision could not be saved.")
                        }
                    }
                }
            }
        }
    }

    @MainActor private func refresh() async {
        await store.refresh()
        if let proposal, let decision { await decision.load(item: proposal, api: store.client) }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
        HStack(spacing: 20) {
            Spacer()
            Button("Skip") { skip(width: 12, button: true) }.disabled(skipLocked).accessibilityIdentifier("review.skip")
            if !card.ticketId.isEmpty {
                if let openTicket {
                    Button("Open ticket") { openTicket(card.ticketId) }.accessibilityIdentifier("review.openTicket")
                } else {
                    NavigationLink("Open ticket", value: AppRoute.ticket(card.ticketId)).accessibilityIdentifier("review.openTicket")
                }
            }
        }.buttonStyle(.plain).panelsScaledFont(size: 13.5, weight: .medium)
            .foregroundStyle(PanelsTheme.Web.secondary).frame(minHeight: 44)
        Text(decision?.ticket?.title ?? card.title).panelsScaledFont(size: 26, relativeTo: .title, weight: .semibold)
            .tracking(-0.26).lineSpacing(1).fixedSize(horizontal: false, vertical: true)
            .foregroundStyle(PanelsTheme.Web.strong).frame(maxWidth: .infinity, alignment: .leading)
        if let recap, !recap.isEmpty {
            Text((try? AttributedString(markdown: recap)) ?? AttributedString(recap))
                .panelsScaledFont(size: 14).lineSpacing(6).foregroundStyle(PanelsTheme.Web.text)
                .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                .accessibilityLabel("Recap").accessibilityValue(recap)
        }
        }
    }

    private func openApproval() {
        guard let decision, decision.ready else { return }
        approvalGuidance = decision.approvalGuidance(api: store.client)
        Haptics.light()
        if decision.nextStage == "done", let holder = decision.ticket?.ceilingHolder {
            busyAction = "approve"
            Task {
                if await decision.approve(api: store.client, ceiling: "done", holder: holder, guidance: approvalGuidance) { complete() }
                else { busyAction = nil; ReviewStyle.error() }
            }
        } else { decision.approvalFlow = DecisionApprovalFlow(); sheet = .approve }
    }
    private func complete() {
        Haptics.light()
        if sheet != nil { resolvedAfterDismiss = true; sheet = nil }
        else { resolveProposal() }
    }
    private func resolveProposal() {
        guard let proposal else { return }
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(260))
            guard !Task.isCancelled else { return }
            onResolved(proposal)
            busyAction = nil
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

/// Review retains its proposal owner and uses the same read-only card as Ticket.
private struct ReviewProposalSection: View {
    let isActive: Bool
    let item: ReviewItem
    @Bindable var decision: ReviewDecision
    @Binding var cardHeight: CGFloat
    let canReload: Bool
    let onReviewer: () -> Void
    @Environment(AppStore.self) private var store
    @State private var appeared = false
    private var reviewer: String {
        switch decision.ticket?.ceilingHolder.kind { case "chief": "Chief"; case "manager": "Manager"; case "ticket": "Worker"; default: "User" }
    }
    private func loadProposal() async {
        let stamp = store.ticketStamp(item.ticketId)
        await decision.load(item: item, api: store.client)
        decision.loadedStamp = stamp
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let proposal = decision.proposal {
                DecisionProposalCard(fieldLabel: decision.fieldLabel(item.field), reviewer: reviewer,
                                     text: proposal.body, baseURL: store.baseURL, onReviewer: onReviewer)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { cardHeight = $0 }
                ForEach(ReviewArtifactLinks.targets(in: proposal.body, baseURL: store.baseURL)) { target in ReviewInlineArtifact(target: target) }
            } else if decision.error == nil { ScreenLoadingView(title: "review").frame(height: cardHeight) }
            if decision.error != nil {
                Button(decision.changed ? "Reload proposal" : "Try again") {
                    Task { await store.refresh(); await decision.load(item: item, api: store.client, discardDraft: decision.changed) }
                }.disabled(decision.busy).panelsScaledFont(size: 12.5)
            }
        }
        .task(id: "\(store.baseURL)|\(store.connectionSession)|\(isActive)") {
            guard isActive else { return }
            let firstAppearance = !appeared; appeared = true
            if firstAppearance, decision.proposal != nil, decision.loadedStamp == store.ticketStamp(item.ticketId) { return }
            guard canReload else { return }
            await loadProposal()
        }
        .reload(onChangeOf: store.ticketStamp(item.ticketId)) { guard isActive, canReload else { return }; await loadProposal() }
    }
}

private struct ReviewDecisionDock: View {
    let isActive: Bool
    let savedWork: ReviewSavedWork
    @Bindable var decision: ReviewDecision
    let api: APIClient
    let busyAction: String?
    let ready: Bool
    let onApprove: () -> Void
    let onReturn: () async -> Void
    @State private var voice = ReviewVoiceCapture()
    @State private var focusRequest = 0
    @State private var blurRequest = 0
    @ScaledMetric(relativeTo: .body) private var microphoneSize: CGFloat = 18
    var body: some View {
        VStack(spacing: 0) {
            if voice.phase != .idle { voiceStatus.padding(.horizontal, 20).padding(.top, 8).background(PanelsTheme.Web.surface) }
            DecisionNoteActions(note: $decision.revisionDraft, showsSendBack: decision.proposal?.field != "brief",
                                canReturn: decision.canReturn, ready: ready && decision.ready,
                                busyAction: busyAction, error: decision.error ?? voice.error ?? decision.scopeChange.message,
                                noteEnabled: voice.phase == .idle,
                                accessory: AnyView(Button {
                                    Haptics.light(); blurRequest += 1
                                    Task { await voice.start(api: api, draftID: decision.revisionDraftID) }
                                } label: {
                                    Image(systemName: "mic").resizable().scaledToFit()
                                        .frame(width: min(microphoneSize, 24), height: min(microphoneSize, 24))
                                        .frame(width: 44, height: 44).contentShape(Rectangle())
                                }
                                    .buttonStyle(.plain).foregroundStyle(PanelsTheme.Web.secondary)
                                    .accessibilityLabel("Record a revision").disabled(decision.busy || voice.phase != .idle)),
                                focusRequest: focusRequest, blurRequest: blurRequest,
                                onApprove: onApprove, onSendBack: { Task { await onReturn() } })
        }
            .onChange(of: isActive, initial: true) { _, active in
                guard active else { voice.preserve(); return }
                savedWork.refresh(LocalStore.server(api.baseURL))
                decision.refreshVoiceDraft()
                voice.restore(api: api, draftID: decision.revisionDraftID, savedWork: decision.revisionDraftID.map { savedWork.voice(LocalStore.server(api.baseURL), draftID: $0) } ?? [])
            }
            .onChange(of: savedWork.key) { _, _ in
                let server = LocalStore.server(api.baseURL)
                guard isActive, savedWork.key == ReviewSavedWork.Key(server: server, revision: LocalStore.shared.outgoingRevision(server)) else { return }
                decision.refreshVoiceDraft()
                voice.restore(api: api, draftID: decision.revisionDraftID, savedWork: decision.revisionDraftID.map { savedWork.voice(LocalStore.server(api.baseURL), draftID: $0) } ?? [])
            }
            .onDisappear { voice.preserve() }
    }
    @ViewBuilder private var voiceStatus: some View {
        HStack(spacing: PanelsTheme.Space.md) {
            Button { Haptics.selection(); voice.cancel(); focusRequest += 1 } label: { Image(systemName: "xmark").frame(width: 44, height: 44) }.accessibilityLabel("Cancel recording")
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
        focusRequest += 1
    }
}
