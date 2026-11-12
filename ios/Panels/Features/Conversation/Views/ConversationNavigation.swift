import SwiftUI
import UniformTypeIdentifiers
import PhotosUI

enum ConversationLayout {
    static let restingInset: CGFloat = 34
    static let cardInset: CGFloat = 12
    static let restingHeight: CGFloat = 48
    static let cardHeight: CGFloat = 95
    static let shapeMotion = Animation.timingCurve(0.2, 0.7, 0.5, 1, duration: 0.31)
    static let lineHeight: CGFloat = 44
    static let surfaceTopPadding: CGFloat = 8
}

struct ConversationNavigation: ViewModifier {
    let owner: ConversationOwner
    let conversationID: String?
    var title = "Conversation"
    var openConversationID: String?
    var refreshError: String?
    var retry: (() async -> Void)?
    @Environment(AppStore.self) private var store
    @State private var opened = false
    @State private var followedLink = false
    @State private var linkedConversationID: String?
    @State private var model = ConversationModel()
    @State private var speech = ConversationSpeech()

    func body(content: Content) -> some View {
        content
            .toolbar(.hidden, for: .tabBar)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                surface(isPage: false)
            }
            .navigationDestination(isPresented: $opened) {
                surface(isPage: true)
                    .toolbar(.hidden, for: .navigationBar)
                    .toolbar(.hidden, for: .tabBar)
            }
            .task {
                guard openConversationID != nil, !followedLink else { return }
                followedLink = true
                linkedConversationID = openConversationID
                opened = true
            }
            .onChange(of: opened) { _, isOpen in
                Haptics.light()
                if !isOpen, linkedConversationID != nil {
                    linkedConversationID = nil
                    model = ConversationModel()
                }
            }
            .onChange(of: store.baseURL) { _, _ in speech.cancel(); model = ConversationModel() }
    }

    private func surface(isPage: Bool) -> some View {
        ConversationSurface(owner: owner, conversationID: isPage ? linkedConversationID ?? conversationID : conversationID, title: title,
                            isPage: isPage, active: isPage || !opened, open: { opened = true },
                            model: model, speech: speech, refreshError: refreshError, retry: retry,
                            checksCurrentConversation: isPage && linkedConversationID != nil)
    }
}

/// Both navigation destinations share the page-owned draft and conversation model.
private struct ConversationSurface: View {
    let owner: ConversationOwner
    let conversationID: String?
    let title: String
    let isPage: Bool
    let active: Bool
    let open: () -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Bindable var model: ConversationModel
    let speech: ConversationSpeech
    var refreshError: String?
    var retry: (() async -> Void)?
    var checksCurrentConversation = false
    @State private var currentConversationConfirmed = false
    private var readOnly: Bool { checksCurrentConversation && !currentConversationConfirmed }
    @State private var changingQueue = false

