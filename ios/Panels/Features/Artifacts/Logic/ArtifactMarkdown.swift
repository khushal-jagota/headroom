import Foundation

struct ArtifactMarkdownBlock: Identifiable, Equatable {
    enum Kind: Equatable { case heading(Int), paragraph, list, quote, code, rule }
    let id: Int
    let kind: Kind
    let text: String

    static func parse(_ source: String) -> [Self] {
        var blocks: [Self] = []
        var paragraph: [String] = []
        var code: [String] = []
        var fence: String?
        func add(_ kind: Kind, _ text: String) { blocks.append(Self(id: blocks.count, kind: kind, text: text)) }
        func flush() {
            if !paragraph.isEmpty { add(.paragraph, paragraph.joined(separator: "\n")); paragraph = [] }
        }
        for line in source.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if let marker = fence {
                if trimmed.hasPrefix(marker) { add(.code, code.joined(separator: "\n")); code = []; fence = nil }
                else { code.append(line) }
                continue
            }
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                flush(); fence = String(trimmed.prefix(3)); continue
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
        if fence != nil { add(.code, code.joined(separator: "\n")) }
        return blocks
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
        var result: [ArtifactTarget] = []
        var seen = Set<String>()
        for block in ArtifactMarkdownBlock.parse(source) where block.kind != .code {
            guard let rich = try? AttributedString(markdown: block.text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace), baseURL: baseURL) else { continue }
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
