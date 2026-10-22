import SwiftUI

struct ReviewView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var queue = ReviewQueue()

    private var current: ReviewItem? { queue.current(in: store.review?.items ?? []) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error = store.errors["Review"] {
                    LoadErrorView(message: error) { await store.refresh() }
                }
                if let item = current {
                    ReviewProposalView(item: item, onSkip: { advance(item, approved: false) }, onResolved: {
                        advance(item, approved: true)
                        Task { await store.refresh() }
                    })
                    .id(item.id)
                    .transition(reduceMotion ? .opacity : .asymmetric(
                        insertion: .opacity.combined(with: .offset(y: PanelsTheme.Space.sm)), removal: .opacity))
                } else if store.review != nil {
                    emptyState
                } else if store.errors["Review"] == nil {
                    ProgressView("Loading Review").frame(maxWidth: .infinity, minHeight: 240)
                }
            }
            .padding(PanelsTheme.Space.xl)
        }
        .navigationTitle("Review")
        .navigationBarTitleDisplayMode(.inline)
        .panelsScreen()
        .refreshable { Haptics.light(); await store.refresh() }
        .onChange(of: store.baseURL) { _, _ in queue = ReviewQueue() }
    }

    private var emptyState: some View {
        VStack(spacing: PanelsTheme.Space.lg) {
            Image(systemName: "checkmark.circle")
                .font(.system(size: 36, weight: .light)).foregroundStyle(PanelsTheme.success)
            Text("Nothing to review right now").font(PanelsTheme.TypeScale.heading)
            if let count = store.review?.runningWorkerCount {
                Text("\(count) \(count == 1 ? "agent" : "agents") in progress")
                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            }
            if !(store.review?.items.isEmpty ?? true), !queue.skipped.isEmpty {
                Button("Revisit skipped items") {
                    Haptics.light()
                    withAnimation(.easeOut(duration: PanelsTheme.Motion.base)) { queue.revisit() }
                }.padding(.top, PanelsTheme.Space.sm)
            }
        }.frame(maxWidth: .infinity).padding(.vertical, PanelsTheme.Space.hero * 2)
    }

    private func advance(_ item: ReviewItem, approved: Bool) {
        withAnimation(.easeOut(duration: reduceMotion ? 0 : PanelsTheme.Motion.base)) {
            if approved { queue.resolve(item) } else { queue.skip(item) }
        }
    }
}

private struct ReviewProposalView: View {
    let item: ReviewItem
    let onSkip: () -> Void
    let onResolved: () -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var decision = ReviewDecision()
    @State private var sheet: ReviewSheet?
    @State private var drag: CGFloat = 0
    @State private var crossedThreshold = false

