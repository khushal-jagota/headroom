import Foundation
import Observation

@MainActor @Observable final class GoalTextDraft {
    var text: String
    let revision: Int
    init(text: String, revision: Int) { self.text = text; self.revision = revision }
}


struct GoalTextSubmission: Sendable {
    let key: String
    let text: String
    let revision: Int
}

/// A route owns unfinished edits and reads shared accepted Goal or holder-page data.
/// Goal writes stay outside the message outbox: no automatic replay.
@MainActor @Observable final class PageSession {
    static func session(id: String, api: APIClient, initial: GoalRecord? = nil, resources: ResourceOwner? = nil) -> PageSession {
        PageSession(id: id, api: api, goal: initial, resources: resources)
    }
    static func holder(_ holder: GoalHolder, api: APIClient, resources: ResourceOwner? = nil) -> PageSession {
        PageSession(id: holder.id ?? "chief", api: api, holder: holder, resources: resources)
    }
    let id: String
    let holder: GoalHolder?
    var holderPage: HolderPage? { holderHandle?.value }
    var page: GoalJSON? { holder == nil ? goal?.page : holderPage?.page }
    var revision: Int { holder == nil ? goal?.revision ?? 0 : holderPage?.pageRevision ?? 0 }
    var statusDate: String? { goal?.statusDate ?? readings?.statusDate }
    var hasContent: Bool { holder == nil ? goal != nil : holderPage != nil }
    var goal: GoalRecord? { goalHandle?.value }
    var readings: GoalReadings? { readingsHandle.value }
    /// Retain the last accepted projection while its document advances or a refresh fails.
    var readingsAreStale: Bool {
        guard let readings else { return false }
        return readings.revision < revision || readingsHandle.isStale || readingsHandle.error != nil
    }
    private(set) var busy = false
    var readError: String? { goalHandle?.error ?? holderHandle?.error ?? readingsHandle.error }
    var saveError: String?
    private var pendingPatch: GoalJSON?
    private var preparePatch: (() async throws -> GoalJSON)?
    var hasPendingEdit: Bool { pendingPatch != nil || preparePatch != nil }
    var hasUnfinishedEdits: Bool {
        busy || hasPendingEdit || !textDrafts.isEmpty || manualEditors.values.contains { $0.draft != nil || $0.pending != nil || $0.busy }
    }
    private var api: APIClient
    let resources: ResourceOwner
    private let goalHandle: ResourceHandle<GoalRecord>?
    private let holderHandle: ResourceHandle<HolderPage>?
    private let readingsHandle: ResourceHandle<GoalReadings>
    private var mounted = 0
    var preferences: [String: GoalChartPreference] = [:]
    var textDrafts: [String: GoalTextDraft] = [:]
    private var textSubmission: GoalTextSubmission?
    private var manualEditors: [String: GoalManualEditor] = [:]
    init(id: String, api: APIClient, goal: GoalRecord? = nil, holder: GoalHolder? = nil, resources: ResourceOwner? = nil) {
        self.id = id; self.api = api; self.holder = holder
        let resources = resources ?? ResourceOwner(api: api); self.resources = resources
        goalHandle = holder == nil ? resources.handle(GoalResources.goal(id)) : nil
        holderHandle = holder.map { resources.handle(GoalResources.holder($0)) }
        readingsHandle = resources.handle(holder.map { GoalResources.holderReadings($0) } ?? GoalResources.readings(id))
        if let goal, goalHandle?.value == nil { goalHandle?.accept(goal) }
    }
    func acquire() {
        mounted += 1
        goalHandle?.acquire(); holderHandle?.acquire(); readingsHandle.acquire()
    }
    func release() {
        guard mounted > 0 else { return }; mounted -= 1
        goalHandle?.release(); holderHandle?.release(); readingsHandle.release()
    }
    func follow() async {
        acquire(); defer { release() }
        await refresh()
        do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
    }
    var baseURL: URL { api.baseURL }
    var nextRefreshAt: Double? { [goal?.nextRefreshAt, readings?.nextRefreshAt].compactMap { $0 }.min() }
    func preference(_ id: String) -> GoalChartPreference { preferences[id] ?? .init() }
    func remember(_ id: String, _ preference: GoalChartPreference) { preferences[id] = preference }
    func manual(_ block: GoalJSON) -> GoalManualEditor {
        let key = block["measure_id"].text
        if let editor = manualEditors[key] { return editor }
        let editor = GoalManualEditor(measureID: key, cumulative: block["manual"]["model"].text == "cumulative_target", api: api, onWrite: { [resources] in
            resources.invalidate { $0.hasPrefix("/api/goals/") && $0.contains("/readings") || $0.hasSuffix("/page/readings") || $0.hasPrefix("/api/measures/") }
        })
        manualEditors[key] = editor; return editor
    }
    func refresh() async {
        async let detail: () = refreshDocument()
        async let observations: () = readingsHandle.refresh()
        _ = await (detail, observations)
    }
    private func refreshDocument() async {
        if let goalHandle { await goalHandle.refresh() }
        else { await holderHandle?.refresh() }
    }
    func save(_ fields: [String: GoalJSON], revision: Int? = nil, textSubmission: GoalTextSubmission? = nil) async {
        guard holder == nil, !busy, !hasPendingEdit else { return }
        self.textSubmission = textSubmission
        preparePatch = { [self] in
            let accepted = try await api.goal(id)
            guard accepted.id == id else { throw APIError(status: 0, message: "The accepted goal does not match this edit.") }
            var patch = fields; patch["expected_revision"] = .number(Double(revision ?? accepted.revision))
            return .object(patch)
        }
        await retry()
    }
    func updateBlock(_ id: String, revision: Int? = nil, textSubmission: GoalTextSubmission? = nil, update: @escaping (GoalJSON) -> GoalJSON) async {
        guard !busy, !hasPendingEdit else { return }
        self.textSubmission = textSubmission
        preparePatch = { [self] in
            let page: GoalJSON
            let acceptedRevision: Int
            if let holder {
                let accepted = try await api.holderPage(holder)
                guard let document = accepted.page else { throw APIError(status: 0, message: "This page no longer exists. Refresh before editing.") }
                page = document; acceptedRevision = accepted.pageRevision
            } else {
                let accepted = try await api.goal(self.id)
                guard accepted.id == self.id else { throw APIError(status: 0, message: "The accepted goal does not match this edit.") }
                page = accepted.page; acceptedRevision = accepted.revision
            }
            var found = false
            let blocks = GoalPresentation.updateBlock(page["blocks"].array, id: id) { found = true; return update($0) }
            guard found else { throw APIError(status: 0, message: "This block no longer exists. Refresh the page before another edit.") }
            return .object(["page": page.setting("blocks", .array(blocks)), "expected_revision": .number(Double(revision ?? acceptedRevision))])
        }
        await retry()
    }
    private func submitPending() async throws {
        if pendingPatch == nil, let preparePatch { pendingPatch = try await preparePatch(); self.preparePatch = nil }
        guard let patch = pendingPatch else { return }
        let acceptedRevision: Int
        if let holder {
            let next = try await api.saveHolderPage(holder, patch: patch)
            acceptedRevision = next.pageRevision
            if next.pageRevision >= revision { holderHandle?.accept(next) }
        } else {
            let next = try await api.saveGoal(id, patch: patch)
            guard next.id == id else { throw APIError(status: 0, message: "The accepted goal does not match this edit.") }
            acceptedRevision = next.revision
            if next.revision >= (goal?.revision ?? -1) { goalHandle?.accept(next) }
        }
        if let submission = textSubmission, let draft = textDrafts[submission.key], draft.revision == submission.revision {
            if draft.text == submission.text { textDrafts[submission.key] = nil }
            else { textDrafts[submission.key] = GoalTextDraft(text: draft.text, revision: acceptedRevision) }
        }
        textSubmission = nil
        pendingPatch = nil; saveError = nil
        resources.invalidate([GoalResources.list.key, goalHandle?.key, holderHandle?.key, readingsHandle.key].compactMap { $0 })
        // Read failure after acceptance must never become Retry save.
        await refresh()
    }
    func retry() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do { try await submitPending() } catch { saveError = error.localizedDescription }
    }
    func discard() async { guard !busy else { return }; pendingPatch = nil; preparePatch = nil; saveError = nil; if let submission = textSubmission { textDrafts[submission.key] = nil }; textSubmission = nil; await refresh() }
}

