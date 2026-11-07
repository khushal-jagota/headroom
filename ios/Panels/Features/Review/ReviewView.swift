import SwiftUI

struct ReviewView: View {
    var openTicket: ((String) -> Void)? = nil
    @Environment(AppStore.self) private var store
    @State private var queue = ReviewQueue()
    @State private var displayedItem: ReviewItem?
    @State private var decisions: [String: ReviewDecision] = [:]
    @State private var messageDecisions = ReviewMessageDecisions()
    @State private var reviewPage: String?
    @State private var cardHeight: CGFloat = 180
    @State private var emptyVisible = false
    private var savedDecisions: [PendingWork] {
        Outbox.shared.entries(LocalStore.server(store.baseURL)).filter { $0.kind == .approval || $0.kind == .sendBack }
    }
    private var queueItems: [ReviewItem] {
        (store.review?.items ?? []).filter { item in
            !savedDecisions.contains { $0.ticketID == item.ticketId && $0.proposal?.field == item.field && $0.proposal?.createdAt == item.waitingSince && [.waiting, .sending, .uncertain, .sent].contains($0.state) }
        }
    }
    /// On a later page the skipped cards of earlier pages are not loaded; they still count.
    private var skippedCount: Int { reviewPage == nil ? queue.skippedCount(in: queueItems) : queue.skipped.subtracting(queue.resolved).count }
    /// As on the web, only a page whose every loaded card was skipped moves on;
    /// resolved cards leave the server queue and the page refills by itself.
    private var pageSkipped: Bool { !queueItems.isEmpty && queueItems.allSatisfy { queue.skipped.contains($0.id) } }
    private var pending: [ReviewItem] { queueItems.filter { !queue.skipped.contains($0.id) && !queue.resolved.contains($0.id) } }

    var body: some View {
        Group {
            if let item = displayedItem ?? queue.current(in: queueItems) {
                if item.isMessage {
                    ReviewMessageView(item: item, decision: messageDecisions.decision(for: item), onSkip: { advance(item, resolved: false) }, onResolved: {
                        advance(item, resolved: true)
                        Task { await store.refresh() }
                    }).id(item.id).transition(.identity)
                } else {
                ReviewProposalView(item: item, decision: decisions[item.id] ?? ReviewDecision(), cardHeight: $cardHeight, openTicket: openTicket.map { action in { action(item.ticketId) } }, onSkip: { advance(item, resolved: false) }, onResolved: {
                    advance(item, resolved: true)
                    Task { await store.refresh() }
                }).id(item.id).transition(.identity)
                }
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
            if let item = displayedItem, !item.isMessage, !queueItems.contains(where: { $0.id == item.id }) { displayedItem = queue.current(in: queueItems) }
            for work in savedDecisions where work.state == .changed {
                if let item = queueItems.first(where: { $0.ticketId == work.ticketID }) {
                    queue.restore(item)
                    if decisions[item.id]?.resolved == true { decisions[item.id] = nil }
                    if displayedItem == nil { displayedItem = item }
                }
            }
        }
        .screenLoadState("review", hasContent: store.review != nil, error: store.errors["Navigation"]) { await store.refresh() }
        .navigationTitle("").navigationBarTitleDisplayMode(.inline).panelsScreen()
        .onChange(of: store.baseURL) { _, _ in queue = ReviewQueue(); displayedItem = nil; decisions = [:]; messageDecisions = ReviewMessageDecisions(); reviewPage = nil }
        // The review window holds 30 cards. Once every loaded one was skipped here,
        // the next page replaces it; Revisit returns to the first.
        .onChange(of: reviewPage, initial: true) { _, page in demandReview(page) }
        .onChange(of: pageSkipped) { _, skipped in if skipped { advancePage() } }
        .task(id: "\(store.baseURL):\(pending.prefix(30).map(\.id).joined(separator: ","))") {
            // Pin the visible ask until its exit finishes, including when the server
            // removes it from the live queue while its approval sheet is closing.
            if displayedItem == nil || (displayedItem?.isMessage == false && !queueItems.contains(where: { $0.id == displayedItem?.id })) { displayedItem = queue.current(in: queueItems) }
            await prefetch()
        }
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
                if !decision.loaded { loads.append { await decision.load(item: item, api: api) } }
            } else {
                let decision = decisions[item.id] ?? ReviewDecision()
                decisions[item.id] = decision
                if decision.proposal == nil { loads.append { await decision.load(item: item, api: api) } }
            }
        }
        await withTaskGroup(of: Void.self) { group in
            for (index, load) in loads.enumerated() {
                if index >= 3 { await group.next() }
                group.addTask { await load() }
            }
        }
        let ids = Set((store.review?.items ?? []).map(\.id))
        decisions = decisions.filter { ids.contains($0.key) || $0.key == displayedItem?.id }
        messageDecisions.retain(ids.union([displayedItem?.id].compactMap { $0 }))
    }

    private func advance(_ item: ReviewItem, resolved: Bool) {
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            if resolved { queue.resolve(item) } else { queue.skip(item) }
            // A revisited message reads its run again, as a freshly opened card does.
            if item.isMessage { messageDecisions.drop(item.id) }
            displayedItem = queue.current(in: queueItems)
        }
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

