import Foundation
import Observation

/// Domain-owned read declaration. Query parameters are part of the server key.
struct ResourceRegistration<Value: Decodable & Sendable>: Sendable {
    let key: String
    var versioned = true
    /// Versionless volatile domains opt into a bounded signal fallback.
    var signalRefreshInterval: TimeInterval? = nil
    var discardOnFailure: (@Sendable (any Error) -> Bool)? = nil
    var nextRefreshAt: (@Sendable (Value) -> Double?)? = nil
    var decode: @Sendable (Data) throws -> Value = { try APIClient.decoder().decode(Value.self, from: $0) }
}

enum ResourceLimits {
    static let probeBatch = 64
    static let concurrentReads = 3
    static let inactiveEntries = 64
    static let inactiveBytes = 4 * 1024 * 1024
    static let inactiveSeconds: TimeInterval = 300
    static let coalescingSeconds = 1.5
    static let fallbackSeconds: TimeInterval = 30
    static let maximumRetrySeconds: TimeInterval = 120
    static let maximumStreamRetrySeconds = 60
    static func retrySeconds(_ failures: Int) -> TimeInterval {
        min(maximumRetrySeconds, fallbackSeconds * pow(2, Double(min(max(0, failures - 1), 2))))
    }
}

fileprivate struct ResourceDecoded: @unchecked Sendable {
    let value: Any
    let deadline: Double?
}
fileprivate struct ResourceDecoder: Sendable {
    let decode: @Sendable (Data) throws -> ResourceDecoded
}

@MainActor @Observable final class ResourceEntry {
    let key: String
    let server: String
    let versioned: Bool
    var publication = 0
    var data: Data?
    var error: String?
    var failure: ServerReadFailure?
    var isStale = true
    var demands = 0
    var lastUsed = Date()
    var epoch: String?
    var revision: String?
    var generation = 0
    var nextRefreshAt: Double?
    @ObservationIgnored fileprivate var decoders: [String: ResourceDecoder] = [:]
    @ObservationIgnored var decoding: Set<String> = []
    @ObservationIgnored var decodeErrors: [String: String] = [:]
    @ObservationIgnored var signalRefreshInterval: TimeInterval?
    @ObservationIgnored var discardOnFailure: (@Sendable (any Error) -> Bool)?
    @ObservationIgnored var lastAttempt = Date.distantPast
    @ObservationIgnored var failures = 0
    @ObservationIgnored var retryAt = Date.distantPast
    @ObservationIgnored var supportsVersionedRead = true
    @ObservationIgnored var acceptedDeadline: Double?
    @ObservationIgnored var probeGeneration = 0
    @ObservationIgnored var targetStamp: (String, String)?
    @ObservationIgnored var fallback: Task<Void, Never>?
    @ObservationIgnored var terminallyDiscarded = false
    @ObservationIgnored var restoreSequence = 0
    @ObservationIgnored var restoring: Task<Void, Never>?
    @ObservationIgnored var decoded: [String: Any] = [:]
    @ObservationIgnored var task: Task<Void, Never>?
    @ObservationIgnored var deadline: Task<Void, Never>?
    init(key: String, server: String, versioned: Bool) { self.key = key; self.server = server; self.versioned = versioned }
}

/// A handle reads shared accepted state. Each mounted scope acquires and releases demand.
@MainActor @Observable final class ResourceHandle<Value: Decodable & Sendable> {
    private(set) var entry: ResourceEntry
    private weak var owner: ResourceOwner?
    private let registration: ResourceRegistration<Value>
    private let type: String
    init(entry: ResourceEntry, owner: ResourceOwner, registration: ResourceRegistration<Value>) {
        self.entry = entry; self.owner = owner; self.registration = registration; self.type = String(reflecting: Value.self)
    }
    var value: Value? {
        _ = entry.publication
        return entry.decoded[type] as? Value
    }
    var error: String? {
        _ = entry.publication
        return entry.error ?? entry.decodeErrors[type]
    }
    var failure: ServerReadFailure? { entry.failure }
    var isStale: Bool { entry.isStale }
    var key: String { entry.key }
    func acquire() {
        guard let owner else { return }
        entry = owner.canonical(entry)
        entry.demands += 1; entry.lastUsed = Date()
        if entry.demands == 1 { owner.mounted(entry) }
    }
    func release() { owner?.release(entry) }
    func refresh() async {
        await owner?.refresh(entry)
    }
    /// Used only for accepted mutation results. It never attaches a probed revision.
    func accept(_ value: Value) where Value: Encodable {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        entry.generation += 1; entry.task?.cancel(); entry.task = nil
        entry.data = try? encoder.encode(value)
        entry.terminallyDiscarded = false
        entry.epoch = nil; entry.revision = nil
        entry.decoded = [type: value]
        entry.publication += 1
        entry.isStale = true
    }
}