/// A mounted route retains dirty edit owners after a Goal leaves its visible membership.
/// Accepted responses and active read demand still belong to the resource owner.
@MainActor @Observable final class PageSessionCollection {
    private var sessions: [String: PageSession] = [:]
    private var selected: Set<String> = []
    private func key(_ id: String, server: URL) -> String { LocalStore.server(server) + ":" + id }
    func session(_ id: String, server: URL) -> PageSession? { sessions[key(id, server: server)] }
    var unfinishedKeys: [String] { sessions.keys.filter { sessions[$0]?.hasUnfinishedEdits == true }.sorted() }
    @discardableResult func update(_ goals: [GoalRecord], api: APIClient, resources: ResourceOwner) -> [PageSession] {
        selected = Set(goals.map { key($0.id, server: api.baseURL) })
        for goal in goals {
            let identity = key(goal.id, server: api.baseURL)
            if sessions[identity] == nil { sessions[identity] = PageSession.session(id: goal.id, api: api, initial: goal, resources: resources) }
        }
        prune()
        return goals.compactMap { session($0.id, server: api.baseURL) }
    }
    func prune() { sessions = sessions.filter { selected.contains($0.key) || $0.value.hasUnfinishedEdits } }
}

/// The route owns this object above replaceable document views. Clean previous
/// selections disappear immediately; unfinished edits stay until Save or discard.
@MainActor @Observable final class PageSessionRoute {
    let goals = PageSessionCollection()
    private var sessions: [String: PageSession] = [:]
    private var selected: String?
    func goal(_ id: String, api: APIClient, resources: ResourceOwner) -> PageSession {
        resolve("goal:" + id, api: api) { PageSession.session(id: id, api: api, resources: resources) }
    }
    func holder(_ holder: GoalHolder, api: APIClient, resources: ResourceOwner) -> PageSession {
        resolve("holder:" + holder.pagePath, api: api) { PageSession.holder(holder, api: api, resources: resources) }
    }
    private func resolve(_ id: String, api: APIClient, create: () -> PageSession) -> PageSession {
        let key = LocalStore.server(api.baseURL) + ":" + id
        selected = key
        let session = sessions[key] ?? create()
        sessions[key] = session
        prune()
        return session
    }
    var unfinishedKeys: [String] { sessions.keys.filter { sessions[$0]?.hasUnfinishedEdits == true }.sorted() }
    var retainedCount: Int { sessions.count }
    func prune() { sessions = sessions.filter { $0.key == selected || $0.value.hasUnfinishedEdits } }
}

struct GoalChartPreference: Equatable { var range = "month"; var expanded = false }

@MainActor @Observable final class GoalManualEditor {
    let measureID: String
    let cumulative: Bool
    private let api: APIClient
    private let onWrite: @MainActor () -> Void
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
        draftBasis = state; draft = state?.value(cumulative: cumulative).map(GoalChartPresentation.plainNumber) ?? ""
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
    init(measureID: String, cumulative: Bool, api: APIClient, onWrite: @escaping @MainActor () -> Void = {}) { self.measureID = measureID; self.cumulative = cumulative; self.api = api; self.onWrite = onWrite }
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
        onWrite()
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
