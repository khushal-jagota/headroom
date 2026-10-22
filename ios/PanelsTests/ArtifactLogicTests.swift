import XCTest
@testable import Panels

final class ArtifactLogicTests: XCTestCase {
    private let base = URL(string: "https://panels.example")!

    func testCatalogLinkKeepsActualOwnerAndEncodesFileNamesOnce() throws {
        let entry = ArtifactEntry(key: "linked", path: "artifacts/Plan #1.html", label: "Plan", kind: "html", href: "/files/managers/si_other/artifacts/Plan%20%231.html", directory: nil, childCount: 0)
        let target = try XCTUnwrap(ArtifactTarget.from(entry: entry, owner: .init(kind: .ticket, id: "t_catalog"), baseURL: base))
        XCTAssertEqual(target.owner, .init(kind: .manager, id: "si_other"))
        XCTAssertEqual(target.path, "artifacts/Plan #1.html")
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
}