/// Transport adapter also permits deterministic admission races without a server host.
struct ResourceRead: Sendable {
    let data: Data
    var epoch: String? = nil
    var revision: String? = nil
}
struct ResourceTransport: Sendable {
    var versions: @Sendable (APIClient, [String]) async throws -> ResourceVersions
    var read: @Sendable (APIClient, String, Bool) async throws -> ResourceRead
    static let live = ResourceTransport(versions: { try await $0.resourceVersions($1) }, read: { api, key, versioned in
        if versioned {
            do {
                let (wire, _) = try await api.responseData("/api/resource-body", query: [.init(name: "key", value: key)])
                let envelope = try JSONSerialization.jsonObject(with: wire) as? [String: Any]
                guard envelope?["key"] as? String == key,
                      let epoch = envelope?["epoch"] as? String, let revision = envelope?["revision"] as? String,
                      let body = envelope?["body"] else { throw URLError(.badServerResponse) }
                return ResourceRead(data: try JSONSerialization.data(withJSONObject: body, options: .fragmentsAllowed), epoch: epoch, revision: revision)
            } catch {
                // Only an unsupported resource endpoint permits an ordinary fallback.
                // Network, auth, update-gate and malformed-success failures get one request.
                guard let error = error as? APIError, error.status == 404 else { throw error }
                try Task.checkCancellation()
            }
        }
        guard let parts = URLComponents(string: key) else { throw URLError(.badURL) }
        return ResourceRead(data: try await api.responseData(parts.path, query: parts.queryItems ?? []).0)
    })
}

/// Only admitted display data enters this schema. Restoration never grants freshness.
struct ResourceCacheEnvelope: Codable {
    static let schema = 1
    let schemaIdentity: Int
    let server: String
    let key: String
    let data: Data
    let epoch: String?
    let revision: String?
    static func identity(_ key: String) -> String { "resource-body:v1:" + key }
}

