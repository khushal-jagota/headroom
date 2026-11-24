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


    /// The reader is the one door for opened files: it keeps a copy, hands it back per
    /// server, and reports an unchanged file (304 or the same bytes) as nothing to draw.
    @MainActor func testReaderKeepsCopiesPerServerAndDrawsOnlyChangedFiles() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtifactFileProtocol.self]
        let session = URLSession(configuration: configuration)
        let client = APIClient(baseURL: URL(string: "https://artifact-test-a.example")!, session: session)
        let other = APIClient(baseURL: URL(string: "https://artifact-test-b.example")!, session: session)
        let target = ArtifactTarget(owner: .init(kind: .ticket, id: "t_copy\(UUID().uuidString.prefix(6))"), path: "artifacts/plan.md", title: "Plan")
        ArtifactFileProtocol.body = "# First"
        ArtifactFileProtocol.bodies = [:]; ArtifactFileProtocol.failing = []; ArtifactFileProtocol.conditionalReads = 0
        ArtifactFileProtocol.missing = []; ArtifactFileProtocol.declaredLength = [:]
        let read = try await client.artifactDocument(target)
        XCTAssertEqual(read.text, "# First")

        let savedCopy = await client.savedArtifactDocument(target)
        let saved = try XCTUnwrap(savedCopy, "An opened file keeps a copy")
        XCTAssertEqual(saved.text, "# First")
        XCTAssertNotEqual(saved.localURL, read.localURL, "Each document owns its own temporary file")
        let savedNil = await other.savedArtifactDocument(target)
        XCTAssertNil(savedNil, "Another server has no copy")

        let unchanged = try await client.refreshedArtifactDocument(target, shown: saved)
        XCTAssertNil(unchanged)
        XCTAssertEqual(ArtifactFileProtocol.conditionalReads, 1, "The copy's version asks for a 304")

        ArtifactFileProtocol.body = "# Second"
        let changed = try await client.refreshedArtifactDocument(target, shown: saved)
        XCTAssertEqual(changed?.text, "# Second")
        let replaced = await client.savedArtifactDocument(target)
        XCTAssertEqual(replaced?.text, "# Second", "The new file replaces the copy")

        let video = ArtifactTarget(owner: target.owner, path: "artifacts/clip.mp4", title: "Clip")
        _ = try await client.artifactDocument(video)
        let videoCopy = await client.savedArtifactDocument(video)
        XCTAssertNil(videoCopy, "Video is not kept")
    }

    /// An HTML page's copy holds the page and the files it loaded from its own owner, and
    /// only those. A refresh that could not read every file is not fresh (nil). A file the
    /// server says is over the limit is not read and leaves the copy. A page the server no
    /// longer has loses its whole copy, and so does any other artifact (the reader's 404).
    @MainActor func testPageCopyKeepsOwnFilesAndADeletedArtifactLosesItsCopy() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtifactFileProtocol.self]
        let base = URL(string: "https://artifact-test-page.example")!
        let client = APIClient(baseURL: base, session: URLSession(configuration: configuration))
        let server = LocalStore.server(base)
        let owner = "t_page\(UUID().uuidString.prefix(6))"
        let target = ArtifactTarget(owner: .init(kind: .ticket, id: owner), path: "artifacts/report.html", title: "Report")
        let pagePath = "/files/tickets/\(owner)/artifacts/report.html"
        let shot = base.appendingPathComponent("files/tickets/\(owner)/artifacts/shots/a.png")
        let big = base.appendingPathComponent("files/tickets/\(owner)/artifacts/shots/big.png")
        ArtifactFileProtocol.bodies = [pagePath: "<img src=shots/a.png>", shot.path: "A", big.path: "B"]
        ArtifactFileProtocol.failing = []; ArtifactFileProtocol.missing = []
        ArtifactFileProtocol.declaredLength = [big.path: ArtifactCopies.fileLimit + 1]
        let loaded = [shot, big, URL(string: "https://elsewhere.example/files/tickets/\(owner)/x.png")!,
                      base.appendingPathComponent("files/tickets/t_other/x.png"), base.appendingPathComponent("files/tickets/\(owner)/clip.mp4")]
        let first = await client.refreshPageCopy(target, files: loaded)
        XCTAssertEqual(first, true)
        let entry = await ArtifactCopies.shared.open(server: server, id: target.id)
        XCTAssertEqual(Set(entry?.files.keys.map { $0 } ?? []), [pagePath, shot.path],
                       "Only the page and its own owner's files join the copy; video, other hosts and a file over the limit do not")
        let unchanged = await client.refreshPageCopy(target, files: [shot])
        XCTAssertEqual(unchanged, false, "Every saved file answers 304")
        ArtifactFileProtocol.failing = [shot.path]
        let partial = await client.refreshPageCopy(target, files: [shot])
        XCTAssertNil(partial, "A file that could not be read leaves the refresh unfinished")
        ArtifactFileProtocol.failing = []
        ArtifactFileProtocol.bodies[shot.path] = "B"
        let changed = await client.refreshPageCopy(target, files: [shot])
        XCTAssertEqual(changed, true)
        let saved = await ArtifactCopies.shared.data(server: server, id: target.id, path: shot.path)
        XCTAssertEqual(saved, Data("B".utf8))

        ArtifactFileProtocol.missing = [pagePath]
        _ = await client.refreshPageCopy(target, files: [shot])
        let deletedPage = await ArtifactCopies.shared.open(server: server, id: target.id)
        XCTAssertNil(deletedPage, "A page the server no longer has loses its copy")

        let note = ArtifactTarget(owner: target.owner, path: "artifacts/note.md", title: "Note")
        let notePath = "/files/tickets/\(owner)/artifacts/note.md"
        ArtifactFileProtocol.bodies[notePath] = "# Note"
        _ = try await client.artifactDocument(note)
        ArtifactFileProtocol.missing = [notePath]
        let shown = await client.savedArtifactDocument(note)
        do { _ = try await client.refreshedArtifactDocument(note, shown: shown); XCTFail("A deleted artifact reads as 404") }
        catch { XCTAssertEqual((error as? APIError)?.status, 404) }
        let deletedNote = await client.savedArtifactDocument(note)
        XCTAssertNil(deletedNote, "The server's 404 removes the copy, so the viewer shows the server's answer")
        ArtifactFileProtocol.missing = []; ArtifactFileProtocol.declaredLength = [:]
    }

    /// Bounds: the copy opened least recently leaves first, the copy just saved stays, one
    /// page cannot outgrow the total, and a copy not opened within the age limit leaves.
    func testCopiesLeaveBySizeAndAge() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("copies-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder) }
        let clock = TestClock()
        let copies = ArtifactCopies(directory: folder, byteLimit: 250, maximumAge: 100, now: { clock.now })
        let sources = folder.appendingPathExtension("sources")
        defer { try? FileManager.default.removeItem(at: sources) }
        func file(_ bytes: Int) throws -> URL {
            let url = sources.appendingPathComponent(UUID().uuidString)
            try FileManager.default.createDirectory(at: sources, withIntermediateDirectories: true)
            try Data(repeating: 1, count: bytes).write(to: url)
            return url
        }
        await copies.save(server: "s", id: "page", main: "/page.html", path: "/page.html", file: try file(50), etag: nil)
        await copies.save(server: "s", id: "page", main: "/page.html", path: "/shot.png", file: try file(50), etag: nil)
        await copies.save(server: "s", id: "loose", main: "/a.html", path: "/stray.png", file: try file(50), etag: nil)
        let stray = await copies.location(server: "s", id: "loose", path: "/stray.png")
        XCTAssertNil(stray, "A page's file joins only a copy that exists")
        clock.now += 10
        await copies.save(server: "s", id: "note", main: "/note.md", path: "/note.md", file: try file(100), etag: nil)
        clock.now += 10
        _ = await copies.open(server: "s", id: "page")
        clock.now += 10
        await copies.save(server: "s", id: "photo", main: "/photo.png", path: "/photo.png", file: try file(100), etag: nil)
        let note = await copies.open(server: "s", id: "note")
        XCTAssertNil(note, "The least recently opened copy leaves first")
        let shot = await copies.location(server: "s", id: "page", path: "/shot.png")
        XCTAssertNotNil(shot, "A recently opened page keeps its files")
        let total = await copies.totalBytes
        XCTAssertEqual(total, 200)

        let kept = await copies.save(server: "s", id: "page", main: "/page.html", path: "/shot.png", file: try file(220), etag: nil)
        XCTAssertFalse(kept, "A page's file that would take its copy over the total is not kept")
        let oldShot = await copies.location(server: "s", id: "page", path: "/shot.png")
        XCTAssertNil(oldShot, "Its older version leaves too, so the page reads the server's")
        let pageCopy = await copies.open(server: "s", id: "page")
        XCTAssertNotNil(pageCopy, "The page keeps its copy")

        let pageFolder = folder.appendingPathComponent(ArtifactCopies.key("s", "page"))
        try Data("stray".utf8).write(to: pageFolder.appendingPathComponent("left.saving"))
        let relaunched = ArtifactCopies(directory: folder, byteLimit: 250, maximumAge: 100, now: { clock.now })
        let afterRelaunch = await relaunched.totalBytes
        XCTAssertEqual(afterRelaunch, 150, "The index on disk matches what is left: the page and the photo")
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: pageFolder.path).count, 1,
                       "A file the index does not name leaves at the next launch")

        clock.now += 101
        let aged = await relaunched.open(server: "s", id: "photo")
        XCTAssertNil(aged, "A copy not opened within the age limit leaves")
        let afterAge = await relaunched.totalBytes
        XCTAssertEqual(afterAge, 0)
    }
}

