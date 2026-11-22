import SwiftUI
import UIKit

enum ReviewStyle {
    static let gutter: CGFloat = 20
    static let actionHeight: CGFloat = 46
    static let enter = 0.32
    static let fadeOut = PanelsTheme.Motion.exit
    static let fadeIn = 0.20
    static let swipeOut = 0.15
    static let swipeFraction: CGFloat = 0.30
    static var entrance: Animation { .timingCurve(0.2, 0.7, 0.3, 1, duration: enter) }
    @MainActor static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    @MainActor static func warning() { UINotificationFeedbackGenerator().notificationOccurred(.warning) }
}

/// Review's two button tiers, equal width in a row: the quiet secondary fill, and the
/// accent fill with dark ink for the one action that moves work forward.
struct ReviewButtonStyle: ButtonStyle {
    var primary = false
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .panelsScaledFont(size: 14.5, weight: .semibold)
            .lineLimit(1).minimumScaleFactor(0.8)
            .padding(.horizontal, PanelsTheme.Space.md)
            .frame(maxWidth: .infinity, minHeight: ReviewStyle.actionHeight)
            .foregroundStyle(primary ? PanelsTheme.ink : PanelsTheme.Web.strong)
            .background(primary ? PanelsTheme.accent : PanelsTheme.Web.composer, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .opacity(isEnabled ? 1 : 0.38)
    }
}

/// A card's bottom controls, above the keyboard and the home indicator, with the page
/// fading in behind them.
struct ReviewDock<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        VStack(spacing: 10) { content }
            .padding(.horizontal, 18).padding(.top, 8).padding(.bottom, 12)
            .background {
                VStack(spacing: 0) {
                    LinearGradient(colors: [PanelsTheme.Web.surface.opacity(0), PanelsTheme.Web.surface], startPoint: .top, endPoint: .bottom).frame(height: 22).offset(y: -22)
                    PanelsTheme.Web.surface
                }.ignoresSafeArea(.container, edges: .bottom)
            }
    }
}

/// Equal-width buttons in one row; stacked at accessibility sizes.
struct ReviewButtonRow<Content: View>: View {
    @ViewBuilder let content: Content
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        if typeSize.isAccessibilitySize { VStack(spacing: PanelsTheme.Space.sm) { content } }
        else { HStack(spacing: PanelsTheme.Space.sm) { content } }
    }
}

/// A note on the card itself, never a banner: a refusal, or a proposal that moved.
struct ReviewInlineNote: View {
    enum Tone { case refused, info }
    let tone: Tone
    let title: String
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).panelsScaledFont(size: 13, weight: .semibold)
            Text(text).panelsScaledFont(size: 13).fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(tone == .refused ? PanelsTheme.Web.errorText : PanelsTheme.accentText)
        .padding(.horizontal, 13).padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tone == .refused ? PanelsTheme.Web.errorSurface : PanelsTheme.accentSurface, in: RoundedRectangle(cornerRadius: 11))
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(tone == .refused ? PanelsTheme.Web.error.opacity(0.28) : PanelsTheme.accent.opacity(0.22), lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(tone == .refused ? "review.refusal" : "review.notice")
    }
}

/// One read-only decision surface, used by Review and Ticket: the stage on the left, the
/// reviewer on the right, then the proposal.
struct DecisionProposalCard: View {
    let fieldLabel: String
    let reviewer: String
    let text: String
    let baseURL: URL
    /// What an artifact linked from the proposal docks below it.
    let dock: ArtifactDock
    var reviewerEnabled = true
    let onReviewer: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Text(fieldLabel).fontWeight(.semibold).foregroundStyle(PanelsTheme.Web.secondary)
                Spacer(minLength: 0)
                Button(action: onReviewer) {
                    HStack(spacing: 4) { Text(reviewer); Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold)) }
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 7))
                        .frame(minHeight: 32)
                }.buttonStyle(.plain).foregroundStyle(PanelsTheme.Web.secondary)
                    .disabled(!reviewerEnabled)
                    .accessibilityLabel("Reviewer, \(reviewer)")
                    .accessibilityIdentifier("decision.reviewer")
            }.panelsScaledFont(size: 13, weight: .medium)
            PanelsMarkdown(source: text, baseURL: baseURL, emphasizesFirstParagraph: true)
        }.padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(PanelsTheme.Web.cardFill, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
            .reviewArtifactLinks(dock: dock)
    }
}

