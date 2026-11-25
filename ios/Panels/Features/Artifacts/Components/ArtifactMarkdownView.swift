import Charts
import SwiftUI

struct ArtifactMarkdownView: View {
    let source: String
    let baseURL: URL
    var body: some View {
        ScrollView {
            PanelsMarkdown(source: source, baseURL: baseURL, textSize: .reading, showsInlineMedia: false, lazy: true)
                .frame(maxWidth: 720, alignment: .leading)
                .padding(PanelsTheme.Space.xl).frame(maxWidth: .infinity)
        }
    }
}

/// The app's one Markdown display: artifacts, proposals, messages, Ticket fields, Goal prose and
/// conversation rows. Code and tables scroll sideways without widening the page; text hugs its
/// own width, so a short message keeps a short bubble.
/// The size a caller reads Markdown at. Long documents read at the body size; Review cards,
/// Ticket fields keep the compact size. Conversation prose uses its approved 15-point size.
enum PanelsMarkdownTextSize {
    case compact, reading, conversation
    /// The paragraph size before Dynamic Type: `.reading` is the system body size.
    var paragraph: CGFloat { switch self { case .compact: 14; case .reading: 17; case .conversation: 15 } }
}

struct PanelsMarkdown: View {
    let source: String
    let baseURL: URL
    var textSize = PanelsMarkdownTextSize.compact
    /// A proposal leads with its first paragraph.
    var emphasizesFirstParagraph = false
    var showsInlineMedia = true
    /// Only the artifact page, a scroll view of its own, lays blocks out lazily. A row in a lazy
    /// list keeps a plain stack, so its height stays stable for Latest and the read point.
    var lazy = false
    @State private var memo = PanelsMarkdownMemo()
    @ScaledMetric(relativeTo: .body) private var scale: CGFloat = 1
    private var size: CGFloat { textSize.paragraph }
    private var codeSize: CGFloat { (textSize == .conversation ? size : size - 1.5) * scale }
    @ScaledMetric(relativeTo: .body) private var bodyLineSpacing: CGFloat = 5
    @ScaledMetric(relativeTo: .body) private var firstParagraphLineSpacing: CGFloat = 4

    var body: some View {
        let document = memo.document(source: source, baseURL: baseURL, codeSize: codeSize)
        if lazy {
            LazyVStack(alignment: .leading, spacing: PanelsTheme.Space.md) { content(document) }
        } else {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.md) { content(document) }
        }
    }

    @ViewBuilder private func content(_ document: PanelsMarkdownMemo.Styled) -> some View {
        ForEach(document.blocks) { block in
            switch block.kind {
            case .rule: Divider().overlay(PanelsTheme.Web.border)
            case .code:
                if let chart = block.chart { ArtifactChartView(chart: chart) }
                else {
                    ScrollView(.horizontal) {
                        Text(block.text).font(.system(size: codeSize, design: .monospaced)).foregroundStyle(PanelsTheme.Web.text)
                            .textSelection(.enabled).padding(PanelsTheme.Space.md).fixedSize(horizontal: true, vertical: false)
                    }.background(PanelsTheme.Web.code, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                }
            case .heading(let level):
                text(document.inline[block.id]).panelsScaledFont(size: textSize == .conversation ? 20 : size + 1, weight: PanelsTheme.TypeScale.markdownHeadingWeight(level))
                    .foregroundStyle(PanelsTheme.Web.strong).accessibilityAddTraits(.isHeader)
                    .padding(.top, block.id == 0 ? 0 : PanelsTheme.Space.sm)
            case .quote:
                HStack(alignment: .top, spacing: PanelsTheme.Space.md) {
                    Rectangle().fill(PanelsTheme.Web.tertiary).frame(width: 2)
                    text(document.inline[block.id]).panelsScaledFont(size: size).foregroundStyle(PanelsTheme.Web.secondary)
                        .lineSpacing(bodyLineSpacing)
                }.fixedSize(horizontal: false, vertical: true)
            case .paragraph, .list:
                if let rows = document.tables[block.id] { table(rows) }
                else {
                    let lead = emphasizesFirstParagraph && block.id == 0 && block.kind == .paragraph
                    text(document.inline[block.id]).panelsScaledFont(size: lead ? size + 1 : size, weight: lead ? .medium : .regular)
                        .foregroundStyle(lead ? PanelsTheme.Web.strong : PanelsTheme.Web.text)
                        .lineSpacing(lead ? firstParagraphLineSpacing : bodyLineSpacing)
                }
            }
        }
        if showsInlineMedia {
            ForEach(document.media) { ArtifactInlineMediaPreview(target: $0) }
        }
    }

    private func text(_ value: AttributedString?) -> some View {
        Text(value ?? AttributedString()).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
    }

    private func table(_ rows: [[AttributedString]]) -> some View {
        ScrollView(.horizontal) {
            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                ForEach(Array(rows.enumerated()), id: \.offset) { index, cells in
                    GridRow {
                        ForEach(Array(cells.enumerated()), id: \.offset) { _, cell in
                            Text(cell).textSelection(.enabled).panelsScaledFont(size: size, weight: index == 0 ? .semibold : .regular)
                                .foregroundStyle(PanelsTheme.Web.text)
                                .fixedSize().padding(PanelsTheme.Space.sm)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(index == 0 ? PanelsTheme.Web.surface : Color.clear)
                                .overlay(Rectangle().stroke(PanelsTheme.Web.border, lineWidth: 1))
                        }
                    }
                }
            }
        }
    }
}

