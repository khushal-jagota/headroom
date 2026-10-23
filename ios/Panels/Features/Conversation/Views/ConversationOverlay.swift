import SwiftUI
import UniformTypeIdentifiers
import PhotosUI

private enum ConversationLayout {
    static let restingInset: CGFloat = 34
    static let cardInset: CGFloat = 12
    static let restingHeight: CGFloat = 48
    static let cardHeight: CGFloat = 95
    static let shapeMotion = Animation.timingCurve(0.2, 0.7, 0.5, 1, duration: 0.31)
    static let pageMotion = Animation.timingCurve(0.32, 0.72, 0, 1, duration: 0.35)
}

struct ConversationOverlay: ViewModifier {
    let owner: ConversationOwner
    let conversationID: String?
    var title = "Conversation"
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var model = ConversationModel()
    @State private var speech = ConversationSpeech()
    @State private var opened = false
    @State private var openingDistance: CGFloat = 0
    @State private var closingDistance: CGFloat = 0
    @State private var modelSheet = false
    @State private var importFile = false
    @State private var pickPicture = false
    @State private var picture: PhotosPickerItem?
    @State private var camera = false
    @State private var changingQueue = false
    @FocusState private var focused: Bool

    private var stopsTurn: Bool { model.status?.turnRunning == true && model.draft.isEmpty && model.attachments.isEmpty && !speech.recording }
    private var card: Bool { (!model.draft.isEmpty || !model.attachments.isEmpty) && !speech.recording && !speech.transcribing }
    private var capturingSpeech: Bool { speech.recording || speech.transcribing || speech.starting }