private struct ReviewProposalView: View {
    let item: ReviewItem
    @Bindable var decision: ReviewDecision
    @Binding var cardHeight: CGFloat
    let openTicket: (() -> Void)?
    let onSkip: () -> Void
    let onResolved: () -> Void
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
    @State private var observedRevision: Int?
    @State private var renaming = false
    @State private var titleDraft = ""
    private enum ReviewSheet: String, Identifiable { case approve; var id: String { rawValue } }
    private var fieldLabel: String { decision.manifest?.fields.first(where: { $0.id == item.field })?.label ?? item.field.capitalized }
    private var reviewer: String {
        switch decision.ticket?.ceilingHolder.kind { case "chief": "Chief"; case "manager": "Manager"; case "ticket": "Worker"; default: "User" }
    }

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                    Group {
                        if let openTicket { Button(action: openTicket) { ticketTitle } }
                        else { NavigationLink(value: AppRoute.ticket(item.ticketId)) { ticketTitle } }
                    }.buttonStyle(.plain).accessibilityIdentifier("review.openTicket")
                        .accessibilityHint("Opens ticket for review")
                        .contextMenu { Button("Rename", systemImage: "pencil") { titleDraft = decision.ticket?.title ?? item.title; renaming = true } }
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                        if let recap = decision.ticket?.recap, !recap.isEmpty {
                            Button { withAnimation(.easeOut(duration: PanelsTheme.Motion.base)) { recapExpanded.toggle() } } label: {
                                Text((try? AttributedString(markdown: recap)) ?? AttributedString(recap))
                                    .font(.subheadline).foregroundStyle(PanelsTheme.muted).lineLimit(recapExpanded || typeSize.isAccessibilitySize ? nil : 4)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }.buttonStyle(.plain).accessibilityLabel("Recap")
                                .accessibilityValue(recap)
                                .accessibilityHint(recapExpanded ? "Collapses recap" : "Expands recap")
                        }
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
                    }
                    if !returning { actionBar }
                }.padding(.horizontal, ReviewStyle.gutter).padding(.top, PanelsTheme.Space.md).padding(.bottom, PanelsTheme.Space.xl)
                    .offset(x: drag)
                    .opacity(visible && !leaving ? 1 : 0)
                    .simultaneousGesture(DragGesture(minimumDistance: 24)
                        .onChanged { value in
                            guard !editing, !returning, !decision.busy, !leaving, abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                            drag = min(0, value.translation.width)
                            let crossed = -drag > geometry.size.width * ReviewStyle.swipeFraction
                            if crossed && !crossedThreshold { Haptics.light() }
                            crossedThreshold = crossed
                        }
                        .onEnded { value in
                            guard !returning, !editing, !decision.busy, !leaving else { return }
                            if -drag > geometry.size.width * ReviewStyle.swipeFraction, abs(value.translation.width) > abs(value.translation.height) * 1.5 {
                                skip(width: geometry.size.width, button: false)
                            } else { withAnimation(.smooth(duration: ReviewStyle.enter, extraBounce: 0)) { drag = 0 } }
                            crossedThreshold = false
                        })
            }
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if returning {
                    actionBar.padding(.horizontal, ReviewStyle.gutter)
                        .padding(.top, PanelsTheme.Space.md)
                        .background(PanelsTheme.surface)
                }
            }
            .panelsRefreshable { await store.refresh(); await decision.load(item: item, api: store.client) }
            .toolbar { reviewToolbar }

        }
        .task(id: "\(store.baseURL):\(store.revision)") {
            guard sheet == nil, !editing, !returning, drag == 0 else { return }
            let firstAppearance = observedRevision == nil
            observedRevision = store.revision
            // A prefetched ask is ready to reveal; do not immediately dim its actions
            // with a redundant load. Subsequent server revisions still refresh it.
            if firstAppearance, decision.proposal != nil { return }
            await decision.load(item: item, api: store.client)
        }
        .onAppear { withAnimation(.timingCurve(0.2, 0.7, 0.3, 1, duration: reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.fadeIn)) { visible = true } }
        .alert("Rename ticket", isPresented: $renaming) {
            TextField("Title", text: $titleDraft)
            Button("Cancel", role: .cancel) {}
            Button("Save") { Haptics.light(); Task { if await decision.rename(api: store.client, title: titleDraft) { Haptics.success() } else { ReviewStyle.error() } } }
        }
        .sheet(item: $sheet, onDismiss: {
            if resolvedAfterDismiss { resolvedAfterDismiss = false; leave(resolved: true) }
        }) { selected in
            switch selected {
            case .approve:
                ReviewApprovalSheet(decision: decision) { ceiling, holder in
                    if await decision.approve(api: store.client, ceiling: ceiling, holder: holder) { complete() }
                    else { ReviewStyle.error() }
                }

            }
        }
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
                .disabled(decision.busy || leaving || returning)
                .accessibilityIdentifier("review.skip")
        }
    }

    private var ticketTitle: some View {
        Text(decision.ticket?.title ?? item.title).font(ReviewStyle.title)
            .fixedSize(horizontal: false, vertical: true).foregroundStyle(PanelsTheme.strong)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var proposalCard: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            Button { openApproval() } label: {
                (Text(fieldLabel).font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong) + Text(" — for \(reviewer) to approve").foregroundStyle(PanelsTheme.muted))
                    .font(.title3).frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain).accessibilityLabel("\(fieldLabel), for \(reviewer) to approve")
                .accessibilityHint("Opens approval options").disabled(!decision.ready)
            if editing {
                TextField("Proposal", text: $decision.draft, axis: .vertical).lineLimit(5...30)
                    .font(.body).lineSpacing(6).focused($editorFocused).accessibilityIdentifier("review.editor")
            } else {
                Button {
                    editing = true; editorFocused = true
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
        .accessibilityAction(named: "Skip") { skip(width: 12, button: true) }
    }

    private var actionBar: some View {
        Group {
            if returning {
                ReviewReturnComposer(decision: decision, fieldLabel: fieldLabel, api: store.client, onCancel: { changeActions(returning: false) }) {
                    if await decision.sendBack(api: store.client, message: decision.revisionDraft) { complete() }
                    else { ReviewStyle.error() }
                }
            } else {
                (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: PanelsTheme.Space.sm)) : AnyLayout(HStackLayout(spacing: PanelsTheme.Space.sm))) {
                    if decision.canReturn {
                        Button("Send back") {
                            Haptics.light(); editorFocused = false; editing = false
                            changeActions(returning: true)
                        }.accessibilityIdentifier("review.sendBack")
                    }
                    Button("Approve") { openApproval() }
                        .buttonStyle(ReviewActionStyle(approval: true))
                        .accessibilityIdentifier("review.approve")
                }
                .buttonStyle(ReviewActionStyle())
                .disabled(!decision.ready || leaving || changingActions)
                .opacity(decision.ready ? 1 : 0.45)
            }
        }.opacity(actionsVisible ? 1 : 0)
    }

    private func changeActions(returning next: Bool) {
        guard !changingActions else { return }
        changingActions = true
        withAnimation(.easeOut(duration: ReviewStyle.fadeOut)) { actionsVisible = false }
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(ReviewStyle.fadeOut))
            guard !Task.isCancelled else { return }
            returning = next
            withAnimation(.easeOut(duration: reduceMotion ? ReviewStyle.fadeOut : PanelsTheme.Motion.base)) { actionsVisible = true }
            changingActions = false
        }
    }
    private func openApproval() { guard decision.ready else { return }; Haptics.light(); editorFocused = false; editing = false; sheet = .approve }
    private func complete() {
        Haptics.light()
        if sheet != nil { resolvedAfterDismiss = true; sheet = nil }
        else { leave(resolved: true) }
    }
    private func skip(width: CGFloat, button: Bool) {
        guard !decision.busy, !leaving, !returning else { return }
        if button { Haptics.selection() }
        leave(resolved: false, swipeWidth: button ? nil : width)
    }
    private func leave(resolved: Bool, swipeWidth: CGFloat? = nil) {
        let duration = swipeWidth == nil || reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.swipeOut
        withAnimation(.easeOut(duration: duration)) {
            leaving = true
            if let swipeWidth, !reduceMotion { drag = -swipeWidth }
        }
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(duration))
            guard !Task.isCancelled else { return }
            if resolved { onResolved() } else { onSkip() }
        }
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
