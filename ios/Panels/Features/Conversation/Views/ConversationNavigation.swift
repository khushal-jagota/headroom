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
    var refreshError: String?
    var retry: (() async -> Void)?
    @Environment(AppStore.self) private var store
    @State private var opened = false
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
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar(.visible, for: .navigationBar)
                    .toolbar(.hidden, for: .tabBar)
            }
            .onChange(of: store.baseURL) { _, _ in speech.cancel(); model = ConversationModel() }
    }

    private func surface(isPage: Bool) -> some View {
        ConversationSurface(owner: owner, conversationID: conversationID, title: title,
                            isPage: isPage, active: isPage || !opened, open: { opened = true },
                            model: model, speech: speech, refreshError: refreshError, retry: retry)
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
            if owner == .chief, model.conversationID == nil { model.conversationID = try? await store.client.chiefConversation().conversationId }
            while !Task.isCancelled {
                await model.refresh(using: store.client, transcript: true)
                let api = store.client
                if let configuration = try? await api.conversationConfiguration(owner, id: model.conversationID), !Task.isCancelled, api.baseURL == store.baseURL { model.applyConfiguration(configuration) }
                do { try await Task.sleep(for: .milliseconds(700)) } catch { return }
            }
        }
        .task(id: "tail|\(store.baseURL)|\(model.conversationID ?? "")|\(model.lens)|\(scenePhase == .active)|\(active)") {
            guard active, scenePhase == .active, let id = model.conversationID else { return }
            while !Task.isCancelled {
                do {
                    for try await _ in store.client.conversationChanges(id, lens: model.lens) {
                        await model.refresh(using: store.client, transcript: true)
                    }
                } catch { if Task.isCancelled { return } }
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
            }
        }
        .onChange(of: LocalStore.shared.workRevision) { _, _ in model.syncOutbox() }
        .onChange(of: model.lens) { _, _ in Task { await model.refresh(using: store.client, transcript: true) } }
        .onChange(of: scenePhase) { _, phase in if phase != .active { speech.cancel() } }
        .onDisappear { speech.cancel() }
        .onChange(of: store.baseURL) { _, _ in speech.cancel() }
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            header
            if isPage {
                ConversationThread(model: model, owner: owner)
            }
            bottom.fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, ConversationLayout.surfaceTopPadding)
        .background(PanelsTheme.raised.ignoresSafeArea(edges: .bottom))
        .foregroundStyle(PanelsTheme.text)
        .tint(PanelsTheme.accent)
        .preferredColorScheme(.dark)
    }

    private var header: some View {
        HStack(spacing: 0) {
            if isPage {
                Text(title).font(PanelsTheme.TypeScale.small).lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.leading, 20)
                Button(model.lens.rawValue.capitalized) {
                    model.lens = model.lens == .focus ? .full : .focus
                    Haptics.selection()
                }
                .font(PanelsTheme.TypeScale.small)
                .frame(width: 60, height: ConversationLayout.lineHeight)
                .accessibilityLabel("Conversation lens")
            } else {
                Button(action: open) {
                    HStack(spacing: 8) {
                        Group {
                            if model.status?.turnRunning == true && model.status?.authority == "current" && model.error == nil {
                                ProgressView().controlSize(.mini)
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
                                Text("Queued").foregroundStyle(PanelsTheme.faint)
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
    @Bindable var model: ConversationModel
    let owner: ConversationOwner
    var readOnly = false
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
                                } else { Text(.init(row.text)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true) }
                                if let note = row.note, note != "Sent" {
                                    HStack {
                                        if note == "Waiting for connection" { Image(systemName: "clock").foregroundStyle(PanelsTheme.faint) }
                                        Text(note).foregroundStyle(note.hasPrefix("Not sent") ? PanelsTheme.error : PanelsTheme.faint)
                                        if let id = row.retryID, !readOnly {
                                            Button { Task { await model.retry(id, to: owner, using: store.client) } } label: {
                                                Text("Retry").padding(.horizontal, 8).padding(.vertical, 4)
                                                    .background(PanelsTheme.accentSurface, in: Capsule())
                                                    .contentShape(Rectangle().inset(by: -8))
                                            }.disabled(model.sending)
                                        }
                                    }.font(PanelsTheme.TypeScale.small).frame(minHeight: 28)
                                }
                            }.padding(row.own ? 12 : 0).background(row.own ? PanelsTheme.raised : .clear, in: RoundedRectangle(cornerRadius: 12))
                            if !row.own { Spacer(minLength: 0) }
                        }.id(row.id)
                    }
                }
                }.padding(.horizontal, 20).padding(.bottom, 12)
            }
            .accessibilityIdentifier("conversation-thread")
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
                            .font(PanelsTheme.TypeScale.small.weight(.medium))
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
                        let catalog: TicketBackendCatalog = try await store.client.get("/api/conversation/backends")
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
    func conversationNavigation(owner: ConversationOwner, conversationID: String?, title: String = "Conversation", refreshError: String? = nil, retry: (() async -> Void)? = nil) -> some View {
        modifier(ConversationNavigation(owner: owner, conversationID: conversationID, title: title, refreshError: refreshError, retry: retry))
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
                    Text("Chief of Staff").font(.title.weight(.semibold))
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
