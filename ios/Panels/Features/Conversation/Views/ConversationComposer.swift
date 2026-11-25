import SwiftUI
import UniformTypeIdentifiers
import PhotosUI
import ImageIO
import CoreTransferable

struct ConversationComposer: View {
    @Bindable var model: ConversationModel
    let owner: ConversationOwner
    var reviewReply = false
    var placeholder: String? = nil
    var sendReply: (() async -> Bool)? = nil
    var statusNote: String?
    var retryReply = false
    var onCaptureChanged: ((Bool) -> Void)? = nil
    /// Off where the words go somewhere that takes no pictures or files, such as an ask's answer.
    var attaches = true
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State var speech = ConversationSpeech()
    var refreshError: String?
    var retry: (() async -> Void)?
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .body) private var iconSize = 18.0
    @State private var modelSheet = false
    @State private var attachOpen = false
    @State private var dropTargeted = false
    @Environment(\.screenTextEntry) private var textEntry
    @State private var inputID = UUID()
    // Unknown Mac activity changes the indicator; Stop still targets the saved turn, as on the web.
    private var stopsTurn: Bool { !reviewReply && model.status?.turnRunning == true && model.draft.isEmpty && model.attachments.isEmpty && !speech.recording }
    private var card: Bool { (!model.draft.isEmpty || !model.attachments.isEmpty) && !speech.recording && !speech.transcribing }
    private var capturingSpeech: Bool { speech.recording || speech.transcribing || speech.starting }

    var body: some View {
        composer
        .sheet(isPresented: $modelSheet) { ConversationModelSheet(model: model, owner: owner) }
        .attachmentPickers(isPresented: $attachOpen, model: model) {
            model.draft = "/"; textEntry?.requestFocus(inputID)
        }
        .onChange(of: capturingSpeech) { _, capturing in
            if capturing { textEntry?.endEditing() }
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
        if !model.attachments.isEmpty || model.preparingPhoto {
            ConversationAttachmentTray(pieces: model.attachments, preparingPhotoNames: model.preparingPhotos.values.sorted(),
                preparing: model.preparingDraftPhotos, preparingVideos: Array(model.preparingVideos.values)) { index in
                guard model.attachments.indices.contains(index) else { return }
                model.attachments.remove(at: index)
            }
        }
        if let statusNote {
            HStack {
                Text(statusNote).frame(maxWidth: .infinity, alignment: .leading)
                if retryReply {
                    Button("Retry reply") { Haptics.light(); Task { _ = await sendReply?() } }
                        .frame(minHeight: 44).disabled(capturingSpeech || model.preparingPhoto)
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
        .overlay {
            if dropTargeted { RoundedRectangle(cornerRadius: card ? 22 : 24).stroke(PanelsTheme.accent, lineWidth: 2) }
        }
        .onDrop(of: [.image], isTargeted: $dropTargeted) { providers in
            guard !capturingSpeech else { return false }
            for provider in providers { receiveImage(provider, fallbackName: "Dropped image") }
            return true
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversation-input-box")
        // In Review's dock the composer spans the row of buttons below it.
        .padding(.horizontal, reviewReply ? 0 : card ? ConversationLayout.cardInset : ConversationLayout.restingInset)
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
                    if attaches {
                        Button { textEntry?.endEditing(); attachOpen = true } label: { Image(systemName: "plus").font(.system(size: min(iconSize, 28))).frame(width: 36, height: 36).background(PanelsTheme.raised, in: Circle()).frame(width: 44, height: 44) }.accessibilityLabel("Add attachment or command")
                    }
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
                }.disabled(speech.starting || speech.transcribing || model.sending || model.preparingPhoto || model.failedVideo != nil)
                    .accessibilityLabel(speech.recording ? "Stop recording" : stopsTurn ? "Stop turn" : card || !model.attachments.isEmpty ? "Send message" : "Record voice input")
            }.padding(.horizontal, card ? 1 : 2).padding(.bottom, card ? 1 : 0)
    }

    @ViewBuilder private var configurationControls: some View {
        Button { textEntry?.endEditing(); modelSheet = true } label: {
            HStack(spacing: 4) {
                ConversationBackendMark(backend: DriverCatalog.composerBackend(model.backendKey, in: store.drivers))
                Text(model.selectedModel?.components(separatedBy: "/").last ?? "Model").lineLimit(1)
            }.font(PanelsTheme.TypeScale.small).padding(.horizontal, 8).padding(.vertical, 4)
                .frame(minHeight: 30).overlay(Capsule().stroke(PanelsTheme.border))
        }.accessibilityLabel("Choose model")
    }

    private var field: some View {
        TextField(placeholder ?? (reviewReply ? "Reply…" : "Message"), text: $model.draft, axis: .vertical)
            .font(PanelsTheme.TypeScale.body).lineLimit(1...5)
            .disabled(capturingSpeech).allowsHitTesting(!capturingSpeech)
            .accessibilityHidden(capturingSpeech)
            .textFieldStyle(.plain).accessibilityIdentifier("conversation-input")
            .modifier(ImagePaste(model: model, enabled: attaches))
            .screenTextInput(inputID)
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
    /// Loads one dropped image in its own format, then attaches it.
    private func receiveImage(_ provider: NSItemProvider, fallbackName: String) {
        let type = provider.registeredContentTypes.first { $0.conforms(to: .image) } ?? .png
        let name = provider.suggestedName ?? fallbackName
        _ = provider.loadDataRepresentation(for: type) { data, error in
            Task { @MainActor in
                if let data {
                    let fileName = (name as NSString).pathExtension.isEmpty ? name + "." + (type.preferredFilenameExtension ?? "png") : name
                    await model.attach(data, type: type, fileName: fileName)
                } else if let error { model.error = error.localizedDescription }
            }
        }
    }
    private func startVoice() {
        textEntry?.endEditing()
        let prefix = model.draft
        Task { await speech.start { model.updateSpeechTranscript($0, prefix: prefix) } }
    }
}

/// One tile for each attachment: the picture itself for images, and the type, name and
/// size for other files. Remove is omitted where the message has already left the composer.
struct ConversationAttachmentTray: View {
    let pieces: [ConversationSend.Piece]
    var preparingPhotoNames: [String] = []
    var preparing = false
    var preparingVideos: [ConversationModel.VideoPreparation] = []
    var remove: ((Int) -> Void)? = nil
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(Array(pieces.enumerated()), id: \.offset) { index, piece in
                    ConversationAttachmentTile(piece: piece, preparing: preparing && piece.piece == "image", remove: remove.map { action in { action(index) } })
                }
                ForEach(preparingVideos) { video in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(video.fileName).lineLimit(1).truncationMode(.middle)
                        ProgressView(value: video.progress).frame(width: 150)
                        Text(video.progress > 0 ? "Compressing video… \(Int(video.progress * 100))%" : "Preparing video…")
                            .foregroundStyle(PanelsTheme.muted)
                    }.font(PanelsTheme.TypeScale.small).padding(10)
                        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("conversation-video-preparing")
                }
                ForEach(Array(preparingPhotoNames.enumerated()), id: \.offset) { _, name in
                    HStack(spacing: 8) {
                        RingSpinner(size: .compact)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(name).lineLimit(1).truncationMode(.middle)
                            Text("Preparing photo…").foregroundStyle(PanelsTheme.muted)
                        }.font(PanelsTheme.TypeScale.small)
                    }
                    .padding(10).background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: 10))
                    .accessibilityIdentifier("conversation-photo-preparing")
                }
            }.padding(.vertical, 2)
        }
        .scrollClipDisabled()
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversation-attachments")
    }
}

