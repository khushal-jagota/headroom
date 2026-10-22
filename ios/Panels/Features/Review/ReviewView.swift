import SwiftUI

struct ReviewView: View {
    var openTicket: ((String) -> Void)? = nil
    @Environment(AppStore.self) private var store
    @State private var queue = ReviewQueue()

    var body: some View {
        Group {
            if let item = queue.current(in: store.review?.items ?? []) {
                ReviewProposalView(item: item, openTicket: openTicket.map { action in { action(item.ticketId) } }, onSkip: { advance(item, resolved: false) }, onResolved: {
                    advance(item, resolved: true)
                    Task { await store.refresh() }
                }).id(item.id).transition(.identity)
            } else if store.review != nil {
                ScrollView {
                    VStack(spacing: PanelsTheme.Space.lg) {
                        Circle().stroke(PanelsTheme.faint, lineWidth: 1.5).frame(width: 10, height: 10)
                        Text("There is nothing to review right now.").font(ReviewStyle.empty).multilineTextAlignment(.center)
                        if let count = store.review?.runningWorkerCount {
                            Text("\(count) \(count == 1 ? "agent" : "agents") in progress").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                        }
                        if !queue.skipped.isEmpty, !(store.review?.items.isEmpty ?? true) {
                            Button("Revisit skipped items") { Haptics.selection(); withAnimation(ReviewStyle.entrance) { queue.revisit() } }
                        }
                    }.frame(maxWidth: .infinity).padding(.horizontal, ReviewStyle.gutter).padding(.top, PanelsTheme.Space.hero * 4)
                }.refreshable { await store.refresh() }
            } else { ProgressView("Loading Review") }
        }
        .overlay(alignment: .top) {
            if let error = store.errors["Review"] { LoadErrorView(message: error) { await store.refresh() }.padding() }
        }
        .navigationTitle("").navigationBarTitleDisplayMode(.inline).panelsScreen()
        .onChange(of: store.baseURL) { _, _ in queue = ReviewQueue() }
    }

    private func advance(_ item: ReviewItem, resolved: Bool) {
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            if resolved { queue.resolve(item) } else { queue.skip(item) }
        }
    }
}