    private enum ReviewSheet: String, Identifiable { case edit, approve, sendBack; var id: String { rawValue } }
    private let swipeThreshold: CGFloat = 84

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
            HStack {
                Button { skip() } label: { Label("Skip", systemImage: "arrow.right") }
                    .accessibilityIdentifier("review.skip")
                    .disabled(decision.busy)
                Spacer()
                NavigationLink(value: AppRoute.ticket(item.ticketId)) {
                    HStack(spacing: PanelsTheme.Space.xs) {
                        Text("Open ticket"); Image(systemName: "chevron.right")
                    }
                }.accessibilityIdentifier("review.openTicket")
            }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)

            Text(decision.ticket?.title ?? item.title)
                .font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong)
                .accessibilityAddTraits(.isHeader)
            if let recap = decision.ticket?.recap, !recap.isEmpty {
                ReviewProse(text: recap).foregroundStyle(PanelsTheme.muted)
            }

            if decision.proposal != nil {
                proposalCard
            } else if decision.loading {
                ProgressView("Loading proposal").frame(maxWidth: .infinity, minHeight: 180)
            }
            if let error = decision.error {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
                    Button(decision.changed ? "Reload proposal" : "Try again") {
                        Haptics.light()
                        Task {
                            await store.refresh()
                            await decision.load(item: item, api: store.client, discardDraft: decision.changed)
                        }
                    }.disabled(decision.busy)
                }
            }
        }
        .task(id: "\(store.baseURL):\(store.revision)") {
            guard sheet == nil else { return }
            await decision.load(item: item, api: store.client)
        }
        .sheet(item: $sheet) { selected in
            switch selected {
            case .edit:
                ReviewEditor(title: "Edit proposal", initialText: decision.draft, actionTitle: "Done") { text in
                    decision.draft = text; Haptics.light(); sheet = nil
                }
            case .approve:
                ReviewApprovalSheet(decision: decision) { ceiling, holder in
                    if await decision.approve(api: store.client, ceiling: ceiling, holder: holder) {
                        Haptics.success(); sheet = nil; onResolved()
                    }
                }
            case .sendBack:
                ReviewReturnSheet(decision: decision) { message in
                    if await decision.sendBack(api: store.client, message: message) {
                        Haptics.success(); sheet = nil; onResolved()
                    }
                }
            }
        }
    }

    private var proposalCard: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                    Text(decision.manifest?.fields.first(where: { $0.id == item.field })?.label ?? item.field.capitalized)
                        .font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong)
                    Text("For approval").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                }
                Spacer()
                Button { Haptics.light(); sheet = .edit } label: { Image(systemName: "pencil") }
                    .accessibilityLabel("Edit proposal").accessibilityIdentifier("review.edit")
                    .frame(minWidth: 44, minHeight: 44)
                    .disabled(decision.busy || decision.loading || decision.changed)
            }
            ReviewProse(text: decision.draft)
            HStack(spacing: PanelsTheme.Space.md) {
                Button { openApproval() } label: {
                    Label("Approve", systemImage: "checkmark").frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
                .tint(PanelsTheme.accent).foregroundStyle(PanelsTheme.ink)
                .accessibilityIdentifier("review.approve").disabled(!decision.ready)
                if decision.proposal?.field != "brief" {
                    Button("Send it back") { Haptics.light(); sheet = .sendBack }
                        .font(PanelsTheme.TypeScale.small)
                        .disabled(!decision.ready || !decision.canReturn)
                }
            }
            if decision.proposal?.field != "brief", !decision.canReturn {
                Text("No worker conversation to send a revision to.")
                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            }
        }
        .padding(PanelsTheme.Space.lg)
        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
        .overlay(alignment: drag > 0 ? .leading : .trailing) {
            if abs(drag) > 20 {
                Image(systemName: drag > 0 ? "checkmark.circle.fill" : "arrow.right.circle.fill")
                    .foregroundStyle(drag > 0 ? PanelsTheme.success : PanelsTheme.accent)
                    .padding(PanelsTheme.Space.sm).opacity(min(abs(drag) / swipeThreshold, 1))
                    .accessibilityHidden(true)
            }
        }
        .offset(x: reduceMotion ? 0 : drag * 0.25)
        .simultaneousGesture(DragGesture(minimumDistance: 24)
            .onChanged { value in
                guard !decision.busy, abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                drag = value.translation.width
                let crossed = abs(drag) >= swipeThreshold
                if crossed && !crossedThreshold { Haptics.light() }
                crossedThreshold = crossed
            }
            .onEnded { value in
                let horizontal = abs(value.translation.width) > abs(value.translation.height) * 1.5
                if horizontal && value.translation.width < -swipeThreshold { skip() }
                else if horizontal && value.translation.width > swipeThreshold { openApproval() }
                withAnimation(.easeOut(duration: PanelsTheme.Motion.base)) { drag = 0 }
                crossedThreshold = false
            })
        .accessibilityAction(named: "Approve") { openApproval() }
        .accessibilityAction(named: "Skip") { skip() }
    }

    private func skip() { guard !decision.busy else { return }; Haptics.light(); onSkip() }
    private func openApproval() { guard decision.ready else { return }; Haptics.light(); sheet = .approve }
}

