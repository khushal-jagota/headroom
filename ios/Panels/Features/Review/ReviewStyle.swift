import SwiftUI
import UIKit

enum ReviewStyle {
    static let gutter: CGFloat = 20
    static let actionHeight: CGFloat = 50
    static let enter = 0.32
    static let fadeOut = PanelsTheme.Motion.exit
    static let fadeIn = 0.20
    static let swipeOut = 0.15
    static let swipeFraction: CGFloat = 0.30
    static let title = PanelsTheme.TypeScale.reviewTitle
    static let empty = PanelsTheme.TypeScale.reviewCompletion
    static var entrance: Animation { .timingCurve(0.2, 0.7, 0.3, 1, duration: enter) }
    @MainActor static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    @MainActor static func warning() { UINotificationFeedbackGenerator().notificationOccurred(.warning) }
}

/// Equal-size decisions; approval uses the web's filled accent and dark ink.
struct ReviewActionStyle: ButtonStyle {
    enum Context { case proposal, message }
    var approval = false
    var context: Context = .proposal
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(context == .proposal ? PanelsTheme.TypeScale.reviewAction : PanelsTheme.TypeScale.body)
            .frame(maxWidth: .infinity, minHeight: ReviewStyle.actionHeight)
            .foregroundStyle(approval ? PanelsTheme.ink : PanelsTheme.strong)
            .background(approval ? PanelsTheme.accent : PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
            .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(approval ? Color.clear : PanelsTheme.border, lineWidth: 1))
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// One read-only decision surface, used by Review and Ticket.
struct DecisionProposalCard: View {
    let fieldLabel: String
    let reviewer: String
    let text: String
    let baseURL: URL
    let onReviewer: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                Text(fieldLabel).foregroundStyle(PanelsTheme.Web.secondary)
                Text("· Approver").foregroundStyle(PanelsTheme.Web.tertiary)
                Button(action: onReviewer) {
                    HStack(spacing: 2) { Text(reviewer); Image(systemName: "chevron.down").font(.system(size: 10)) }
                        .frame(minHeight: 28)
                }.buttonStyle(.plain).foregroundStyle(PanelsTheme.Web.text)
                    .accessibilityIdentifier("decision.reviewer")
            }.panelsScaledFont(size: 12.5, weight: .medium)
            DecisionMarkdown(source: text, baseURL: baseURL)
        }.padding(PanelsTheme.Web.cardPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(PanelsTheme.Web.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius).stroke(PanelsTheme.Web.border, lineWidth: 1))
            .reviewArtifactLinks()
    }
}

