import Foundation
import Observation

extension APIClient {
    func reviewAsk(_ conversationID: String) async throws -> ReviewAskState {
        try await get("/api/conversation/conversations/\(conversationID)")
    }
    /// The last read of this conversation saved on the phone, with when it was saved.
    @MainActor func savedReviewAsk(_ conversationID: String) -> (value: ReviewAskState, saved: Date)? {
        cachedRead("/api/conversation/conversations/\(conversationID)")
    }
}

/// One ask card. The answer is saved in the outbox and the card leaves at once; an ask
/// answered elsewhere just leaves.
@MainActor @Observable final class ReviewAskDecision {
    private(set) var state: ReviewAskState?
    /// Read live, or from the phone's saved copy while the server cannot be reached.
    private(set) var loaded = false
    /// A live read found nothing waiting: the ask was answered elsewhere.
    private(set) var gone = false
    var error: String?
    /// Chosen labels per question.
    var selected: [String: [String]] = [:]
    /// The answer in the owner's own words, in the shared composer. It answers the first
    /// question that takes words, and its draft is kept per conversation.
    let composer = ConversationModel()
    private var loading = false

    var permission: ReviewAskState.Permission? { state?.pendingPermissionAsk }
    var userInput: ReviewAskState.UserInput? { state?.pendingUserInput }
    /// The executor still stops on this ask, as the web's executorAnswerAvailable reads it.
    /// An ask taken from the view counts only while that view is recent.
    var available: Bool {
        state?.isRunning == true && state?.waiting == true && windowFreshAt.map { Self.windowAskCurrent(viewFreshAt: $0, now: .now) } ?? true
    }
    /// The view's ask stands in for a read only while the view's last fresh snapshot is recent.
    static func windowAskCurrent(viewFreshAt: Date?, now: Date) -> Bool {
        viewFreshAt.map { now.timeIntervalSince($0) <= DisplayCacheConfiguration.pendingAskMaximumAge } ?? false
    }
    /// When the view behind the shown ask was fresh; nil when the ask came from a read.
    private var windowFreshAt: Date?

    /// The approving and declining options of a permission ask, once each.
    var approveOption: ReviewAskState.PermissionOption? { option(prefix: "allow") }
    var declineOption: ReviewAskState.PermissionOption? { option(prefix: "reject") }
    private func option(prefix: String) -> ReviewAskState.PermissionOption? {
        let options = permission?.options.filter { $0.optionKind.hasPrefix(prefix) } ?? []
        return options.first { $0.optionKind == prefix + "_once" } ?? options.first
    }

    /// The question the composer's words answer.
    var wordsQuestion: ReviewAskState.Question? { userInput?.questions.first { $0.takesWords } }

    /// `viewFreshAt` is when the view that carries `item` was last fresh from the server.
    func load(item: ReviewItem, api: APIClient, viewFreshAt: Date?, now: Date = .now) async {
        guard let id = item.conversationId, !loading else { return }
        dropStaleWindowAsk(now: now)
        if let owner = item.owner { composer.restore(owner: owner, api: api, draftID: "review-ask:\(id)", recoverConversationID: false) }
        if state == nil, let saved = api.savedReviewAsk(id) { accept(saved.value, knownAt: saved.saved) }
        // The view's ask stands in only for a read that predates it: with no read, or a read
        // made before the ask was raised. A later read already knows whether it still waits.
        if let windowAsk = item.pendingAsk, windowAsk.askIdentity != state?.askIdentity,
           Self.windowAskCurrent(viewFreshAt: viewFreshAt, now: now) {
            // An ask with no raised time is never newer than a read.
            let raised = item.waitingSince == ReviewItem.unknownAskTime ? .distantPast : Date(timeIntervalSince1970: item.waitingSince)
            if stateKnownAt.map({ $0 < raised }) ?? true { accept(windowAsk, knownAt: raised); windowFreshAt = viewFreshAt }
        }
        loading = true; defer { loading = false }
        do {
            let read = try await api.reviewAsk(id)
            guard !Task.isCancelled else { return }
            accept(read, knownAt: .now)
            error = nil
            if !read.waiting { gone = true }
        } catch {
            // Offline the saved copy stays; the answer is saved and sent when the server answers.
            guard !Task.isCancelled, !(error is URLError) else { return }
            if state == nil { self.error = error.localizedDescription }
        }
    }
    /// When the shown ask was known to wait: a read's time, or the view's ask's raised time.
    private var stateKnownAt: Date?
    /// An ask taken from a view that has since grown old waits for the connection.
    @discardableResult private func dropStaleWindowAsk(now: Date) -> Bool {
        guard let windowFreshAt, !Self.windowAskCurrent(viewFreshAt: windowFreshAt, now: now) else { return false }
        state = nil; stateKnownAt = nil; self.windowFreshAt = nil; loaded = false
        return true
    }
    private func accept(_ read: ReviewAskState, knownAt: Date) {
        // A new question starts with nothing chosen.
        if read.pendingUserInput?.requestId != state?.pendingUserInput?.requestId { selected = [:] }
        state = read
        stateKnownAt = knownAt
        windowFreshAt = nil
        loaded = true
    }