    var body: some View {
        conversation
        .task(id: "\(store.baseURL)|\(conversationID ?? "new")|\(scenePhase == .active)|\(active)") {
            guard active, scenePhase == .active else { return }
            model.conversationID = conversationID ?? model.conversationID
            if owner == .chief, model.conversationID == nil {
                let saved: ConversationReference? = store.client.cached("/api/chief/conversation")
                model.conversationID = saved?.conversationId
            }
            model.restore(owner: owner, api: store.client)
            if case .ticket(let id) = owner {
                let api = store.client
                model.machineLocale = api.cachedTicket(id)?.locale
                let ticket = try? await api.ticket(id)
                guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
                if let ticket { model.machineLocale = ticket.locale }
            }
            if owner == .chief, model.conversationID == nil {
                let currentID = try? await store.client.chiefConversation().conversationId
                guard !Task.isCancelled else { return }
                model.conversationID = currentID
            }
            if checksCurrentConversation {
                do { currentConversationConfirmed = try await store.client.currentConversation(owner) == conversationID }
                catch { currentConversationConfirmed = false; model.error = error.localizedDescription }
            }
            // The open tail below delivers the transcript and status. A conversation that
            // has not started yet has only its owner's start values to read.
            if model.conversationID == nil { await readConfiguration() }
        }
        .task(id: "tail|\(store.baseURL)|\(model.conversationID ?? "")|\(model.lens)|\(scenePhase == .active)|\(active)") {
            guard active, scenePhase == .active, let id = model.conversationID else { return }
            var misses = 0
            while !Task.isCancelled {
                model.beginTail()
                var applied = false, failed = false
                do {
                    for try await frame in store.client.conversationTail(id, lens: model.lens) {
                        let before = configurationFacts
                        guard await model.applyTail(frame, using: store.client) else { break }
                        // Held prompts, the read boundary and the running model change with a
                        // new owner message, a turn edge or a held prompt's arrival, and are
                        // read once per connection.
                        if !applied || configurationFacts != before { await readConfiguration() }
                        applied = true
                    }
                } catch {
                    if Task.isCancelled { return }
                    model.error = error.localizedDescription
                    applied = false
                    failed = true
                }
                if Task.isCancelled { return }
                // A gap or an ended healthy stream reopens promptly for a fresh snapshot, and a
                // failed connection after 2 s. A stream the server ends cleanly before its first
                // frame waits 2, 4, 8 then 30 s, so a conversation the server cannot read does
                // not cost it a request every 2 s.
                misses = applied || failed ? 0 : misses + 1
                let wait: Duration = applied ? .milliseconds(250) : failed ? .seconds(2) : .seconds(misses > 3 ? 30 : 1 << misses)
                do { try await Task.sleep(for: wait) } catch { return }
            }
        }
        .onChange(of: LocalStore.shared.workRevision) { _, _ in
            let before = model.outgoing
            model.syncOutbox()
            // A saved message can be held by the server; its queue lives in the configuration.
            if active, scenePhase == .active, model.outgoing != before { Task { await readConfiguration() } }
        }
        .onChange(of: scenePhase) { _, phase in if phase != .active { speech.cancel() } }
        .onDisappear { speech.cancel() }
        .onChange(of: store.baseURL) { _, _ in speech.cancel() }
    }

    private var configurationFacts: String {
        "\(model.status?.rest.latestOwnerMessageSequence ?? -1)|\(model.status?.turnRunning.map(String.init) ?? "unknown")|\(model.deliveredHeldPromptCount)"
    }