private struct ReviewProposalView: View {
    let item: ReviewItem
    let openTicket: (() -> Void)?
    let onSkip: () -> Void
    let onResolved: () -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var decision = ReviewDecision()
    @State private var sheet: ReviewSheet?
    @State private var editing = false
    @FocusState private var editorFocused: Bool
    @State private var recapExpanded = false
    @State private var drag: CGFloat = 0
    @State private var crossedThreshold = false
    @State private var leaving = false
    @State private var renaming = false
    @State private var titleDraft = ""
    private enum ReviewSheet: String, Identifiable { case approve, sendBack; var id: String { rawValue } }
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
                        .contextMenu { Button("Rename", systemImage: "pencil") { titleDraft = decision.ticket?.title ?? item.title; renaming = true } }
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                        if let recap = decision.ticket?.recap, !recap.isEmpty {
                            Button { withAnimation(.easeOut(duration: PanelsTheme.Motion.base)) { recapExpanded.toggle() } } label: {
                                Text((try? AttributedString(markdown: recap)) ?? AttributedString(recap))
                                    .font(.subheadline).foregroundStyle(PanelsTheme.muted).lineLimit(recapExpanded ? nil : 4)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }.buttonStyle(.plain).accessibilityLabel(recapExpanded ? "Collapse recap" : "Expand recap")
                        }
                        if decision.proposal != nil { proposalCard }
                        else if decision.loading { ProgressView("Loading proposal").frame(maxWidth: .infinity, minHeight: 180) }
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
                    .animation(reduceMotion ? nil : ReviewStyle.entrance, value: decision.proposal != nil)
                    .offset(x: reduceMotion ? 0 : drag)
                    .opacity(leaving ? 0 : 1)
                    .simultaneousGesture(DragGesture(minimumDistance: 24)
                        .onChanged { value in
                            guard !editing, !decision.busy, !leaving, abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                            drag = min(0, value.translation.width)
                            let crossed = -drag > geometry.size.width * ReviewStyle.swipeFraction
                            if crossed && !crossedThreshold { Haptics.light() }
                            crossedThreshold = crossed
                        }
                        .onEnded { value in
                            if -drag > geometry.size.width * ReviewStyle.swipeFraction, abs(value.translation.width) > abs(value.translation.height) * 1.5 {
                                skip(width: geometry.size.width, button: false)
                            } else { withAnimation(.smooth(duration: ReviewStyle.enter, extraBounce: 0)) { drag = 0 } }
                            crossedThreshold = false
                        })
                }.padding(.horizontal, ReviewStyle.gutter).padding(.top, PanelsTheme.Space.md).padding(.bottom, PanelsTheme.Space.xl)
            }
            .refreshable { await store.refresh(); await decision.load(item: item, api: store.client) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if editing {
                        Button("Done") { editorFocused = false; editing = false }
                    } else {
                        Button("Skip") { skip(width: 12, button: true) }.foregroundStyle(PanelsTheme.muted).disabled(decision.busy || leaving).accessibilityIdentifier("review.skip")
                    }
                }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { actionBar }
        .task(id: "\(store.baseURL):\(store.revision)") {
            guard sheet == nil, !editing, drag == 0 else { return }
            await decision.load(item: item, api: store.client)
        }
        .alert("Rename ticket", isPresented: $renaming) {
            TextField("Title", text: $titleDraft)
            Button("Cancel", role: .cancel) {}
            Button("Save") { Haptics.light(); Task { if await decision.rename(api: store.client, title: titleDraft) { Haptics.success() } else { ReviewStyle.error() } } }
        }
        .sheet(item: $sheet) { selected in
            switch selected {
            case .approve:
                ReviewApprovalSheet(decision: decision) { ceiling, holder in
                    if await decision.approve(api: store.client, ceiling: ceiling, holder: holder) { complete() }
                    else { ReviewStyle.error() }
                }
            case .sendBack:
                ReviewReturnSheet(decision: decision, fieldLabel: fieldLabel, api: store.client) {
                    if await decision.sendBack(api: store.client, message: decision.revisionDraft) { complete() }
                    else { ReviewStyle.error() }
                }
            }
        }
    }

    private var ticketTitle: some View {
        Text(decision.ticket?.title ?? item.title).font(ReviewStyle.title).foregroundStyle(PanelsTheme.strong)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var proposalCard: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            Button { openApproval() } label: {
                (Text(fieldLabel).fontWeight(.semibold).foregroundStyle(PanelsTheme.strong) + Text(" — for \(reviewer) to approve").foregroundStyle(PanelsTheme.muted))
                    .font(.title3).frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain).accessibilityLabel("Who reviews: \(reviewer)").disabled(!decision.ready)
            if editing {
                TextField("Proposal", text: $decision.draft, axis: .vertical).lineLimit(5...30)
                    .font(.body).lineSpacing(6).focused($editorFocused).accessibilityIdentifier("review.editor")
            } else {
                Button {
                    editing = true; editorFocused = true
                } label: { ReviewProse(text: decision.draft) }
                    .buttonStyle(.plain).accessibilityLabel("Edit proposal").accessibilityIdentifier("review.edit")
                    .disabled(decision.busy || decision.loading || decision.changed)
            }
        }
        .padding(ReviewStyle.gutter)
        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
        .overlay(alignment: .top) { RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(Color.white.opacity(0.05), lineWidth: 1) }
        .shadow(color: .black.opacity(0.12), radius: 20, y: 8)
        .accessibilityAction(named: "Skip") { skip(width: 12, button: true) }
    }

    private var actionBar: some View {
        HStack(spacing: PanelsTheme.Space.sm) {
            Button("Approve") { openApproval() }
                .frame(maxWidth: .infinity, minHeight: ReviewStyle.actionHeight)
                .background(PanelsTheme.accent, in: Capsule()).foregroundStyle(PanelsTheme.ink)
                .accessibilityIdentifier("review.approve").disabled(!decision.ready || leaving)
                .opacity(decision.ready ? 1 : 0.45)
            if decision.canReturn {
                Button("Send back") { editorFocused = false; editing = false; sheet = .sendBack }
                    .foregroundStyle(PanelsTheme.muted).frame(minHeight: ReviewStyle.actionHeight)
                    .disabled(!decision.ready || leaving).accessibilityIdentifier("review.sendBack")
            }
        }.padding(.horizontal, ReviewStyle.gutter).padding(.vertical, PanelsTheme.Space.lg).background(PanelsTheme.surface)
    }
    private func openApproval() { guard decision.ready else { return }; editorFocused = false; editing = false; sheet = .approve }
    private func complete() { Haptics.success(); sheet = nil; onResolved() }
    private func skip(width: CGFloat, button: Bool) {
        guard !decision.busy, !leaving else { return }
        if button { Haptics.selection() }
        withAnimation(.easeOut(duration: PanelsTheme.Motion.base)) { leaving = true; drag = reduceMotion ? 0 : -width }
        Task { try? await Task.sleep(for: .seconds(PanelsTheme.Motion.base)); onSkip() }
    }
}