    func toggle(_ label: String, in question: ReviewAskState.Question) {
        let current = selected[question.questionId] ?? []
        if question.multiSelect {
            selected[question.questionId] = current.contains(label) ? current.filter { $0 != label } : current + [label]
        } else {
            selected[question.questionId] = [label]
            // One answer: a choice replaces the words, as on the web.
            if question.questionId == wordsQuestion?.questionId { composer.draft = "" }
        }
    }
    /// Words typed for a one-answer question replace its choice.
    func wordsChanged() {
        guard let question = wordsQuestion, !question.multiSelect,
              !composer.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        selected[question.questionId] = []
    }
    /// What one question answers, as the web's userInputAnswerFor gives it.
    func answers(for question: ReviewAskState.Question) -> [String] {
        let chosen = selected[question.questionId] ?? []
        let own = question.questionId == wordsQuestion?.questionId ? composer.draft.trimmingCharacters(in: .whitespacesAndNewlines) : ""
        if own.isEmpty { return chosen }
        return question.multiSelect ? chosen + [own] : [own]
    }
    /// Every question has an answer.
    var complete: Bool {
        guard let questions = userInput?.questions, !questions.isEmpty else { return false }
        return questions.allSatisfy { !answers(for: $0).isEmpty }
    }

    /// Saves the answer to a question. Returns false when it cannot be saved; the card stays.
    func sendAnswer(item: ReviewItem, api: APIClient, now: Date = .now) -> Bool {
        // A tap after the view's ask grew old puts the card back to waiting.
        guard !dropStaleWindowAsk(now: now), available, complete, let input = userInput, let id = item.conversationId, let owner = item.owner else { return false }
        let questions = input.questions.map { question in
            AskAnswer.Question(id: question.questionId, prompt: question.question, answers: answers(for: question), labels: answers(for: question))
        }
        guard enqueue(AskAnswer(conversationID: id, request: .userInput(requestID: input.requestId), questions: questions), owner: owner, api: api) else { return false }
        composer.draft = ""
        return true
    }
    /// Saves a permission answer with one of the ask's own options.
    func answerPermission(_ option: ReviewAskState.PermissionOption, item: ReviewItem, api: APIClient, now: Date = .now) -> Bool {
        guard !dropStaleWindowAsk(now: now), available, let ask = permission, let id = item.conversationId, let owner = item.owner else { return false }
        let question = AskAnswer.Question(id: ask.askId, prompt: ask.title, answers: [option.optionId], labels: [option.label])
        return enqueue(AskAnswer(conversationID: id, request: .permission(askID: ask.askId, optionID: option.optionId), questions: [question]), owner: owner, api: api)
    }
    private func enqueue(_ answer: AskAnswer, owner: ConversationOwner, api: APIClient) -> Bool {
        do { try Outbox.shared.enqueueAskAnswer(answer, owner: owner, api: api); error = nil; return true }
        catch { self.error = "Couldn’t save this answer: \(error.localizedDescription)"; return false }
    }
}