    private func readConfiguration() async {
        let api = store.client
        if let configuration = try? await api.conversationConfiguration(owner, id: model.conversationID), !Task.isCancelled, api.baseURL == store.baseURL {
            model.applyConfiguration(configuration)
        }
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            header
            if isPage, model.machineLocale == "macos" {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                    TicketMachineLabel(locale: model.machineLocale)
                    if TicketMachine.activityUnknown(model.machineLocale, health: store.executorHealth) {
                        Text("Current activity on Mac is unknown.")
                        Text(store.executorHealth?.contactSentence ?? "Last contact: unknown.")
                    }
                }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, AgentPageLayout.gutter)
            }
            if isPage {
                ConversationThread(model: model, owner: owner, readOnly: readOnly, active: active && scenePhase == .active)
            }
            if readOnly {
                Text("Historical conversation · read only").font(PanelsTheme.TypeScale.small)
                    .foregroundStyle(PanelsTheme.faint).padding()
            } else {
                bottom.fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, isPage ? 0 : ConversationLayout.surfaceTopPadding)
        .background((isPage ? PanelsTheme.surface : PanelsTheme.raised).ignoresSafeArea(edges: isPage ? .all : .bottom))
        .foregroundStyle(PanelsTheme.text)
        .tint(PanelsTheme.accent)
        .preferredColorScheme(.dark)
    }

    private var header: some View {
        HStack(spacing: 0) {
            if isPage {
                AgentPageControls {
                    Menu {
                        Picker("Conversation lens", selection: $model.lens) {
                            ForEach(ConversationLens.allCases, id: \.self) { lens in
                                Text(lens.rawValue.capitalized).tag(lens)
                            }
                        }
                        .onChange(of: model.lens) { _, _ in Haptics.selection() }
                    } label: {
                        Image(systemName: "ellipsis").frame(minWidth: 44, minHeight: 44)
                    }
                    .accessibilityLabel("Conversation actions")
                    .accessibilityIdentifier("conversation-actions")
                    .accessibilityValue(model.lens.rawValue.capitalized)
                }
                .padding(.horizontal, AgentPageLayout.gutter)
            } else {
                Button(action: open) {
                    HStack(spacing: 8) {
                        Group {
                            if model.status?.turnRunning == true && model.status?.authority == "current" && model.error == nil && !TicketMachine.conversationActivityUnknown(model.machineLocale, owner: owner, health: store.executorHealth) {
                                RingSpinner(size: .compact)
                            } else {
                                Circle().fill(PanelsTheme.accent).frame(width: 8, height: 8).opacity(model.hasUnread ? 1 : 0)
                            }
                        }.frame(width: 12)
                        let latest = model.latestMessage
                        Text(.init(latest?.previewText ?? "Conversation"))
                            .font(PanelsTheme.TypeScale.small).lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if latest?.own == true, let note = latest?.note, note != "Sent" {
                            Text(note).font(PanelsTheme.TypeScale.caption).lineLimit(1).layoutPriority(1)
                        }
                        Image(systemName: "chevron.right").font(.system(size: 11))
                    }
                    .padding(.leading, 20).padding(.trailing, 14)
                    .frame(minHeight: ConversationLayout.lineHeight).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(model.latestMessage?.previewText ?? "Open conversation")
                .accessibilityIdentifier("conversation-line")
            }
        }
        .frame(minHeight: ConversationLayout.lineHeight)
        .foregroundStyle(PanelsTheme.faint)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversation-header")
    }

    private var bottom: some View {
        VStack(spacing: 8) {
            if !model.heldPrompts.isEmpty {
                VStack(spacing: 4) {
                    ForEach(model.heldPrompts, id: \.heldPromptId) { held in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(held.text).lineLimit(2)
                            HStack {
                                Text(held.note).foregroundStyle(PanelsTheme.faint)
                                Spacer(minLength: 8)
                                Button("Steer in") { changeQueue(held, discard: false) }
                                Button { changeQueue(held, discard: true) } label: {
                                    Image(systemName: "xmark").font(.system(size: 18)).frame(width: 44, height: 44)
                                }.accessibilityLabel("Remove queued message")
                            }
                        }.font(PanelsTheme.TypeScale.small).padding(.horizontal, 12)
                            .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: 12))
                    }
                }.disabled(changingQueue).padding(.horizontal, 12)
            }
            ConversationComposer(model: model, owner: owner, speech: speech, refreshError: refreshError, retry: retry)
        }
        .padding(.bottom, 1)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversation-composer-region")
    }

    private func changeQueue(_ held: ConversationConfiguration.HeldPrompt, discard: Bool) {
        guard let id = model.conversationID, !changingQueue else { return }
        changingQueue = true
        Task {
            defer { changingQueue = false }
            do {
                let result = try await store.client.changeHeldPrompt(id, heldID: held.heldPromptId, discard: discard)
                model.applyHeldChange(result, senderID: held.senderMessageId ?? held.heldPromptId)
                await model.refresh(using: store.client, transcript: true)
                model.applyConfiguration(try await store.client.conversationConfiguration(owner, id: id))
            } catch { model.error = error.localizedDescription }
        }
    }
}