/// Preserve Markdown paragraph boundaries while using native selectable text and links.
private struct ReviewProse: View {
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            ForEach(Array(text.components(separatedBy: "\n\n").enumerated()), id: \.offset) { _, paragraph in
                Text((try? AttributedString(markdown: paragraph, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(paragraph))
                    .font(PanelsTheme.TypeScale.body).lineSpacing(4).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

private struct ReviewEditor: View {
    let title: String
    let actionTitle: String
    let onSave: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text: String
    init(title: String, initialText: String, actionTitle: String, onSave: @escaping (String) -> Void) {
        self.title = title; self.actionTitle = actionTitle; self.onSave = onSave
        _text = State(initialValue: initialText)
    }
    var body: some View {
        NavigationStack {
            TextEditor(text: $text).font(PanelsTheme.TypeScale.body).padding(PanelsTheme.Space.lg)
                .scrollContentBackground(.hidden).background(PanelsTheme.surface)
                .accessibilityLabel(title).accessibilityIdentifier("review.editor")
                .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { Haptics.light(); dismiss() } }
                    ToolbarItem(placement: .confirmationAction) { Button(actionTitle) { onSave(text) } }
                }
        }.presentationDragIndicator(.visible)
    }
}

private struct ReviewApprovalSheet: View {
    @Bindable var decision: ReviewDecision
    let onApprove: (String, Principal) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var ceiling = ""
    @State private var holder = Principal(kind: "owner", id: "owner")

    private var holders: [(String, Principal)] {
        var values = [("User", Principal(kind: "owner", id: "owner")), ("Chief of Staff", Principal(kind: "chief", id: "chief"))]
        if let ticket = decision.ticket {
            if let id = ticket.managerId { values.append((ticket.managerTitle ?? "Orchestrator", Principal(kind: "manager", id: id))) }
            if !values.contains(where: { $0.1 == ticket.ceilingHolder }) { values.append((ticket.ceilingHolder.id, ticket.ceilingHolder)) }
        }
        return values
    }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Continue until", selection: $ceiling) {
                        ForEach(decision.approvalStages) { Text($0.label).tag($0.id) }
                    }
                    Picker("Then ask", selection: $holder) {
                        ForEach(holders, id: \.1) { Text($0.0).tag($0.1) }
                    }
                }
                Section {
                    Button {
                        Haptics.light()
                        Task { await onApprove(ceiling, holder) }
                    } label: {
                        HStack { Spacer(); if decision.busy { ProgressView() } else { Text("Approve") }; Spacer() }
                    }.disabled(!decision.ready || ceiling.isEmpty).accessibilityIdentifier("review.confirmApproval")
                }
                if let error = decision.error { Text(error).foregroundStyle(PanelsTheme.error) }
            }.panelsScreen().navigationTitle("Approve proposal").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { Haptics.light(); dismiss() }.disabled(decision.busy) } }
        }
        .onAppear { ceiling = decision.approvalStages.first?.id ?? ""; holder = decision.ticket?.ceilingHolder ?? holder }
        .onChange(of: ceiling) { _, _ in Haptics.selection() }
        .onChange(of: holder) { _, _ in Haptics.selection() }
        .presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
        .interactiveDismissDisabled(decision.busy)
    }
}

private struct ReviewReturnSheet: View {
    @Bindable var decision: ReviewDecision
    let onReturn: (String) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var message = ""
    var body: some View {
        NavigationStack {
            Form {
                Section("What needs changing") {
                    TextEditor(text: $message).frame(minHeight: 160).accessibilityLabel("What needs changing")
                }
                if let error = decision.error { Text(error).foregroundStyle(PanelsTheme.error) }
                Button {
                    Haptics.light(); Task { await onReturn(message) }
                } label: {
                    HStack { Spacer(); if decision.busy { ProgressView() } else { Text("Send it back") }; Spacer() }
                }.disabled(!decision.ready || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }.panelsScreen().navigationTitle("Send it back").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { Haptics.light(); dismiss() }.disabled(decision.busy) } }
        }.presentationDragIndicator(.visible).interactiveDismissDisabled(decision.busy)
    }
}
