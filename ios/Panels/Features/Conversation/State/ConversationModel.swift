import Foundation
import Observation
import UniformTypeIdentifiers

@MainActor @Observable final class ConversationModel {
    @ObservationIgnored private let outgoingStore: LocalStore
    @ObservationIgnored private let imageWorkStarted: @Sendable () -> Void
    init(outgoingStore: LocalStore = .shared, imageWorkStarted: @escaping @Sendable () -> Void = {}) {
        self.outgoingStore = outgoingStore
        self.imageWorkStarted = imageWorkStarted
    }
    var machineLocale: String?
    var conversationID: String? { didSet { if oldValue != conversationID { invalidateReader() } } }
    private var composerRevision = 0
    private var composerConfigurationGeneration = 0
    private var clearedPhotoDrafts: [String: (revision: Int, draftKey: String)] = [:]
    var draft = "" { didSet { composerRevision += 1; saveDraft() } }
    private var persistenceAPI: APIClient?
    private var persistenceOwner: ConversationOwner?
    private var restoring = false
    private var customDraftID: String?
    private var recoversConversationIDFromOutbox = true
    private var draftKey: String { customDraftID ?? "conversation:" + (persistenceOwner?.path ?? "") }
    private func saveDraft() {
        guard !restoring, let api = persistenceAPI else { return }
        LocalStore.shared.saveDraft(draft, server: LocalStore.server(api.baseURL), id: draftKey)
    }
    func restore(owner: ConversationOwner, api: APIClient, draftID: String? = nil, savedWork: [PendingWork]? = nil, recoverConversationID: Bool = true) {
        recoversConversationIDFromOutbox = recoverConversationID
        guard persistenceAPI?.baseURL != api.baseURL || persistenceOwner != owner || customDraftID != draftID else { syncOutbox(savedWork: savedWork); return }
        composerConfigurationGeneration += 1
        preparingPhotos = [:]; preparingVideos = [:]; draftPreparationID = nil; preparingOutgoingID = nil; outgoingPreparationID = nil; preparedAttachments = []
        outgoing = []; retryAliases = [:]; supersededIDs = []; clearedPhotoDrafts = [:]
        persistenceAPI = api; persistenceOwner = owner; customDraftID = draftID; restoring = true
        defer { restoring = false }
        items = []; expandedBodies = [:]; status = nil; older = nil
        loadedConversationID = nil; loadedLens = nil; pageProjectionVersion = nil
        visibleRows = []; rowOrder = []; liveRow = nil; liveReference = nil; liveTurnKey = nil
        displayByteSizes = [:]; attachments = []; lastDisplaySave = .distantPast; hasNewerHistory = false
        draft = LocalStore.shared.draft(server: LocalStore.server(api.baseURL), id: draftKey) ?? ""
        if let data = try? LocalStore.shared.record(server: LocalStore.server(api.baseURL), kind: "attachments", id: draftKey)?.payload {
            attachments = (try? JSONDecoder().decode([ConversationSend.Piece].self, from: data)) ?? []
        }
        invalidateReader()
        if let id = conversationID,
           let data = try? LocalStore.shared.record(server: LocalStore.server(api.baseURL), kind: "cache", id: displayID(id))?.payload,
           let saved = try? JSONDecoder().decode(ConversationDisplaySnapshot.self, from: data),
           saved.schema == 1, saved.conversationID == id, saved.lens == lens.rawValue {
            status = saved.status
            items = saved.page.items; older = saved.page.older
            loadedLens = lens; loadedConversationID = id; pageProjectionVersion = saved.page.projectionVersion
            admitHistoryScope(saved.page.scope ?? .init(kind: "timeline", key: nil), keys: Set(items.map(\.key)))
            retainDisplay()
            rememberRows(items.map(\.id))
        }
        syncOutbox(savedWork: savedWork)
    }
    /// Re-read the owner's shared composer when another surface edited its draft.
    func reloadDraft() {
        guard let api = persistenceAPI else { return }
        restoring = true
        defer { restoring = false }
        let server = LocalStore.server(api.baseURL)
        // Assign only what changed: each assignment counts as a composer edit, and a
        // pending photo rejection restores its draft only while no edit happened.
        let storedDraft = LocalStore.shared.draft(server: server, id: draftKey) ?? ""
        let storedAttachments = (try? LocalStore.shared.record(server: server, kind: "attachments", id: draftKey))
            .flatMap { try? JSONDecoder().decode([ConversationSend.Piece].self, from: $0.payload) } ?? []
        if draft != storedDraft { draft = storedDraft }
        if attachments != storedAttachments { attachments = storedAttachments }
    }

    /// Change the displayed destination without changing the owner's unsent work
    /// or the destinations of messages already recorded in the outbox.
    func selectConversation(_ id: String?) {
        recoversConversationIDFromOutbox = false
        guard conversationID != id else { return }
        saveDisplay(force: true)
        conversationID = id
        items = []; expandedBodies = [:]; status = nil; older = nil
        loadedConversationID = nil; loadedLens = nil; pageProjectionVersion = nil
        visibleRows = []; rowOrder = []; liveRow = nil; liveReference = nil; liveTurnKey = nil
        liveConversationID = nil; displayByteSizes = [:]; lastDisplaySave = .distantPast
        replaceHistoryOnSnapshot = false; hasNewerHistory = false
        readThroughSequence = 0; readPositionConversationID = nil; hasUnread = false
        heldPrompts = []; error = nil; loadingOlder = false
        outgoing = []; retryAliases = [:]; supersededIDs = []
        pinnedMessageIDs = []; outgoingText = [:]; outgoingAttachments = [:]
        backendKey = nil; selectedModel = nil; reasoningEffort = nil
        runningModel = nil; runningEffort = nil
        syncOutbox()
    }

    private(set) var readerGeneration = 0
    private var tailGeneration = 0
    private var historyGeneration = 0
    private var oversizedBodyKey: String?
    private var historyScopes = ConversationHistoryScopes()
    private struct DisplayWrite: Sendable {
        let snapshot: ConversationDisplaySnapshot
        let server: String
        let id: String
        let generation: Int
    }
    @ObservationIgnored private var displayEncodingTask: Task<Void, Never>?
    @ObservationIgnored private var pendingDisplayWrite: DisplayWrite?
    private var pageProjectionVersion: Int?
    private var currentRemoteKeys: Set<String> = []
    private var visibleRows: Set<String> = []
    private var lastDisplaySave = Date.distantPast
    private var displayByteSizes: [String: (body: ConversationBody, count: Int)] = [:]
    @ObservationIgnored private let displayByteEncoder = JSONEncoder()
    private var replaceHistoryOnSnapshot = false
    private(set) var hasNewerHistory = false

