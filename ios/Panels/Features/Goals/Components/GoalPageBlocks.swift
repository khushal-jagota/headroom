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
        case "text": text(block["text"].text, markdown: true)
        case "chart": GoalChart(block: block, session: session)
        case "checklist": GoalChecklist(block: block, session: session, supplementary: true)
        case "grid": GoalPageBlocks(blocks: block["blocks"].array, session: session)
        case "section":
            VStack(alignment: .leading, spacing: 20) {
                text(block["title"].text).panelsScaledFont(size: 20, weight: .medium)
                if let summary = block["summary"].string { text(summary).font(.footnote).foregroundStyle(PanelsTheme.Goal.faint) }
                if block["views"].array.isEmpty { GoalPageBlocks(blocks: block["blocks"].array, session: session) } else { views }
            }
        case "segmented": views
        case "table": GoalPageTable(block: block)
        case "callout":
            VStack(alignment: .leading, spacing: 12) {
                if let title = block["title"].string { text(title).fontWeight(.medium) }
                text(block["text"].text).foregroundStyle(PanelsTheme.muted)
            }.modifier(GoalPanel()).overlay(alignment: .leading) { Capsule().fill(PanelsTheme.Goal.tone(block["tone"].text)).frame(width: 2).padding(.vertical, 24) }
        case "metrics":
            VStack(spacing: 16) {
                ForEach(Array(block["items"].array.enumerated()), id: \.offset) { index, item in
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(alignment: .top) {
                            text(item["label"].text).panelsScaledFont(size: 15)
                            if let change = item["change"].string { text(change).panelsScaledFont(size: 11).foregroundStyle(PanelsTheme.Goal.tone(item["tone"].text)).padding(6).background(PanelsTheme.Goal.raised, in: Capsule()) }
                        }
                        text(item["value"].text).panelsScaledFont(size: 30, relativeTo: .title, weight: .medium)
                        if let hint = item["hint"].string { text(hint).font(.caption).foregroundStyle(PanelsTheme.Goal.faint) }
                    }.modifier(GoalPanel())
                }
            }
        case "properties":
            VStack(alignment: .leading, spacing: 20) {
                ForEach(Array(block["items"].array.enumerated()), id: \.offset) { index, item in
                    VStack(alignment: .leading, spacing: 8) {
                        text(item["label"].text).font(.caption).foregroundStyle(PanelsTheme.Goal.faint)
                        text(item["value"].text).font(.subheadline)
                    }
                }
            }
        case "status":
            HStack(spacing: 8) {
                Circle().fill(PanelsTheme.Goal.tone(block["tone"].text)).frame(width: 6, height: 6)
                text(block["label"].text).panelsScaledFont(size: 15, weight: .medium)
            }.foregroundStyle(PanelsTheme.Goal.tone(block["tone"].text))
        case "rows":
            VStack(spacing: 0) {
                ForEach(Array(block["items"].array.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 6) {
                            text(item["title"].text).panelsScaledFont(size: 15, weight: .medium)
                            if let detail = item["detail"].string { text(detail).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Goal.faint) }
                        }
                        VStack(alignment: .trailing, spacing: 6) {
                            if let status = item["status"].string { text(status).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Goal.tone(item["tone"].text)) }
                            if let meta = item["meta"].string { text(meta).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Goal.faint) }
                        }.frame(maxWidth: 120)
                    }.padding(.vertical, 16)
                    if index < block["items"].array.count - 1 { Divider() }
                }
            }
        default: EmptyView()
        }
    }
    @ViewBuilder private func text(_ value: String, markdown: Bool = false) -> some View {
        if markdown { PanelsMarkdown(source: value, baseURL: session.baseURL, textSize: .reading) } else { GoalReadText(value: value) }
    }
    private var views: some View {
        let views = block["views"].array
        let current = views.first { $0.id == selectedView } ?? views.first
        return VStack(alignment: .leading, spacing: 24) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 20) {
                    ForEach(views, id: \.id) { view in
                        VStack(spacing: 4) {
                            Button(view["label"].text) { selectedView = view.id }
                                .panelsScaledFont(size: 13, weight: .medium).foregroundStyle(current?.id == view.id ? PanelsTheme.strong : PanelsTheme.Goal.faint)
                                .accessibilityAddTraits(current?.id == view.id ? .isSelected : [])
                            Rectangle().fill(current?.id == view.id ? PanelsTheme.muted : .clear).frame(height: 1)
                        }
                    }
                }
            }
            if let current { GoalPageBlocks(blocks: current["blocks"].array, session: session) }
        }
    }
}

/// Phone table: one outer frame, headers and rows, original row identity
/// survives sorting. Deliberate owner exception to the web's nested table frame.
private struct GoalPageTable: View {
    let block: GoalJSON
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
                GoalReadText(value: caption).panelsScaledFont(size: 17, weight: .medium)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 0) {
                    GridRow {
                        ForEach(Array(block["columns"].array.enumerated()), id: \.offset) { index, column in
                            HStack(spacing: 6) {
                                GoalReadText(value: column.text)
                                Button { if sortColumn == index { ascending.toggle() } else { sortColumn = index; ascending = true } } label: { Image(systemName: sortColumn == index ? (ascending ? "arrow.up" : "arrow.down") : "arrow.up.arrow.down") }.accessibilityLabel("Sort by " + column.text)
                            }.fixedSize().font(.caption).padding(.vertical, 10)
                                .accessibilityIdentifier("table-column-" + block.id + "-" + String(index))
                        }
                    }
                    Divider().gridCellUnsizedAxes(.horizontal)
                    ForEach(rows, id: \.0) { rowIndex, row in
                        GridRow {
                            ForEach(Array(row.array.enumerated()), id: \.offset) { index, cell in
                                cellView(cell, rowIndex: rowIndex, column: index)
                                    .fixedSize(horizontal: true, vertical: false).padding(.vertical, 8)
                                    .accessibilityIdentifier("table-cell-" + block.id + "-" + String(rowIndex) + "-" + String(index))
                            }
                        }
                        if rowIndex != rows.last?.0 { Divider().gridCellUnsizedAxes(.horizontal) }
                    }
                }
            }.accessibilityIdentifier("goal-table-" + block.id)
            if rows.isEmpty {
                GoalReadText(value: block["empty_message"].string ?? "No rows").font(.footnote).foregroundStyle(PanelsTheme.Goal.faint)
            }
        }.modifier(GoalPanel(padding: 16))
    }
    private func cellView(_ cell: GoalJSON, rowIndex: Int, column: Int) -> some View {
        HStack(spacing: 4) {
            if block["cell_tones"].array.indices.contains(rowIndex), block["cell_tones"].array[rowIndex].array.indices.contains(column), let tone = block["cell_tones"].array[rowIndex].array[column].string { Circle().fill(PanelsTheme.Goal.tone(tone)).frame(width: 4, height: 4) }
            GoalReadText(value: cell.text)
                .panelsScaledFont(size: column == 0 || column == readingColumn ? 13 : 11, weight: column == 0 ? .medium : .regular)
                .foregroundStyle(column == 0 || column == readingColumn ? PanelsTheme.strong : PanelsTheme.Goal.faint)
                .monospacedDigit()
        }
    }
}

/// One Goal text field, shown as plain text.
struct GoalReadText: View {
    let value: String
    var body: some View { Text(value).frame(maxWidth: .infinity, alignment: .leading) }
}
