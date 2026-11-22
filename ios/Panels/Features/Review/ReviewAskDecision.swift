import Foundation
import Observation

/// The ask an agent's running turn waits on, read from its conversation as the web's
/// ReviewAskCard reads it (`GET /api/conversation/conversations/{id}`).
struct ReviewAskState: Decodable, Equatable, Sendable {
    struct PermissionOption: Decodable, Equatable, Sendable { let optionId: String; let label: String; let optionKind: String }
    struct Permission: Decodable, Equatable, Sendable {
        let askId: String
        let title: String
        let detail: String?
        let options: [PermissionOption]
    }
    struct Option: Decodable, Equatable, Sendable { let label: String; let description: String? }
    struct Question: Decodable, Equatable, Sendable {
        let questionId: String
        let header: String?
        let question: String
        let options: [Option]
        let multiSelect: Bool
        let allowOther: Bool
        /// The owner may answer in words.
        var takesWords: Bool { allowOther || options.isEmpty }
    }
    struct UserInput: Decodable, Equatable, Sendable { let requestId: String; let questions: [Question] }
    let isRunning: Bool
    let pendingPermissionAsk: Permission?
    let pendingUserInput: UserInput?
    var waiting: Bool { pendingPermissionAsk != nil || pendingUserInput != nil }
}

extension APIClient {
    func reviewAsk(_ conversationID: String) async throws -> ReviewAskState {
        try await get("/api/conversation/conversations/\(conversationID)")
    }
    /// The last read of this conversation saved on the phone.
    @MainActor func savedReviewAsk(_ conversationID: String) -> ReviewAskState? {
        cached("/api/conversation/conversations/\(conversationID)")
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
    /// Words per question.
    var words: [String: String] = [:]
    private var loading = false

    var permission: ReviewAskState.Permission? { state?.pendingPermissionAsk }
    var userInput: ReviewAskState.UserInput? { state?.pendingUserInput }
    /// The executor still stops on this ask, as the web's executorAnswerAvailable reads it.
    var available: Bool { state?.isRunning == true && state?.waiting == true }

    /// The approving and declining options of a permission ask, once each.
    var approveOption: ReviewAskState.PermissionOption? { option(prefix: "allow") }
    var declineOption: ReviewAskState.PermissionOption? { option(prefix: "reject") }
    private func option(prefix: String) -> ReviewAskState.PermissionOption? {
        let options = permission?.options.filter { $0.optionKind.hasPrefix(prefix) } ?? []
        return options.first { $0.optionKind == prefix + "_once" } ?? options.first
    }

    func load(item: ReviewItem, api: APIClient) async {
        guard let id = item.conversationId, !loading else { return }
        if state == nil, let saved = api.savedReviewAsk(id) { accept(saved) }
        loading = true; defer { loading = false }
        do {
            let read = try await api.reviewAsk(id)
            guard !Task.isCancelled else { return }
            accept(read)
            error = nil
            if !read.waiting { gone = true }
        } catch {
            // Offline the saved copy stays; the answer is saved and sent when the server answers.
            guard !Task.isCancelled, !(error is URLError) else { return }
            if state == nil { self.error = error.localizedDescription }
        }
    }
    private func accept(_ read: ReviewAskState) {
        // A new question starts with an empty draft.
        if read.pendingUserInput?.requestId != state?.pendingUserInput?.requestId { selected = [:]; words = [:] }
        state = read
        loaded = true
    }

    func toggle(_ label: String, in question: ReviewAskState.Question) {
        let current = selected[question.questionId] ?? []
        if question.multiSelect {
            selected[question.questionId] = current.contains(label) ? current.filter { $0 != label } : current + [label]
        } else {
            selected[question.questionId] = [label]
            words[question.questionId] = ""
        }
    }
    func setWords(_ text: String, for question: ReviewAskState.Question) {
        words[question.questionId] = text
        if !question.multiSelect, !text.isEmpty { selected[question.questionId] = [] }
    }
    /// What one question answers, as the web's userInputAnswerFor gives it.
    func answers(for question: ReviewAskState.Question) -> [String] {
        let chosen = selected[question.questionId] ?? []
        let own = (words[question.questionId] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if own.isEmpty { return chosen }
        return question.multiSelect ? chosen + [own] : [own]
    }
    /// Every question has an answer.
    var complete: Bool {
        guard let questions = userInput?.questions, !questions.isEmpty else { return false }
        return questions.allSatisfy { !answers(for: $0).isEmpty }
    }

    /// Saves the answer to a question. Returns false when it cannot be saved; the card stays.
    func sendAnswer(item: ReviewItem, api: APIClient) -> Bool {
        guard available, complete, let input = userInput, let id = item.conversationId, let owner = item.owner else { return false }
        let questions = input.questions.map { question in
            AskAnswer.Question(id: question.questionId, prompt: question.question, answers: answers(for: question), labels: answers(for: question))
        }
        return enqueue(AskAnswer(conversationID: id, request: .userInput(requestID: input.requestId), questions: questions), owner: owner, api: api)
    }
    /// Saves a permission answer with one of the ask's own options.
    func answerPermission(_ option: ReviewAskState.PermissionOption, item: ReviewItem, api: APIClient) -> Bool {
        guard available, let ask = permission, let id = item.conversationId, let owner = item.owner else { return false }
        let question = AskAnswer.Question(id: ask.askId, prompt: ask.title, answers: [option.optionId], labels: [option.label])
        return enqueue(AskAnswer(conversationID: id, request: .permission(askID: ask.askId, optionID: option.optionId), questions: [question]), owner: owner, api: api)
    }
    private func enqueue(_ answer: AskAnswer, owner: ConversationOwner, api: APIClient) -> Bool {
        do { try Outbox.shared.enqueueAskAnswer(answer, owner: owner, api: api); error = nil; return true }
        catch { self.error = "Couldn’t save this answer: \(error.localizedDescription)"; return false }
    }
}
