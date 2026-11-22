import Foundation

struct ArtifactMarkdownBlock: Identifiable, Equatable {
    enum Kind: Equatable { case heading(Int), paragraph, list, quote, code, rule }
    let id: Int
    let kind: Kind
    let text: String
    /// A closed `panels-chart` fence whose spec the web would draw. Any other fence stays code.
    var chart: ArtifactChart? = nil

    static func parse(_ source: String) -> [Self] {
        var blocks: [Self] = []
        var paragraph: [String] = []
        var code: [String] = []
        // The opening run (three or more backticks or tildes) and the text after it.
        var fence: (run: String, info: String)?
        func add(_ kind: Kind, _ text: String, chart: ArtifactChart? = nil) {
            blocks.append(Self(id: blocks.count, kind: kind, text: text, chart: chart))
        }
        func flush() {
            if !paragraph.isEmpty { add(.paragraph, paragraph.joined(separator: "\n")); paragraph = [] }
        }
        for line in source.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if let open = fence {
                // As in CommonMark, only a run of the same mark, at least as long, closes a fence.
                if let mark = open.run.first, trimmed.count >= open.run.count, trimmed.allSatisfy({ $0 == mark }) {
                    let text = code.joined(separator: "\n")
                    // The web's rule: the language follows the run directly, with nothing after it.
                    add(.code, text, chart: open.info == "panels-chart" ? ArtifactChart(source: text) : nil)
                    code = []; fence = nil
                } else { code.append(line) }
                continue
            }
            if let mark = trimmed.first, mark == "`" || mark == "~" {
                let run = trimmed.prefix { $0 == mark }
                if run.count >= 3 { flush(); fence = (String(run), String(trimmed.dropFirst(run.count))); continue }
            }
            if trimmed.isEmpty { flush(); continue }
            let hashes = trimmed.prefix { $0 == "#" }.count
            if (1...6).contains(hashes), trimmed.dropFirst(hashes).first == " " {
                flush(); add(.heading(hashes), String(trimmed.dropFirst(hashes + 1))); continue
            }
            if ["---", "***", "___"].contains(trimmed) { flush(); add(.rule, ""); continue }
            if trimmed.hasPrefix("> ") { flush(); add(.quote, String(trimmed.dropFirst(2))); continue }
            if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") || trimmed.hasPrefix("+ ") {
                flush(); add(.list, "• " + trimmed.dropFirst(2)); continue
            }
            if trimmed.range(of: #"^\d+\.\s"#, options: .regularExpression) != nil {
                flush(); add(.list, trimmed); continue
            }
            paragraph.append(line)
        }
        flush()
        // An unclosed fence keeps its text as code, and is never a chart.
        if fence != nil { add(.code, code.joined(separator: "\n")) }
        return blocks
    }

    /// A paragraph whose second line is a delimiter row is a table: the header row, then the body rows.
    var tableRows: [[String]]? {
        guard kind == .paragraph else { return nil }
        let lines = text.components(separatedBy: .newlines)
        guard lines.count > 1, lines[0].contains("|"),
              lines[1].range(of: #"^\s*\|?\s*:?-{3,}"#, options: .regularExpression) != nil else { return nil }
        return ([lines[0]] + Array(lines.dropFirst(2))).map { line in
            var cells = line.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
            if cells.first == "" { cells.removeFirst() }; if cells.last == "" { cells.removeLast() }
            return cells
        }
    }
}

/// One reading of a Markdown source: its blocks, each block's inline text, and the photos and
/// videos it links. A view keeps one per source, so a redraw parses nothing again.
struct ArtifactMarkdownDocument {
    let blocks: [ArtifactMarkdownBlock]
    /// Inline text by block id, for headings, quotes, lists and paragraphs that are not tables.
    let inline: [Int: AttributedString]
    /// Table cells by block id, header row first.
    let tables: [Int: [[AttributedString]]]
    let media: [ArtifactTarget]

    init(source: String, baseURL: URL) {
        func rich(_ text: String) -> AttributedString {
            (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace), baseURL: baseURL)) ?? AttributedString(text)
        }
        blocks = ArtifactMarkdownBlock.parse(source)
        var inline: [Int: AttributedString] = [:]
        var tables: [Int: [[AttributedString]]] = [:]
        var prose: [AttributedString] = []
        for block in blocks where block.kind != .code && block.kind != .rule {
            if let rows = block.tableRows {
                let cells = rows.map { $0.map(rich) }
                tables[block.id] = cells; prose += cells.joined()
            } else {
                let text = rich(block.text)
                inline[block.id] = text; prose.append(text)
            }
        }
        self.inline = inline
        self.tables = tables
        media = ArtifactInlineMedia.targets(in: prose, baseURL: baseURL)
    }
}

