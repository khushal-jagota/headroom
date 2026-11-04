import Foundation
import Observation

@MainActor @Observable final class GoalTextDraft {
    var text: String
    let revision: Int
    init(text: String, revision: Int) { self.text = text; self.revision = revision }
}


/// Hosts share accepted data and unfinished commands for this server and goal.
/// Goal writes stay outside the message outbox: no automatic replay.
@MainActor @Observable final class GoalSession {
    private static var sessions: [String: GoalSession] = [:]
    static func session(id: String, api: APIClient, initial: GoalRecord? = nil) -> GoalSession {
        let key = api.baseURL.absoluteString + ":" + id
        if let current = sessions[key] {
            current.api = api
            if let initial, initial.revision > (current.goal?.revision ?? -1) { current.goal = initial }
            return current
        }
        let created = GoalSession(id: id, api: api, goal: initial)
        sessions[key] = created; return created
    }
    let id: String
    private(set) var goal: GoalRecord?
    private(set) var readings: GoalReadings?
    private(set) var busy = false
    var readError: String?
    var saveError: String?
    private var pendingPatch: GoalJSON?
    private var preparePatch: (() async throws -> GoalJSON)?
    var hasPendingEdit: Bool { pendingPatch != nil || preparePatch != nil }
    private var api: APIClient
    private var reading = false
    var preferences: [String: GoalChartPreference] = [:]
    var textDrafts: [String: GoalTextDraft] = [:]
    var submittedTextKey: String?
    private var manualEditors: [String: GoalManualEditor] = [:]
    init(id: String, api: APIClient, goal: GoalRecord? = nil) { self.id = id; self.api = api; self.goal = goal }
    var baseURL: URL { api.baseURL }
    func preference(_ id: String) -> GoalChartPreference { preferences[id] ?? .init() }
    func remember(_ id: String, _ preference: GoalChartPreference) { preferences[id] = preference }
    func manual(_ block: GoalJSON) -> GoalManualEditor {
        let key = block["measure_id"].text
        if let editor = manualEditors[key] { return editor }
        let editor = GoalManualEditor(measureID: key, cumulative: block["manual"]["model"].text == "cumulative_target", api: api)
        manualEditors[key] = editor; return editor
    }
    func refresh() async {
        guard !reading else { return }
        reading = true; defer { reading = false }
        do {
            async let detail = api.goal(id)
            async let observations = api.goalReadings(id)
            let next = try await detail
            if next.id == id, next.revision >= (goal?.revision ?? -1) { goal = next }
            let nextReadings = try await observations
            if nextReadings.goalId == id, nextReadings.revision >= (readings?.revision ?? -1), nextReadings.revision >= (goal?.revision ?? 0) { readings = nextReadings }
            readError = nil
        } catch { if !Task.isCancelled { readError = error.localizedDescription } }
    }
    func save(_ fields: [String: GoalJSON], revision: Int? = nil) async {
        guard !busy, !hasPendingEdit else { return }
        preparePatch = { [self] in
            let accepted = try await api.goal(id)
            guard accepted.id == id else { throw APIError(status: 0, message: "The accepted goal does not match this edit.") }
            var patch = fields; patch["expected_revision"] = .number(Double(revision ?? accepted.revision))
            return .object(patch)
        }
        await retry()
    }
    func updateBlock(_ id: String, revision: Int? = nil, update: @escaping (GoalJSON) -> GoalJSON) async {
        guard !busy, !hasPendingEdit else { return }
        preparePatch = { [self] in
            let accepted = try await api.goal(self.id)
            guard accepted.id == self.id else { throw APIError(status: 0, message: "The accepted goal does not match this edit.") }
            var found = false
            let blocks = GoalPresentation.updateBlock(accepted.page["blocks"].array, id: id) { found = true; return update($0) }
            guard found else { throw APIError(status: 0, message: "This block no longer exists. Refresh the goal before another edit.") }
            return .object(["page": accepted.page.setting("blocks", .array(blocks)), "expected_revision": .number(Double(revision ?? accepted.revision))])
        }
        await retry()
    }
    private func submitPending() async throws {
        if pendingPatch == nil, let preparePatch { pendingPatch = try await preparePatch(); self.preparePatch = nil }
        guard let patch = pendingPatch else { return }
        let next = try await api.saveGoal(id, patch: patch)
        if next.id == id, next.revision >= (goal?.revision ?? -1) { goal = next }
        pendingPatch = nil; saveError = nil
        // Read failure after acceptance must never become Retry save.
        await refresh()
    }
    func retry() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do { try await submitPending() } catch { saveError = error.localizedDescription }
    }
    func discard() async { guard !busy else { return }; pendingPatch = nil; preparePatch = nil; saveError = nil; if let submittedTextKey { textDrafts[submittedTextKey] = nil; self.submittedTextKey = nil }; await refresh() }
}

