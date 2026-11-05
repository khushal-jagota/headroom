import SwiftUI

/// Recursive native renderer for the twelve headroom.page block kinds.
struct GoalPageBlocks: View {
    let blocks: [GoalJSON]
    let session: PageSession
    var body: some View {
        VStack(alignment: .leading, spacing: 32) {
            ForEach(blocks, id: \.id) { block in GoalPageBlock(block: block, session: session) }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}
private struct GoalPageBlock: View {
    let block: GoalJSON
    let session: PageSession
    @Environment(\.pageTicketHolder) private var holder
    @State private var selectedView: String?
    @ViewBuilder var body: some View {
        switch block.type {
        case "tickets": if let holder { HolderTickets(holder: holder) }
        case "text": edit(block["text"].text, "Text", ["text"], markdown: true).panelsScaledFont(size: 18)
        case "chart": GoalChart(block: block, session: session)
        case "checklist": GoalChecklist(block: block, session: session, supplementary: true)
        case "grid": GoalPageBlocks(blocks: block["blocks"].array, session: session)
        case "section":
            VStack(alignment: .leading, spacing: 20) {
                edit(block["title"].text, "Section title", ["title"]).panelsScaledFont(size: 20, weight: .medium)
                if let summary = block["summary"].string { edit(summary, "Section summary", ["summary"]).font(.footnote).foregroundStyle(PanelsTheme.Goal.faint) }
                if block["views"].array.isEmpty { GoalPageBlocks(blocks: block["blocks"].array, session: session) } else { views }
            }
        case "segmented": views
        case "table": GoalPageTable(block: block, session: session)
        case "callout":
            VStack(alignment: .leading, spacing: 12) {
                if let title = block["title"].string { edit(title, "Callout title", ["title"]).fontWeight(.medium) }
                edit(block["text"].text, "Callout text", ["text"]).foregroundStyle(PanelsTheme.muted)
            }.modifier(GoalPanel()).overlay(alignment: .leading) { Capsule().fill(PanelsTheme.Goal.tone(block["tone"].text)).frame(width: 2).padding(.vertical, 24) }
        case "metrics":
            VStack(spacing: 16) {
                ForEach(Array(block["items"].array.enumerated()), id: \.offset) { index, item in
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(alignment: .top) {
                            edit(item["label"].text, "Metric label", ["items", String(index), "label"]).panelsScaledFont(size: 15)
                            if let change = item["change"].string { edit(change, "Metric change", ["items", String(index), "change"]).panelsScaledFont(size: 11).foregroundStyle(PanelsTheme.Goal.tone(item["tone"].text)).padding(6).background(PanelsTheme.Goal.raised, in: Capsule()) }
                        }
                        edit(item["value"].text, "Metric value", ["items", String(index), "value"]).panelsScaledFont(size: 30, relativeTo: .title, weight: .medium)
                        if let hint = item["hint"].string { edit(hint, "Metric hint", ["items", String(index), "hint"]).font(.caption).foregroundStyle(PanelsTheme.Goal.faint) }
                    }.modifier(GoalPanel())
                }
            }
        case "properties":
            VStack(alignment: .leading, spacing: 20) {
                ForEach(Array(block["items"].array.enumerated()), id: \.offset) { index, item in
                    VStack(alignment: .leading, spacing: 8) {
                        edit(item["label"].text, "Property label", ["items", String(index), "label"]).font(.caption).foregroundStyle(PanelsTheme.Goal.faint)
                        edit(item["value"].text, "Property value", ["items", String(index), "value"]).font(.subheadline)
                    }
                }
            }
        case "status":
            HStack(spacing: 8) {
                Circle().fill(PanelsTheme.Goal.tone(block["tone"].text)).frame(width: 6, height: 6)
                edit(block["label"].text, "Status label", ["label"]).panelsScaledFont(size: 15, weight: .medium)
            }.foregroundStyle(PanelsTheme.Goal.tone(block["tone"].text))
        case "rows":
            VStack(spacing: 0) {
                ForEach(Array(block["items"].array.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 6) {
                            edit(item["title"].text, "Row title", ["items", String(index), "title"]).panelsScaledFont(size: 15, weight: .medium)
                            if let detail = item["detail"].string { edit(detail, "Row detail", ["items", String(index), "detail"]).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Goal.faint) }
                        }
                        VStack(alignment: .trailing, spacing: 6) {
                            if let status = item["status"].string { edit(status, "Row status", ["items", String(index), "status"]).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Goal.tone(item["tone"].text)) }
                            if let meta = item["meta"].string { edit(meta, "Row meta", ["items", String(index), "meta"]).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Goal.faint) }
                        }.frame(maxWidth: 120)
                    }.padding(.vertical, 16)
                    if index < block["items"].array.count - 1 { Divider() }
                }
            }
        default: EmptyView()
        }
    }
    private func edit(_ value: String, _ label: String, _ path: [String], markdown: Bool = false) -> some View {
        GoalEditableText(value: value, label: label, session: session, blockID: block.id, path: path, markdown: markdown, multiline: true)
    }
    private var views: some View {
        let views = block["views"].array
        let current = views.first { $0.id == selectedView } ?? views.first
        return VStack(alignment: .leading, spacing: 24) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 20) {
                    ForEach(Array(views.enumerated()), id: \.element.id) { index, view in
                        VStack(spacing: 4) {
                            Button(view["label"].text) { selectedView = view.id }
                                .panelsScaledFont(size: 13, weight: .medium).foregroundStyle(current?.id == view.id ? PanelsTheme.strong : PanelsTheme.Goal.faint)
                                .accessibilityAddTraits(current?.id == view.id ? .isSelected : [])
                                .contextMenu {
                                    Button("Edit view label") { session.textDrafts[block.id + ":views." + String(index) + ".label"] = .init(text: view["label"].text, revision: session.revision) }
                                }
                            if session.textDrafts[block.id + ":views." + String(index) + ".label"] != nil {
                                edit(view["label"].text, "View label", ["views", String(index), "label"])
                            }
                            Rectangle().fill(current?.id == view.id ? PanelsTheme.muted : .clear).frame(height: 1)
                        }
                    }
                }
            }
            if let current { GoalPageBlocks(blocks: current["blocks"].array, session: session) }
        }
    }
}