    func body(content: Content) -> some View {
        GeometryReader { geometry in
            ZStack {
                content.brightness(opened ? -0.4 : -0.4 * min(1, openingDistance / max(1, geometry.size.height)))
                    .allowsHitTesting(!opened && openingDistance == 0).accessibilityHidden(opened)
                VStack(spacing: 0) {
                    header
                    ConversationThread(model: model, owner: owner, opened: opened)
                }
                .background(PanelsTheme.surface)
                .offset(y: reduceMotion ? 0 : opened ? closingDistance : geometry.size.height - openingDistance)
                .opacity(opened || openingDistance > 0 ? 1 : 0)
                .allowsHitTesting(opened).accessibilityHidden(!opened)
                .zIndex(1)
            }.clipped()
        }
        .toolbar(opened ? .hidden : .automatic, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .safeAreaInset(edge: .bottom, spacing: 0) { bottom }
        .sheet(isPresented: $modelSheet) { ConversationModelSheet(model: model) }
        .photosPicker(isPresented: $pickPicture, selection: $picture, matching: .images)
        .onChange(of: picture) { _, selected in
            guard let selected else { return }
            Task {
                do {
                    guard let bytes = try await selected.loadTransferable(type: Data.self), bytes.count <= 20_000_000 else { model.error = "Choose a picture smaller than 20 MB."; return }
                    let type = selected.supportedContentTypes.first ?? .jpeg
                    model.attachments.append(.init(piece: "image", data: bytes.base64EncodedString(), mediaType: type.preferredMIMEType ?? "image/jpeg", fileName: "Picture." + (type.preferredFilenameExtension ?? "jpg")))
                    picture = nil
                } catch { model.error = error.localizedDescription }
            }
        }
        .sheet(isPresented: $camera) {
            ConversationCamera { data in
                if let data { model.attachments.append(.init(piece: "image", data: data.base64EncodedString(), mediaType: "image/jpeg", fileName: "Photo.jpg")) }
                camera = false
            }.ignoresSafeArea()
        }
        .fileImporter(isPresented: $importFile, allowedContentTypes: [.data]) { result in
            do {
                let url = try result.get()
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                let data = try Data(contentsOf: url)
                guard data.count <= 20_000_000 else { model.error = "Choose a file smaller than 20 MB."; return }
                model.attachments.append(.init(piece: "file", text: nil, data: data.base64EncodedString(), mediaType: UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream", fileName: url.lastPathComponent))
            } catch { model.error = error.localizedDescription }
        }
        .task(id: "\(store.baseURL)|\(conversationID ?? "new")|\(scenePhase == .active)") {
            guard scenePhase == .active else { return }
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
        .task(id: "tail|\(store.baseURL)|\(model.conversationID ?? "")|\(model.lens)|\(scenePhase == .active)") {
            guard scenePhase == .active, let id = model.conversationID else { return }
            while !Task.isCancelled {
                do {
                    for try await _ in store.client.conversationChanges(id, lens: model.lens) {
                        await model.refresh(using: store.client, transcript: true)
                    }
                } catch { if Task.isCancelled { return } }
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
            }
        }
        .onChange(of: LocalStore.shared.revision) { _, _ in model.syncOutbox() }
        .onChange(of: model.lens) { _, _ in Task { await model.refresh(using: store.client, transcript: true) } }
        .onChange(of: scenePhase) { _, phase in if phase != .active { speech.cancel() } }
        .onChange(of: capturingSpeech) { _, capturing in if capturing { focused = false } }
        .onDisappear { speech.cancel() }
        .onChange(of: store.baseURL) { _, _ in speech.cancel(); model = ConversationModel() }
    }

    private func show(_ value: Bool) {
        focused = false
        withAnimation(reduceMotion ? .easeOut(duration: PanelsTheme.Motion.base) : ConversationLayout.pageMotion) { opened = value; openingDistance = 0; closingDistance = 0 }
    }
    private var header: some View {
        HStack {
            Button { show(false) } label: { Image(systemName: "chevron.down").frame(width: 44, height: 44) }.accessibilityLabel("Close conversation")
            Spacer(minLength: 0)
            Text(title).font(.system(size: 16, weight: .semibold)).lineLimit(1)
            Spacer(minLength: 0)
            Button(model.lens.rawValue.capitalized) { model.lens = model.lens == .focus ? .full : .focus; Haptics.selection() }
                .font(.system(size: 17)).frame(minWidth: 44, minHeight: 44).accessibilityLabel("Conversation lens")
        }.padding(.horizontal, 12).frame(height: 52).foregroundStyle(PanelsTheme.text)
            .contentShape(Rectangle()).gesture(DragGesture()
                .onChanged { closingDistance = max(0, $0.translation.height) }
                .onEnded { value in
                    if value.translation.height > 110 || value.predictedEndTranslation.height > 220 { show(false) }
                    else { withAnimation(reduceMotion ? nil : ConversationLayout.pageMotion) { closingDistance = 0 } }
                })
    }

    private var bottom: some View {
        VStack(spacing: 8) {
            Button { show(true) } label: {
                HStack(spacing: 8) {
                    Group {
                        if model.status?.turnRunning == true && model.status?.authority == "current" && model.error == nil { ProgressView().controlSize(.mini) }
                        else { Circle().fill(PanelsTheme.accent).frame(width: 8, height: 8).opacity(model.hasUnread ? 1 : 0) }
                    }.frame(width: 12)
                    let latest = model.latestMessage
                    Text(.init((latest?.own == true ? "You: " : "") + (latest?.text ?? "Conversation"))).font(PanelsTheme.TypeScale.small).lineLimit(1)
                    if latest?.own == true, let note = latest?.note, note != "Sent" {
                        Text(note).font(PanelsTheme.TypeScale.caption).lineLimit(1).layoutPriority(1)
                    }
                    Image(systemName: "chevron.up").font(.system(size: 11)).foregroundStyle(PanelsTheme.faint)
                }.foregroundStyle(PanelsTheme.faint).padding(.leading, 20).padding(.trailing, 14).frame(height: 44)
            }.buttonStyle(.plain)
                .accessibilityIdentifier("conversation-line")
                .simultaneousGesture(DragGesture()
                    .onChanged { if !opened { openingDistance = max(0, -$0.translation.height) } }
                    .onEnded { value in
                        if -value.translation.height > 110 || -value.predictedEndTranslation.height > 220 { show(true) }
                        else { withAnimation(reduceMotion ? nil : ConversationLayout.pageMotion) { openingDistance = 0 } }
                    })
            if model.outgoing.contains(where: { $0.note == "Queued" }) {
                VStack(spacing: 4) {
                    ForEach(model.outgoing.filter { $0.note == "Queued" }) { message in
                        HStack(spacing: 8) {
                            Text("Queued").foregroundStyle(PanelsTheme.faint)
                            Text(message.text).lineLimit(1)
                            Spacer(minLength: 0)
                            if let held = model.heldPrompts.first(where: { ($0.senderMessageId ?? $0.heldPromptId) == message.id }) {
                                Button("Steer in") { changeQueue(held, discard: false) }
                                Button { changeQueue(held, discard: true) } label: { Image(systemName: "xmark").frame(width: 30, height: 36) }.accessibilityLabel("Remove queued message")
                            }
                        }.font(PanelsTheme.TypeScale.small).padding(.horizontal, 12).frame(minHeight: 36)
                            .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: 12))
                    }
                }.disabled(changingQueue).padding(.horizontal, 12)
            }
            if let error = model.error ?? speech.error {
                Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error).padding(.horizontal, 20)
            }
            if !model.attachments.isEmpty {
                HStack {
                    Text(model.attachments.compactMap(\.fileName).joined(separator: ", ")).lineLimit(1)
                    Button("Remove") { model.attachments = [] }
                }.font(PanelsTheme.TypeScale.small).padding(.horizontal, 20)
            }
            composer
        }
        .padding(.top, 8).padding(.bottom, 1)
        .background(PanelsTheme.raised)
        .overlay(alignment: .top) { Rectangle().fill(PanelsTheme.border).frame(height: 1) }
    }

    private var composer: some View {
        ZStack(alignment: .bottom) {
            // Rest and typed layouts share one field, preserving keyboard focus.
            // Speech owns input exclusively; its draft stays in the model while
            // the field leaves both the hit-test and accessibility hierarchies.
            if capturingSpeech {
                Color.clear.frame(height: ConversationLayout.restingHeight)
            } else {
                VStack(spacing: 0) {
                    field.padding(.horizontal, card ? 16 : 46)
                        .padding(.top, card ? 14 : 12).padding(.bottom, card ? 7 : 12)
                    Color.clear.frame(height: card ? 45 : 0)
                }
            }
            HStack(spacing: 0) {
                if speech.recording || speech.transcribing || speech.starting {
                    Button { speech.cancel() } label: { Image(systemName: "xmark").frame(width: 44, height: 44) }.accessibilityLabel("Cancel recording")
                    if speech.starting {
                        Text("Microphone…").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    } else {
                        HStack(spacing: 3) {
                            ForEach(0..<18) { index in
                                Capsule().fill(PanelsTheme.muted).frame(width: 2, height: 2 + CGFloat(speech.level) * CGFloat(8 + (index * 7 % 17)))
                            }
                        }.frame(maxWidth: .infinity, maxHeight: 24)
                            .animation(reduceMotion ? nil : .linear(duration: PanelsTheme.Motion.fast), value: speech.level)
                            .accessibilityLabel(speech.recording ? "Recording" : "Turning speech into text")
                        if speech.recording, let start = speech.startedAt { Text(start, style: .timer).monospacedDigit().font(PanelsTheme.TypeScale.small) }
                        else { ProgressView().controlSize(.mini) }
                    }
                    Spacer(minLength: 0)
                } else {
                    Menu {
                        Button("File", systemImage: "paperclip") { importFile = true }
                        Button("Picture", systemImage: "photo") { pickPicture = true }
                        Button("Photo", systemImage: "camera") { camera = true }.disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
                        Button("Command", systemImage: "slash.circle") { model.draft = "/"; focused = true }
                    } label: { Image(systemName: "plus").frame(width: 30, height: 30).background(PanelsTheme.raised, in: Circle()).frame(width: 44, height: 44) }.accessibilityLabel("Add attachment or command")
                        .simultaneousGesture(TapGesture().onEnded { focused = false })
                    if card {
                        Button { focused = false; modelSheet = true } label: {
                            HStack(spacing: 4) {
                                ConversationBackendMark(backend: model.backendKey ?? "codex")
                                Text(model.selectedModel?.components(separatedBy: "/").last ?? "Model").lineLimit(1)
                            }.font(PanelsTheme.TypeScale.small).padding(.horizontal, 8).frame(height: 30)
                                .overlay(Capsule().stroke(PanelsTheme.border))
                        }.accessibilityLabel("Choose model")
                        Menu {
                            Button("Steer") { model.deliveryMode = "steer"; Haptics.selection() }
                            Button("Queue") { model.deliveryMode = "queue"; Haptics.selection() }
                            Button("Send now") { model.deliveryMode = "send_now"; Haptics.selection() }
                        } label: { Text(model.deliveryMode == "send_now" ? "Send now" : model.deliveryMode.capitalized).font(PanelsTheme.TypeScale.small).padding(.horizontal, 8).frame(height: 44) }
                            .simultaneousGesture(TapGesture().onEnded { focused = false })
                        Spacer(minLength: 0)
                    } else { Spacer(minLength: 0) }
                }
                if !speech.recording && !speech.starting && !speech.transcribing && (card || stopsTurn) {
                    Button { startVoice() } label: { Image(systemName: "mic").frame(width: 36, height: 44) }.accessibilityLabel("Record voice input")
                }
                Button { primaryAction() } label: {
                    Image(systemName: speech.recording || stopsTurn ? "stop.fill" : card || !model.attachments.isEmpty ? "arrow.up" : "mic")
                        .font(.system(size: 15, weight: .medium)).foregroundStyle(PanelsTheme.ink)
                        .frame(width: 30, height: 30).background(PanelsTheme.strong, in: Circle())
                        .frame(width: 44, height: 44)
                }.disabled(speech.starting || speech.transcribing || model.sending)
                    .accessibilityLabel(speech.recording ? "Stop recording" : stopsTurn ? "Stop turn" : card || !model.attachments.isEmpty ? "Send message" : "Record voice input")
            }.padding(.horizontal, card ? 1 : 2).padding(.bottom, card ? 1 : 0)
        }
        .frame(minHeight: card ? ConversationLayout.cardHeight : ConversationLayout.restingHeight)
        .background(PanelsTheme.surface, in: RoundedRectangle(cornerRadius: card ? 22 : 24))
        .overlay(RoundedRectangle(cornerRadius: card ? 22 : 24).stroke(PanelsTheme.border))
        .padding(.horizontal, card ? ConversationLayout.cardInset : ConversationLayout.restingInset)
        .animation(reduceMotion ? nil : ConversationLayout.shapeMotion, value: card)
        .foregroundStyle(PanelsTheme.text)
    }
    private var field: some View {
        TextField("Message", text: $model.draft, axis: .vertical)
            .font(PanelsTheme.TypeScale.body).lineLimit(1...5).focused($focused)
            .disabled(capturingSpeech).allowsHitTesting(!capturingSpeech)
            .accessibilityHidden(capturingSpeech)
            .textFieldStyle(.plain).accessibilityIdentifier("conversation-input")
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
    private func primaryAction() {
        if speech.recording { speech.stop() }
        else if card || !model.attachments.isEmpty {
            Haptics.light()
            Task { if await model.send(to: owner, using: store.client) { Haptics.success() } else { Haptics.error() } }
        } else if stopsTurn, let id = model.conversationID {
            Haptics.light()
            Task {
                do { try await store.client.interruptConversation(id); await model.refresh(using: store.client, transcript: true) }
                catch { model.error = error.localizedDescription }
            }
        } else { startVoice() }
    }
    private func startVoice() {
        focused = false
        let prefix = model.draft
        Task { await speech.start { model.updateSpeechTranscript($0, prefix: prefix) } }
    }
}

private struct ConversationThread: View {
    @Bindable var model: ConversationModel
    let owner: ConversationOwner
    let opened: Bool
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var followsLatest = true
    @State private var scrolling = false
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
                                if row.item?.body?.reference != nil, let item = row.item, model.expandedBodies[item.key] == nil {
                                    Button("Read message") { Task { await model.loadBody(item, using: store.client) } }
                                } else { Text(.init(row.text)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true) }
                                if let note = row.note {
                                    HStack {
                                        if note == "Waiting for connection" { Image(systemName: "clock").foregroundStyle(PanelsTheme.faint) }
                                        Text(note).foregroundStyle(note.hasPrefix("Not sent") ? PanelsTheme.error : PanelsTheme.faint)
                                        if let id = row.retryID {
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
                Color.clear.frame(height: 0).id("latest")
                }.padding(.horizontal, 20).padding(.bottom, 12)
            }
            .contentMargins(.top, 12, for: .scrollContent)
            .task(id: opened) {
                guard opened else { return }
                await model.prepareOpening(using: store.client)
                guard !Task.isCancelled else { return }
                if let id = model.firstUnreadID { followsLatest = false; proxy.scrollTo(id, anchor: .top) }
                else { followsLatest = true; proxy.scrollTo("latest", anchor: .bottom) }
            }
            .defaultScrollAnchor(.bottom, for: .initialOffset)
            .defaultScrollAnchor(.top, for: .sizeChanges)
            .scrollDismissesKeyboard(.interactively)
            .onScrollPhaseChange { _, phase, context in
                scrolling = phase != .idle
                // Stop following at touch-down, before a live update can race the
                // first geometry callback. Rejoin only once the reader stops at bottom.
                if phase == .tracking || phase == .interacting { followsLatest = false }
                if phase == .idle {
                    followsLatest = context.geometry.contentSize.height - context.geometry.visibleRect.maxY < 60
                }
            }
            .onScrollGeometryChange(for: CGSize.self) { CGSize(width: $0.containerSize.height, height: $0.contentSize.height) } action: { _, _ in
                if followsLatest && !scrolling { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .onScrollGeometryChange(for: Bool.self) { geometry in
                geometry.contentSize.height - geometry.visibleRect.maxY < 60
            } action: { _, atBottom in
                if scrolling && !atBottom { followsLatest = false }
            }
            .onChange(of: model.rows.map { $0.id + $0.text + ($0.note ?? "") }) { _, _ in
                if followsLatest && !scrolling { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .overlay(alignment: .bottom) {
                if !followsLatest {
                    Button { followsLatest = true; withAnimation(reduceMotion ? nil : PanelsTheme.Motion.entrance) { proxy.scrollTo("latest", anchor: .bottom) } } label: {
                        Label("Latest", systemImage: "arrow.down").font(PanelsTheme.TypeScale.small).padding(.horizontal, 16).frame(height: 44)
                            .background(PanelsTheme.raised, in: Capsule()).overlay(Capsule().stroke(PanelsTheme.border))
                    }.padding(.bottom, 10)
                }
            }
        }
    }
}

private struct ConversationBackendMark: View {
    let backend: String
    var body: some View {
        Image("conversation-" + backend).renderingMode(backend == "hermes" ? .original : .template).resizable().scaledToFit().frame(width: 16, height: 16).accessibilityLabel(backend.capitalized)
    }
}

private struct ConversationModelSheet: View {
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
    func conversationOverlay(owner: ConversationOwner, conversationID: String?, title: String = "Conversation") -> some View {
        modifier(ConversationOverlay(owner: owner, conversationID: conversationID, title: title))
    }
}

#if DEBUG
struct ConversationSmokeView: View {
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text("Chief of Staff").font(.title.weight(.semibold))
                    Text("Keep the whole operation in view.").foregroundStyle(PanelsTheme.faint)
                    ForEach(0..<12) { Text("Worker \($0 + 1)").frame(maxWidth: .infinity, minHeight: 50, alignment: .leading); Divider() }
                }.padding(.horizontal, 20)
            }.panelsScreen().conversationOverlay(owner: .chief, conversationID: "stub", title: "Chief of Staff")
        }
    }
}
#endif

private struct ConversationCamera: UIViewControllerRepresentable {
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