/// The proposal's controls: the note for the worker with its voice button, then Skip
/// (in Review), Return and Approve in one equal-width row.
struct DecisionNoteActions: View {
    @Binding var note: String
    let showsSendBack: Bool
    let canReturn: Bool
    let ready: Bool
    var busyAction: String? = nil
    var error: String? = nil
    var noteEnabled = true
    var accessory: AnyView? = nil
    /// The note's pictures and files, where the note takes them (Review).
    var attachments: ConversationModel? = nil
    var focusRequest = 0
    var blurRequest = 0
    /// Review's Skip; the Ticket page has none.
    var onSkip: (() -> Void)? = nil
    var skipEnabled = true
    let onApprove: () -> Void
    let onSendBack: () -> Void
    @Environment(\.screenTextEntry) private var textEntry
    @State private var inputID = UUID()
    @State private var attachOpen = false
    private var focused: Bool { textEntry?.focus.wrappedValue == inputID }
    var body: some View {
        ReviewDock {
            VStack(alignment: .leading, spacing: 6) {
                if let attachments, !attachments.attachments.isEmpty || attachments.preparingPhoto {
                    ConversationAttachmentTray(pieces: attachments.attachments, preparingPhotoNames: attachments.preparingPhotos.values.sorted(),
                        preparing: attachments.preparingDraftPhotos, remove: noteEnabled && busyAction == nil ? { index in
                            guard attachments.attachments.indices.contains(index) else { return }
                            attachments.attachments.remove(at: index)
                        } : nil)
                        .padding(.top, 6)
                }
                if let error = attachments?.error {
                    Text(error).panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.error)
                }
                HStack(alignment: .bottom, spacing: 4) {
                    if attachments != nil {
                        Button { textEntry?.endEditing(); attachOpen = true } label: {
                            Image(systemName: "plus").font(.system(size: 16, weight: .medium)).frame(width: 40, height: 40).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain).foregroundStyle(PanelsTheme.Web.secondary)
                        .disabled(!noteEnabled || busyAction != nil)
                        .accessibilityLabel("Add a picture or file")
                        .accessibilityIdentifier("review.attach")
                    }
                    noteField
                    if let accessory { accessory }
                }
            }.padding(.leading, attachments == nil ? 12 : 2).padding(.trailing, 4).padding(.vertical, 1)
                .background(PanelsTheme.Web.composer, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(focused ? PanelsTheme.Web.accent.opacity(0.6) : Color.clear, lineWidth: 1))
            ReviewButtonRow {
                if let onSkip {
                    Button { textEntry?.endEditing(); onSkip() } label: { Text("Skip") }
                        .buttonStyle(ReviewButtonStyle()).disabled(!skipEnabled || busyAction != nil)
                        .accessibilityIdentifier("review.skip")
                }
                if showsSendBack {
                    Button {
                        if note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { textEntry?.requestFocus(inputID) }
                        else { textEntry?.endEditing(); onSendBack() }
                    } label: { actionLabel("Return", active: busyAction == "back") }
                        .buttonStyle(ReviewButtonStyle())
                        .disabled(!ready || !canReturn || !noteEnabled || busyAction != nil)
                        .accessibilityIdentifier("review.sendBack")
                }
                Button { textEntry?.endEditing(); onApprove() } label: { actionLabel("Approve", active: busyAction == "approve") }
                    .buttonStyle(ReviewButtonStyle(primary: true))
                    .disabled(!ready || !noteEnabled || busyAction != nil).accessibilityIdentifier("review.approve")
            }
            if showsSendBack && !canReturn {
                Text("No existing Worker conversation to return it to.")
                    .panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.tertiary).frame(maxWidth: .infinity, alignment: .leading)
            }
            if let error {
                Text(error).panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.error).frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("review.decisionError")
            }
        }
        .foregroundStyle(PanelsTheme.Web.text)
        .modifier(NoteAttachmentPickers(isPresented: $attachOpen, model: attachments))
        .onChange(of: noteEnabled) { _, enabled in if !enabled { textEntry?.endEditing() } }
        .onChange(of: focusRequest) { _, _ in textEntry?.requestFocus(inputID) }
        .onChange(of: blurRequest) { _, _ in textEntry?.endEditing() }
    }
    private var noteField: some View {
        TextField("Add a note for the worker…", text: $note, axis: .vertical)
            .lineLimit(1...5).panelsScaledFont(size: 15)
            .foregroundStyle(PanelsTheme.Web.strong)
            .frame(minHeight: 32).padding(.vertical, 5)
            .disabled(!noteEnabled || busyAction != nil)
            .accessibilityIdentifier("review.revision")
            .modifier(NotePaste(model: attachments))
            .screenTextInput(inputID)
    }
    private func actionLabel(_ title: String, active: Bool) -> some View {
        Text(title).opacity(active ? 0 : 1).overlay { if active { RingSpinner(size: .compact, color: PanelsTheme.Web.text) } }
    }
}

/// The shared Attach sheet and pickers, where the note takes attachments.
private struct NoteAttachmentPickers: ViewModifier {
    @Binding var isPresented: Bool
    let model: ConversationModel?
    @ViewBuilder func body(content: Content) -> some View {
        if let model { content.attachmentPickers(isPresented: $isPresented, model: model) } else { content }
    }
}

/// Pasted pictures join the note, as they join a message.
private struct NotePaste: ViewModifier {
    let model: ConversationModel?
    @ViewBuilder func body(content: Content) -> some View {
        if let model { content.modifier(ImagePaste(model: model)) } else { content }
    }
}
