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