    private func invalidateReader() {
        readerGeneration += 1
        historyGeneration += 1
        releaseOversizedBody()
        tailGeneration += 1
        pendingDisplayWrite = nil
        bodyErrors = [:]
        historyScopes = ConversationHistoryScopes()
        currentRemoteKeys = []; tailCoverage = nil; tailFirstOrder = nil
        if let current = status { status = ConversationStatus(authority: "stale", turnRunning: nil, liveOutput: nil, rest: current.rest) }
    }
    private func displayID(_ id: String) -> String { "conversation-display:v1:" + id + ":" + lens.rawValue }
    private func releaseOversizedBody() {
        guard let key = oversizedBodyKey else { return }
        expandedBodies[key] = nil
        displayByteSizes["expanded:" + key] = nil
        oversizedBodyKey = nil
        // The cached row text shares this large string too.
        projectedRows = []; projectedRevision = -1
    }
    func setVisibleRows(_ ids: Set<String>) {
        visibleRows = ids.union(items.filter { ids.contains(retryAliases[$0.id] ?? $0.id) }.map(\.key))
        if let key = oversizedBodyKey, !visibleRows.contains(key) {
            releaseOversizedBody()
        }
    }
    func returnToLatest(using service: any ConversationServing) async {
        replaceHistoryOnSnapshot = true
        await refresh(using: service, transcript: true)
    }
    private func admitHistoryScope(_ scope: ConversationCursor.Scope, keys: Set<String>) {
        let discarded = historyScopes.admit(scope, keys: keys, visibleKeys: visibleRows,
            active: .init(kind: "timeline", key: nil))
        guard !discarded.isEmpty else { return }
        items.removeAll { discarded.contains($0.key) }
        expandedBodies = expandedBodies.filter { !discarded.contains($0.key) }
    }
    /// Compute unseen inline sizes before admission, without serializing on main.
    private func bodySizes(for incoming: [ConversationItem]) async -> [String: (body: ConversationBody, count: Int)] {
        var unseen: [String: ConversationBody] = [:]
        for item in incoming {
            guard let body = item.body?.value else { continue }
            let key = "inline:" + item.key
            if displayByteSizes[key]?.body != body { unseen[key] = body }
        }
        guard !unseen.isEmpty else { return [:] }
        return await Task.detached(priority: .utility) { [unseen] in
            let encoder = JSONEncoder()
            return unseen.mapValues { body in (body: body, count: (try? encoder.encode(body).count) ?? 0) }
        }.value
    }
    private func retainDisplay(preferOlder: Bool = false) {
        let retained = ConversationRetention.retain(items, bodies: expandedBodies, visible: visibleRows, preferOlder: preferOlder, oversizedKey: oversizedBodyKey) { [self] key, body in
            if let previous = displayByteSizes[key], previous.body == body { return previous.count }
            let count = (try? displayByteEncoder.encode(body).count) ?? 0
            displayByteSizes[key] = (body, count)
            return count
        }
        if retained.removedOldest, let first = retained.items.first?.order, let projection = pageProjectionVersion ?? older?.projectionVersion {
            older = ConversationCursor(projectionVersion: projection, lens: lens.rawValue,
                scope: .init(kind: "timeline", key: nil), order: first)
        }
        if retained.removedNewest { hasNewerHistory = true }
        items = retained.items; expandedBodies = retained.bodies
        if let key = oversizedBodyKey, retained.bodies[key] == nil { releaseOversizedBody() }
        let keys = Set(items.map(\.key))
        currentRemoteKeys.formIntersection(keys)
        bodyErrors = bodyErrors.filter { keys.contains($0.key) }
        historyScopes.retain(keys)
        displayByteSizes = displayByteSizes.filter { key, _ in
            if key.hasPrefix("inline:") { return keys.contains(String(key.dropFirst(7))) }
            return retained.bodies[String(key.dropFirst(9))] != nil
        }
        let rowIDs = Set(items.map { retryAliases[$0.id] ?? $0.id })
            .union(outgoing.map { retryAliases[$0.id] ?? $0.id }).union(liveRow.map { [$0.id] } ?? [])
        rowOrder.removeAll { !rowIDs.contains($0) }
    }
    func saveDisplay(force: Bool = false) {
        guard let api = persistenceAPI, let id = conversationID, let status,
              loadedConversationID == id, loadedLens == lens,
              force || Date().timeIntervalSince(lastDisplaySave) >= DisplayCacheConfiguration.checkpointInterval else { return }
        let stale = ConversationStatus(authority: "stale", turnRunning: nil, liveOutput: nil, rest: status.rest)
        let saved = ConversationDisplaySnapshot(schema: 1, conversationID: id, lens: lens.rawValue,
            page: ConversationPage(items: items, older: older, projectionVersion: pageProjectionVersion), status: stale)
        let write = DisplayWrite(snapshot: saved, server: LocalStore.server(api.baseURL), id: displayID(id), generation: readerGeneration)
        lastDisplaySave = .now
        if displayEncodingTask != nil { pendingDisplayWrite = write }
        else { encodeDisplay(write) }
    }
    /// One encode plus one replaceable pending snapshot. Encoding runs off main,
    /// then the reader generation decides whether its bytes can enter the cache.
    private func encodeDisplay(_ write: DisplayWrite) {
        displayEncodingTask = Task { [weak self] in
            let data = await Task.detached(priority: .utility) { try? JSONEncoder().encode(write.snapshot) }.value
            guard let self else { return }
            if let data, write.generation == self.readerGeneration {
                LocalStore.shared.enqueueCacheResponse(server: write.server, id: write.id, data: data)
            }
            self.displayEncodingTask = nil
            if let pending = self.pendingDisplayWrite {
                self.pendingDisplayWrite = nil
                self.encodeDisplay(pending)
            }
        }
    }