struct ConversationThread: View {
    @State private var recoveryID: String?
    @Bindable var model: ConversationModel
    let owner: ConversationOwner
    var readOnly = false
    var active = false
    @State private var appeared = false
    @State private var visibleRowIDs: Set<String> = []
    private var readBoundary: ConversationReadBoundary? {
        model.visibleReadBoundary(rowIDs: visibleRowIDs, active: active && appeared)
    }
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var scrollPolicy = ConversationScrollPolicy()
    @State private var position = ScrollPosition(edge: .bottom)
    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                LazyVStack(alignment: .leading, spacing: 20) {
                    if model.older != nil {
                        Button("Earlier messages") { Task { await model.loadOlder(using: store.client) } }
                            .disabled(model.loadingOlder).frame(maxWidth: .infinity, minHeight: 44)
                    }
                    ForEach(model.rows) { row in
                        HStack {
                            if row.own { Spacer(minLength: 28) }
                            VStack(alignment: .leading, spacing: 6) {
                                if let sender = row.senderLabel {
                                    Text(sender).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                                }
                                if row.item?.body?.reference != nil, let item = row.item, model.expandedBodies[item.key] == nil {
                                    Button("Read message") { Task { await model.loadBody(item, using: store.client) } }
                                } else if !row.text.isEmpty { Text(.init(row.text)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true) }
                                if !row.attachments.isEmpty { ConversationAttachmentTray(pieces: row.attachments) }
                                if let note = row.note, note != "Sent" {
                                    HStack {
                                        if note == "Waiting for connection" { Image(systemName: "clock").foregroundStyle(PanelsTheme.faint) }
                                        Text(note).foregroundStyle(note.hasPrefix("Not sent") ? PanelsTheme.error : PanelsTheme.faint)
                                        if let id = row.retryID, !readOnly {
                                            Button {
                                                if model.recoveries[id] == nil || model.recoveries[id] == .retry { Task { await model.retry(id, to: owner, using: store.client) } }
                                                else { recoveryID = id }
                                            } label: {
                                                Text(model.recoveryLabel(id)).padding(.horizontal, 8).padding(.vertical, 4)
                                                    .background(PanelsTheme.accentSurface, in: Capsule())
                                                    .contentShape(Rectangle().inset(by: -8))
                                            }.disabled(model.sending)
                                            .confirmationDialog("The earlier result is unknown. Another attempt can send this message twice.", isPresented: Binding(get: { recoveryID == id }, set: { if !$0 { recoveryID = nil } }), titleVisibility: .visible) {
                                                Button(model.recoveries[id]?.unknownDestination == true ? "Start a new conversation with this message" : model.recoveryLabel(id)) {
                                                    model.recover(id, using: store.client, startNew: model.recoveries[id]?.unknownDestination == true)
                                                    recoveryID = nil
                                                }
                                                Button("Keep waiting", role: .cancel) { recoveryID = nil }
                                            }
                                        }
                                    }.font(PanelsTheme.TypeScale.small).frame(minHeight: 28)
                                }
                            }.padding(row.own ? 12 : 0).background(row.own ? PanelsTheme.raised : .clear, in: RoundedRectangle(cornerRadius: 12))
                            if !row.own { Spacer(minLength: 0) }
                        }.id(row.id)
                            .onScrollVisibilityChange(threshold: 0.01) { visible in
                                if visible { visibleRowIDs.insert(row.id) }
                                else { visibleRowIDs.remove(row.id) }
                            }
                    }
                }
                }.padding(.horizontal, 20).padding(.bottom, 12)
            }
            .accessibilityIdentifier("conversation-thread")
            .onAppear { appeared = true }
            .onDisappear { appeared = false; visibleRowIDs = [] }
            .onChange(of: model.conversationID) { _, _ in visibleRowIDs = [] }
            .onChange(of: model.lens) { _, _ in visibleRowIDs = [] }
            .task(id: readBoundary) {
                await model.acknowledgeVisibleRows(visibleRowIDs, active: active && appeared, using: store.client)
            }
            .contentMargins(.top, 12, for: .scrollContent)
            .task {
                await model.prepareOpening(using: store.client)
                guard !Task.isCancelled else { return }
                scrollPolicy.open(atUnread: model.firstUnreadID != nil)
                if let id = model.firstUnreadID { proxy.scrollTo(id, anchor: .top) }
                else { position.scrollTo(edge: .bottom) }
            }
            .defaultScrollAnchor(.bottom, for: .initialOffset)
            .defaultScrollAnchor(scrollPolicy.shouldFollowContent ? .bottom : .top, for: .sizeChanges)
            .scrollDismissesKeyboard(.interactively)
            .scrollPosition($position)
            .onScrollPhaseChange { old, phase, context in
                if phase == .tracking || phase == .interacting { scrollPolicy.beginGesture() }
                if phase == .idle {
                    if scrollPolicy.returningToLatest {
                        scrollPolicy.finishReturn()
                        position.scrollTo(edge: .bottom)
                    } else if old != .animating && scrollPolicy.userScrolling {
                        scrollPolicy.endGesture(distance: distanceFromLatest(context.geometry))
                    }
                }
            }
            .onScrollGeometryChange(for: CGSize.self) {
                CGSize(width: $0.containerSize.height, height: $0.contentSize.height)
            } action: { _, _ in
                if scrollPolicy.shouldFollowContent { position.scrollTo(edge: .bottom) }
            }
            .onScrollGeometryChange(for: Double.self) { distanceFromLatest($0) } action: { _, distance in
                scrollPolicy.measure(distance: distance)
            }
            .onChange(of: model.rows.last.map { [$0.id, $0.text, $0.note ?? ""] }) { old, _ in
                if old != nil { scrollPolicy.receiveOutput() }
                // Layout, not the pre-layout text update, owns bottom alignment.
            }
            .overlay(alignment: .bottomTrailing) {
                ZStack {
                    if scrollPolicy.showLatest {
                        Button {
                            scrollPolicy.returnToLatest()
                            withAnimation(reduceMotion ? nil : PanelsTheme.Motion.entrance, completionCriteria: .removed) {
                                position.scrollTo(edge: .bottom)
                            } completion: {
                                // Also handles Reduce Motion / a no-op scroll, which
                                // need not enter the native animating phase.
                                if scrollPolicy.returningToLatest {
                                    scrollPolicy.finishReturn()
                                    position.scrollTo(edge: .bottom)
                                }
                            }
                        } label: {
                            HStack(spacing: 6) {
                                if scrollPolicy.hasNewOutput {
                                    Circle().fill(PanelsTheme.accent).frame(width: 6, height: 6)
                                }
                                Text("Latest")
                                Image(systemName: "arrow.down")
                            }
                            .font(PanelsTheme.TypeScale.latestAction)
                            .foregroundStyle(PanelsTheme.text)
                            .padding(.horizontal, 14).frame(height: 44)
                            .background(PanelsTheme.raised, in: Capsule())
                            .overlay(Capsule().stroke(PanelsTheme.border))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Latest")
                        .accessibilityValue(scrollPolicy.hasNewOutput ? "New output" : "")
                        .accessibilityHint("Scroll to the latest message")
                        .padding(.trailing, 16).padding(.bottom, 16)
                        .transition(.opacity)
                    }
                }
                .animation(reduceMotion ? nil : PanelsTheme.Motion.fade, value: scrollPolicy.showLatest)
            }
        }
    }

    private func distanceFromLatest(_ geometry: ScrollGeometry) -> Double {
        max(0, geometry.contentSize.height - geometry.visibleRect.maxY)
    }
}