/// One parse per source, kept by the view: a redraw, such as another row's reply streaming in,
/// reads the stored blocks and styled text instead of parsing again.
@MainActor private final class PanelsMarkdownMemo {
    struct Styled {
        let blocks: [ArtifactMarkdownBlock]
        let inline: [Int: AttributedString]
        let tables: [Int: [[AttributedString]]]
        let media: [ArtifactTarget]
    }
    private struct Key: Equatable { let source: String; let baseURL: URL; let codeSize: CGFloat }
    private var key: Key?
    private var styled: Styled?

    func document(source: String, baseURL: URL, codeSize: CGFloat) -> Styled {
        let next = Key(source: source, baseURL: baseURL, codeSize: codeSize)
        if next == key, let styled { return styled }
        let document = ArtifactMarkdownDocument(source: source, baseURL: baseURL)
        func style(_ text: AttributedString) -> AttributedString {
            var value = text
            for run in value.runs {
                if run.inlinePresentationIntent?.contains(.code) == true {
                    value[run.range].font = .system(size: codeSize, design: .monospaced)
                    value[run.range].backgroundColor = PanelsTheme.Web.code
                }
                if run.link != nil {
                    value[run.range].foregroundColor = PanelsTheme.Web.strong
                    value[run.range].underlineStyle = .single
                }
            }
            return value
        }
        let result = Styled(blocks: document.blocks, inline: document.inline.mapValues(style),
                            tables: document.tables.mapValues { $0.map { $0.map(style) } }, media: document.media)
        key = next; styled = result
        return result
    }
}