private final class TestClock: @unchecked Sendable { var now = Date(timeIntervalSince1970: 1_000) }

/// Serves one Markdown body with its version, and a 304 when the phone names that version.
private final class ArtifactFileProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var body = ""
    /// Bodies by request path, over `body`; a path in `failing` loses its connection.
    nonisolated(unsafe) static var bodies: [String: String] = [:]
    nonisolated(unsafe) static var failing: Set<String> = []
    /// Paths that answer 404, and a Content-Length to declare in place of the real one.
    nonisolated(unsafe) static var missing: Set<String> = []
    nonisolated(unsafe) static var declaredLength: [String: Int] = [:]
    nonisolated(unsafe) static var conditionalReads = 0
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host?.hasPrefix("artifact-test") == true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        if Self.failing.contains(request.url!.path) { client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost)); return }
        let data = Data((Self.bodies[request.url!.path] ?? Self.body).utf8)
        let etag = "\"\(data.hashValue)\""
        let conditional = request.value(forHTTPHeaderField: "If-None-Match")
        if conditional != nil { Self.conditionalReads += 1 }
        let path = request.url!.path
        let status = Self.missing.contains(path) ? 404 : conditional == etag ? 304 : 200
        var headers = ["ETag": etag]
        if let length = Self.declaredLength[path] { headers["Content-Length"] = String(length) }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: headers)!, cacheStoragePolicy: .notAllowed)
        if status == 200 { client?.urlProtocol(self, didLoad: data) }
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