struct ConversationBackendMark: View {
    let backend: String
    var body: some View {
        Image("conversation-" + backend).renderingMode(backend == "hermes" ? .original : .template).resizable().scaledToFit().frame(width: 16, height: 16).accessibilityLabel(backend.capitalized)
    }
}

struct ConversationModelSheet: View {
    @Bindable var model: ConversationModel
    let owner: ConversationOwner
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var backends: [TicketBackend] = []
    @State private var backend = ""
    @State private var selection = ""
    @State private var effort = ""
    @State private var error: String?
    private var selected: TicketBackend? { backends.first { $0.id == backend } }
    private var efforts: [String] { selected?.availableModels.first { $0.id == selection }?.reasoningEffortOptions ?? selected?.reasoningEffortOptions ?? [] }
    var body: some View {
        NavigationStack {
            Form {
                Section("Agent") {
                    Picker("Agent", selection: $backend) {
                        ForEach(backends.filter(\.installed)) { entry in
                            HStack { ConversationBackendMark(backend: entry.id); Text(entry.id.capitalized) }.tag(entry.id)
                        }
                    }.pickerStyle(.segmented).disabled(model.conversationID != nil)
                }
                Section("Model") {
                    Picker("Model", selection: $selection) {
                        ForEach(selected?.availableModels.filter(\.enabled) ?? []) { item in Text(item.displayName ?? item.id).tag(item.id) }
                    }.pickerStyle(.inline).labelsHidden()
                }
                if !efforts.isEmpty {
                    Section("Reasoning") { Picker("Reasoning", selection: $effort) { ForEach(efforts, id: \.self) { Text($0.capitalized).tag($0) } }.pickerStyle(.segmented) }
                }
                if let error { Text(error).foregroundStyle(PanelsTheme.error) }
                Button {
                    model.backendKey = backend; model.selectedModel = selection; model.reasoningEffort = effort.isEmpty ? nil : effort
                    Haptics.selection(); dismiss()
                } label: { Text("Use \(selection)\(effort.isEmpty ? "" : ", " + effort)").frame(maxWidth: .infinity, minHeight: 44) }
                    .listRowBackground(PanelsTheme.accent).foregroundStyle(PanelsTheme.ink).disabled(selection.isEmpty)
            }.panelsScreen().navigationTitle("Model").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
                .task {
                    backend = model.backendKey ?? ""; selection = model.selectedModel ?? ""; effort = model.reasoningEffort ?? ""
                    do {
                        let catalog = try await store.client.conversationBackends(owner: owner)
                        backends = catalog.backends
                        if backend.isEmpty { backend = backends.first(where: \.installed)?.id ?? "" }
                        if selection.isEmpty { selection = selected?.defaultModelId ?? "" }
                    }
                    catch { self.error = error.localizedDescription }
                }
                .onChange(of: backend) { old, _ in if !old.isEmpty { selection = selected?.defaultModelId ?? ""; effort = selected?.defaultReasoningEffort ?? "" } }
                .onChange(of: selection) { _, _ in if !efforts.contains(effort) { effort = efforts.first ?? "" } }
        }.presentationDragIndicator(.visible)
    }
}