/// Proposal Markdown keeps code and tables readable without widening the page.
struct DecisionMarkdown: View {
    let source: String
    let baseURL: URL
    var emphasizesFirstParagraph = true
    @ScaledMetric(relativeTo: .body) private var codeSize: CGFloat = 12.5
    @ScaledMetric(relativeTo: .body) private var bodyLineSpacing: CGFloat = 5
    @ScaledMetric(relativeTo: .body) private var firstParagraphLineSpacing: CGFloat = 4
    private var blocks: [ArtifactMarkdownBlock] { ArtifactMarkdownBlock.parse(source) }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(blocks) { block in
                switch block.kind {
                case .rule: Divider().overlay(PanelsTheme.Web.border)
                case .code:
                    ScrollView(.horizontal) {
                        Text(block.text).font(.system(size: codeSize, design: .monospaced)).textSelection(.enabled)
                            .padding(12).fixedSize(horizontal: true, vertical: false)
                    }.background(PanelsTheme.Web.code, in: RoundedRectangle(cornerRadius: 8))
                case .heading:
                    inline(block.text).panelsScaledFont(size: 15, weight: .semibold)
                        .foregroundStyle(PanelsTheme.Web.strong).accessibilityAddTraits(.isHeader)
                case .quote:
                    HStack(alignment: .top, spacing: 12) {
                        Rectangle().fill(PanelsTheme.Web.tertiary).frame(width: 2)
                        inline(block.text).panelsScaledFont(size: 14).foregroundStyle(PanelsTheme.Web.secondary)
                    }.fixedSize(horizontal: false, vertical: true)
                case .paragraph, .list:
                    if let rows = table(block.text) {
                        ScrollView(.horizontal) {
                            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                                ForEach(Array(rows.enumerated()), id: \.offset) { index, cells in
                                    GridRow {
                                        ForEach(Array(cells.enumerated()), id: \.offset) { _, cell in
                                            inline(cell).panelsScaledFont(size: 14, weight: index == 0 ? .semibold : .regular)
                                                .fixedSize().padding(8)
                                                .frame(maxWidth: .infinity, alignment: .leading)
                                                .background(index == 0 ? PanelsTheme.Web.surface : Color.clear)
                                                .overlay(Rectangle().stroke(PanelsTheme.Web.border, lineWidth: 1))
                                        }
                                    }
                                }
                            }
                        }
                    } else {
                        inline(block.text).panelsScaledFont(size: emphasizesFirstParagraph && block.id == blocks.first?.id && block.kind == .paragraph ? 15 : 14,
                                                           weight: emphasizesFirstParagraph && block.id == blocks.first?.id && block.kind == .paragraph ? .medium : .regular)
                            .foregroundStyle(emphasizesFirstParagraph && block.id == blocks.first?.id && block.kind == .paragraph ? PanelsTheme.Web.strong : PanelsTheme.Web.text)
                            .lineSpacing(emphasizesFirstParagraph && block.id == blocks.first?.id && block.kind == .paragraph ? firstParagraphLineSpacing : bodyLineSpacing)
                    }
                }
            }
        }.lineSpacing(bodyLineSpacing).frame(maxWidth: .infinity, alignment: .leading)
    }
    private func inline(_ text: String) -> some View {
        var value = (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace), baseURL: baseURL)) ?? AttributedString(text)
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
        return Text(value).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
    }
    private func table(_ text: String) -> [[String]]? {
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

struct DecisionNoteActions: View {
    @Binding var note: String
    let showsSendBack: Bool
    let canReturn: Bool
    let ready: Bool
    var busyAction: String? = nil
    var error: String? = nil
    var noteEnabled = true
    var accessory: AnyView? = nil
    var focusRequest = 0
    var blurRequest = 0
    let onApprove: () -> Void
    let onSendBack: () -> Void
    @FocusState private var focused: Bool
    var body: some View {
        VStack(alignment: .trailing, spacing: 12) {
            HStack(alignment: .bottom, spacing: 4) {
                TextField("Add a note for the worker…", text: $note, axis: .vertical)
                    .lineLimit(1...5).panelsScaledFont(size: 16).focused($focused)
                    .frame(minHeight: 32).padding(.vertical, 5)
                    .disabled(!noteEnabled || busyAction != nil)
                    .accessibilityIdentifier("review.revision")
                if let accessory { accessory }
            }.padding(.leading, 16).padding(.trailing, 6).padding(.vertical, 1)
                .background(PanelsTheme.Web.composer, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(focused ? PanelsTheme.Web.accent.opacity(0.6) : Color.clear, lineWidth: 1))
            HStack(spacing: 8) {
                Spacer(minLength: 0)
                if showsSendBack {
                    Button {
                        if note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { focused = true }
                        else { focused = false; onSendBack() }
                    } label: { actionLabel("Send it back", active: busyAction == "back") }
                        .disabled(!ready || !canReturn || !noteEnabled || busyAction != nil)
                        .accessibilityIdentifier("review.sendBack")
                }
                Button { focused = false; onApprove() } label: { actionLabel("Approve", active: busyAction == "approve") }
                    .disabled(!ready || !noteEnabled || busyAction != nil).accessibilityIdentifier("review.approve")
            }.buttonStyle(DecisionButtonStyle())
            if showsSendBack && !canReturn {
                Text("No existing Worker conversation to send a revision to.")
                    .panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.tertiary).multilineTextAlignment(.trailing)
            }
            if let error {
                Text(error).panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.error).multilineTextAlignment(.trailing)
            }
        }.padding(.horizontal, 20).padding(.top, 28).padding(.bottom, 12)
            .background {
                VStack(spacing: 0) {
                    LinearGradient(colors: [.clear, PanelsTheme.Web.surface], startPoint: .top, endPoint: .bottom).frame(height: 24)
                    PanelsTheme.Web.surface
                }.ignoresSafeArea(.container, edges: .bottom)
            }
            .foregroundStyle(PanelsTheme.Web.text)
            .onChange(of: noteEnabled) { _, enabled in if !enabled { focused = false } }
            .onChange(of: focusRequest) { _, _ in focused = true }
            .onChange(of: blurRequest) { _, _ in focused = false }
    }
    private func actionLabel(_ title: String, active: Bool) -> some View {
        Text(title).opacity(active ? 0 : 1).overlay { if active { RingSpinner(size: .compact, color: PanelsTheme.Web.text) } }
    }
}