/// Phone table: one outer frame, editable headers and rows, original row identity
/// survives sorting. Deliberate owner exception to the web's nested table frame.
private struct GoalPageTable: View {
    let block: GoalJSON
    let session: PageSession
    @State private var sortColumn: Int?
    @State private var ascending = true
    private var rows: [(Int, GoalJSON)] {
        let indexed = Array(block["rows"].array.enumerated()).map { ($0.offset, $0.element) }
        guard let sortColumn else { return indexed }
        return indexed.sorted { a, b in
            let left = a.1.array.indices.contains(sortColumn) ? a.1.array[sortColumn].text : ""
            let right = b.1.array.indices.contains(sortColumn) ? b.1.array[sortColumn].text : ""
            let result = left.compare(right, options: [.numeric, .caseInsensitive])
            if result == .orderedSame { return a.0 < b.0 }
            return ascending ? result == .orderedAscending : result == .orderedDescending
        }
    }
    private var readingColumn: Int? {
        block["columns"].array.indices.dropFirst().last { index in
            let values = block["rows"].array.compactMap { $0.array.indices.contains(index) ? $0.array[index].string : nil }.filter { $0 != "—" && !$0.isEmpty }
            return !values.isEmpty && values.allSatisfy { $0.range(of: #"^[^\p{L}]*[0-9][^\p{L}]*$"#, options: .regularExpression) != nil }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let caption = block["caption"].string {
                GoalEditableText(value: caption, label: "Table caption", session: session, blockID: block.id, path: ["caption"]).panelsScaledFont(size: 17, weight: .medium)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 20) {
                    ForEach(Array(block["columns"].array.enumerated()), id: \.offset) { index, column in
                        HStack(spacing: 6) {
                            GoalEditableText(value: column.text, label: "Column \(index + 1)", session: session, blockID: block.id, path: ["columns", String(index)])
                            Button { if sortColumn == index { ascending.toggle() } else { sortColumn = index; ascending = true } } label: { Image(systemName: sortColumn == index ? (ascending ? "arrow.up" : "arrow.down") : "arrow.up.arrow.down") }.accessibilityLabel("Sort by " + column.text)
                        }.fixedSize().font(.caption)
                    }
                }.padding(.vertical, 10)
            }.overlay(alignment: .top) { Divider() }.overlay(alignment: .bottom) { Divider() }
            ForEach(rows, id: \.0) { rowIndex, row in
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .firstTextBaseline) {
                        if let first = row.array.first {
                            GoalEditableText(value: first.text, label: "Row \(rowIndex + 1), \(block["columns"].array.first?.text ?? "name")", session: session, blockID: block.id, path: ["rows", String(rowIndex), "0"]).panelsScaledFont(size: 13, weight: .medium)
                        }
                        if let column = readingColumn, row.array.indices.contains(column) {
                            Spacer(minLength: 8)
                            GoalEditableText(value: row.array[column].text, label: "Row \(rowIndex + 1), column \(column + 1)", session: session, blockID: block.id, path: ["rows", String(rowIndex), String(column)]).panelsScaledFont(size: 13).monospacedDigit()
                        }
                    }
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 14) { cells(rowIndex, row) }
                        VStack(alignment: .leading, spacing: 8) { cells(rowIndex, row) }
                    }
                }.padding(.vertical, 8)
                if rowIndex != rows.last?.0 { Divider() }
            }
            if rows.isEmpty {
                GoalEditableText(value: block["empty_message"].string ?? "No rows", label: "Empty message", session: session, blockID: block.id, path: ["empty_message"], editable: !block["empty_message"].isNull).font(.footnote).foregroundStyle(PanelsTheme.Goal.faint)
            }
        }.modifier(GoalPanel(padding: 16))
    }
    @ViewBuilder private func cells(_ rowIndex: Int, _ row: GoalJSON) -> some View {
        ForEach(Array(row.array.enumerated().dropFirst().filter { $0.offset != readingColumn }), id: \.offset) { index, cell in
            HStack(spacing: 4) {
                if block["cell_tones"].array.indices.contains(rowIndex), block["cell_tones"].array[rowIndex].array.indices.contains(index), let tone = block["cell_tones"].array[rowIndex].array[index].string { Circle().fill(PanelsTheme.Goal.tone(tone)).frame(width: 4, height: 4) }
                GoalEditableText(value: cell.text, label: "Row \(rowIndex + 1), column \(index + 1)", session: session, blockID: block.id, path: ["rows", String(rowIndex), String(index)]).panelsScaledFont(size: 11).foregroundStyle(PanelsTheme.Goal.faint)
            }
        }
    }
}