/// One raw body per normalized server and exact key. Navigation and tails remain separate.
@MainActor final class ResourceOwner {
    private var api: APIClient
    private var serverIdentity: String
    private let cacheStore: LocalStore
    private let transport: ResourceTransport
    private var generation = 0
    private var entries: [String: ResourceEntry] = [:]
    private var handles: [String: [ObjectIdentifier: Any]] = [:]
    private var activeReads = 0
    private var probeFailures = 0
    private var probeRetryAt = Date.distantPast
    private var running = false
    private var pending = false
    private var scheduledReads: [String: Task<Void, Never>] = [:]
    private var reconciliation: Task<Void, Never>?
    private var wake: Task<Void, Never>?
    private var changes: Task<Void, Never>?
    private var expiry: Task<Void, Never>?
    init(api: APIClient, transport: ResourceTransport = .live, cacheStore: LocalStore = .shared) {
        self.api = api; self.serverIdentity = LocalStore.server(api.baseURL); self.transport = transport; self.cacheStore = cacheStore
    }
    var server: String { serverIdentity }
    var admissionGeneration: Int { generation }
    func handle<T>(_ registration: ResourceRegistration<T>) -> ResourceHandle<T> {
        let identity = server + ":" + registration.key
        let handleType = ObjectIdentifier(T.self)
        if let existing = handles[identity]?[handleType] as? ResourceHandle<T> { return existing }
        let entry: ResourceEntry
        if let existing = entries[identity] { entry = existing }
        else {
            entry = ResourceEntry(key: registration.key, server: server, versioned: registration.versioned)
            entries[identity] = entry
        }
        let type = String(reflecting: T.self)
        entry.signalRefreshInterval = registration.signalRefreshInterval
        entry.discardOnFailure = registration.discardOnFailure
        entry.decoders[type] = ResourceDecoder(decode: { data in
            let value = try registration.decode(data)
            return ResourceDecoded(value: value, deadline: registration.nextRefreshAt?(value))
        })
        let handle = ResourceHandle(entry: entry, owner: self, registration: registration)
        handles[identity, default: [:]][handleType] = handle
        if entry.data != nil { decodeRetained(entry, type: type) }
        else if entry.restoring == nil { restore(entry) }
        return handle
    }
    /// Cache I/O, envelope parsing and typed body decoding happen off the main actor.
    private func restore(_ entry: ResourceEntry) {
        // A known terminal document failure cannot be repaired by its older cache.
        guard !entry.terminallyDiscarded else { return }
        entry.restoreSequence += 1
        let expected = generation, sequence = entry.restoreSequence, publication = entry.publication
        let store = cacheStore, server = entry.server, key = entry.key, decoders = entry.decoders
        entry.restoring = Task { [weak self] in
            defer { if sequence == entry.restoreSequence { entry.restoring = nil } }
            let payload = await store.cachedResponse(server: server, id: ResourceCacheEnvelope.identity(key))
            let restored = await Task.detached { () -> (ResourceCacheEnvelope, [String: ResourceDecoded])? in
                guard let payload, let saved = try? JSONDecoder().decode(ResourceCacheEnvelope.self, from: payload),
                      saved.schemaIdentity == ResourceCacheEnvelope.schema, saved.server == server, saved.key == key,
                      (saved.epoch == nil) == (saved.revision == nil) else { return nil }
                var values: [String: ResourceDecoded] = [:]
                for (type, decoder) in decoders { values[type] = try? decoder.decode(saved.data) }
                guard !values.isEmpty else { return nil }
                return (saved, values)
            }.value
            guard let self, expected == generation, publication == entry.publication,
                  !entry.terminallyDiscarded, entry.data == nil else { return }
            if let (saved, values) = restored {
                entry.data = saved.data; entry.epoch = saved.epoch; entry.revision = saved.revision
                entry.decoded = values.mapValues(\.value); entry.acceptedDeadline = values.values.compactMap(\.deadline).min(); entry.publication += 1
                entry.isStale = true
            }
            entry.restoring = nil
            for type in entry.decoders.keys where entry.decoded[type] == nil { decodeRetained(entry, type: type) }
        }
    }
    private func decodeRetained(_ entry: ResourceEntry, type: String) {
        guard entry.decoded[type] == nil, !entry.decoding.contains(type), let data = entry.data, let decoder = entry.decoders[type] else { return }
        entry.decoding.insert(type)
        let publication = entry.publication
        Task {
            let result = await Task.detached { Result { try decoder.decode(data) } }.value
            entry.decoding.remove(type)
            guard publication == entry.publication else { decodeRetained(entry, type: type); return }
            switch result {
            case .success(let value): entry.decoded[type] = value.value; entry.publication += 1
            case .failure(let error): entry.decodeErrors[type] = error.localizedDescription; entry.publication += 1
            }
        }
    }
    func configure(api: APIClient) {
        stop()
        self.api = api; serverIdentity = LocalStore.server(api.baseURL)
        probeFailures = 0; probeRetryAt = .distantPast
        // Even an A → B → A switch receives a new generation.
        for entry in entries.values { entry.isStale = true }
    }
    func start() {
        guard !running else { return }
        running = true
        evict()
        let expected = generation, api = api
        changes = Task { [weak self] in
            guard let self else { return }
            await reconcile(includeUnversioned: true, waitForReads: false)
            var failures = 0
            while !Task.isCancelled, generation == expected {
                do {
                    var first = true
                    for try await _ in api.changes() {
                        guard generation == expected, !Task.isCancelled else { return }
                        if first { first = false; failures = 0; await reconcile(includeUnversioned: true, waitForReads: false) }
                        else { signal() }
                    }
                } catch { if Task.isCancelled { return } }
                guard generation == expected else { return }
                failures += 1
                do { try await Task.sleep(for: .seconds(min(ResourceLimits.maximumStreamRetrySeconds, 3 * (1 << min(failures - 1, 5))))) } catch { return }
            }
        }
    }
    func stop() {
        generation += 1; running = false; pending = false
        changes?.cancel(); changes = nil; wake?.cancel(); wake = nil; expiry?.cancel(); expiry = nil
        reconciliation?.cancel(); reconciliation = nil
        for task in scheduledReads.values { task.cancel() }; scheduledReads = [:]
        for entry in entries.values {
            entry.generation += 1; entry.task?.cancel(); entry.task = nil
            entry.deadline?.cancel(); entry.deadline = nil; entry.nextRefreshAt = nil; entry.isStale = true
            entry.restoring?.cancel(); entry.restoring = nil
            entry.fallback?.cancel(); entry.fallback = nil
        }
    }
    func signal() {
        guard wake == nil else { return }
        let expected = generation
        wake = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(ResourceLimits.coalescingSeconds)) } catch { return }
            guard let self, generation == expected else { return }
            wake = nil
            await reconcile(waitForReads: false)
        }
    }
    func invalidate(where affected: (String) -> Bool) {
        invalidate(entries.values.filter { $0.server == server && affected($0.key) }.map(\.key))
    }
    func invalidate(_ keys: [String]) {
        for entry in entries.values where entry.server == server && keys.contains(entry.key) {
            entry.isStale = true; supersede(entry)
            if entry.demands > 0 { Task { await self.refresh(entry) } }
        }
    }
    private var reconnectPending = false
    func reconcile(includeUnversioned: Bool = false, waitForReads: Bool = true) async {
        reconnectPending = reconnectPending || includeUnversioned
        pending = true
        if let reconciliation {
            await reconciliation.value
            if waitForReads { for read in scheduledReads.values { await read.value } }
            return
        }
        let expected = generation
        let task = Task { [weak self] in
            await Task.yield()
            guard let self else { return }
            while pending, generation == expected, !Task.isCancelled {
                pending = false
                let reconnect = reconnectPending; reconnectPending = false
                await probe(expected: expected, includeUnversioned: reconnect)
            }
        }
        reconciliation = task
        await task.value
        if generation == expected { reconciliation = nil }
        if waitForReads { for read in scheduledReads.values { await read.value } }
    }
    private func probe(expected: Int, includeUnversioned: Bool) async {
        let active = entries.values.filter { $0.server == server && $0.demands > 0 }
        let keys = active.filter(\.versioned).map(\.key)
        let api = api
        var versions: [String: (String, String?)] = [:]
        var failed = false
        do {
            guard Date() >= probeRetryAt else { throw URLError(.notConnectedToInternet) }
            for offset in stride(from: 0, to: keys.count, by: ResourceLimits.probeBatch) {
                let batch = Array(keys[offset..<min(offset + ResourceLimits.probeBatch, keys.count)])
                let result = try await transport.versions(api, batch)
                guard expected == generation, !Task.isCancelled else { return }
                for resource in result.resources where batch.contains(resource.key) { versions[resource.key] = (result.epoch, resource.revision) }
            }
            probeFailures = 0; probeRetryAt = .distantPast
        } catch {
            failed = true
            if Date() >= probeRetryAt {
                probeFailures += 1
                probeRetryAt = Date().addingTimeInterval(ResourceLimits.retrySeconds(probeFailures))
            }
        }
        guard expected == generation, !Task.isCancelled else { return }
        for entry in active where entry.demands > 0 {
            let stamp = versions[entry.key]
            let knownRevision = stamp?.1
            if !failed, entry.versioned { entry.supportsVersionedRead = knownRevision != nil }
            let changed = knownRevision != nil && (entry.epoch != stamp?.0 || entry.revision != knownRevision)
            if (changed || failed || (entry.versioned && knownRevision == nil)), !entry.isStale { entry.isStale = true }
            if let stamp, let revision = stamp.1 { entry.targetStamp = (stamp.0, revision); entry.probeGeneration += 1 }
            let fallback = failed || (entry.versioned && knownRevision == nil)
            let interval = entry.versioned ? ResourceLimits.fallbackSeconds : entry.signalRefreshInterval
            let fallbackDue = (fallback || !entry.versioned) && interval.map { Date().timeIntervalSince(entry.lastAttempt) >= $0 } == true
            if (fallback || !entry.versioned), let interval, !fallbackDue {
                scheduleFallback(entry, after: max(entry.lastAttempt.addingTimeInterval(interval), entry.retryAt))
            }
            if changed || entry.data == nil || includeUnversioned || fallbackDue || (entry.versioned && entry.isStale && !fallback) {
                // A probe records the wanted revision, but cannot revoke a one-shot read.
                // Its completion checks that stamp and performs one follow-up if needed.
                guard Date() >= entry.retryAt else { scheduleFallback(entry, after: entry.retryAt); continue }
                if scheduledReads[entry.key] == nil {
                    scheduledReads[entry.key] = Task {
                        await self.refresh(entry, automatic: true)
                        if expected == self.generation { self.scheduledReads[entry.key] = nil }
                    }
                }
            }
        }
        evict()
    }
    private func scheduleFallback(_ entry: ResourceEntry, after date: Date) {
        guard entry.fallback == nil, entry.demands > 0 else { return }
        let expected = generation
        entry.fallback = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(max(0, date.timeIntervalSinceNow))) } catch { return }
            guard let self, generation == expected, entry.demands > 0 else { return }
            entry.fallback = nil
            if Date() < entry.retryAt { scheduleFallback(entry, after: entry.retryAt); return }
            await refresh(entry, automatic: true)
        }
    }
    private func supersede(_ entry: ResourceEntry) {
        entry.generation += 1; entry.task?.cancel(); entry.task = nil
    }
    fileprivate func canonical(_ entry: ResourceEntry) -> ResourceEntry {
        let key = entry.server + ":" + entry.key
        if let current = entries[key] { return current }
        entries[key] = entry
        return entry
    }
    fileprivate func mounted(_ entry: ResourceEntry) {
        guard entry.server == server else { return }
        if entry.data == nil, entry.restoring == nil { restore(entry) }
        Task { await self.refresh(entry) }
    }
    fileprivate func release(_ entry: ResourceEntry) {
        entry.demands = max(0, entry.demands - 1); entry.lastUsed = Date()
        if entry.demands == 0 { entry.fallback?.cancel(); entry.fallback = nil; supersede(entry); entry.deadline?.cancel(); entry.deadline = nil; entry.nextRefreshAt = nil }
        evict()
    }
    fileprivate func refresh(_ entry: ResourceEntry, automatic: Bool = false) async {
        guard entry.server == server, !automatic || Date() >= entry.retryAt else { return }
        if let task = entry.task { await task.value; return }
        let expected = generation, readGeneration = entry.generation, api = api
        let task = Task { [weak self] in
            guard let self else { return }
            while activeReads >= ResourceLimits.concurrentReads {
                do { try await Task.sleep(for: .milliseconds(10)) } catch { return }
                guard expected == generation, readGeneration == entry.generation else { return }
            }
            guard expected == generation, readGeneration == entry.generation, !Task.isCancelled else { return }
            activeReads += 1; entry.lastAttempt = Date()
            defer { activeReads -= 1 }
            for attempt in 0..<2 {
                do {
                    let probeGeneration = entry.probeGeneration
                    let result = try await transport.read(api, entry.key, entry.versioned && entry.supportsVersionedRead)
                    guard expected == generation, readGeneration == entry.generation, entry.server == server, !Task.isCancelled else { return }
                    if probeGeneration != entry.probeGeneration,
                       let target = entry.targetStamp, let epoch = result.epoch, let revision = result.revision,
                       (target.0 != epoch || target.1 != revision) {
                        if attempt == 0 { continue }
                        throw APIError(status: 0, message: "The response changed during refresh. Try again.")
                    }
                    let data = result.data, epoch = result.epoch, revision = result.revision
                    let changed = entry.data != data
                    let decoders = entry.decoders.filter { changed || entry.decoded[$0.key] == nil }
                    let envelope = ResourceCacheEnvelope(schemaIdentity: ResourceCacheEnvelope.schema, server: entry.server,
                                                         key: entry.key, data: data, epoch: epoch, revision: revision)
                    let processed = try await Task.detached { () throws -> ([String: ResourceDecoded], Data?) in
                        var values: [String: ResourceDecoded] = [:]
                        for (type, decoder) in decoders { values[type] = try decoder.decode(data) }
                        return (values, try? JSONEncoder().encode(envelope))
                    }.value
                    guard expected == generation, readGeneration == entry.generation, entry.server == server, !Task.isCancelled else { return }
                    if probeGeneration != entry.probeGeneration,
                       let target = entry.targetStamp, let epoch, let revision,
                       (target.0 != epoch || target.1 != revision) {
                        if attempt == 0 { continue }
                        throw APIError(status: 0, message: "The response changed during refresh. Try again.")
                    }
                    entry.restoring?.cancel(); entry.restoring = nil
                    entry.terminallyDiscarded = false
                    if changed {
                        entry.data = data; entry.decoded = processed.0.mapValues(\.value)
                        entry.decodeErrors = [:]; entry.publication += 1
                    } else {
                        for (type, value) in processed.0 { entry.decoded[type] = value.value }
                        if !processed.0.isEmpty { entry.publication += 1 }
                    }
                    if entry.epoch != epoch { entry.epoch = epoch }
                    if entry.revision != revision { entry.revision = revision }
                    if entry.isStale != (revision == nil) { entry.isStale = revision == nil }
                    if entry.error != nil { entry.error = nil }
                    if entry.failure != nil { entry.failure = nil }
                    entry.lastUsed = Date()
                    entry.failures = 0; entry.retryAt = .distantPast
                    if let payload = processed.1 {
                        cacheStore.enqueueCacheResponse(server: entry.server, id: ResourceCacheEnvelope.identity(entry.key), data: payload)
                    }
                    if changed { entry.acceptedDeadline = processed.0.values.compactMap(\.deadline).min() }
                    if let deadline = entry.acceptedDeadline { schedule(entry, at: deadline) }
                    for type in entry.decoders.keys where entry.decoded[type] == nil { decodeRetained(entry, type: type) }
                    return
                } catch {
                    guard expected == generation, readGeneration == entry.generation, !Task.isCancelled else { return }
                    entry.failures += 1
                    entry.retryAt = Date().addingTimeInterval(ResourceLimits.retrySeconds(entry.failures))
                    if entry.discardOnFailure?(error) == true {
                        entry.terminallyDiscarded = true
                        entry.data = nil; entry.decoded = [:]; entry.decodeErrors = [:]; entry.publication += 1
                    }
                    entry.error = error.localizedDescription; entry.failure = ServerReadFailure(error); entry.isStale = true
                    return
                }
            }
        }
        entry.task = task
        await task.value
        if expected == generation, readGeneration == entry.generation {
            entry.task = nil
        }
    }
    fileprivate func schedule(_ entry: ResourceEntry, at timestamp: Double) {
        guard entry.demands > 0, timestamp > Date().timeIntervalSince1970, entry.nextRefreshAt != timestamp else { return }
        entry.nextRefreshAt = timestamp; entry.deadline?.cancel()
        let expected = generation
        entry.deadline = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(max(0, timestamp - Date().timeIntervalSince1970))) } catch { return }
            guard let self, expected == generation, entry.demands > 0 else { return }
            entry.nextRefreshAt = nil; entry.isStale = true; await refresh(entry)
        }
    }
    private func evict() {
        let inactive = entries.values.filter { $0.demands == 0 }.sorted { $0.lastUsed > $1.lastUsed }
        var bytes = 0
        for (index, entry) in inactive.enumerated() {
            bytes += entry.data?.count ?? 0
            if index >= ResourceLimits.inactiveEntries || bytes > ResourceLimits.inactiveBytes || Date().timeIntervalSince(entry.lastUsed) > ResourceLimits.inactiveSeconds {
                supersede(entry); entry.data = nil; entry.publication += 1; entry.decoded = [:]; entry.isStale = true
                let identity = entry.server + ":" + entry.key
                entries[identity] = nil
                handles[identity] = nil
            }
        }
        expiry?.cancel(); expiry = nil
        if let oldest = entries.values.filter({ $0.demands == 0 }).map(\.lastUsed).min() {
            let delay = max(0.01, ResourceLimits.inactiveSeconds - Date().timeIntervalSince(oldest))
            expiry = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(delay)) } catch { return }
                self?.evict()
            }
        }
    }
}