private struct ReviewProse: View {
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            ForEach(Array(text.components(separatedBy: "\n\n").enumerated()), id: \.offset) { _, paragraph in
                Text((try? AttributedString(markdown: paragraph, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(paragraph))
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
        if let current = decision.ticket?.ceilingHolder, !values.contains(where: { $0.1 == current }) { values.append(("Worker", current)) }
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
                        Divider().overlay(PanelsTheme.border)
                    }
                }
                Text("Who reviews").reviewEyebrow().padding(.top, PanelsTheme.Space.sm)
                Picker("Who reviews", selection: $holder) {
                    ForEach(holders, id: \.1) { Text($0.0).tag($0.1) }
                }.pickerStyle(.segmented)
                Button {
                    Haptics.light(); Task { await onApprove(ceiling, holder) }
                } label: {
                    HStack { Spacer(); if decision.busy { ProgressView().tint(PanelsTheme.ink) } else { Text("Approve until \(selectedLabel)") }; Spacer() }.frame(minHeight: ReviewStyle.actionHeight)
                }.background(PanelsTheme.accent, in: Capsule()).foregroundStyle(PanelsTheme.ink)
                    .disabled(!decision.ready || ceiling.isEmpty).accessibilityIdentifier("review.confirmApproval")
                if let error = decision.error { Text(error).font(.footnote).foregroundStyle(PanelsTheme.error) }
            }.padding(ReviewStyle.gutter).padding(.top, PanelsTheme.Space.lg)
        }.background(PanelsTheme.raised)
        .onAppear { ceiling = decision.approvalStages.first?.id ?? ""; holder = decision.ticket?.ceilingHolder ?? holder }
        .onChange(of: ceiling) { _, _ in Haptics.selection() }.onChange(of: holder) { _, _ in Haptics.selection() }
        .presentationDetents([.medium, .large]).presentationDragIndicator(.visible).interactiveDismissDisabled(decision.busy)
    }
}

private struct ReviewReturnSheet: View {
    @Bindable var decision: ReviewDecision
    let fieldLabel: String
    let api: APIClient
    let onReturn: () async -> Void
    @State private var voice = ReviewVoiceCapture()
    @FocusState private var focused: Bool
    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            Text("Send back · \(fieldLabel)").reviewEyebrow()
            TextField("Tell the worker what to change…", text: $decision.revisionDraft, axis: .vertical)
                .lineLimit(3...6).font(.body).focused($focused).accessibilityIdentifier("review.revision")
                .disabled(voice.phase != .idle || decision.busy)
            if voice.phase != .idle { voiceStatus }
            HStack(spacing: PanelsTheme.Space.md) {
                if voice.phase == .idle {
                    Button { focused = false; Task { await voice.start() } } label: { Image(systemName: "mic").frame(width: 44, height: 44) }
                        .background(PanelsTheme.recessed, in: Circle()).foregroundStyle(PanelsTheme.muted).accessibilityLabel("Record a revision")
                        .disabled(decision.busy)
                }
                Button {
                    Haptics.light(); Task { await onReturn() }
                } label: {
                    HStack { Spacer(); if decision.busy { ProgressView().tint(PanelsTheme.ink) } else { Text("Send back") }; Spacer() }.frame(minHeight: 44)
                }.background(PanelsTheme.accent, in: Capsule()).foregroundStyle(PanelsTheme.ink)
                    .disabled(!decision.ready || decision.revisionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || voice.phase != .idle)
                    .opacity(decision.revisionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.45 : 1)
                    .accessibilityIdentifier("review.confirmReturn")
            }
            if let error = decision.error ?? voice.error { Text(error).font(.footnote).foregroundStyle(PanelsTheme.error) }
        }.padding(ReviewStyle.gutter).padding(.top, PanelsTheme.Space.lg).frame(maxWidth: .infinity, alignment: .leading)
        }.scrollDismissesKeyboard(.interactively)
            .background(PanelsTheme.raised).presentationDetents([.large]).presentationDragIndicator(.visible)
            .interactiveDismissDisabled(decision.busy).onAppear { focused = true }.onDisappear { voice.cancel() }
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
                Button { Task { append(await voice.stop(api: api)) } } label: { Image(systemName: "stop.fill").frame(width: 44, height: 44) }.background(PanelsTheme.accent, in: Circle()).foregroundStyle(PanelsTheme.ink).accessibilityLabel("Stop and transcribe")
            case .transcribing: Text("transcribing"); Spacer(); ProgressView()
            case .failed:
                Text("transcription failed · kept").font(.footnote)
                Spacer()
                Button("Retry") { Task { append(await voice.transcribe(api: api)) } }
            case .idle: EmptyView()
            }
        }.foregroundStyle(PanelsTheme.muted)
    }
    private func append(_ transcript: String?) {
        if let transcript { decision.revisionDraft = ReviewVoiceCapture.append(transcript, to: decision.revisionDraft) }
        focused = true
    }
}

private extension Text {
    func reviewEyebrow() -> some View { self.font(.caption2.weight(.semibold)).foregroundStyle(PanelsTheme.faint) }
}