/// A `panels-chart` spec that passes the web's checks (web/src/lib/markdown/charts/validation.ts).
/// It becomes a goal chart block, so the goal chart's label order and number formats apply unchanged.
struct ArtifactChart: Equatable, Sendable {
    struct Series: Equatable, Sendable {
        /// The plot key the web assigns: `actual`, then `inline-1`, `inline-2`. Names may repeat.
        let key: String
        let name: String
    }
    let block: GoalJSON
    let series: [Series]
    /// One entry per label, in the order labels first appear across the series.
    let data: [GoalChartDatum]

    var isLine: Bool { block.kind == "line" }
    var stacked: Bool { block["stacked"].bool == true }
    var title: String { block["title"].string ?? "Reading" }
    /// A reading, with the spec's prefix, suffix or unit and its fixed decimals.
    func reading(_ value: Double?) -> String { value == nil ? "No reading" : GoalChartPresentation.format(block, value) }
    /// An axis label: abbreviated, and showing the unit only with a prefix or a percent sign.
    func tick(_ value: Double) -> String { GoalChartPresentation.axisTick(block, value) }

    init?(source: String) {
        guard let json = try? JSONDecoder().decode(GoalJSON.self, from: Data(source.utf8)),
              Self.isValid(json), case .object(var spec) = json else { return nil }
        let authored = spec["series"]?.array ?? []
        let keyed = authored.enumerated().map { index, item in item.setting("key", .string(index == 0 ? "actual" : "inline-\(index)")) }
        spec["series"] = .array(keyed)
        spec["type"] = .string("chart"); spec["id"] = .string("chat-chart"); spec["placement"] = .string("primary")
        block = .object(spec)
        series = keyed.map { Series(key: $0["key"].text, name: $0["name"].text) }
        data = GoalChartPresentation.data(block, values: [], range: "all")
    }

    /// Readings stay within ±(2^53 − 1), so spans and stacked totals cannot overflow.
    private static let readingBound = 9_007_199_254_740_991.0

    /// The web's strict schema: no unknown keys at any level, optional is not nullable, and
    /// `stacked` belongs to bar charts only.
    private static func isValid(_ json: GoalJSON) -> Bool {
        func only(_ object: [String: GoalJSON], _ keys: Set<String>) -> Bool { Set(object.keys).isSubset(of: keys) }
        func optionalText(_ value: GoalJSON?) -> Bool { value == nil || value?.string != nil }
        guard case .object(let spec) = json, only(spec, ["kind", "title", "unit", "format", "stacked", "series"]),
              let kind = spec["kind"]?.string, kind == "bar" || kind == "line",
              optionalText(spec["title"]), optionalText(spec["unit"]) else { return false }
        if let stacked = spec["stacked"], stacked.bool == nil || kind != "bar" { return false }
        if let format = spec["format"] {
            guard case .object(let options) = format, only(options, ["prefix", "suffix", "decimals"]),
                  optionalText(options["prefix"]), optionalText(options["suffix"]) else { return false }
            if let decimals = options["decimals"] {
                guard case .number(let places) = decimals, places.rounded() == places, (0...20).contains(places) else { return false }
            }
        }
        guard case .array(let series)? = spec["series"], !series.isEmpty else { return false }
        return series.allSatisfy { item in
            guard case .object(let line) = item, only(line, ["name", "points"]), line["name"]?.string != nil,
                  case .array(let points)? = line["points"], !points.isEmpty else { return false }
            var labels = Set<String>()
            return points.allSatisfy { item in
                guard case .object(let point) = item, only(point, ["label", "value"]),
                      let label = point["label"]?.string, labels.insert(label).inserted, let value = point["value"] else { return false }
                if case .number(let reading) = value { return reading.isFinite && abs(reading) <= readingBound }
                return value == .null
            }
        }
    }
}

/// Only photos and videos earn an automatic read while prose is on screen.
/// All other managed links remain ordinary text until the reader follows them.
enum ArtifactInlineMedia {
    static func isPhoto(_ target: ArtifactTarget) -> Bool {
        target.kind == .image || ["gif", "tif", "tiff"].contains((target.path as NSString).pathExtension.lowercased())
    }

    static func isVideo(_ target: ArtifactTarget) -> Bool {
        ["mp4", "mov", "m4v"].contains((target.path as NSString).pathExtension.lowercased())
    }

    static func targets(in source: String, baseURL: URL) -> [ArtifactTarget] {
        ArtifactMarkdownDocument(source: source, baseURL: baseURL).media
    }

    /// Code and charts never reach here, so literal text cannot load a photo.
    static func targets(in prose: [AttributedString], baseURL: URL) -> [ArtifactTarget] {
        var result: [ArtifactTarget] = []
        var seen = Set<String>()
        for rich in prose {
            var link: URL?
            var label = ""
            func appendLink() {
                guard let link,
                      let target = ArtifactTarget.from(link, baseURL: baseURL, title: label),
                      isPhoto(target) || isVideo(target), seen.insert(target.id).inserted else { return }
                result.append(target)
            }
            for run in rich.runs {
                let url = run.link ?? run.imageURL
                if url != link {
                    appendLink(); link = url; label = ""
                }
                if url != nil { label += String(rich[run.range].characters) }
            }
            appendLink()
        }
        return result
    }
}
