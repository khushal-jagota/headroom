import SwiftUI
import UIKit

/// The server chooses each row's mode and position. This view supplies the native
/// form: a message, turn head, work disclosure, tool call or quiet system line.
struct ConversationTranscriptRow: View {
    let row: ConversationRow
    @Bindable var model: ConversationModel
    let activityUnknown: Bool
    var answerNote: String? = nil
    private var displayedNote: String? { answerNote ?? row.note }
    @Environment(AppStore.self) private var store
    @State private var toolExpanded = false
    @State private var progress: String?
    @State private var wholeDetail: String?
    @State private var fullDetailError: String?
    @State private var loadingFullDetail = false
    @ScaledMetric(relativeTo: .body) private var outputLineHeight = 20.0

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if row.breakBefore {
                Rectangle().fill(PanelsTheme.muted).frame(width: 28, height: 2)
                    .padding(.top, 4).padding(.bottom, 10).accessibilityHidden(true)
            }
            switch row.presentation {
            case .turn: turn
            case .workGroup: workGroup
            case .tool: tool
            case .system: system
            case .message, .narration: message
            }
        }
        .font(ConversationTypography.body)
    }

    @ViewBuilder private var turn: some View {
        if let item = row.item {
            HStack(spacing: 8) {
                if item.settled != true && !activityUnknown { RingSpinner(size: .compact) }
                if item.settled != true {
                    if activityUnknown { Text("Current activity is unknown") }
                    else { ConversationElapsedLabel(prefix: "Working for", startedAt: item.startedAtUnixMilliseconds) }
                } else if let seconds = item.durationSeconds {
                    Text("Worked for " + ConversationElapsedLabel.duration(max(0, Int(seconds))))
                } else { Text("Worked") }
                Spacer(minLength: 0)
                if model.lens == .focus, (item.foldedMessageCount ?? 0) > 0 {
                    Button {
                        Task { await model.toggleScope(item, using: store.client) }
                    } label: {
                        HStack(spacing: 4) {
                            Text("Earlier messages")
                            Image(systemName: row.scopeExpanded ? "chevron.up" : "chevron.down").font(ConversationTypography.caption)
                        }
                    }.frame(minHeight: 44).accessibilityValue(row.scopeExpanded ? "Expanded" : "Collapsed")
                    .accessibilityIdentifier("conversation-turn-disclosure")
                }
            }
            .font(ConversationTypography.body).foregroundStyle(PanelsTheme.faint)
            .task(id: "\(item.key)|\(model.lens)") {
                if model.lens == .full { await model.loadScope(item, using: store.client) }
            }
            scopeFooter(item)
        }
    }

    @ViewBuilder private var workGroup: some View {
        if let item = row.item {
            if item.entryCount == 1 {
                Color.clear.frame(height: 0).task { await model.loadScope(item, using: store.client) }
            } else {
                Button {
                    Task { await model.toggleScope(item, using: store.client) }
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: row.scopeExpanded ? "chevron.down" : "chevron.right")
                            .font(ConversationTypography.caption)
                        Text(item.entryCount.map { "\($0) tool calls" } ?? "Tool calls")
                        Spacer(minLength: 8)
                        if item.settled != true {
                            Text(activityUnknown ? "Unknown" : "Running").font(ConversationTypography.metadata)
                        }
                    }.frame(minHeight: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain).foregroundStyle(PanelsTheme.muted)
                .accessibilityIdentifier("conversation-work-disclosure")
                .accessibilityValue(row.scopeExpanded ? "Expanded" : "Collapsed")
                if row.scopeLoading { RingSpinner(size: .compact) }
            }
            scopeFooter(item)
        }
    }

    @ViewBuilder private func scopeFooter(_ item: ConversationItem) -> some View {
        if row.scopeExpanded, model.scopeHasOlder(item) {
            Button("Earlier work") { Task { await model.loadEarlierScope(item, using: store.client) } }
                .frame(minHeight: 44).disabled(row.scopeLoading)
        }
        if let error = model.bodyErrors[item.key] {
            HStack {
                Text(error).font(ConversationTypography.metadata).foregroundStyle(PanelsTheme.error)
                Button("Try again") {
                    Task {
                        if model.scopeHasOlder(item), row.scopeExpanded { await model.loadEarlierScope(item, using: store.client) }
                        else { await model.loadScope(item, using: store.client) }
                    }
                }.frame(minHeight: 44)
            }
        }
    }

    private var tool: some View {
        DisclosureGroup(isExpanded: $toolExpanded) {
            VStack(alignment: .leading, spacing: 8) {
                if let item = row.item, item.body?.reference != nil, model.expandedBodies[item.key] == nil {
                    if let error = model.bodyErrors[item.key] {
                        Text(error).foregroundStyle(PanelsTheme.error)
                        Button("Try again") { Task { await model.loadBody(item, using: store.client) } }
                    } else { RingSpinner(size: .compact) }
                } else {
                    if let started = row.body?.startedDetail, !started.isEmpty, started != row.body?.detail {
                        output(started)
                    }
                    if let detail = wholeDetail ?? progress ?? row.body?.detail, !detail.isEmpty {
                        output(detail)
                    } else { Text("No additional detail").foregroundStyle(PanelsTheme.faint) }
                    if row.body?.cappedDetailSequence != nil, wholeDetail == nil {
                        Button(loadingFullDetail ? "Reading full output…" : "See full output") { Task { await loadFullDetail() } }
                            .disabled(loadingFullDetail).frame(minHeight: 44)
                            .accessibilityIdentifier("conversation-full-tool-output")
                    }
                    if let fullDetailError { Text(fullDetailError).foregroundStyle(PanelsTheme.error) }
                }
            }
            .font(ConversationTypography.body)
            .padding(.vertical, 6)
        } label: {
            HStack(spacing: 8) {
                Text(row.body?.title ?? "Tool call")
                Spacer(minLength: 8)
                if let state = row.body?.status {
                    Text(state == "running" && activityUnknown ? "Unknown" : state.capitalized)
                        .font(ConversationTypography.metadata).foregroundStyle(PanelsTheme.faint)
                }
            }.frame(minHeight: 44)
        }
        .accessibilityIdentifier("conversation-tool-disclosure")
        .tint(PanelsTheme.muted)
        .task(id: "\(toolExpanded)|\(row.item?.body?.reference?.version ?? "")|\(row.body?.toolCallId ?? "")|\(model.status?.liveToolProgress?.version ?? "")") {
            guard toolExpanded else { progress = nil; return }
            if let item = row.item, item.body?.reference != nil, model.expandedBodies[item.key] == nil {
                await model.loadBody(item, using: store.client)
                return
            }
            guard let id = model.conversationID, let toolID = row.body?.toolCallId,
                  model.status?.liveToolKey == toolID, let reference = model.status?.liveToolProgress else { progress = nil; return }
            do {
                let value = try await store.client.conversationLiveText(id, reference: reference)
                try Task.checkCancellation()
                if model.conversationID == id { progress = value; fullDetailError = nil }
            } catch {
                if !Task.isCancelled { fullDetailError = ServerReadFailure.note(error) ?? "This output did not load." }
            }
        }
        .onChange(of: row.body?.cappedDetailSequence) { _, _ in wholeDetail = nil; fullDetailError = nil }
    }

    private func output(_ text: String) -> some View {
        let lines = min(17, text.split(separator: "\n", omittingEmptySubsequences: false).count)
        return ScrollView([.horizontal, .vertical]) {
            PanelsSelectableText(text, font: .monospacedSystemFont(ofSize: outputLineHeight * 0.75, weight: .regular), color: UIColor(PanelsTheme.text))
                .fixedSize(horizontal: true, vertical: false).padding(10)
        }
        .frame(height: CGFloat(max(1, lines)) * outputLineHeight + 20)
        .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: 8))
    }

    private func loadFullDetail() async {
        guard !loadingFullDetail, let id = model.conversationID, let sequence = row.body?.cappedDetailSequence else { return }
        loadingFullDetail = true; fullDetailError = nil
        defer { loadingFullDetail = false }
        do {
            let text = try await store.client.conversationToolDetail(id, sequence: sequence)
            try Task.checkCancellation()
            guard model.conversationID == id, row.body?.cappedDetailSequence == sequence else { return }
            if let text { wholeDetail = text }
            else { fullDetailError = "This output is no longer available." }
        } catch {
            if !Task.isCancelled, model.conversationID == id { fullDetailError = ServerReadFailure.note(error) ?? "This output did not load." }
        }
    }

    private var systemText: String {
        if row.body?.kind == "turn_stopped" { return "Stopped before it finished" }
        if row.body?.kind == "turn_ended", row.body?.ending == "interrupted" {
            if let key = row.item?.turnKey,
               let duration = model.items.first(where: { $0.kind == "turn" && $0.key == key })?.durationSeconds,
               duration > 0 {
                return "Stopped after " + ConversationElapsedLabel.duration(Int(duration))
            }
            return "Stopped before it finished"
        }
        return row.text
    }

    @ViewBuilder private var system: some View {
        VStack(alignment: .leading, spacing: 6) {
            PanelsSelectableText(systemText, font: .preferredFont(forTextStyle: .subheadline), color: UIColor(PanelsTheme.faint))
            if let note = displayedNote { PanelsSelectableText(note, font: .preferredFont(forTextStyle: .footnote), color: UIColor(PanelsTheme.faint)) }

        }.font(ConversationTypography.body).foregroundStyle(PanelsTheme.faint)
    }

    @ViewBuilder private var message: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let sender = row.senderLabel {
                Text(sender).font(ConversationTypography.metadata).foregroundStyle(PanelsTheme.faint)
            }
            if row.item?.body?.reference != nil, let item = row.item, model.expandedBodies[item.key] == nil {
                Button("Read message") { Task { await model.loadBody(item, using: store.client) } }
                if let error = model.bodyErrors[item.key] { Text(error).foregroundStyle(PanelsTheme.error).font(ConversationTypography.metadata) }
            } else if !row.pieces.isEmpty {
                if row.own {
                    ForEach(Array(row.pieces.enumerated()).filter { $0.element.piece != "text" }, id: \.offset) { index, piece in
                        media(piece, index: row.pieces[..<index].filter { $0.piece != "text" }.count)
                    }
                    ForEach(Array(row.pieces.enumerated()).filter { $0.element.piece == "text" }, id: \.offset) { _, piece in
                        markdown(piece.text ?? "")
                    }
                } else {
                    ForEach(Array(row.pieces.enumerated()), id: \.offset) { index, piece in
                        if piece.piece == "text" { markdown(piece.text ?? "") }
                        else { media(piece, index: row.pieces[..<index].filter { $0.piece != "text" }.count) }
                    }
                }
            } else {
                if !row.attachments.isEmpty {
                    ConversationAttachmentTray(pieces: row.attachments, preparing: row.retryID == model.preparingOutgoingID && model.preparingOutgoingID != nil)
                }
                if !row.text.isEmpty { markdown(row.text) }
            }
            if !row.own, let note = displayedNote {
                PanelsSelectableText(note, font: .preferredFont(forTextStyle: .footnote), color: UIColor(PanelsTheme.faint)).frame(minHeight: 28)
            }
        }
        .opacity(row.presentation == .narration ? 0.75 : 1)
        .padding(row.own ? 12 : 0)
        .background(row.own ? PanelsTheme.raised : .clear, in: RoundedRectangle(cornerRadius: 12))
    }

    private func markdown(_ source: String) -> some View {
        PanelsMarkdown(source: source, baseURL: store.baseURL, textSize: .conversation)
    }
    private func media(_ piece: ConversationBody.Piece, index: Int) -> some View {
        ConversationMediaTile(piece: piece, conversationID: model.conversationID,
                              localFallback: row.attachments.indices.contains(index) ? row.attachments[index] : nil)
    }
}

/// A timed native label stays truthful when the server supplies no start time.
struct ConversationElapsedLabel: View {
    let prefix: String
    let startedAt: Int64?
    var body: some View {
        if let startedAt {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                Text("\(prefix) \(Self.duration(max(0, Int(context.date.timeIntervalSince1970 - Double(startedAt) / 1000))))")
                    .monospacedDigit()
            }
        } else { Text("Working…") }
    }
    static func duration(_ seconds: Int) -> String {
        if seconds >= 3600 { return "\(seconds / 3600)h \((seconds % 3600) / 60)m" }
        if seconds >= 60 { return "\(seconds / 60)m \(seconds % 60)s" }
        return "\(seconds)s"
    }
}