struct ConversationAttachmentTile: View {
    let piece: ConversationSend.Piece
    var preparing = false
    var remove: (() -> Void)? = nil
    @State private var thumbnail: UIImage?
    @ScaledMetric(relativeTo: .body) private var side = 64.0
    private var name: String { piece.fileName ?? (piece.piece == "image" ? "Image" : "File") }
    private var kind: String { let ext = (name as NSString).pathExtension.uppercased(); return ext.isEmpty ? "FILE" : String(ext.prefix(4)) }
    private var byteCount: Int64 { piece.localVideo?.byteCount ?? Int64((piece.data?.utf8.count ?? 0) / 4 * 3) }
    var body: some View {
        Group {
            if piece.piece == "image" {
                ZStack {
                    PanelsTheme.raised
                    if let thumbnail { Image(uiImage: thumbnail).resizable().scaledToFill() } else { RingSpinner(size: .compact) }
                }
                .frame(width: side, height: side).clipShape(RoundedRectangle(cornerRadius: 10))
            } else {
                HStack(spacing: 8) {
                    Text(kind)
                        .font(PanelsTheme.TypeScale.caption.weight(.semibold)).foregroundStyle(PanelsTheme.muted)
                        .frame(width: 40, height: 40).background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name).font(PanelsTheme.TypeScale.small).lineLimit(1).truncationMode(.middle)
                        Text(ByteCountFormatter.string(fromByteCount: byteCount, countStyle: .file))
                            .font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                        if let error = piece.localVideo?.preparationError {
                            Text(error).font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                                .accessibilityIdentifier("conversation-video-error")
                        }
                    }.frame(maxWidth: 160, alignment: .leading)
                }
                .padding(.leading, 8).padding(.trailing, remove == nil ? 10 : 30)
                .frame(minHeight: side).background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: 10))
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(PanelsTheme.border))
        .overlay(alignment: .bottom) {
            if preparing {
                Text("Preparing photo…").font(PanelsTheme.TypeScale.caption)
                    .padding(3).background(PanelsTheme.surface)
            }
        }
        .overlay(alignment: .topTrailing) {
            if let remove {
                Button { Haptics.light(); remove() } label: {
                    Image(systemName: "xmark").font(.system(size: 10, weight: .bold)).foregroundStyle(PanelsTheme.text)
                        .frame(width: 22, height: 22).background(PanelsTheme.surface, in: Circle())
                        .overlay(Circle().stroke(PanelsTheme.border))
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .offset(x: 14, y: -14)
                .accessibilityLabel("Remove \(name)")
            }
        }
        .padding(.top, remove == nil ? 0 : 8).padding(.trailing, remove == nil ? 0 : 8)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(name)
        .accessibilityIdentifier("conversation-attachment")
        .task(id: piece) {
            guard piece.piece == "image", let encoded = piece.data else { return }
            let target = CGSize(width: side * 3, height: side * 3)
            thumbnail = await Task.detached(priority: .userInitiated) {
                guard let data = Data(base64Encoded: encoded), let image = UIImage(data: data) else { return nil }
                return await image.byPreparingThumbnail(ofSize: target) ?? image
            }.value
        }
    }
}

