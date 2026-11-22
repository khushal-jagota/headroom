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
        let blocks = ArtifactMarkdownBlock.parse("# Plan\n\n- [Read](other.md)\n\n```swift\n# not a heading\nlet x = 1\n```\n\n> Caveat\n\n````markdown\n```swift\ninner\n```\n````\n\n```\nunfinished")
        XCTAssertEqual(blocks.map(\.kind), [.heading(1), .list, .code, .quote, .code, .code])
        XCTAssertEqual(blocks[1].text, "• [Read](other.md)")
        XCTAssertEqual(blocks[2].text, "# not a heading\nlet x = 1")
        XCTAssertEqual(blocks[4].text, "```swift\ninner\n```")
        XCTAssertEqual(blocks.last?.text, "unfinished")
        XCTAssertEqual(ArtifactMarkdownBlock.parse("```npm test``` runs it\n\nAfter").map(\.kind), [.paragraph, .paragraph])
    }

    // Risk: a chart the web would leave as code (or the reverse) draws differently on the phone.
    // Gap: no earlier test reads a fence's language or a chart spec. Layer: host logic, no view.
    private let chartSpec = #"{"kind":"bar","title":"Merged PRs","stacked":true,"format":{"prefix":"$","decimals":2},"series":[{"name":"Panels","points":[{"label":"Week 1","value":-2.5},{"label":"Week 2","value":null}]},{"name":"Panels","points":[{"label":"Week 3","value":4},{"label":"Week 1","value":1500}]}]}"#

    func testOnlyAClosedPanelsChartFenceWithAValidSpecBecomesAChart() throws {
        let fence = "```panels-chart\n" + chartSpec + "\n```"
        let chart = try XCTUnwrap(ArtifactMarkdownBlock.parse("Before\n\n" + fence).last?.chart)
        XCTAssertEqual(chart.series.map(\.key), ["actual", "inline-1"])
        XCTAssertTrue(chart.stacked)
        for source in [fence.replacingOccurrences(of: "panels-chart", with: "panels-chart extra"),
                       fence.replacingOccurrences(of: "panels-chart", with: " panels-chart"),
                       fence.replacingOccurrences(of: "panels-chart", with: "json"),
                       String(fence.dropLast()), fence.replacingOccurrences(of: "\n```", with: "\n~~~"),
                       "~~~~panels-chart\n" + chartSpec + "\n~~~~"] {
            let blocks = ArtifactMarkdownBlock.parse(source)
            XCTAssertEqual(blocks.map(\.kind), [.code], source)
            XCTAssertEqual(blocks.first?.chart == nil, !source.hasPrefix("~~~~"), source)
        }
        let line = #"{"kind":"line","series":[{"name":"A","points":[{"label":"a","value":1}]}]}"#
        XCTAssertEqual(ArtifactChart(source: line)?.isLine, true)
        let invalid = [#"{"#, "null", "[]", #"{"kind":"gauge","series":[{"name":"A","points":[{"label":"a","value":1}]}]}"#,
                       line.replacingOccurrences(of: #""kind":"line""#, with: #""kind":"line","stacked":false"#),
                       line.replacingOccurrences(of: #""kind":"line""#, with: #""kind":"bar","stacked":1"#),
                       line.replacingOccurrences(of: #""kind":"line""#, with: #""kind":"bar","title":null"#),
                       line.replacingOccurrences(of: #""kind":"line""#, with: #""kind":"bar","measure_id":"x""#),
                       line.replacingOccurrences(of: #""kind":"line""#, with: #""kind":"bar","format":{"decimals":21}"#),
                       line.replacingOccurrences(of: #""kind":"line""#, with: #""kind":"bar","format":{"decimals":1.5}"#),
                       line.replacingOccurrences(of: #""kind":"line""#, with: #""kind":"bar","format":{"suffix":4}"#),
                       line.replacingOccurrences(of: #""name":"A""#, with: #""name":"A","color":"red""#),
                       line.replacingOccurrences(of: #","value":1"#, with: ""),
                       line.replacingOccurrences(of: #""value":1"#, with: #""value":true"#),
                       line.replacingOccurrences(of: #""value":1"#, with: #""value":"1""#),
                       line.replacingOccurrences(of: #""value":1"#, with: #""value":1e999"#),
                       line.replacingOccurrences(of: #""value":1"#, with: #""value":9007199254740992"#),
                       line.replacingOccurrences(of: #"{"label":"a","value":1}"#, with: #"{"label":"a","value":1},{"label":"a","value":2}"#),
                       line.replacingOccurrences(of: #"{"label":"a","value":1}"#, with: ""),
                       #"{"kind":"line","series":[]}"#]
        for source in invalid { XCTAssertNil(ArtifactChart(source: source), source) }
        let rejected = ArtifactMarkdownBlock.parse("```panels-chart\n" + invalid[5] + "\n```")
        XCTAssertEqual(rejected.map(\.kind), [.code])
        XCTAssertEqual(rejected.first?.text, invalid[5])
    }

    func testChartKeepsLabelOrderNullsAndTheWebsNumberFormats() throws {
        let chart = try XCTUnwrap(ArtifactChart(source: chartSpec))
        XCTAssertEqual(chart.data.map(\.id), ["Week 1", "Week 2", "Week 3"])
        XCTAssertEqual(chart.data.map { $0.values["actual"] }, [-2.5, nil, nil])
        XCTAssertEqual(chart.data.map { $0.values["inline-1"] }, [1500, nil, 4])
        XCTAssertEqual(chart.reading(1500), "$1,500.00")
        XCTAssertEqual(chart.reading(nil), "No reading")
        XCTAssertEqual(chart.tick(1500), "$1.5k")
        let units = try XCTUnwrap(ArtifactChart(source: #"{"kind":"line","unit":"ms","series":[{"name":"A","points":[{"label":"a","value":12.4}]}]}"#))
        XCTAssertEqual(units.title, "Reading")
        XCTAssertEqual(units.reading(12.4), "12 ms")
        XCTAssertEqual(units.tick(12.5), "12.5")
        let percent = try XCTUnwrap(ArtifactChart(source: #"{"kind":"bar","format":{"suffix":"%","decimals":1},"series":[{"name":"A","points":[{"label":"a","value":50}]}]}"#))
        XCTAssertEqual(percent.reading(50), "50.0%")
        XCTAssertEqual(percent.tick(50), "50%")
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
