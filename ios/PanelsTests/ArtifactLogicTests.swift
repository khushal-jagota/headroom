import XCTest
@testable import Panels

final class ArtifactLogicTests: XCTestCase {
    private let base = URL(string: "https://panels.example")!

    func testCatalogLinkKeepsActualOwnerAndEncodesFileNamesOnce() throws {
        let entry = ArtifactEntry(key: "linked", path: "artifacts/Plan #1.html", label: "Plan", kind: "html", href: "/files/managers/si_other/artifacts/Plan%20%231.html", directory: nil, childCount: 0)
        let target = try XCTUnwrap(ArtifactTarget.from(entry: entry, owner: .init(kind: .ticket, id: "t_catalog"), baseURL: base))
        XCTAssertEqual(target.owner, .init(kind: .manager, id: "si_other"))
        XCTAssertEqual(target.path, "artifacts/Plan #1.html")
        let copied = try XCTUnwrap(ArtifactTarget.from(target.previewURL(baseURL: base), baseURL: base))
        XCTAssertEqual(copied.owner, target.owner)
        XCTAssertEqual(copied.path, target.path)
        XCTAssertEqual(try APIClient(baseURL: base).artifactURL(target).absoluteString, "https://panels.example/files/managers/si_other/artifacts/Plan%20%231.html")
        let preview = URL(string: "https://panels.example/#/preview?source=ticket&ticket=t_linked&path=artifacts%2FPlan%20%231.md")!
        let previewTarget = try XCTUnwrap(ArtifactTarget.from(preview, baseURL: base))
        XCTAssertEqual(previewTarget.owner.id, "t_linked")
        XCTAssertEqual(previewTarget.path, "artifacts/Plan #1.md")
        XCTAssertNil(ArtifactTarget.from(URL(string: "https://elsewhere.example/files/tickets/t_a/file.md")!, baseURL: base))
    }

    func testUnsafeManagedPathsCannotBecomeFileRequests() {
        for path in ["../secret", "/absolute", "a//b", "a\\b", "a/%2e%2e/b", "a/%252f/b"] {
            let target = ArtifactTarget(owner: .init(kind: .ticket, id: "t_a"), path: path, title: "File")
            XCTAssertThrowsError(try APIClient(baseURL: base).artifactURL(target), path)
        }
        XCTAssertTrue(ArtifactTarget.isSafePath("artifacts/a file #1.md"))
    }

    func testMarkdownKeepsCodeLiteralAndDoesNotLoseUnclosedFence() {
        let blocks = ArtifactMarkdownBlock.parse("# Plan\n\n- [Read](other.md)\n\n```swift\n# not a heading\nlet x = 1\n```\n\n> Caveat\n\n```\nunfinished")
        XCTAssertEqual(blocks.map(\.kind), [.heading(1), .list, .code, .quote, .code])
        XCTAssertEqual(blocks[1].text, "• [Read](other.md)")
        XCTAssertEqual(blocks[2].text, "# not a heading\nlet x = 1")
        XCTAssertEqual(blocks.last?.text, "unfinished")
    }

    func testProseLoadsOnlyLinkedPhotosAndVideos() throws {
        let fileExtensions = ["txt", "md", "json", "html", "log", "csv", "pdf", "svg", "mp3", "m4a", "zip", "unknown"]
        let files = fileExtensions.map { "[Read \($0)](/files/tickets/t_other/artifacts/file.\($0))" }.joined(separator: " ")
        let extraPhotoExtensions = ["GIF", "tif", "tiff"]
        let extraPhotos = extraPhotoExtensions.map { "[Photo \($0)](/files/tickets/t_other/artifacts/photo.\($0))" }.joined(separator: " ")
        let source = "Read [Migration verification](/files/tickets/t_8rmd1ebk/artifacts/migration-verification.txt) before approval. " + files + " [Review **photo**](/files/tickets/t_other/artifacts/photo.PNG) " + extraPhotos + " [Video](/files/managers/si_other/artifacts/clip.mp4)"
        let targets = ArtifactInlineMedia.targets(in: source, baseURL: base)
        XCTAssertEqual(targets.map(\.path), ["artifacts/photo.PNG", "artifacts/photo.GIF", "artifacts/photo.tif", "artifacts/photo.tiff", "artifacts/clip.mp4"])
        XCTAssertEqual(targets.map(\.title), ["Review photo", "Photo GIF", "Photo tif", "Photo tiff", "Video"])
        XCTAssertEqual(targets.map(\.owner), Array(repeating: ArtifactOwner(kind: .ticket, id: "t_other"), count: 4) + [.init(kind: .manager, id: "si_other")])
        let rich = try AttributedString(markdown: source, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace), baseURL: base)
        XCTAssertTrue(String(rich.characters).contains("Read Migration verification before approval."))
    }

    func testInlineMediaIgnoresCodeExternalLinksAndDuplicateTargets() {
        let source = """
        `[Literal](/files/tickets/t_a/literal.png)`

        ```markdown
        [Fenced](/files/tickets/t_a/fenced.mov)
        ```

        [External](https://elsewhere.example/files/tickets/t_a/external.jpg)
        ![Photo](/files/tickets/t_a/photo.jpg)
        [Again](/files/tickets/t_a/photo.jpg)
        [Video](/files/tickets/t_a/clip.MOV)
        [Other video](/files/tickets/t_a/other.m4v)
        """
        let targets = ArtifactInlineMedia.targets(in: source, baseURL: base)
        XCTAssertEqual(targets.map(\.path), ["photo.jpg", "clip.MOV", "other.m4v"])
        XCTAssertEqual(targets.first?.title, "Photo")
    }

}