/// Pastes images from the edit menu. Text paste stays with the text field.
/// The SwiftUI paste hook exists from iOS 27 and the Xcode 27 SDK (Swift 6.4). Earlier
/// systems and older SDKs, such as CI's Xcode 26, keep picker and drag only.
/// Pasted pictures join the model's attachments.
struct ImagePaste: ViewModifier {
    let model: ConversationModel
    var enabled = true
    func body(content: Content) -> some View {
        #if compiler(>=6.4)
        if #available(iOS 27.0, *), enabled {
            content.pasteDestination(for: Data.self) { items in
                for data in items {
                    Task {
                        let type = await Task.detached(priority: .userInitiated) {
                            CGImageSourceCreateWithData(data as CFData, nil).flatMap(CGImageSourceGetType).flatMap { UTType($0 as String) } ?? .png
                        }.value
                        await model.attach(data, type: type, fileName: "Pasted image." + (type.preferredFilenameExtension ?? "png"))
                    }
                }
            } validator: { $0 }
        } else { content }
        #else
        content
        #endif
    }
}

/// Photos owns the received URL only during this import. Keep a private copy for preparation.
private struct PickedVideo: Transferable, Sendable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .movie) { received in
            let source = received.file
            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString).appendingPathExtension(source.pathExtension)
            try await Task.detached(priority: .userInitiated) {
                try FileManager.default.copyItem(at: source, to: destination)
            }.value
            return PickedVideo(url: destination)
        }
    }
}

