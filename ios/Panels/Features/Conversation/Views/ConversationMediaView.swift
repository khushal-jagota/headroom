import AVKit
import ImageIO
import SwiftUI
import UIKit
import QuickLook

/// Conversation-only type sizes. Review cards keep their compact presentation.
enum ConversationTypography {
    static let body = Font.subheadline
    static let metadata = Font.footnote
    static let caption = Font.caption2
}

/// Stored attachments remain typed all the way to this view. A matching outgoing
/// picture stays visible while the server's stored replacement loads.
struct ConversationMediaTile: View {
    let piece: ConversationBody.Piece
    let conversationID: String?
    var localFallback: ConversationSend.Piece? = nil
    @Environment(AppStore.self) private var store
    @State private var document: ConversationMediaFile?
    @State private var thumbnail: UIImage?
    @State private var error: String?
    @State private var showing = false
    @State private var attemptedIdentity: String?
    private var requestIdentity: String { "\(store.baseURL)|\(conversationID ?? "")|\(piece.storedFileId ?? "")" }
    private var name: String { piece.fileName ?? (piece.piece == "image" ? "Image" : "File") }
    private var isImage: Bool { piece.piece == "image" || piece.mediaType?.hasPrefix("image/") == true }
    private var isVideo: Bool { piece.mediaType?.hasPrefix("video/") == true }
    private var media: Bool { isImage || isVideo }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { showing = true } label: {
                if media {
                    ZStack {
                        RoundedRectangle(cornerRadius: 10).fill(PanelsTheme.raised)
                        if let thumbnail {
                            Image(uiImage: thumbnail).resizable().scaledToFit().frame(maxHeight: 240)
                        } else if error == nil { RingSpinner(size: .compact).frame(height: 140) }
                        else { Image(systemName: isVideo ? "video" : "photo").frame(height: 140).foregroundStyle(PanelsTheme.faint) }
                        if isVideo {
                            Image(systemName: "play.fill").foregroundStyle(.white).padding(14).background(.black.opacity(0.65), in: Circle())
                        }
                    }
                    .frame(maxWidth: .infinity).clipShape(RoundedRectangle(cornerRadius: 10))
                } else {
                    Label(name, systemImage: "doc").font(ConversationTypography.body)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                }
            }
            .buttonStyle(.plain).disabled(document == nil)
            .accessibilityLabel(isVideo ? "Play \(name)" : isImage ? "Open \(name) full screen" : "Open \(name)")
            .accessibilityIdentifier(isVideo ? "conversation-video" : isImage ? "conversation-image" : "conversation-file")
            if let error {
                HStack(alignment: .firstTextBaseline) {
                    Text(error).foregroundStyle(PanelsTheme.muted)
                    Button("Try again") { Task { await load() } }
                }.font(ConversationTypography.metadata)
            }
        }
        .task(id: requestIdentity) { await load() }
        .fullScreenCover(isPresented: $showing) {
            if let document {
                ConversationMediaViewer(document: document, name: name, isImage: isImage, isVideo: isVideo)
            }
        }
    }

    private func load() async {
        guard !Task.isCancelled else { return }
        let identity = requestIdentity
        if attemptedIdentity != identity {
            document = nil; thumbnail = nil; error = nil; showing = false
            attemptedIdentity = identity
        }
        guard document == nil else { return }
        error = nil
        let client = store.client
        do {
            if thumbnail == nil, let encoded = localFallback?.data {
                let localThumbnail = await Task.detached(priority: .userInitiated) {
                    guard let data = Data(base64Encoded: encoded) else { return nil as UIImage? }
                    return Self.imageThumbnail(data: data)
                }.value
                try Task.checkCancellation()
                guard identity == attemptedIdentity else { return }
                thumbnail = localThumbnail
            }
            guard let conversationID, let id = piece.storedFileId else {
                throw APIError(status: 0, message: "This attachment is unavailable.")
            }
            let downloaded = try await client.downloadConversationFile(conversationID, storedFileID: id, fileName: piece.fileName)
            if isVideo {
                let asset = AVURLAsset(url: downloaded.url)
                guard try await asset.load(.isPlayable) else { throw APIError(status: 0, message: "This video could not be opened.") }
            }
            let video = isVideo, image = isImage
            let preview = await Task.detached(priority: .userInitiated) {
                if image, let data = try? Data(contentsOf: downloaded.url) { return Self.imageThumbnail(data: data) }
                if video {
                    let generator = AVAssetImageGenerator(asset: AVURLAsset(url: downloaded.url))
                    generator.appliesPreferredTrackTransform = true
                    generator.maximumSize = CGSize(width: 900, height: 900)
                    if let frame = try? generator.copyCGImage(at: .zero, actualTime: nil) { return UIImage(cgImage: frame) }
                }
                return nil as UIImage?
            }.value
            try Task.checkCancellation()
            guard identity == attemptedIdentity else { return }
            if isImage, preview == nil { throw APIError(status: 0, message: "This image could not be opened.") }
            if let preview { thumbnail = preview }
            document = downloaded; error = nil
        } catch {
            if !Task.isCancelled, identity == attemptedIdentity {
                self.error = ServerReadFailure.note(error) ?? "This attachment could not load. Try again."
            }
        }
    }

    nonisolated private static func imageThumbnail(data: Data) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 900
              ] as CFDictionary) else { return nil }
        return UIImage(cgImage: image)
    }
}

private struct ConversationMediaViewer: View {
    let document: ConversationMediaFile
    let name: String
    let isImage: Bool
    let isVideo: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?
    @State private var player: AVPlayer?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if let image { ArtifactImageView(image: image).accessibilityLabel(name) }
                else if let player { VideoPlayer(player: player).onDisappear { player.pause() } }
                else if let error { Text(error).foregroundStyle(PanelsTheme.muted).padding() }
                else if !isImage && !isVideo { ConversationFilePreview(url: document.url) }
                else { RingSpinner() }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(PanelsTheme.surface)
            .navigationTitle(name).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() }.accessibilityIdentifier("conversation-media-close") } }
        }
        .preferredColorScheme(.dark)
        .task {
            if isImage {
                image = await Task.detached(priority: .userInitiated) { UIImage(contentsOfFile: document.url.path) }.value
                if image == nil { error = "This image could not be opened." }
            } else if isVideo { player = AVPlayer(url: document.url); player?.play() }
        }
    }
}

private struct ConversationFilePreview: UIViewControllerRepresentable {
    let url: URL
    func makeCoordinator() -> Coordinator { Coordinator(url: url) }
    func makeUIViewController(context: Context) -> QLPreviewController {
        let controller = QLPreviewController()
        controller.dataSource = context.coordinator
        return controller
    }
    func updateUIViewController(_ controller: QLPreviewController, context: Context) {}
    final class Coordinator: NSObject, QLPreviewControllerDataSource {
        let url: URL
        init(url: URL) { self.url = url }
        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }
        func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> any QLPreviewItem { url as NSURL }
    }
}