    private func refreshRejectedPhotoDrafts(api: APIClient) {
        let server = LocalStore.server(api.baseURL)
        for (id, cleared) in clearedPhotoDrafts {
            guard let fact = try? outgoingStore.outgoing.summary(server: server, id: id) else { continue }
            if fact.state == "failed" {
                if cleared.draftKey == draftKey {
                    if composerRevision == cleared.revision { reloadDraft() }
                    error = fact.note
                }
                clearedPhotoDrafts.removeValue(forKey: id)
            } else if fact.state == "sent" {
                clearedPhotoDrafts.removeValue(forKey: id)
            }
        }
    }
    private func retrySaved(_ id: String, api: APIClient) async {
        guard !sending, !preparingPhoto else { return }
        let revision = composerRevision
        let generation = composerConfigurationGeneration
        let identity = UUID()
        outgoingPreparationID = identity; preparingOutgoingID = id
        defer {
            if outgoingPreparationID == identity { outgoingPreparationID = nil; preparingOutgoingID = nil }
        }
        guard let next = await Outbox.shared.retry(id, api: api, imageWorkStarted: imageWorkStarted),
              generation == composerConfigurationGeneration,
              let work = Outbox.shared.work(LocalStore.server(api.baseURL), id: next),
              work.rejectedPhotoDraft?.id == draftKey else { return }
        clearedPhotoDrafts.removeValue(forKey: id)
        if composerRevision == revision { reloadDraft() }
        clearedPhotoDrafts[next] = (composerRevision, draftKey)
    }
    func syncOutbox(savedWork: [PendingWork]? = nil) {
        guard let api = persistenceAPI, let owner = persistenceOwner else { return }
        refreshRejectedPhotoDrafts(api: api)
        let previous = Dictionary(outgoing.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let received = Set(items.compactMap(\.senderMessageId))
        func rendered(_ work: PendingWork) -> Outgoing {
            if let prior = work.priorMessageIDs, let first = prior.first {
                supersededIDs.formUnion(prior); retryAliases[work.id] = first
            }
            return Outgoing(id: work.id, text: work.message?.content.compactMap(\.text).joined(separator: "\n") ?? work.displayText,
                attachments: work.message?.content.filter { $0.piece != "text" } ?? [])
        }
        // Only a refusal shows on the row: a red mark that retries on tap.
        func marked(_ row: Outgoing, state: String, note: String) -> Outgoing {
            var row = row
            row.note = state == PendingWork.State.failed.rawValue ? note : nil
            row.retryable = state == PendingWork.State.failed.rawValue
            return row
        }
        if let savedWork {
            // Review supplies an already-selected snapshot, which can belong to
            // another store. Its membership and outcomes are authoritative here.
            outgoing = savedWork.compactMap { work -> Outgoing? in
                guard work.kind == .message, work.owner == owner, !supersededIDs.contains(work.id),
                    !received.contains(work.id) || work.state != .sent else { return nil }
                return marked(previous[work.id] ?? rendered(work), state: work.state.rawValue, note: work.note)
            }.filter { !supersededIDs.contains($0.id) }
            rememberRows(outgoing.map { retryAliases[$0.id] ?? $0.id })
            return
        }
        let server = LocalStore.server(api.baseURL)
        if recoversConversationIDFromOutbox, conversationID == nil { conversationID = try? outgoingStore.outgoing.latestConversationID(server: server, owner: owner) }
        let selected = (try? outgoingStore.outgoing.conversationSummaries(server: server, owner: owner, conversationID: conversationID)) ?? []
        outgoing = selected.compactMap { fact -> Outgoing? in
            guard !supersededIDs.contains(fact.id), !received.contains(fact.id) || fact.state != "sent" else { return nil }
            // Intent bytes are immutable. Keep only the already-rendered content;
            // delivery updates replace metadata without decoding it again.
            if let row = previous[fact.id] { return marked(row, state: fact.state, note: fact.note) }
            guard let work = try? outgoingStore.outgoing.work(server: server, id: fact.id), work.owner == owner else { return nil }
            return marked(rendered(work), state: fact.state, note: fact.note)
        }.filter { !supersededIDs.contains($0.id) }
        rememberRows(outgoing.map { retryAliases[$0.id] ?? $0.id })
    }
    var lens: ConversationLens = .focus { didSet { if oldValue != lens { invalidateReader() } } }
    var items: [ConversationItem] = [] { didSet { if oldValue != items { projectionRevision += 1 } } }
    var expandedBodies: [String: ConversationBody] = [:] { didSet { if oldValue != expandedBodies { projectionRevision += 1 } } }
    private(set) var bodyErrors: [String: String] = [:]
    var status: ConversationStatus?
    var hasUnread = false
    private var readThroughSequence = 0
    private var readPositionConversationID: String?
    var heldPrompts: [ConversationConfiguration.HeldPrompt] = [] { didSet { if oldValue != heldPrompts { projectionRevision += 1 } } }
    var older: ConversationCursor?
    var error: String?
    var sending = false
    var loadingOlder = false
    var outgoing: [Outgoing] = [] { didSet { if oldValue != outgoing { projectionRevision += 1 } } }
    struct Outgoing: Identifiable, Equatable {
        let id: String
        let text: String
        var note: String?
        var retryable = false
        var attachments: [ConversationSend.Piece] = []
    }
    struct VideoPreparation: Identifiable {
        let id: UUID
        let fileName: String
        var progress: Double = 0
    }
    private(set) var preparingVideos: [UUID: VideoPreparation] = [:]
    var failedVideo: String? { attachments.compactMap { $0.localVideo?.preparationError }.first }
    private(set) var preparingPhotos: [UUID: String] = [:]
    private var draftPreparationID: UUID?
    var preparingDraftPhotos: Bool { draftPreparationID != nil }
    private(set) var preparingOutgoingID: String?
    private var outgoingPreparationID: UUID?
    var preparingPhoto: Bool { !preparingVideos.isEmpty || !preparingPhotos.isEmpty || preparingDraftPhotos || preparingOutgoingID != nil }
    private var preparedAttachments: [ConversationSend.Piece] = []
    var deliveryMode = "steer"
    /// The conversation's backend can join a running turn, as the server last said.
    var supportsSteer = true
    var attachments: [ConversationSend.Piece] = [] {
        didSet {
            composerRevision += 1
            guard !restoring, let api = persistenceAPI else { return }
            do {
                try LocalStore.shared.put(server: LocalStore.server(api.baseURL), kind: "attachments", id: draftKey, data: JSONEncoder().encode(attachments))
                let retained = Set(attachments.compactMap { $0.localVideo?.filePath })
                for path in Set(oldValue.compactMap { $0.localVideo?.filePath }).subtracting(retained) where !path.isEmpty {
                    if try LocalStore.shared.outgoing.videoIsUnowned(path) {
                        try? FileManager.default.removeItem(atPath: path)
                    }
                }
            }
            catch { self.error = "Attachments could not be saved: \(error.localizedDescription)" }
        }
    }
    /// The one way a picked, pasted, dragged or photographed file joins the message.
    /// Images become image pieces; anything else is a file piece.
    /// Camera encoding also runs outside the main actor, before ordinary image intake.
    @discardableResult func attachPhoto(fileName: String, data: @escaping @Sendable () -> Data?) async -> Bool {
        let generation = composerConfigurationGeneration
        let identity = UUID()
        preparingPhotos[identity] = fileName
        defer { preparingPhotos.removeValue(forKey: identity) }
        let bytes = await Task.detached(priority: .userInitiated, operation: data).value
        guard generation == composerConfigurationGeneration else { return false }
        guard let bytes else { error = ConversationImagePreparation.Failure.unreadable.localizedDescription; return false }
        preparingPhotos.removeValue(forKey: identity)
        return await attach(bytes, type: .jpeg, fileName: fileName)
    }
    /// The source stays alive until its file is copied into the persistent video store.
    @discardableResult func attachVideo(fileName: String, load: () async throws -> URL,
                                       release: (URL) -> Void = { _ in }) async -> Bool {
        let generation = composerConfigurationGeneration
        let identity = UUID()
        preparingVideos[identity] = VideoPreparation(id: identity, fileName: fileName)
        defer { preparingVideos.removeValue(forKey: identity) }
        do {
            let source = try await load()
            defer { release(source) }
            let video = try await VideoAttachments.importFile(source, fileName: fileName) { [weak self] progress in
                Task { @MainActor in
                    guard let self, self.composerConfigurationGeneration == generation else { return }
                    self.preparingVideos[identity]?.progress = progress
                }
            }
            guard generation == composerConfigurationGeneration else {
                try? FileManager.default.removeItem(at: video.fileURL)
                return false
            }
            attachments.append(.init(piece: "attachment_upload",
                                     mediaType: UTType(filenameExtension: (video.fileName as NSString).pathExtension)?.preferredMIMEType ?? "video/mp4",
                                     fileName: video.fileName, localVideo: video))
            return video.preparationError == nil
        } catch {
            if generation == composerConfigurationGeneration {
                let failed = SavedVideo(id: identity.uuidString, fileName: fileName, filePath: "", byteCount: 0,
                                        preparationError: error.localizedDescription)
                attachments.append(.init(piece: "attachment_upload", fileName: fileName, localVideo: failed))
            }
            return false
        }
    }
    @discardableResult func attach(_ data: Data, type: UTType?, fileName: String) async -> Bool {
        let image = type?.conforms(to: .image) == true
        guard image || data.count <= 20_000_000 else { error = "Choose a file smaller than 20 MB."; return false }
        guard image else {
            attachments.append(.init(piece: "file", data: data.base64EncodedString(),
                mediaType: type?.preferredMIMEType ?? "application/octet-stream", fileName: fileName))
            return true
        }
        let identity = UUID()
        let generation = composerConfigurationGeneration
        let mediaType = type?.preferredMIMEType ?? "image/jpeg"
        preparingPhotos[identity] = fileName
        defer { preparingPhotos.removeValue(forKey: identity) }
        do {
            let piece = await Task.detached(priority: .userInitiated) {
                ConversationSend.Piece(piece: "image", data: data.base64EncodedString(), mediaType: mediaType, fileName: fileName)
            }.value
            let prepared = try await ConversationImagePreparation.prepare([piece], imageWorkStarted: imageWorkStarted)
            // Another attachment can arrive, or be removed, while this photo converts.
            // Only the combined budget needs another pass; accepted photos skip ImageIO.
            while true {
                guard generation == composerConfigurationGeneration else { return false }
                let previous = attachments
                let combined = try await ConversationImagePreparation.prepare(previous + prepared,
                    alreadyPrepared: preparedAttachments + prepared, imageWorkStarted: imageWorkStarted)
                guard generation == composerConfigurationGeneration else { return false }
                guard attachments == previous else { continue }
                attachments = combined
                preparedAttachments = combined
                return true
            }
        } catch {
            if generation == composerConfigurationGeneration { self.error = error.localizedDescription }
            return false
        }
    }
    func preparedContent() async throws -> [ConversationSend.Piece] {
        if let failedVideo { throw NSError(domain: "VideoAttachment", code: 1, userInfo: [NSLocalizedDescriptionKey: failedVideo]) }
        guard !preparingPhoto else { throw CancellationError() }
        let revision = composerRevision
        let generation = composerConfigurationGeneration
        let captured = attachments
        let identity = UUID()
        draftPreparationID = identity
        defer { if draftPreparationID == identity { draftPreparationID = nil } }
        let prepared: [ConversationSend.Piece]
        do {
            prepared = try await ConversationImagePreparation.prepare(captured,
                alreadyPrepared: preparedAttachments, imageWorkStarted: imageWorkStarted)
        } catch {
            guard revision == composerRevision, generation == composerConfigurationGeneration else { throw CancellationError() }
            throw error
        }
        guard revision == composerRevision, generation == composerConfigurationGeneration else { throw CancellationError() }
        if prepared != attachments { attachments = prepared }
        preparedAttachments = prepared
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        return (text.isEmpty ? [] : [.init(text: text)]) + prepared
    }
    private var outgoingText: [String: String] = [:]
    private var outgoingAttachments: [String: [ConversationSend.Piece]] = [:]
    var backendKey: String?
    var selectedModel: String?
    var reasoningEffort: String?
    // What the conversation runs on, as the server last said. The server holds a message
    // that names a different model until a new turn, so only a real change is sent.
    private var runningModel: String?
    private var runningEffort: String?
    private func configureIntent(_ message: inout ConversationSend) {
        message.mode = deliveryMode
        message.backendKey = conversationID == nil ? backendKey : nil
        message.model = conversationID == nil || selectedModel != runningModel ? selectedModel : nil
        let effortChanged = conversationID == nil || reasoningEffort != runningEffort
        message.reasoningEffort = effortChanged ? reasoningEffort : nil
        message.reasoningEffortIsSet = effortChanged
    }
    private var pinnedMessageIDs: Set<String> = []
    private var rowOrder: [String] = [] { didSet { if oldValue != rowOrder { projectionRevision += 1 } } }
    private var supersededIDs: Set<String> = [] { didSet { if oldValue != supersededIDs { projectionRevision += 1 } } }
    private var liveRow: ConversationRow? { didSet { if oldValue != liveRow { projectionRevision += 1 } } }
    private var liveTurnKey: String?
    private var refreshing = false
    private var refreshAgain = false
    private var requestedTranscript = false
    private var retryAliases: [String: String] = [:] { didSet { if oldValue != retryAliases { projectionRevision += 1 } } }

    private var projectionRevision = 0
    @ObservationIgnored private var projectedRevision = -1
    @ObservationIgnored private var projectedRows: [ConversationRow] = []
    @ObservationIgnored private(set) var projectionBuildCount = 0
    private var liveConversationID: String?
    private var liveReference: ConversationBodyReference?

    var rows: [ConversationRow] {
        let revision = projectionRevision
        if projectedRevision != revision {
            projectedRows = projectRows()
            projectedRevision = revision
            projectionBuildCount += 1
        }
        return projectedRows
    }
    private func projectRows() -> [ConversationRow] {
        let received = Set(items.compactMap(\.senderMessageId))
        let remote = items.compactMap { item -> ConversationRow? in
            guard !supersededIDs.contains(item.id) else { return nil }
            guard let body = expandedBodies[item.key] ?? item.body?.value else {
                return item.body?.reference == nil ? nil : ConversationRow(id: item.id, text: "Read message", own: false, note: nil, item: item)
            }
            guard !body.text.isEmpty else { return nil }
            let local = outgoing.first { $0.id == item.senderMessageId }
            let note: String?
            switch body.kind {
            case "prompt_refused": note = "Not delivered · " + (body.refusalDetail ?? ConversationDelivery.refusalText(body.reason))
            case "prompt_discarded": note = "Not delivered · no longer applicable"
            case "permission_ask", "user_input": note = body.answerSentence ?? (body.state == "live" ? "Respond on the web" : body.state?.capitalized)
            default: note = nil
            }
            return ConversationRow(id: retryAliases[item.id] ?? item.id, text: body.text, own: body.isOwnerPrompt, note: note,
                                   senderLabel: body.isPrompt && !body.isOwnerPrompt ? body.senderLabel : nil,
                                   retryID: local?.retryable == true ? local?.id : nil, item: item)
        }
        var combined = remote + outgoing.filter { !received.contains($0.id) }.map {
            ConversationRow(id: retryAliases[$0.id] ?? $0.id, text: $0.text, own: true, note: $0.note, retryID: $0.retryable ? $0.id : nil, attachments: $0.attachments)
        }
        if let liveRow, !liveRow.text.isEmpty { combined.append(liveRow) }
        let positions = Dictionary(uniqueKeysWithValues: rowOrder.enumerated().map { ($0.element, $0.offset) })
        return combined.sorted { (positions[$0.id] ?? Int.max) < (positions[$1.id] ?? Int.max) }
    }
    var latestMessage: ConversationRow? {
        rows.last(where: { $0.own || $0.senderLabel != nil || ["agent_message", "user_input", "permission_ask"].contains($0.item?.body?.value?.kind ?? "") || $0.id == liveRow?.id })
    }
    var latestLine: String {
        guard let row = latestMessage else { return "Conversation" }
        return row.previewText
    }
    /// The closed dock follows compact status facts without admitting transcript rows.
    var compactPreview: String {
        if let latest = latestMessage, latest.item == nil, latest.own { return latest.previewText }
        guard let status, status.authority == "current" else { return latestMessage?.previewText ?? "Conversation" }
        if let happened = status.rest.latestHappened,
           ["prompt", "agent_message", "streaming_agent_message"].contains(happened.kind),
           let excerpt = happened.excerpt {
            let sender = happened.kind == "prompt" ? happened.senderLabel ?? "You" : nil
            let label = sender.map { ["owner", "You"].contains($0) ? "You" : $0 }
            return label.map { $0 + ": " + excerpt } ?? excerpt
        }
        return status.rest.latestOwnerMessagePreview ?? latestMessage?.previewText ?? "Conversation"
    }

    /// Held prompts whose rows have arrived: the server's queue has moved on.
    var deliveredHeldPromptCount: Int {
        let received = Set(items.compactMap(\.senderMessageId))
        return heldPrompts.filter { received.contains($0.senderMessageId ?? $0.heldPromptId) }.count
    }
    func applyConfiguration(_ configuration: ConversationConfiguration) {
        heldPrompts = configuration.heldPrompts
        if readPositionConversationID != conversationID {
            readPositionConversationID = conversationID
            readThroughSequence = 0
        }
        readThroughSequence = max(readThroughSequence, configuration.ownerReadThroughSequence ?? 0)
        hasUnread = (status?.rest.latestOwnerMessageSequence ?? 0) > readThroughSequence
        // The selection follows the conversation until the owner picks something else.
        if backendKey == nil || (selectedModel == runningModel && reasoningEffort == runningEffort) {
            backendKey = configuration.backendKey
            selectedModel = configuration.model
            reasoningEffort = configuration.reasoningEffort
        }
        runningModel = configuration.model
        runningEffort = configuration.reasoningEffort
        supportsSteer = configuration.supportsSteer
        // Steer is not offered for this backend, so the choice it would keep becomes Queue.
        if !supportsSteer && deliveryMode == "steer" { deliveryMode = "queue" }
        let received = Set(items.compactMap(\.senderMessageId))
        for held in configuration.heldPrompts {
            let id = held.senderMessageId ?? held.heldPromptId
            if !received.contains(id), !outgoing.contains(where: { $0.id == id }) {
                outgoing.append(.init(id: id, text: held.text))
                pinnedMessageIDs.insert(id)
                rememberRows([id])
            }
        }
    }
    /// Only a rendered, visible remote row supplies evidence. Status/tail sequence
    /// and local outgoing rows cannot acknowledge content the person did not see.
    func visibleReadBoundary(rowIDs: Set<String>, active: Bool) -> ConversationReadBoundary? {
        guard active, let id = conversationID, loadedConversationID == id, loadedLens == lens else { return nil }
        let sequence = rows.compactMap { row -> Int? in
            guard rowIDs.contains(row.id), let item = row.item, currentRemoteKeys.contains(item.key),
                  item.body?.reference == nil || expandedBodies[item.key] != nil else { return nil }
            return item.sourceSequence
        }.max() ?? 0
        let alreadyRead = readPositionConversationID == id ? readThroughSequence : 0
        guard sequence > alreadyRead else { return nil }
        return ConversationReadBoundary(conversationID: id, throughSequence: sequence)
    }

    func acknowledgeVisibleRows(_ rowIDs: Set<String>, active: Bool, using service: any ConversationServing) async {
        guard !Task.isCancelled, let boundary = visibleReadBoundary(rowIDs: rowIDs, active: active) else { return }
        if let api = persistenceAPI {
            // The read point moves on screen at once. The outbox carries it to the server.
            do {
                try Outbox.shared.enqueueMarkRead(owner: persistenceOwner, conversationID: boundary.conversationID, through: boundary.throughSequence, api: api)
                advanceReadPoint(boundary.throughSequence)
            } catch { self.error = error.localizedDescription }
            return
        }
        let generation = readerGeneration
        do {
            let confirmed = try await service.markConversationRead(boundary.conversationID, through: boundary.throughSequence)
            guard !Task.isCancelled, generation == readerGeneration, conversationID == boundary.conversationID else { return }
            advanceReadPoint(confirmed)
        } catch {
            // A read acknowledgment is idempotent; keep unread state on failure.
            // The next actual visibility change can try again, never a poll alone.
        }
    }
    private func advanceReadPoint(_ through: Int) {
        if readPositionConversationID != conversationID {
            readPositionConversationID = conversationID
            readThroughSequence = 0
        }
        readThroughSequence = max(readThroughSequence, through)
        hasUnread = (status?.rest.latestOwnerMessageSequence ?? 0) > readThroughSequence
    }

    var firstUnreadID: String? {
        guard hasUnread else { return nil }
        return items.first { ($0.sourceSequence ?? 0) > readThroughSequence && ["agent_message", "user_input", "permission_ask"].contains($0.body?.value?.kind ?? "") }.map { retryAliases[$0.id] ?? $0.id }
    }
    func prepareOpening(using service: any ConversationServing) async {
        while hasUnread, older != nil, let first = items.compactMap(\.sourceSequence).min(), first > readThroughSequence {
            let cursor = older?.order.sequence
            await loadOlder(using: service)
            if error != nil || cursor == older?.order.sequence || Task.isCancelled { return }
        }
    }
    func applyHeldChange(_ result: ConversationHeldChange, senderID: String) {
        if result.discarded == true { setNote(senderID, "Not delivered · removed from queue") }
        else if let fate = result.fate {
            _ = applyDelivery(ConversationDelivery(conversationId: nil, fate: fate, refusalReason: result.refusalReason, reason: result.reason), id: senderID)
        }
    }
    private var loadedLens: ConversationLens?
    private var loadedConversationID: String?

    func updateSpeechTranscript(_ text: String, prefix: String) {
        let prefix = prefix.trimmingCharacters(in: .whitespacesAndNewlines)
        let transcript = text.trimmingCharacters(in: .whitespacesAndNewlines)
        draft = [prefix, transcript].filter { !$0.isEmpty }.joined(separator: " ")
    }

    func refresh(using service: any ConversationServing, transcript: Bool) async {
        requestedTranscript = requestedTranscript || transcript
        refreshAgain = true
        guard !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        repeat {
            refreshAgain = false
            let transcript = requestedTranscript
            requestedTranscript = false
            await refreshSnapshot(using: service, transcript: transcript)
        } while refreshAgain && !Task.isCancelled
    }
    private func refreshSnapshot(using service: any ConversationServing, transcript: Bool) async {
        guard !Task.isCancelled, let id = conversationID else { return }
        let requestedLens = lens
        let generation = readerGeneration
        let streamGeneration = tailGeneration
        do {
            let snapshot = try await service.conversationSnapshot(id, lens: requestedLens, transcript: transcript)
            guard generation == readerGeneration else { return }
            try await admit(snapshot, id: id, lens: requestedLens, transcript: transcript, generation: generation, streamGeneration: streamGeneration, using: service)
            error = nil
        } catch { if !Task.isCancelled, generation == readerGeneration { reportReadFailure(error) } }
    }
    /// A read that could not reach the server says nothing: the screen keeps what it has
    /// and reads again when the connection returns. With the conversation on screen, no
    /// connection error shows. Other failures still show.
    func reportReadFailure(_ error: Error) {
        guard let note = ServerReadFailure.note(error), items.isEmpty || (error as NSError).domain != NSURLErrorDomain else { return }
        self.error = note
    }

    // MARK: Open tail

    /// The open tail's cursor and the first order of its window, both from its last snapshot.
    private var tailCoverage: Int?
    private var tailFirstOrder: ConversationCursor.Order?
    @discardableResult func beginTail() -> Int {
        tailGeneration += 1
        tailCoverage = nil; tailFirstOrder = nil
        currentRemoteKeys = []
        return tailGeneration
    }
    func endTail(generation: Int) {
        guard generation == tailGeneration else { return }
        tailGeneration += 1
        tailCoverage = nil; tailFirstOrder = nil
        if let current = status { status = ConversationStatus(authority: "stale", turnRunning: nil, liveOutput: nil, rest: current.rest) }
        saveDisplay(force: true)
    }

    /// Applies one frame of the open tail without reading the transcript again. Only a
    /// changed live-output reference or unsettled outgoing messages read anything.
    /// Returns false when the stream must be reopened for a fresh snapshot: a reset, a
    /// gap, an invalidated window, or a changed runtime epoch or stop, as on the web.
    /// The server marks the window invalidated with every removed row; that removal is
    /// applied here, as the web applies it, and the stream stays open.
    func applyTail(_ frame: ConversationTailFrame, transcript: Bool = true, generation suppliedGeneration: Int? = nil, using service: any ConversationServing) async -> Bool {
        let generation = suppliedGeneration ?? tailGeneration
        guard generation == tailGeneration, let id = conversationID else { return false }
        let requestedLens = lens
        switch frame {
        case .reset: return false
        case .snapshot(let snapshot):
            do {
                try await admit(snapshot, id: id, lens: requestedLens, transcript: transcript, generation: readerGeneration, streamGeneration: generation, using: service)
                guard generation == tailGeneration, id == conversationID, lens == requestedLens else { return false }
                tailCoverage = snapshot.coverage?.throughSequence
                tailFirstOrder = snapshot.page.items.first?.order
                error = nil
                return true
            } catch { if !Task.isCancelled, generation == tailGeneration { reportReadFailure(error) }; return false }
        case .changes(let changes):
            guard let coverage = tailCoverage, changes.fromSequence == coverage, changes.lens == requestedLens.rawValue,
                  changes.removedKeys.count > 0 || !changes.invalidatedScopes.contains(where: { $0.kind == "timeline" }),
                  changes.status.stoppedWithoutEnding == status?.stoppedWithoutEnding,
                  changes.status.runtimeEpoch == status?.runtimeEpoch else { return false }
            do {
                let (liveText, receipts) = try await lightReads(changes.status, id: id, transcript: transcript, using: service)
                let sizes = transcript ? await bodySizes(for: changes.upserts) : [:]
                try Task.checkCancellation()
                guard generation == tailGeneration, id == conversationID, lens == requestedLens, tailCoverage == coverage else { return false }
                displayByteSizes.merge(sizes) { _, new in new }
                if status != changes.status { status = changes.status }
                if !transcript {
                    applyReceipts(receipts)
                    tailCoverage = changes.coverage.throughSequence
                    error = nil
                    return true
                }
                let timeline = changes.scopeChanges.first { $0.scope.kind == "timeline" }
                let removed = Set(changes.removedKeys)
                let leftWindow = Set(timeline?.removedKeys ?? [])
                // Explicitly loaded history before the window keeps its rows.
                func inWindow(_ item: ConversationItem) -> Bool {
                    guard let first = tailFirstOrder, let order = item.order else { return true }
                    return (order.sequence, order.slot) >= (first.sequence, first.slot)
                }
                var next = items.filter { !removed.contains($0.key) && !(leftWindow.contains($0.key) && inWindow($0)) }
                let upserts = Dictionary(changes.upserts.map { ($0.key, $0) }, uniquingKeysWith: { _, latest in latest })
                for key in timeline?.itemKeys ?? [] {
                    guard let item = upserts[key] else { continue }
                    if let at = next.firstIndex(where: { $0.key == key }) { next[at] = item; continue }
                    let at = next.firstIndex { other in
                        guard let order = item.order, let otherOrder = other.order else { return false }
                        return (order.sequence, order.slot) < (otherOrder.sequence, otherOrder.slot)
                    } ?? next.count
                    next.insert(item, at: at)
                }
                settleLiveRow(candidates: changes.upserts, output: changes.status.liveOutput)
                if items != next { items = next }
                currentRemoteKeys.subtract(removed)
                currentRemoteKeys.formUnion(changes.upserts.map(\.key))
                retainDisplay()
                reconcileItems()
                applyLive(changes.status.liveOutput, text: liveText, id: id)
                applyReceipts(receipts)
                tailCoverage = changes.coverage.throughSequence
                saveDisplay()
                error = nil
                return true
            } catch { if !Task.isCancelled, generation == tailGeneration { reportReadFailure(error) }; return false }
        }
    }

    // MARK: Shared admission

    /// One coherent snapshot, from an explicit read or the open tail.
    private func admit(_ snapshot: ConversationSnapshot, id: String, lens requestedLens: ConversationLens, transcript: Bool, generation: Int, streamGeneration: Int? = nil, using service: any ConversationServing) async throws {
        let (liveText, receipts) = try await lightReads(snapshot.status, id: id, transcript: transcript, using: service)
        let sizes = transcript ? await bodySizes(for: snapshot.page.items) : [:]
        try Task.checkCancellation()
        guard generation == readerGeneration, streamGeneration == nil || streamGeneration == tailGeneration,
              id == conversationID, lens == requestedLens else { return }
        displayByteSizes.merge(sizes) { _, new in new }
        if status != snapshot.status { status = snapshot.status }
        if transcript {
            pageProjectionVersion = snapshot.page.projectionVersion ?? snapshot.page.older?.projectionVersion
            if replaceHistoryOnSnapshot { visibleRows = [] }
            if replaceHistoryOnSnapshot || loadedConversationID != id || loadedLens != requestedLens { historyGeneration += 1; releaseOversizedBody(); items = []; expandedBodies = [:]; hasNewerHistory = false; replaceHistoryOnSnapshot = false; loadedConversationID = id; loadedLens = requestedLens; older = snapshot.page.older; liveRow = nil; liveTurnKey = nil }
            settleLiveRow(candidates: snapshot.page.items, output: snapshot.status.liveOutput)
            // Replace the current window; retain explicitly loaded history before it.
            let keys = Set(snapshot.page.items.map(\.key))
            let nextItems: [ConversationItem]
            if let first = snapshot.page.items.first, let at = items.firstIndex(where: { $0.key == first.key }) {
                nextItems = Array(items.prefix(at)).filter { !keys.contains($0.key) } + snapshot.page.items
            } else {
                if !items.isEmpty { historyGeneration += 1 }
                nextItems = snapshot.page.items; older = snapshot.page.older
            }
            if items != nextItems { items = nextItems }
            currentRemoteKeys.formUnion(snapshot.page.items.map(\.key))
            admitHistoryScope(snapshot.page.scope ?? .init(kind: "timeline", key: nil), keys: Set(snapshot.page.items.map(\.key)))
            retainDisplay()
            reconcileItems()
        }
        if transcript { applyLive(snapshot.status.liveOutput, text: liveText, id: id) }
        applyReceipts(receipts)
        if transcript { saveDisplay() }
    }

    /// The only reads a frame may need: changed live text and unsettled receipts.
    private func lightReads(_ status: ConversationStatus, id: String, transcript: Bool, using service: any ConversationServing) async throws -> (String?, [ConversationReceipt]) {
        let liveText: String?
        if transcript, let output = status.liveOutput {
            if id == liveConversationID, output.reference == liveReference, let liveRow { liveText = liveRow.text }
            else { liveText = try await service.conversationLiveText(id, reference: output.reference) }
        } else { liveText = nil }
        let receiptIDs = Array(outgoing.map(\.id).suffix(64))
        let receipts = receiptIDs.isEmpty ? [] : try await service.conversationReceipts(id, messageIDs: receiptIDs)
        return (liveText, receipts)
    }

    /// A recorded reply that replaces the streamed one keeps the streamed row's identity.
    private func settleLiveRow(candidates: [ConversationItem], output: ConversationLiveOutput?) {
        if let liveRow, output?.reference.version != liveRow.id.replacingOccurrences(of: "live:", with: "", options: .anchored),
           let settled = candidates.last(where: { incoming in
               incoming.turnKey == liveTurnKey && incoming.body?.value?.kind == "agent_message" && !items.contains(where: { $0.key == incoming.key })
           }) {
            retryAliases[settled.id] = liveRow.id
        }
    }

    private func reconcileItems() {
        insertRemoteRows(items.map { retryAliases[$0.id] ?? $0.id })
        let received = Set(items.compactMap(\.senderMessageId))
        outgoing.removeAll { message in
            guard received.contains(message.id) else { return false }
            // A refusal remains retryable even after its server row arrives.
            let delivered = items.first(where: { $0.senderMessageId == message.id })?.body?.value?.kind == "prompt"
            if delivered { outgoingText[message.id] = nil; outgoingAttachments[message.id] = nil }
            return delivered
        }
        for item in items {
            guard let senderID = item.senderMessageId else { continue }
            switch item.body?.value?.kind {
            case "prompt_refused": setNote(senderID, "Not delivered", retryable: true)
            case "prompt_discarded": setNote(senderID, "Not delivered · no longer applicable")
            default: break
            }
        }
    }

    private func applyLive(_ output: ConversationLiveOutput?, text liveText: String?, id: String) {
        if let output, let liveText, !liveText.isEmpty {
            liveConversationID = id
            liveReference = output.reference
            liveTurnKey = output.turnKey
            let identity = "live:" + output.reference.version
            let row = ConversationRow(id: identity, text: liveText, own: false, note: nil)
            if liveRow != row { liveRow = row }
            rememberRows([identity])
        } else { liveRow = nil; liveTurnKey = nil; liveReference = nil }
    }

    private func applyReceipts(_ receipts: [ConversationReceipt]) {
        for receipt in receipts {
            switch receipt.fate {
            case "prompt", "prompt_uncertain": setNote(receipt.senderMessageId, nil)
            case "prompt_refused": setNote(receipt.senderMessageId, "Not delivered", retryable: true)
            case "prompt_discarded": setNote(receipt.senderMessageId, "Not delivered · no longer applicable")
            default: break
            }
        }
    }
    func loadOlder(using service: any ConversationServing) async {
        guard !loadingOlder, let id = conversationID, let cursor = older else { return }
        let generation = readerGeneration
        let history = historyGeneration
        loadingOlder = true
        defer { loadingOlder = false }
        do {
            let page = try await service.conversationPage(id, cursor: cursor)
            let sizes = await bodySizes(for: page.items)
            try Task.checkCancellation()
            guard generation == readerGeneration, history == historyGeneration, cursor.lens == lens.rawValue, id == conversationID else { return }
            displayByteSizes.merge(sizes) { _, new in new }
            let keys = Set(items.map(\.key))
            let earlier = page.items.filter { !keys.contains($0.key) }
            items.insert(contentsOf: earlier, at: 0)
            let earlierIDs = earlier.map(\.id).filter { !rowOrder.contains($0) }
            rowOrder.insert(contentsOf: earlierIDs, at: 0)
            older = page.older
            pageProjectionVersion = page.projectionVersion ?? cursor.projectionVersion
            currentRemoteKeys.formUnion(page.items.map(\.key))
            admitHistoryScope(page.scope ?? cursor.scope, keys: Set(page.items.map(\.key)))
            retainDisplay(preferOlder: true)
            saveDisplay()
        } catch { if !Task.isCancelled, generation == readerGeneration, history == historyGeneration { reportReadFailure(error) } }
    }
    func loadBody(_ item: ConversationItem, using service: any ConversationServing) async {
        guard let id = conversationID, let reference = item.body?.reference else { return }
        let generation = readerGeneration
        let requestedLens = lens
        do {
            let body = try await service.conversationBody(id, reference: reference)
            guard !Task.isCancelled, generation == readerGeneration, id == conversationID, requestedLens == lens,
                  items.contains(where: { $0.key == item.key && $0.body?.reference == reference }) else { return }
            let bytes = await Task.detached(priority: .utility) { try? JSONEncoder().encode(body).count }.value
            guard !Task.isCancelled, generation == readerGeneration, id == conversationID, requestedLens == lens,
                  items.contains(where: { $0.key == item.key && $0.body?.reference == reference }) else { return }
            guard let bytes, bytes <= ConversationRetention.oversizedBodyLimit else {
                let message = "Message is too large. Open it on the web."
                bodyErrors[item.key] = message
                error = message
                return
            }
            if bytes > ConversationRetention.inlineByteLimit {
                guard visibleRows.contains(item.key) else { return }
                if let previous = oversizedBodyKey, previous != item.key {
                    releaseOversizedBody()
                }
                oversizedBodyKey = item.key
            }
            bodyErrors[item.key] = nil
            displayByteSizes["expanded:" + item.key] = (body, bytes)
            expandedBodies[item.key] = body
            retainDisplay()
        } catch { if !Task.isCancelled, generation == readerGeneration { reportReadFailure(error) } }
    }
    @discardableResult func send(to owner: ConversationOwner, using service: any ConversationServing) async -> Bool {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !sending, !preparingPhoto, !text.isEmpty || !attachments.isEmpty else { return false }
        let generation = readerGeneration
        let content: [ConversationSend.Piece]
        do { content = try await preparedContent() }
        catch is CancellationError { return false }
        catch { self.error = error.localizedDescription; return false }
        guard generation == readerGeneration else { return false }
        if let api = persistenceAPI {
            let id = UUID().uuidString
            var message = ConversationSend(conversationId: conversationID, content: content,
                senderMessageId: id, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000))
            configureIntent(&message)
            let work = PendingWork(id: id, created: .now, kind: .message, owner: owner, message: message)
            do {
                let server = LocalStore.server(api.baseURL)
                let capture = try LocalStore.shared.outgoing.captureDraft(server: server, id: draftKey)
                try LocalStore.shared.outgoing.enqueue(work, server: server, capturedDraft: capture)
                restoring = true; draft = ""; attachments = []; restoring = false
                if content.contains(where: { $0.piece == "image" }) { clearedPhotoDrafts[id] = (composerRevision, draftKey) }
                syncOutbox()
                Task { await Outbox.shared.drain(api: api); syncOutbox() }
                return true
            } catch { self.error = "Message is still in the composer: \(error.localizedDescription)"; return false }
        }
        let id = UUID().uuidString
        outgoing.append(Outgoing(id: id, text: text, attachments: attachments))
        pinnedMessageIDs.insert(id)
        rememberRows([id])
        outgoingText[id] = text
        outgoingAttachments[id] = attachments
        let originalDraft = draft
        attachments = []
        draft = ""
        return await deliver(id: id, text: text, to: owner, using: service,
            rejectedDraft: (originalDraft, composerRevision))
    }

