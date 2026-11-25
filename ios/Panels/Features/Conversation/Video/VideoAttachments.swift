import AVFoundation
import Foundation

enum VideoAttachments {
    typealias Export = @Sendable (URL, URL, @escaping @Sendable (Double) -> Void) async throws -> Void

    /// Complete the copy while the picker still owns its source URL.
    static func importFile(_ url: URL, fileName: String,
                           progress: @escaping @Sendable (Double) -> Void) async throws -> SavedVideo {
        try await prepareFile(url, fileName: fileName,
                              directory: LocalStore.directory.appendingPathComponent("videos", isDirectory: true),
                              progress: progress, export: exportVideo)
    }

    /// The export boundary lets host tests prove the size policy without a large video fixture.
    static func prepareFile(_ url: URL, fileName: String, directory: URL,
                            maximumBytes: Int64 = VideoAttachmentPolicy.maximumBytes,
                            progress: @escaping @Sendable (Double) -> Void,
                            export: @escaping Export) async throws -> SavedVideo {
        try await Task.detached(priority: .userInitiated) {
            let manager = FileManager.default
            try manager.createDirectory(at: directory, withIntermediateDirectories: true)
            let id = UUID().uuidString
            let suffix = (fileName as NSString).pathExtension
            let original = directory.appendingPathComponent(id + (suffix.isEmpty ? "" : "." + suffix))
            let byteCount: Int64
            do {
                try manager.copyItem(at: url, to: original)
                byteCount = try size(original)
            } catch {
                try? manager.removeItem(at: original)
                throw error
            }
            guard byteCount > maximumBytes else {
                progress(1)
                return SavedVideo(id: id, fileName: fileName, filePath: original.path, byteCount: byteCount)
            }
            let compressed = directory.appendingPathComponent(id + "-compressed.mp4")
            do {
                progress(0)
                try await export(original, compressed, progress)
                let compressedBytes = try size(compressed)
                guard compressedBytes > 0, compressedBytes <= maximumBytes else {
                    throw APIError(status: 413, message: "This video is still larger than 250 MiB after compression. Choose a shorter clip.")
                }
                try manager.removeItem(at: original)
                progress(1)
                let name = (fileName as NSString).deletingPathExtension + ".mp4"
                return SavedVideo(id: id, fileName: name, filePath: compressed.path, byteCount: compressedBytes)
            } catch {
                try? manager.removeItem(at: compressed)
                return SavedVideo(id: id, fileName: fileName, filePath: original.path,
                                  byteCount: byteCount, preparationError: error.localizedDescription)
            }
        }.value
    }

    private static func size(_ url: URL) throws -> Int64 {
        let values = try url.resourceValues(forKeys: [.fileSizeKey])
        guard let count = values.fileSize else { throw CocoaError(.fileReadUnknown) }
        return Int64(count)
    }

    private static func exportVideo(_ source: URL, _ output: URL,
                                    progress: @escaping @Sendable (Double) -> Void) async throws {
        let asset = AVURLAsset(url: source)
        let presets = AVAssetExportSession.exportPresets(compatibleWith: asset)
        let preset = presets.contains(AVAssetExportPresetHEVC1920x1080)
            ? AVAssetExportPresetHEVC1920x1080 : AVAssetExportPreset1920x1080
        guard let session = AVAssetExportSession(asset: asset, presetName: preset) else {
            throw APIError(status: 0, message: "This video cannot be compressed. Choose another clip.")
        }
        let states = session.states(updateInterval: 0.25)
        let monitor = Task {
            for await state in states {
                if Task.isCancelled { break }
                if case .exporting(let value) = state { progress(value.fractionCompleted) }
            }
        }
        defer { monitor.cancel() }
        try await session.export(to: output, as: .mp4)
    }
}