struct GoalChartPreference: Equatable { var range = "month"; var expanded = false }

@MainActor @Observable final class GoalManualEditor {
    let measureID: String
    let cumulative: Bool
    private let api: APIClient
    private(set) var state: GoalManualState?
    private(set) var busy = false
    private(set) var pending: GoalManualCommand?
    private(set) var conflicted = false
    private var preflightPrepared = false
    var error: String?
    var draft: String? = nil
    private var draftBasis: GoalManualState?
    func beginEditing() {
        guard !busy, pending == nil else { return }
        draftBasis = state; draft = state?.value(cumulative: cumulative).map { String($0) } ?? ""
    }
    func prepare() async -> Bool {
        guard !busy, pending == nil else { return false }
        guard let draft else { return true }
        guard let value = Double(draft.trimmingCharacters(in: .whitespacesAndNewlines)), value.isFinite else { error = "Enter a number."; return false }
        if value == state?.value(cumulative: cumulative) { self.draft = nil; draftBasis = nil; error = nil; return true }
        await write(value: value)
        return pending == nil && error == nil
    }
    private var selectedDate: String?
    private var readGeneration = 0
    private(set) var history: [String: GoalJSON] = [:]
    init(measureID: String, cumulative: Bool, api: APIClient) { self.measureID = measureID; self.cumulative = cumulative; self.api = api }
    func accept(_ next: GoalManualState, date: String? = nil) {
        guard next.measureId == measureID, date == nil || next.date == date,
              next.definitionRevision >= (state?.definitionRevision ?? -1), next.writeRevision >= (state?.writeRevision ?? -1) else { return }
        state = next
        if selectedDate == nil || cumulative { selectedDate = next.date }
        history[next.date] = .object(["measure_id": .string(measureID), "date": .string(next.date), "value": next.observation?["value"] ?? .null, "status": .string(next.observation == nil ? "missing_input" : "value")])
    }
    func refresh(date: String? = nil) async {
        guard !busy, pending == nil else { return }
        readGeneration += 1; let generation = readGeneration
        do {
            let requested = cumulative ? nil : date ?? selectedDate
            let next = try await api.manualState(measureID, date: requested)
            guard generation == readGeneration else { return }
            accept(next, date: requested)
            if let date { selectedDate = date }
            error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    func write(value: Double? = nil, delta: Double? = nil) async {
        guard !busy, pending == nil, let state = value != nil ? draftBasis ?? state : state else { return }
        busy = true; defer { busy = false }
        readGeneration += 1
        // Own the command before the cumulative current-date read suspends.
        pending = GoalManualCommand(requestId: UUID().uuidString, date: state.date, expectedDefinitionRevision: state.definitionRevision, basis: cumulative ? "latest" : "date", operation: delta == nil ? "replace" : "increment", value: value, delta: delta, expectedWriteRevision: delta == nil ? state.writeRevision : nil)
        do {
            preflightPrepared = !cumulative
            try await submit()
        } catch { await failed(error) }
    }
    private func submit() async throws {
        if !preflightPrepared, pending != nil {
            pending?.date = try await api.manualState(measureID).currentDate
            preflightPrepared = true
        }
        guard let pending else { return }
        let result = try await api.writeManual(measureID, command: pending)
        accept(result.state, date: result.receipt["date"].string)
        self.pending = nil; draft = nil; draftBasis = nil; conflicted = false; error = nil
    }
    private func failed(_ failure: Error) async {
        conflicted = (failure as? APIError)?.status == 409
        error = conflicted ? "This reading changed. Discard edit to use the accepted value, or edit again." : failure.localizedDescription
        if conflicted, let next = try? await api.manualState(measureID, date: cumulative ? nil : pending?.date) { accept(next) }
    }
    func retry() async {
        guard !busy, !conflicted else { return }; busy = true; defer { busy = false }
        do { try await submit() } catch { await failed(error) }
    }
    func discard() async { guard !busy else { return }; pending = nil; draft = nil; draftBasis = nil; conflicted = false; await refresh() }
}
