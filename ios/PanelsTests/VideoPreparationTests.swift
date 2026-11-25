import XCTest
@testable import Panels

final class VideoPreparationTests: XCTestCase {
    func testFirstMessageUploadIncludesExplicitNullConversation() throws {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let data = try encoder.encode(VideoUploadDestination(kind: "chief", id: "chief", expectedConversationId: nil))
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertTrue(body["expected_conversation_id"] is NSNull)
    }

    private func directory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testOriginalAtLimitCopiesBeforeProviderFileDisappearsWithoutExport() async throws {
        let root = try directory()
        let source = root.appendingPathComponent("provider.mov")
        let bytes = Data(repeating: 7, count: 32)
        try bytes.write(to: source)
        let saved = try await VideoAttachments.prepareFile(source, fileName: "clip.mov",
            directory: root.appendingPathComponent("saved"), maximumBytes: 32, progress: { _ in }) { _, _, _ in
                XCTFail("An original at the limit must not export")
            }
        try FileManager.default.removeItem(at: source)
        XCTAssertEqual(try Data(contentsOf: saved.fileURL(in: root)), bytes)
        XCTAssertEqual(saved.fileName, "clip.mov")
        XCTAssertNil(saved.preparationError)
    }

    func testSavedVideoResolvesAfterContainerRootMoves() async throws {
        let before = try directory()
        let source = before.appendingPathComponent("provider.mov")
        let bytes = Data("queued across update".utf8)
        try bytes.write(to: source)
        let saved = try await VideoAttachments.prepareFile(source, fileName: "clip.mov",
            directory: before.appendingPathComponent("videos"), progress: { _ in }) { _, _, _ in
                XCTFail("Small clip must not export")
            }
        let persisted = try JSONEncoder().encode(saved)
        let after = before.deletingLastPathComponent().appendingPathComponent(UUID().uuidString)
        try FileManager.default.moveItem(at: before, to: after)
        defer { try? FileManager.default.removeItem(at: after) }
        let restored = try JSONDecoder().decode(SavedVideo.self, from: persisted)
        XCTAssertTrue(restored.filePath.hasPrefix("videos/"))
        XCTAssertEqual(try Data(contentsOf: restored.fileURL(in: after)), bytes)
    }

    func testOversizeExportReplacesOriginalOnlyAfterValidOutput() async throws {
        let root = try directory()
        let source = root.appendingPathComponent("provider.mov")
        try Data(repeating: 7, count: 33).write(to: source)
        let saved = try await VideoAttachments.prepareFile(source, fileName: "clip.mov",
            directory: root.appendingPathComponent("saved"), maximumBytes: 32, progress: { _ in }) { input, output, progress in
                XCTAssertTrue(FileManager.default.fileExists(atPath: input.path))
                try Data(repeating: 1, count: 16).write(to: output)
                progress(1)
            }
        XCTAssertEqual(saved.byteCount, 16)
        XCTAssertEqual(saved.fileName, "clip.mp4")
        XCTAssertNil(saved.preparationError)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: saved.fileURL(in: root).deletingLastPathComponent().path).count, 1)
    }

    func testUnusableExportRetainsFailedAttachmentAndOriginal() async throws {
        let root = try directory()
        let source = root.appendingPathComponent("provider.mov")
        try Data(repeating: 7, count: 33).write(to: source)
        let saved = try await VideoAttachments.prepareFile(source, fileName: "clip.mov",
            directory: root.appendingPathComponent("saved"), maximumBytes: 32, progress: { _ in }) { _, output, _ in
                try Data(repeating: 1, count: 33).write(to: output)
            }
        XCTAssertNotNil(saved.preparationError)
        XCTAssertTrue(FileManager.default.fileExists(atPath: saved.fileURL(in: root).path))
        XCTAssertEqual(saved.byteCount, 33)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: saved.fileURL(in: root).deletingLastPathComponent().path).count, 1)
    }
}