extension View {
    func conversationNavigation(owner: ConversationOwner, conversationID: String?, title: String = "Conversation", openConversationID: String? = nil, refreshError: String? = nil, retry: (() async -> Void)? = nil) -> some View {
        modifier(ConversationNavigation(owner: owner, conversationID: conversationID, title: title, openConversationID: openConversationID, refreshError: refreshError, retry: retry))
    }
}

#if DEBUG
/// Uses real detail pages with the existing loopback-only conversation test host.
struct ConversationDetailSmokeView: View {
    var body: some View {
        NavigationStack {
            List {
                NavigationLink("Ticket fixture") { TicketView(ticketID: "t_02zd37tz") }
                NavigationLink("Orchestrator fixture") { OrchestratorView(managerID: "si_4chgag3w") }
                NavigationLink("Chief fixture") { ChiefView() }
            }.panelsScreen()
        }
    }
}

struct ConversationSmokeView: View {
    @Environment(AppStore.self) private var store
    @State private var refreshError: String?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text("Chief of Staff").font(PanelsTheme.TypeScale.detailTitle)
                    Text("Keep the whole operation in view.").foregroundStyle(PanelsTheme.faint)
                    ForEach(0..<12) { Text("Worker \($0 + 1)").frame(maxWidth: .infinity, minHeight: 50, alignment: .leading); Divider() }
                    NavigationLink("Worker details") {
                        Text("Worker details").frame(maxWidth: .infinity, maxHeight: .infinity)
                            .panelsScreen().navigationTitle("Worker details")
                    }
                }.padding(.horizontal, 20)
            }.accessibilityIdentifier("conversation-page").panelsScreen().conversationNavigation(owner: .chief, conversationID: "stub", title: "Chief of Staff", refreshError: refreshError, retry: refresh)
                .screenLoadState("Chief of Staff", hasContent: true, error: refreshError, showsRefreshError: false, retry: refresh)
                .task {
                    while !Task.isCancelled {
                        await refresh()
                        do { try await Task.sleep(for: .milliseconds(700)) } catch { return }
                    }
                }
        }
    }
    private func refresh() async {
        do { _ = try await store.client.chiefConversation(); refreshError = nil }
        catch { if !Task.isCancelled { refreshError = error.localizedDescription } }
    }
}
#endif

struct ConversationCamera: UIViewControllerRepresentable {
    let completed: (Data?) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(completed: completed) }
    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera; picker.delegate = context.coordinator
        return picker
    }
    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}
    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let completed: (Data?) -> Void
        init(completed: @escaping (Data?) -> Void) { self.completed = completed }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { completed(nil) }
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            completed((info[.originalImage] as? UIImage)?.jpegData(compressionQuality: 0.85))
        }
    }
}
