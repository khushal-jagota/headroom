import SwiftUI

/// An ask card: a worker's question, answered with Send answer, or a permission ask,
/// answered with Decline or Approve. The answer is saved in the outbox and the card
/// leaves at once; an ask answered elsewhere just leaves.
struct ReviewAskCard: View {
    let item: ReviewItem
    let isActive: Bool
    @Bindable var decision: ReviewAskDecision
    let motion: ReviewCardMotion
    let onSkip: (CGFloat?) -> Void
    let onResolve: (_ afterExit: (() -> Void)?) -> Void
    @Environment(AppStore.self) private var store
    @State private var finished = false
    private var from: String {
        switch item.ownerKind { case "ticket": "the Worker"; case "manager": "the Manager"; default: "the Chief" }
    }

    var body: some View {
        ReviewCardFrame(title: item.title, recap: item.recap, motion: motion, locked: finished, onSkip: onSkip,
                        refresh: { await decision.load(item: item, api: store.client, viewFreshAt: store.viewFreshAt) }) {
            VStack(alignment: .leading, spacing: 14) {
                if let permission = decision.permission {
                    askCard(label: "Permission ask") {
                        Text(permission.title).textSelection(.enabled).panelsScaledFont(size: 15, weight: .medium).foregroundStyle(PanelsTheme.Web.strong)
                            .fixedSize(horizontal: false, vertical: true)
                        if let detail = permission.detail, !detail.isEmpty {
                            ScrollView(.horizontal) {
                                Text(detail).font(.system(size: 12.5, design: .monospaced)).textSelection(.enabled)
                                    .padding(12).fixedSize(horizontal: true, vertical: false)
                            }
                            .background(PanelsTheme.Web.code, in: RoundedRectangle(cornerRadius: 8))
                            .accessibilityIdentifier("review.ask.detail")
                        }
                    }
                } else if let input = decision.userInput {
                    askCard(label: "Question") {
                        ForEach(input.questions, id: \.questionId) { question in
                            questionView(question)
                        }
                    }
                } else if decision.error == nil && !decision.loaded {
                    ScreenLoadingView(title: "question")
                }
                if let error = decision.error {
                    Text(error).textSelection(.enabled).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Web.error)
                    if !decision.loaded { Button("Try again") { Task { await decision.load(item: item, api: store.client, viewFreshAt: store.viewFreshAt) } }.panelsScaledFont(size: 13) }
                }
            }
        } dock: {
            ReviewDock {
                // Words go in the app's shared composer; its send answers, like Send answer.
                if let question = decision.wordsQuestion, let owner = item.owner {
                    ConversationComposer(model: decision.composer, owner: owner, reviewReply: true,
                                         placeholder: question.options.isEmpty ? "Type your answer…" : "Or answer in your own words…",
                                         sendReply: { sendAnswer() }, attaches: false)
                        .disabled(finished || !decision.available)
                }
                ReviewButtonRow {
                    Button("Skip") { Haptics.selection(); onSkip(nil) }
                        .buttonStyle(ReviewButtonStyle()).disabled(finished).accessibilityIdentifier("review.skip")
                    if decision.permission != nil {
                        Button("Decline") { answer(decision.declineOption) }
                            .buttonStyle(ReviewButtonStyle()).disabled(finished || !decision.available || decision.declineOption == nil)
                            .accessibilityIdentifier("review.ask.decline")
                        Button("Approve") { answer(decision.approveOption) }
                            .buttonStyle(ReviewButtonStyle(primary: true)).disabled(finished || !decision.available || decision.approveOption == nil)
                            .accessibilityIdentifier("review.ask.approve")
                    } else {
                        Button("Send answer") { sendAnswer() }
                            .buttonStyle(ReviewButtonStyle(primary: true)).disabled(finished || !decision.available || !decision.complete)
                            .accessibilityIdentifier("review.ask.send")
                    }
                }
            }
        }
        .task(id: "\(isActive):\(store.connectionSession)") {
            guard isActive else { return }
            await decision.load(item: item, api: store.client, viewFreshAt: store.viewFreshAt)
            if decision.gone { finish() }
        }
        .onChange(of: decision.gone) { _, gone in if gone, isActive { finish() } }
        .onChange(of: decision.composer.draft) { _, _ in decision.wordsChanged() }
    }

    private func askCard<Content: View>(label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                Text(label).textSelection(.enabled).fontWeight(.semibold).foregroundStyle(PanelsTheme.Web.secondary)
                Text("· From \(from)").textSelection(.enabled).foregroundStyle(PanelsTheme.Web.tertiary)
            }.panelsScaledFont(size: 13, weight: .medium)
            content()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .modifier(DecisionForegroundFade())
        .background(PanelsTheme.Web.cardFill, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
    }

    @ViewBuilder private func questionView(_ question: ReviewAskState.Question) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let header = question.header, !header.isEmpty {
                Text(header).textSelection(.enabled).panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.tertiary)
            }
            Text(question.question).textSelection(.enabled).panelsScaledFont(size: 15, weight: .medium).foregroundStyle(PanelsTheme.Web.strong)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 4)
            ForEach(question.options, id: \.label) { option in
                let chosen = (decision.selected[question.questionId] ?? []).contains(option.label)
                Button { Haptics.selection(); decision.toggle(option.label, in: question) } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: question.multiSelect ? (chosen ? "checkmark.square.fill" : "square") : (chosen ? "largecircle.fill.circle" : "circle"))
                            .foregroundStyle(chosen ? PanelsTheme.accent : PanelsTheme.Web.tertiary)
                            .padding(.top, 1)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(option.label).panelsScaledFont(size: 13.5, weight: .semibold).foregroundStyle(PanelsTheme.Web.strong)
                            if let description = option.description, !description.isEmpty {
                                Text(description).panelsScaledFont(size: 12.5).foregroundStyle(PanelsTheme.Web.secondary)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                    .multilineTextAlignment(.leading)
                    .padding(.horizontal, 13).padding(.vertical, 11)
                    .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                    .background(chosen ? PanelsTheme.accentSurface : PanelsTheme.Web.surface2, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(chosen ? PanelsTheme.accent.opacity(0.48) : Color.clear, lineWidth: 1))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(finished || !decision.available)
                .accessibilityAddTraits(chosen ? .isSelected : [])
                .accessibilityIdentifier("review.ask.option.\(option.label)")
                .systemCopyText([option.label, option.description].compactMap { $0 }.joined(separator: "\n"))
            }
        }
    }

    @discardableResult private func sendAnswer() -> Bool {
        Haptics.light()
        guard decision.sendAnswer(item: item, api: store.client) else { ReviewStyle.error(); return false }
        Haptics.success(); finish()
        return true
    }
    private func answer(_ option: ReviewAskState.PermissionOption?) {
        guard let option else { return }
        Haptics.light()
        if decision.answerPermission(option, item: item, api: store.client) { Haptics.success(); finish() } else { ReviewStyle.error() }
    }
    private func finish() {
        guard !finished else { return }
        finished = true
        onResolve(nil)
    }
}