    func retry(_ id: String, to owner: ConversationOwner, using service: any ConversationServing) async {
        if let api = persistenceAPI { await retrySaved(id, api: api); syncOutbox(); return }
        guard !sending, let at = outgoing.firstIndex(where: { $0.id == id && $0.retryable }) else { return }
        let old = outgoing[at]
        // In-memory attempts already captured prepared bytes at their original send.
        let prepared = outgoingAttachments[id] ?? old.attachments
        // An explicitly refused attempt has a terminal server identity. A new attempt
        // keeps the same visual identity but gets its own delivery receipt.
        let next = UUID().uuidString
        retryAliases[next] = retryAliases[id] ?? id
        supersededIDs.insert(id)
        outgoingText[next] = outgoingText.removeValue(forKey: id)
        outgoingAttachments.removeValue(forKey: id)
        outgoingAttachments[next] = prepared
        items.removeAll { $0.senderMessageId == id }
        outgoing[at] = Outgoing(id: next, text: old.text, attachments: prepared)
        _ = await deliver(id: next, text: outgoingText[next] ?? old.text, to: owner, using: service)
    }

    private func deliver(id: String, text: String, to owner: ConversationOwner, using service: any ConversationServing,
                         rejectedDraft: (text: String, revision: Int)? = nil) async -> Bool {
        sending = true
        defer { sending = false }
        var message = ConversationSend(conversationId: conversationID, content: (text.isEmpty ? [] : [.init(text: text)]) + (outgoingAttachments[id] ?? []), senderMessageId: id, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000))
        configureIntent(&message)
        let generation = readerGeneration
        do {
            let result = try await service.sendConversation(owner, message: message)
            guard generation == readerGeneration else { return false }
            if let newID = result.conversationId { conversationID = newID }
            return applyDelivery(result, id: id)
        } catch let error as APIError where error.status == 422 && message.content.contains(where: { $0.piece == "image" }) {
            guard generation == readerGeneration else { return false }
            setNote(id, "Not sent · " + error.message, retryable: true)
            if let rejectedDraft, composerRevision == rejectedDraft.revision {
                draft = rejectedDraft.text
                attachments = message.content.filter { $0.piece != "text" }
            }
            self.error = error.message
        } catch let error as URLError where [.notConnectedToInternet, .cannotConnectToHost, .cannotFindHost].contains(error.code) {
            guard generation == readerGeneration else { return false }
            setNote(id, "Not sent", retryable: true)
        } catch {
            guard generation == readerGeneration else { return false }
            // Timeout or a lost response does not prove the server rejected it.
            setNote(id, nil)
        }
        return false
    }
    private func applyDelivery(_ result: ConversationDelivery, id: String) -> Bool {
            switch result.fate {
            case "started", "injected", "queued": setNote(id, nil); return true
            case "suppressed": setNote(id, "Not delivered · \(result.reason ?? "no longer applicable")")
            case "refused": setNote(id, "Not delivered · \(ConversationDelivery.refusalText(result.refusalReason))", retryable: true)
            default: setNote(id, nil)
            }
        return false
    }
    private func insertRemoteRows(_ ids: [String]) {
        var known = Set(rowOrder)
        for (index, id) in ids.enumerated() where known.insert(id).inserted {
            if let following = ids.dropFirst(index + 1).first(where: { rowOrder.contains($0) && !pinnedMessageIDs.contains($0) }), let at = rowOrder.firstIndex(of: following) {
                rowOrder.insert(id, at: at)
            } else { rowOrder.append(id) }
        }
    }
    private func rememberRows(_ ids: [String]) {
        var known = Set(rowOrder)
        let added = ids.filter { known.insert($0).inserted }
        if !added.isEmpty { rowOrder.append(contentsOf: added) }
    }
    private func setNote(_ id: String, _ note: String?, retryable: Bool = false) {
        if let at = outgoing.firstIndex(where: { $0.id == id }) {
            outgoing[at].note = note
            outgoing[at].retryable = retryable
        }
    }
}