/// A `panels-chart` snapshot as the web's chat chart draws it: the title, the plot and, with two
/// or more series, a plain legend in the plot colours. No headline, range switch or inspection.
private struct ArtifactChartView: View {
    let chart: ArtifactChart

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            Text(chart.title).panelsScaledFont(size: 15, weight: .semibold).foregroundStyle(PanelsTheme.Web.strong)
                .accessibilityAddTraits(.isHeader)
            plot.frame(height: PanelsTheme.Goal.plotHeight)
            if chart.series.count > 1 { legend }
        }
        .padding(PanelsTheme.Space.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(PanelsTheme.Web.code, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius).stroke(PanelsTheme.Web.border, lineWidth: 1))
    }

    /// Bars use the goal composition palette; lines the goal series palette. Both start with the
    /// accent, and every series past the third keeps the darkest blue, as on the web.
    private func color(_ index: Int) -> Color { PanelsTheme.Goal.seriesColor(nil, index: min(index, 2), composed: !chart.isLine) }

    private var plot: some View {
        Chart {
            if chart.isLine {
                ForEach(Array(chart.series.enumerated()), id: \.element.key) { index, series in
                    ForEach(linePoints(series)) { point in
                        if index == 0 {
                            AreaMark(x: .value("Label", point.label), y: .value("Value", point.value), series: .value("Line", point.line), stacking: .unstacked)
                                .foregroundStyle(LinearGradient(colors: [color(0).opacity(0.16), color(0).opacity(0)], startPoint: .top, endPoint: .bottom))
                                .interpolationMethod(.monotone).accessibilityHidden(true)
                        }
                        LineMark(x: .value("Label", point.label), y: .value("Value", point.value), series: .value("Line", point.line))
                            .foregroundStyle(color(index)).interpolationMethod(.monotone)
                            .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                            .accessibilityLabel("\(series.name), \(point.label)").accessibilityValue(chart.reading(point.value))
                        // A reading with no neighbour has no line to sit on.
                        if point.alone {
                            PointMark(x: .value("Label", point.label), y: .value("Value", point.value))
                                .foregroundStyle(color(index)).symbolSize(28).accessibilityHidden(true)
                        }
                    }
                }
            } else {
                ForEach(chart.data) { datum in
                    ForEach(Array(chart.series.enumerated()), id: \.element.key) { index, series in
                        if let value = datum.values[series.key] {
                            if chart.stacked {
                                BarMark(x: .value("Label", datum.id), y: .value("Value", value))
                                    .foregroundStyle(color(index))
                                    .accessibilityLabel("\(series.name), \(datum.label)").accessibilityValue(chart.reading(value))
                            } else {
                                BarMark(x: .value("Label", datum.id), y: .value("Value", value))
                                    .foregroundStyle(color(index)).cornerRadius(PanelsTheme.Radius.sm)
                                    .position(by: .value("Series", series.key))
                                    .accessibilityLabel("\(series.name), \(datum.label)").accessibilityValue(chart.reading(value))
                            }
                        }
                    }
                }
            }
        }
        .chartXScale(domain: chart.data.map(\.id))
        .chartYScale(domain: .automatic(includesZero: true))
        .chartXAxis {
            AxisMarks(values: axisLabels) { value in
                AxisValueLabel { Text(value.as(String.self) ?? "").font(.system(size: 11)).foregroundStyle(PanelsTheme.Web.tertiary) }
            }
        }
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine().foregroundStyle(PanelsTheme.Goal.grid)
                AxisValueLabel {
                    if let number = value.as(Double.self) {
                        Text(chart.tick(number)).font(.system(size: 11)).foregroundStyle(PanelsTheme.Web.tertiary)
                    }
                }
            }
        }
        .chartLegend(.hidden)
    }

    /// Every label while they fit; past six, about five evenly spaced, as Chart.js skips ticks.
    private var axisLabels: [String] {
        let labels = chart.data.map(\.id)
        guard labels.count > 6 else { return labels }
        let step = Int((Double(labels.count) / 5).rounded(.up))
        return stride(from: 0, to: labels.count, by: step).map { labels[$0] }
    }

    private struct LinePoint: Identifiable {
        let id: String
        let label: String
        let value: Double
        /// A missing reading breaks the line, so each unbroken run is its own line.
        let line: String
        let alone: Bool
    }

    private func linePoints(_ series: ArtifactChart.Series) -> [LinePoint] {
        var runs: [[(String, Double)]] = [[]]
        for datum in chart.data {
            if let value = datum.values[series.key] { runs[runs.count - 1].append((datum.id, value)) }
            else if !(runs.last?.isEmpty ?? true) { runs.append([]) }
        }
        return runs.enumerated().flatMap { run, points in
            points.map { LinePoint(id: $0.0, label: $0.0, value: $0.1, line: "\(series.key)#\(run)", alone: points.count == 1) }
        }
    }

    private var legend: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: PanelsTheme.Space.lg) { legendItems }
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) { legendItems }
        }.panelsScaledFont(size: 12.5)
    }

    private var legendItems: some View {
        ForEach(Array(chart.series.enumerated()), id: \.element.key) { index, series in
            Text(series.name).foregroundStyle(color(index))
        }
    }
}

/// This view never receives a non-media file, so prose cannot open one in full.
private struct ArtifactInlineMediaPreview: View {
    let target: ArtifactTarget
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var document: ArtifactDocument?
    @State private var image: UIImage?
    @State private var error: String?
    /// Set once this preview read the server's file. Until then the next connection reads again.
    @State private var isFresh = false

    var body: some View {
        Group {
            if let image {
                Button { follow() } label: {
                    Image(uiImage: image).resizable().scaledToFit()
                        .frame(maxHeight: PanelsTheme.inlineArtifactMediaHeight)
                }.buttonStyle(.plain)
                    .accessibilityLabel(target.title)
                    .accessibilityIdentifier("inline-artifact-image")
            } else if let document, ArtifactInlineMedia.isVideo(target) {
                ArtifactMediaView(url: document.localURL)
                    .frame(height: PanelsTheme.inlineArtifactMediaHeight)
                    .accessibilityLabel(target.title)
                    .accessibilityIdentifier("inline-artifact-video")
            } else if let error {
                LoadErrorView(message: error) { await load() }
            } else { RingSpinner() }
        }
        .task(id: "\(store.baseURL)|\(target.id)|\(isFresh ? 0 : store.connectionSession)") { await load() }
    }

    private func follow() {
        if let url = try? store.client.artifactURL(target) { openURL(url) }
    }
    private func load() async {
        do {
            // A photo draws from its saved copy at once. A video has no copy and reads each time.
            try await loadArtifact(target, client: store.client, shown: document) { result in
                try Task.checkCancellation()
                let loadedImage = ArtifactInlineMedia.isPhoto(target) ? UIImage(contentsOfFile: result.localURL.path) : nil
                if ArtifactInlineMedia.isPhoto(target), loadedImage == nil {
                    throw APIError(status: 0, message: "This image could not be opened.")
                }
                image = loadedImage; document = result; error = nil
            }
            isFresh = true
        } catch {
            // The server no longer has this file: its answer replaces the copy.
            if (error as? APIError)?.status == 404 { image = nil; document = nil }
            if !Task.isCancelled, document == nil { self.error = ServerReadFailure.note(error) }
        }
    }
}
