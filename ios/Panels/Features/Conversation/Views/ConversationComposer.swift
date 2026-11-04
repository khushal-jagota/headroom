import SwiftUI
import UniformTypeIdentifiers
import PhotosUI

struct ConversationComposer: View {
    @Bindable var model: ConversationModel
    let owner: ConversationOwner
    var reviewReply = false
    var sendReply: (() async -> Bool)? = nil
    var statusNote: String?
    var retryReply = false
    var onCaptureChanged: ((Bool) -> Void)? = nil
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State var speech = ConversationSpeech()
    var refreshError: String?
    var retry: (() async -> Void)?
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .body) private var iconSize = 18.0
    @State private var modelSheet = false
    @State private var importFile = false
    @State private var pickPicture = false
    @State private var picture: PhotosPickerItem?
    @State private var camera = false
    @FocusState private var focused: Bool
    private var stopsTurn: Bool { !reviewReply && model.status?.turnRunning == true && model.draft.isEmpty && model.attachments.isEmpty && !speech.recording }
    private var card: Bool { (!model.draft.isEmpty || !model.attachments.isEmpty) && !speech.recording && !speech.transcribing }
    private var capturingSpeech: Bool { speech.recording || speech.transcribing || speech.starting }

    var body: some View {
        composer
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
        .onChange(of: capturingSpeech) { _, capturing in
            if capturing { focused = false }
            onCaptureChanged?(capturing)
        }
        .onChange(of: scenePhase) { _, phase in if phase != .active { speech.cancel() } }
        .onDisappear { speech.cancel() }
    }
    @ViewBuilder private var feedback: some View {
        if let error = speech.error ?? model.error ?? refreshError {
            HStack(alignment: .center) {
                Text(error).frame(maxWidth: .infinity, alignment: .leading)
                Button("Try again") {
                    Task {
                        if speech.error != nil { startVoice() }
                        else {
                            await retry?()
                            await model.refresh(using: store.client, transcript: true)
                        }
                    }
                }.frame(minHeight: 44)
            }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                .accessibilityIdentifier("conversation-error")
        }
        if !model.attachments.isEmpty {
            HStack {
                Text(model.attachments.compactMap(\.fileName).joined(separator: ", ")).lineLimit(1)
                Button("Remove") { model.attachments = [] }
            }.font(PanelsTheme.TypeScale.small)
        }
        if let statusNote {
            HStack {
                Text(statusNote).frame(maxWidth: .infinity, alignment: .leading)
                if retryReply {
                    Button("Retry reply") { Haptics.light(); Task { _ = await sendReply?() } }
                        .frame(minHeight: 44).disabled(capturingSpeech)
                }
            }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
        }
    }

    private var composer: some View {
        VStack(spacing: 0) {
            VStack(spacing: 8) { feedback }
                .padding(.horizontal, 16)
            ZStack(alignment: .bottom) {
                if capturingSpeech {
                    Color.clear.frame(height: ConversationLayout.restingHeight)
                } else {
                    VStack(spacing: 0) {
                        field.padding(.horizontal, card ? 16 : 46)
                            .padding(.top, card ? 14 : 12).padding(.bottom, card ? 7 : 12)
                        if card {
                            if typeSize.isAccessibilitySize && !reviewReply {
                                VStack(alignment: .leading, spacing: 4) { configurationControls }
                                    .padding(.horizontal, 12)
                            }
                            controls
                        }
                    }
                }
                if !card || capturingSpeech { controls }

            }
            .frame(minHeight: card ? ConversationLayout.cardHeight : ConversationLayout.restingHeight)
        }
        .background(PanelsTheme.surface, in: RoundedRectangle(cornerRadius: card ? 22 : 24))
        .overlay(RoundedRectangle(cornerRadius: card ? 22 : 24).stroke(PanelsTheme.border))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversation-input-box")
        .padding(.horizontal, card ? ConversationLayout.cardInset : ConversationLayout.restingInset)
        .animation(reduceMotion ? nil : ConversationLayout.shapeMotion, value: card)
        .foregroundStyle(PanelsTheme.text)
    }
    private var controls: some View {
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
                        else { RingSpinner(size: .compact) }
                    }
                    Spacer(minLength: 0)
                } else {
                    Menu {
                        Button("File", systemImage: "paperclip") { importFile = true }
                        Button("Picture", systemImage: "photo") { pickPicture = true }
                        Button("Photo", systemImage: "camera") { camera = true }.disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
                        Button("Command", systemImage: "slash.circle") { model.draft = "/"; focused = true }
                    } label: { Image(systemName: "plus").font(.system(size: min(iconSize, 28))).frame(width: 36, height: 36).background(PanelsTheme.raised, in: Circle()).frame(width: 44, height: 44) }.accessibilityLabel("Add attachment or command")
                    if card && !reviewReply && !typeSize.isAccessibilitySize { configurationControls }
                    Spacer(minLength: 0)
                }
                if !speech.recording && !speech.starting && !speech.transcribing && (card || stopsTurn) {
                    Button { startVoice() } label: { Image(systemName: "mic").font(.system(size: min(iconSize, 28))).frame(width: 44, height: 44) }.accessibilityLabel("Record voice input")
                }
                Button { primaryAction() } label: {
                    Image(systemName: speech.recording || stopsTurn ? "stop.fill" : card || !model.attachments.isEmpty ? "arrow.up" : "mic")
                        .font(.system(size: min(iconSize, 28), weight: .medium)).foregroundStyle(PanelsTheme.ink)
                        .frame(width: typeSize.isAccessibilitySize ? 40 : 30, height: typeSize.isAccessibilitySize ? 40 : 30).background(PanelsTheme.strong, in: Circle())
                        .frame(width: 44, height: 44)
                }.disabled(speech.starting || speech.transcribing || model.sending)
                    .accessibilityLabel(speech.recording ? "Stop recording" : stopsTurn ? "Stop turn" : card || !model.attachments.isEmpty ? "Send message" : "Record voice input")
            }.padding(.horizontal, card ? 1 : 2).padding(.bottom, card ? 1 : 0)
    }

    @ViewBuilder private var configurationControls: some View {
        Button { focused = false; modelSheet = true } label: {
            HStack(spacing: 4) {
                ConversationBackendMark(backend: model.backendKey ?? "codex")
                Text(model.selectedModel?.components(separatedBy: "/").last ?? "Model").lineLimit(1)
            }.font(PanelsTheme.TypeScale.small).padding(.horizontal, 8).padding(.vertical, 4)
                .frame(minHeight: 30).overlay(Capsule().stroke(PanelsTheme.border))
        }.accessibilityLabel("Choose model")
        Menu {
            Button("Steer") { model.deliveryMode = "steer"; Haptics.selection() }
            Button("Queue") { model.deliveryMode = "queue"; Haptics.selection() }
            Button("Send now") { model.deliveryMode = "send_now"; Haptics.selection() }
        } label: {
            Text(model.deliveryMode == "send_now" ? "Send now" : model.deliveryMode.capitalized)
                .font(PanelsTheme.TypeScale.small).padding(.horizontal, 8).frame(minHeight: 44)
        }
    }

    private var field: some View {
        TextField(reviewReply ? "Reply…" : "Message", text: $model.draft, axis: .vertical)
            .font(PanelsTheme.TypeScale.body).lineLimit(1...5).focused($focused)
            .disabled(capturingSpeech).allowsHitTesting(!capturingSpeech)
            .accessibilityHidden(capturingSpeech)
            .textFieldStyle(.plain).accessibilityIdentifier("conversation-input")
    }
    private func primaryAction() {
        if speech.recording { speech.stop() }
        else if card || !model.attachments.isEmpty {
            Haptics.light()
            Task {
                let accepted: Bool
                if let sendReply { _ = await sendReply(); return }
                else { accepted = await model.send(to: owner, using: store.client) }
                if accepted { Haptics.success() } else { Haptics.error() }
            }
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