struct DecisionButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.panelsScaledFont(size: 13.5, weight: .medium)
            .padding(.horizontal, 18).frame(minHeight: 44)
            .background(configuration.isPressed ? PanelsTheme.Web.selected : PanelsTheme.Web.composer, in: RoundedRectangle(cornerRadius: 8))
            .opacity(isEnabled ? 1 : 0.4)
    }
}

struct DecisionApprovalSheet: View {
    let stages: [ReviewWorkerManifest.Stage]
    let nextStage: String
    let holder: Principal
    let managerID: String?
    var reviewerOnly = false
    let save: (String?, Principal) async throws -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selected: String?
    @State private var busy = false
    @State private var error: String?
    private var reviewers: [(String, Principal)] {
        var values = [("User", Principal(kind: "owner", id: "owner"))]
        if let managerID { values.append(("Manager", Principal(kind: "manager", id: managerID))) }
        values.append(("Chief", Principal(kind: "chief", id: "chief")))
        return values
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    if selected != nil && !reviewerOnly {
                        Button { selected = nil } label: { Image(systemName: "chevron.left").frame(width: 28, height: 36) }
                            .accessibilityLabel("Back to How far")
                    }
                    Text(reviewerOnly ? "Who reviews" : selected.map { selectedID in "Who reviews · until \(stages.first { $0.id == selectedID }?.label ?? selectedID)" } ?? "How far")
                        .textCase(.uppercase).tracking(0.66).panelsScaledFont(size: 11, weight: .medium)
                        .foregroundStyle(PanelsTheme.Web.tertiary)
                }.frame(minHeight: 36).padding(.horizontal, 12)
                if reviewerOnly || selected != nil {
                    ForEach(reviewers, id: \.1) { name, principal in
                        Button {
                            busy = true; error = nil
                            Task {
                                do { try await save(reviewerOnly ? nil : selected, principal); dismiss() }
                                catch { self.error = error.localizedDescription }
                                busy = false
                            }
                        } label: { row(name, selected: principal == holder) }
                            .accessibilityIdentifier("decision.reviewer.\(principal.kind)")
                    }
                } else {
                    ForEach(stages) { stage in
                        Button { selected = stage.id; Haptics.selection() } label: { row(stage.label, selected: stage.id == nextStage) }
                            .accessibilityIdentifier("decision.stage.\(stage.id)")
                    }
                }
                if let error { Text(error).panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.error).padding(12) }
            }.padding(8)
        }.buttonStyle(.plain).disabled(busy).background(PanelsTheme.Web.raised)
            .presentationCornerRadius(24).presentationDragIndicator(.visible)
            .presentationDetents([.medium, .large]).interactiveDismissDisabled(busy)
    }
    private func row(_ label: String, selected: Bool) -> some View {
        HStack { Text(label); Spacer(); if selected { Image(systemName: "checkmark").foregroundStyle(PanelsTheme.Web.accent) } }
            .panelsScaledFont(size: 14).foregroundStyle(PanelsTheme.Web.text)
            .frame(minHeight: 44).padding(.horizontal, 12).contentShape(Rectangle())
    }
}
