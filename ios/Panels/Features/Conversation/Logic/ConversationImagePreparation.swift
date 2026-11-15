import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Match conversation intake without changing files or already-recorded outgoing intents.
enum ConversationImagePreparation {
    static let maximumImageBytes = 3 * 1024 * 1024
    private static let maximumWorkingDimension = 4096
    private static let validationDimension = 64
    private static let jpegQualities = [0.85, 0.65, 0.45]
    private static let downscaleNumerator = 3
    private static let downscaleDenominator = 4
    enum Failure: LocalizedError {
        case unreadable, cannotFit
        var errorDescription: String? {
            switch self {
            case .unreadable: "This photo could not be read. Choose another photo. Your draft is kept."
            case .cannotFit: "These photos could not fit in one message. Remove a photo and try again. Your draft is kept."
            }
        }
    }
    static func prepare(_ pieces: [ConversationSend.Piece]) throws -> [ConversationSend.Piece] {
        let indices = pieces.indices.filter { pieces[$0].piece == "image" }
        guard !indices.isEmpty else { return pieces }
        var result = pieces
        var sources: [Int: CGImageSource] = [:]
        var bytes: [Int: Data] = [:]
        for index in indices {
            guard let encoded = pieces[index].data, let data = Data(base64Encoded: encoded),
                  let source = CGImageSourceCreateWithData(data as CFData, nil),
                  CGImageSourceGetCount(source) > 0,
                  thumbnail(source, side: validationDimension) != nil else { throw Failure.unreadable }
            sources[index] = source
            bytes[index] = data
            if let mediaType = supportedMediaType(data) {
                result[index].mediaType = mediaType
                let fileExtension = ["image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"][mediaType]!
                result[index].fileName = ((pieces[index].fileName ?? "Photo") as NSString).deletingPathExtension + "." + fileExtension
            } else {
                let converted = try jpeg(source, budget: maximumImageBytes)
                bytes[index] = converted
                result[index] = jpegPiece(pieces[index], data: converted)
            }
        }
        if bytes.values.reduce(0, { $0 + $1.count }) > maximumImageBytes {
            // A fixed share bounds the whole message and needs no quality-allocation policy.
            let budget = maximumImageBytes / indices.count
            guard budget > 0 else { throw Failure.cannotFit }
            for index in indices where bytes[index]!.count > budget {
                let data = try jpeg(sources[index]!, budget: budget)
                result[index] = jpegPiece(pieces[index], data: data)
            }
        }
        return result
    }
    private static func jpegPiece(_ piece: ConversationSend.Piece, data: Data) -> ConversationSend.Piece {
        var result = piece
        result.data = data.base64EncodedString()
        result.mediaType = "image/jpeg"
        result.fileName = ((piece.fileName ?? "Photo") as NSString).deletingPathExtension + ".jpg"
        return result
    }
    private static func supportedMediaType(_ data: Data) -> String? {
        if data.starts(with: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) { return "image/png" }
        if data.starts(with: [0xff, 0xd8, 0xff]) { return "image/jpeg" }
        if data.starts(with: Data("GIF87a".utf8)) || data.starts(with: Data("GIF89a".utf8)) { return "image/gif" }
        if data.starts(with: Data("RIFF".utf8)), data.count >= 12, data[8..<12] == Data("WEBP".utf8) { return "image/webp" }
        return nil
    }
    private static func thumbnail(_ source: CGImageSource, side: Int) -> CGImage? {
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true, kCGImageSourceThumbnailMaxPixelSize: side]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }
    private static func jpeg(_ source: CGImageSource, budget: Int) throws -> Data {
        guard let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int else { throw Failure.unreadable }
        var side = min(maximumWorkingDimension, max(width, height))
        while side > 0 {
            guard let image = thumbnail(source, side: side) else { throw Failure.unreadable }
            for quality in jpegQualities {
                let data = NSMutableData()
                guard let destination = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else { throw Failure.unreadable }
                CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
                guard CGImageDestinationFinalize(destination) else { throw Failure.unreadable }
                if data.length <= budget { return data as Data }
            }
            side = side > 1 ? max(1, side * downscaleNumerator / downscaleDenominator) : 0
        }
        throw Failure.cannotFit
    }
}