/// The Attach sheet and the pickers it opens, adding to one composer model. The Command
/// row shows only where `command` is given.
private struct AttachmentPickers: ViewModifier {
    @Binding var isPresented: Bool
    let model: ConversationModel
    let command: (() -> Void)?
    /// The picker the Attach sheet chose. It opens once the sheet has closed.
    @State private var choice: AttachChoice?
    @State private var importFile = false
    @State private var pickPicture = false
    @State private var picture: PhotosPickerItem?
    @State private var camera = false

    func body(content: Content) -> some View {
        content
            .sheet(isPresented: $isPresented, onDismiss: open) {
                AttachSheet(cameraAvailable: UIImagePickerController.isSourceTypeAvailable(.camera), commands: command != nil) { choice = $0 }
            }
            .photosPicker(isPresented: $pickPicture, selection: $picture, matching: .any(of: [.images, .videos]), preferredItemEncoding: .current)
            .onChange(of: picture) { _, selected in
                guard let selected else { return }
                Task {
                    do {
                        if let type = selected.supportedContentTypes.first(where: { $0.conforms(to: .movie) }) {
                            await model.attachVideo(fileName: "Video." + (type.preferredFilenameExtension ?? "mov"), load: {
                                guard let video = try await selected.loadTransferable(type: PickedVideo.self) else {
                                    throw CocoaError(.fileReadUnknown)
                                }
                                return video.url
                            }, release: { try? FileManager.default.removeItem(at: $0) })
                        } else {
                            guard let bytes = try await selected.loadTransferable(type: Data.self) else { return }
                            let type = selected.supportedContentTypes.first ?? .jpeg
                            await model.attach(bytes, type: type, fileName: "Picture." + (type.preferredFilenameExtension ?? "jpg"))
                        }
                        picture = nil
                    } catch { model.error = error.localizedDescription }
                }
            }
            .sheet(isPresented: $camera) {
                ConversationCamera { capture in
                    camera = false
                    switch capture {
                    case .photo(let image):
                        Task { await model.attachPhoto(fileName: "Photo.jpg") { image.jpegData(compressionQuality: 0.85) } }
                    case .video(let url):
                        Task { await model.attachVideo(fileName: url.lastPathComponent, load: { url },
                                                      release: { try? FileManager.default.removeItem(at: $0) }) }
                    case .failure(let message):
                        Task {
                            await model.attachVideo(fileName: "Video.mov", load: {
                                throw NSError(domain: "VideoAttachment", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
                            })
                        }
                    case nil: break
                    }
                }.ignoresSafeArea()
            }
            .fileImporter(isPresented: $importFile, allowedContentTypes: [.data]) { result in
                do {
                    let url = try result.get()
                    Task {
                        let access = url.startAccessingSecurityScopedResource()
                        defer { if access { url.stopAccessingSecurityScopedResource() } }
                        let type = UTType(filenameExtension: url.pathExtension)
                        if type?.conforms(to: .movie) == true || ["mp4", "mov", "webm", "m4v"].contains(url.pathExtension.lowercased()) {
                            await model.attachVideo(fileName: url.lastPathComponent, load: { url })
                        } else {
                            do {
                                let data = try await Task.detached(priority: .userInitiated) { try Data(contentsOf: url) }.value
                                await model.attach(data, type: type, fileName: url.lastPathComponent)
                            } catch { model.error = error.localizedDescription }
                        }
                    }
                } catch { model.error = error.localizedDescription }
            }
    }
    private func open() {
        defer { choice = nil }
        switch choice {
        case .picture: pickPicture = true
        case .camera: camera = true
        case .file: importFile = true
        case .command: command?()
        case nil: break
        }
    }
}

extension View {
    func attachmentPickers(isPresented: Binding<Bool>, model: ConversationModel, command: (() -> Void)? = nil) -> some View {
        modifier(AttachmentPickers(isPresented: isPresented, model: model, command: command))
    }
}
